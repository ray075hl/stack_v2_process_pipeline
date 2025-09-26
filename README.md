# stack v2 处理流程

1. 首先下载index 

2. 根据index下载源码
python download_stack_v2_source_code.py --input_path xxx --output_path yyy

3. 过滤问题json + 分割成最大10000行的文件
python fix_json_data.py --input_path yyy --output_path zzz

4. 规则过滤
```
bash script/setup_ray_env.sh  # 运行前先设置自己的env_path

python rule_score.py --pattern "xxx/*/*.jsonl" --saved_path '保存目录'

```

5. 模型打分
```
# 对于长度小于32k的样本
python regression_model_score.py --input_path_pattern xxx/*.parquet --output_path '保存目录'

# 对于长度大于32k且小于64k的样本, 使用llm进行打分, 大模型打分具有随机性，重复打分5次取最小值，来消除随机的影响
python llm_score.py \
    --input_path 'xxx' \
    --output_path 'yyy' \
    --tokenizer_path 'zzz' \
    --model_path 'model_path' \
    --seq_max_length '65536' \
    --nodes '16' \
    --repeat_score_times '5'
```

