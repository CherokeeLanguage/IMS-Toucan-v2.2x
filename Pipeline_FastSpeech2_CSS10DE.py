"""
Train a non-autoregressive FastSpeech 2 model on the german single speaker dataset by Hokuspokus

This requires having a trained TransformerTTS model in the right directory to knowledge distill the durations.
"""

import os
import random
import warnings

import torch

from FastSpeech2.FastSpeech2 import FastSpeech2
from FastSpeech2.FastSpeechDataset import FastSpeechDataset
from FastSpeech2.fastspeech2_train_loop import train_loop

warnings.filterwarnings("ignore")

torch.manual_seed(17)
random.seed(17)


def build_path_to_transcript_dict():
    path_to_transcript = dict()
    with open("Corpora/CSS10_DE/transcript.txt", encoding="utf8") as f:
        transcriptions = f.read()
    trans_lines = transcriptions.split("\n")
    for line in trans_lines:
        if line.strip() != "":
            path_to_transcript["Corpora/CSS10/" + line.split("|")[0]] = line.split("|")[2]
    return path_to_transcript


if __name__ == '__main__':
    print("Preparing")
    cache_dir = os.path.join("Corpora", "CSS10_DE")
    save_dir = os.path.join("Models", "FastSpeech2", "SingleSpeaker", "CSS10_DE")
    if not os.path.exists(cache_dir):
        os.makedirs(cache_dir)
    if not os.path.exists(save_dir):
        os.makedirs(save_dir)

    path_to_transcript_dict = build_path_to_transcript_dict()

    train_set = FastSpeechDataset(path_to_transcript_dict,
                                  train=True,
                                  acoustic_model_name="Transformer_German_Single.pt",
                                  cache_dir=cache_dir,
                                  lang="de",
                                  min_len=50000,
                                  max_len=230000)
    valid_set = FastSpeechDataset(path_to_transcript_dict,
                                  train=False,
                                  acoustic_model_name="Transformer_German_Single.pt",
                                  cache_dir=cache_dir,
                                  lang="de",
                                  min_len=50000,
                                  max_len=230000)

    model = FastSpeech2(idim=131, odim=80, spk_embed_dim=None)

    print("Training model")
    train_loop(net=model,
               train_dataset=train_set,
               eval_dataset=valid_set,
               device=torch.device("cuda:2"),
               config=model.get_conf(),
               save_directory=save_dir,
               epochs=3000,  # just kill the process at some point
               batchsize=32,
               gradient_accumulation=1)