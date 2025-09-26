import ray
import re
import os
import argparse
import pandas as pd

from transformers import AutoTokenizer
from ray.data import DataContext
from ray.data.llm import vLLMEngineProcessorConfig, build_llm_processor

from llm_prompt_for_code_score import quality_scoring_prompt


# ray init
ROOT_PATH = os.getcwd()

sys.path.append(ROOT_PATH)
ray.init(runtime_env={"working_dir": ROOT_PATH})
# data context
DataContext.get_current().wait_for_min_actors_s = 36000


class AddTokenLength:
    def __init__(self, tokenizer_path="/mnt/public/models/Qwen2.5-Coder-32B-Instruct"):
        self.tokenizer = AutoTokenizer.from_pretrained(tokenizer_path, use_fast=True)

    def get_token_length(self, row):
        tokens = self.tokenizer(row["text"])["input_ids"]
        return str(len(tokens))

    def get_text_token_length_function(self, batch):
        """
        获取text字段的token数量
        """ 
        batch = batch.assign(tokens=batch.apply(self.get_token_length, axis=1))
        return batch

def preprocess_function(row):
    system_prompt = "You are a helpful assistant."
    user_prompt = quality_scoring_prompt.format_map({"CONTENT": row['text'], "LANGUAGE": row['language']})
    return dict(
        messages=[
            {"role": "system", "content": f"{system_prompt}"},
            {"role": "user", "content": f"{user_prompt}"},
        ],
        sampling_params=dict(
            temperature=0.7,
            max_tokens=4096,  # 相当于hf里的max_new_tokens
            detokenize=False,
            repetition_penalty=1.05, 
            top_p=0.8, 
            top_k=20, 
        ),
    )
    

def extract_score(text):
    # 使用正则匹配 [[数字]] 并提取数字
    match = re.search(r'\[\[(\d+)\]\]', text)
    if match:
        score = match.group(1)  # 提取匹配到的数字
        return str(int(score))
    else:
        print("未找到分数")
        return "0"
    
def postprocess_function(row):
    # 获取 ratting
    quality_model_score = extract_score(row["generated_text"])
    row['quality_model_score'] = quality_model_score
    return row


def collect_values(group: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "id": [group["id"].iloc[0]],
        "repo_name": [group["repo_name"].iloc[0]],
        "path": [group["path"].iloc[0]],
        "language": [group["language"].iloc[0]],
        "text": [group["text"].iloc[0]],
        "resp": [group["resp"].iloc[0]],
        "quality_model_score": [str(min([float(value) for value in group["resp"]]))]
        
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    
    parser.add_argument('-i', '--input_path')
    parser.add_argument('-o', '--output_path')
    parser.add_argument('-t', '--tokenizer_path', type='str', default='/mnt/public/models/Qwen2.5-Coder-32B-Instruct')
    parser.add_argument('-m', '--model_path', type='str', default='/mnt/public/models/Qwen2.5-Coder-32B-Instruct')
    parser.add_argument('-s', '--seq_max_length', type='str', default='65536')  # 64k
    parser.add_argument('-n', '--nodes', type='str', default='16')  # 机器数量
    parser.add_argument('-r', '--repeat_score_times', type='str', default='5')  # 多次打分取最小值，用来消除随机性
    args = parser.parse_args()

    max_seq_length = int(args.seq_max_length)
    
    input_path = args.input_path
    output_path = args.output_path
    tokenizer_path = args.tokenizer_path
    model_path = args.model_path
    machine_number = int(args.nodes)
    repeat_score_times = int(args.repeat_score_times)
    
    config = vLLMEngineProcessorConfig(
        model_source=model_path,
        engine_kwargs=dict(
            max_model_len=131072,  # 128k
            enable_chunked_prefill=True,
            enable_prefix_caching=True,
            tensor_parallel_size=8,  # 一个llm使用几个gpu加载
        ),
        concurrency=machine_number,  # 实例个数 可以认为是机器数
        batch_size=4,
    )

    processor = build_llm_processor(
        config,
        preprocess=preprocess_function,
        postprocess=lambda row: dict(
            resp=extract_score(row["generated_text"]), **row
        ),
    )

    ds = ray.data.read_parquet(input_path, override_num_blocks=1500)

    # 经过回归模型打分的数据
    ds_short_context = ds.filter(lambda row: row["quality_model_score"] != '100')
    ds_short_context.write_parquet(output_path, min_rows_per_file=10000)

    # 大于32k的样本在上一轮打分中被赋值'100'
    ds_long_context = ds.filter(lambda row: row["quality_model_score"] == '100')
    ds_long_context = ds_long_context.map_batches(
            AddTokenLength(tokenizer_path=tokenizer_path).get_text_token_length_function, batch_format='pandas'
        )

    # 32K < 长文本 < 64K
    ds_long_context_lt_64k = ds_long_context.filter(lambda row: int(row['tokens']) < max_seq_length)

    ds_long_context_lt_64k_repeat5times = \
        ds_long_context_lt_64k.flat_map(
            lambda x: [{**x, "id": hash(x['text'])} for _ in range(repeat_score_times)]
            ) # 重复5次, 最后得分取最小
    ds_long_context_lt_64k_repeat5times = processor(ds_long_context_lt_64k_repeat5times)

    ds_long_context_lt_64k_grouped = \
        ds_long_context_lt_64k_repeat5times.groupby("id").map_groups(collect_values, batch_format="pandas")
    
     # select columns drop "tokens"
    ds_long_context_lt_64k_grouped = \
        ds_long_context_lt_64k_grouped.select_columns(['text', 'repo_name', 'quality_model_score', 'path', 'language'])
        
    ds_long_context_lt_64k_grouped.write_parquet(output_path, min_rows_per_file=10000)
