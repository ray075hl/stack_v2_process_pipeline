from typing import Dict
import numpy as np
import torch
from transformers import pipeline
from transformers import AutoTokenizer
import ray
import os
import glob
import argparse

ray.init()
# Pick the largest batch size that can fit on our GPUs.
# If doing CPU inference you might need to lower considerably (e.g. to 10).


DEVICE = "cuda" if torch.cuda.is_available() else "cpu"


class TextClassifier:
    def __init__(self, 
                 model_path="/mnt/public/huangliang45/code_score/code_quality_model",
                 tokenizer_path="/mnt/public/models/ds_v3",
                 ):
        self.classifier = pipeline("text-classification", model=model_path, device="cuda")
        self.tokenizer = AutoTokenizer.from_pretrained(tokenizer_path, use_fast=True)

    def get_token_length(self, text):
        tokens = self.tokenizer(text)["input_ids"]
        return len(tokens)
    
    def __call__(self, batch: Dict[str, np.ndarray]):
        # Convert the numpy array of images into a list of PIL images which is the format the HF pipeline expects.
        # print(batch['content'])
        batch_dict_of_lists = batch.to_dict(orient='list')
        texts = batch_dict_of_lists["text"]
        batch_scores = ['100'] * len(texts)
        
        valid_indexs = []
        valid_texts = []
        invalid_list = []
        for index, text in enumerate(texts):
            if self.get_token_length(text) > 8192*4:  # 大于32k不使用回归模型打分
                invalid_list.append((index, text))
            else:
                valid_indexs.append(index)
                valid_texts.append(text)
        if len(valid_texts) == 0:
            pass
        else:
            outputs = self.classifier(valid_texts, batch_size=len(valid_texts))            
            for index, output in zip(valid_indexs, outputs):
                batch_scores[index] = str(output["score"])

        batch["quality_model_score"]= batch_scores
        return batch
    
    def process(self, batch: Dict[str, np.ndarray]):
        batch_dict_of_lists = batch.to_dict(orient='list')
        texts = batch_dict_of_lists["text"]
        batch_scores = ['100'] * len(texts)
        
        valid_indexs = []
        valid_texts = []
        invalid_list = []
        for index, text in enumerate(texts):
            if get_token_length(text) > 8192*4:
                invalid_list.append((index, text))
            else:
                valid_indexs.append(index)
                valid_texts.append(text)
        if len(valid_texts) == 0:
            pass
        else:
            outputs = self.classifier(valid_texts, batch_size=len(valid_texts))
            
            for index, output in zip(valid_indexs, outputs):
                batch_scores[index] = str(output["score"])

        batch["quality_model_score"]= batch_scores
        return batch

if __name__ == "__main__":
    
    parser = argparse.ArgumentParser()
    
    parser.add_argument('-i', '--input_path_pattern')
    parser.add_argument('-o', '--output_path')

    args = parser.parse_args()
    
    # input_path_pattern = '/mnt/public/huangliang45/tmp_save_code/ray_test_only3col/*/*.parquet'
    # output_path = '/mnt/public/huangliang45/tmp_save_code/ray_test_only3col_with_score'
    input_path_pattern = args.input_path_pattern
    output_path = args.output_path
    
    input_file_list = glob.glob(input_path_pattern)

    ds = ray.data.read_parquet(input_file_list)

    predictions = ds.map_batches(
        TextClassifier,
        batch_format="pandas",
        compute=ray.data.ActorPoolStrategy(size=nodes_num*8),  
        # concurrency=16, # Use 4 model replicas. Change this number based on the number of GPUs in your cluster.
        num_gpus=1,  # Specify GPUs per model replica
        batch_size=1 # Use batch size from above.
    )

    predictions.write_parquet(output_path, min_rows_per_file=10000)