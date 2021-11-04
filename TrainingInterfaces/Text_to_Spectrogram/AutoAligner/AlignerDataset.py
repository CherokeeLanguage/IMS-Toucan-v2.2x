import os
import random

import soundfile as sf
import torch
from numpy import trim_zeros
from torch.multiprocessing import Manager
from torch.multiprocessing import Process
from torch.utils.data import Dataset
from tqdm import tqdm
from unsilence import Unsilence

from Preprocessing.ArticulatoryCombinedTextFrontend import ArticulatoryCombinedTextFrontend
from Preprocessing.AudioPreprocessor import AudioPreprocessor


class AlignerDataset(Dataset):

    def __init__(self,
                 path_to_transcript_dict,
                 cache_dir,
                 lang,
                 loading_processes=8,
                 min_len_in_seconds=1,
                 max_len_in_seconds=20,
                 cut_silences=True,
                 rebuild_cache=False,
                 verbose=False,
                 include_priors=False):
        self.include_priors = include_priors
        os.makedirs(os.path.join(cache_dir, "normalized_audios"), exist_ok=True)
        os.makedirs(os.path.join(cache_dir, "normalized_unsilenced_audios"), exist_ok=True)
        if not os.path.exists(os.path.join(cache_dir, "aligner_train_cache.pt")) or rebuild_cache:
            resource_manager = Manager()
            self.path_to_transcript_dict = resource_manager.dict(path_to_transcript_dict)
            key_list = list(self.path_to_transcript_dict.keys())
            random.shuffle(key_list)
            # build cache
            print("... building dataset cache ...")
            self.datapoints = resource_manager.list()
            # make processes
            key_splits = list()
            process_list = list()
            for i in range(loading_processes):
                key_splits.append(key_list[i * len(key_list) // loading_processes:(i + 1) * len(key_list) // loading_processes])
            for key_split in key_splits:
                process_list.append(
                    Process(target=self.cache_builder_process,
                            args=(key_split,
                                  lang,
                                  min_len_in_seconds,
                                  max_len_in_seconds,
                                  cut_silences,
                                  cache_dir,
                                  verbose),
                            daemon=True))
                process_list[-1].start()
            for process in process_list:
                process.join()
            self.datapoints = list(self.datapoints)
            tensored_datapoints = list()
            # we had to turn all of the tensors to numpy arrays to avoid shared memory
            # issues. Now that the multi-processing is over, we can convert them back
            # to tensors to save on conversions in the future.
            print("Converting into convenient format...")
            norm_waves = list()
            for datapoint in tqdm(self.datapoints):
                tensored_datapoints.append([torch.Tensor(datapoint[0]),
                                            torch.LongTensor(datapoint[1]),
                                            torch.Tensor(datapoint[2]),
                                            torch.LongTensor(datapoint[3])])
                norm_waves.append(torch.Tensor(datapoint[-1]))

            self.datapoints = tensored_datapoints

            pop_indexes = list()
            for index, el in enumerate(self.datapoints):
                try:
                    if len(el[0][0]) != 66:
                        pop_indexes.append(index)
                except TypeError:
                    pop_indexes.append(index)
            for pop_index in sorted(pop_indexes, reverse=True):
                print(f"There seems to be a problem in the transcriptions. Deleting datapoint {pop_index}.")
                self.datapoints.pop(pop_index)

            # save to cache
            torch.save((self.datapoints, norm_waves), os.path.join(cache_dir, "aligner_train_cache.pt"))
        else:
            # just load the datapoints from cache
            self.datapoints = torch.load(os.path.join(cache_dir, "aligner_train_cache.pt"), map_location='cpu')
            self.datapoints = self.datapoints[0]  # don't need the waves here

            for el in self.datapoints:
                try:
                    if len(el[0][0]) != 66:
                        print(f"Inconsistency in text tensors in {cache_dir}!")
                except TypeError:
                    print(f"Inconsistency in text tensors in {cache_dir}!")

        print(f"Prepared {len(self.datapoints)} datapoints in {cache_dir}.")

    def cache_builder_process(self,
                              path_list,
                              lang,
                              min_len,
                              max_len,
                              cut_silences,
                              cache_dir,
                              verbose):
        process_internal_dataset_chunk = list()
        tf = ArticulatoryCombinedTextFrontend(language=lang, use_word_boundaries=True)
        _, sr = sf.read(path_list[0])
        ap = AudioPreprocessor(input_sr=sr, output_sr=None, melspec_buckets=80, hop_length=256, n_fft=1024, cut_silence=cut_silences)
        # the unsilence tool unfortunately writes files with a sample rate that we cannot control, so we need special cases
        ap_post = None

        for path in tqdm(path_list):
            if self.path_to_transcript_dict[path].strip() == "":
                continue

            name = path.split("/")[-1].split(".")[:-1]
            if len(name) == 1:
                name = name[0]
            else:
                name = ".".join(name)
            suffix = path.split(".")[-1]
            _norm_unsilenced_path = os.path.join(os.path.join(cache_dir, "normalized_unsilenced_audios"), name + "_unsilenced." + suffix)
            _norm_path = os.path.join(os.path.join(cache_dir, "normalized_audios"), name + "." + suffix)
            if not os.path.exists(_norm_unsilenced_path):
                if not os.path.exists(_norm_path):
                    wave, sr = sf.read(path)
                    dur_in_seconds = len(wave) / sr
                    if not (min_len <= dur_in_seconds <= max_len):
                        if verbose:
                            print(f"Excluding {_norm_unsilenced_path} because of its duration of {round(dur_in_seconds, 2)} seconds.")
                            continue
                    try:
                        norm_wave = ap.audio_to_wave_tensor(normalize=True, audio=wave)
                    except ValueError:
                        continue
                    dur_in_seconds = len(norm_wave) / 16000
                    if not (min_len <= dur_in_seconds <= max_len):
                        if verbose:
                            print(f"Excluding {_norm_unsilenced_path} because of its duration of {round(dur_in_seconds, 2)} seconds.")
                        continue
                    sf.write(file=_norm_path, data=norm_wave.detach().numpy(), samplerate=sr)
                unsilence = Unsilence(_norm_path)
                unsilence.detect_silence(silence_time_threshold=0.1, short_interval_threshold=0.03, stretch_time=0.025)
                unsilence.render_media(_norm_unsilenced_path, silent_speed=12, silent_volume=0, audio_only=True)
            try:
                wave, sr = sf.read(_norm_unsilenced_path)
                if ap_post is None:
                    ap_post = AudioPreprocessor(input_sr=sr, output_sr=16000, melspec_buckets=80, hop_length=256, n_fft=1024, cut_silence=cut_silences)
                if sr != ap_post.sr:
                    print(f"Inconsistent sample rate! {_norm_unsilenced_path}")
                    continue
                norm_wave = ap_post.resample(torch.Tensor(wave))
                dur_in_seconds = len(norm_wave) / 16000
                if not (min_len <= dur_in_seconds <= max_len):
                    if verbose:
                        print(f"Excluding {_norm_unsilenced_path} because of its duration of {round(dur_in_seconds, 2)} seconds.")
                    continue
            except RuntimeError:
                # not sure why this sometimes happens, but it is very rare, so it should be fine.
                continue

            norm_wave = torch.tensor(trim_zeros(norm_wave.numpy()))
            # raw audio preprocessing is done
            transcript = self.path_to_transcript_dict[path]
            cached_text = tf.string_to_tensor(transcript).squeeze(0).cpu().numpy()
            try:
                if len(cached_text[0]) != 66:
                    print(f"There seems to be a problem with the following transcription: {transcript}")
                    continue
            except TypeError:
                print(f"There seems to be a problem with the following transcription: {transcript}")
                continue
            cached_text_len = torch.LongTensor([len(cached_text)]).numpy()
            cached_speech = ap.audio_to_mel_spec_tensor(audio=norm_wave, normalize=False, explicit_sampling_rate=16000).transpose(0, 1).cpu().numpy()
            cached_speech_len = torch.LongTensor([len(cached_speech)]).numpy()
            process_internal_dataset_chunk.append([cached_text,
                                                   cached_text_len,
                                                   cached_speech,
                                                   cached_speech_len,
                                                   norm_wave.cpu().detach().numpy()])
        self.datapoints += process_internal_dataset_chunk

    def __getitem__(self, index):
        return self.datapoints[index][0], \
               self.datapoints[index][1], \
               self.datapoints[index][2], \
               self.datapoints[index][3]

    def __len__(self):
        return len(self.datapoints)