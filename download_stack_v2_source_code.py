import os
import json
import time
import argparse
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from tqdm import tqdm
import smart_open
from datetime import timedelta
from multiprocessing import Manager
from itertools import islice
import boto3
from botocore.config import Config
import psutil
import boto3
from botocore import UNSIGNED
from botocore.config import Config
from datasets import load_dataset


def chunked_iterable(iterable, chunk_size):
    """惰性分块迭代器"""
    it = iter(iterable)
    while True:
        chunk = list(islice(it, chunk_size))
        if not chunk:
            break
        yield chunk

def pop_invalid_col(row):
    for col in ["visit_date", "revision_date", "committer_date", "gha_event_created_at", "gha_created_at"]:
        del row[col]
    return row

def download_worker(row, s3):
    """下载工作线程"""
    # print(row.keys())
    # blob_id, src_encoding = row["blob_id"], row["src_encoding"]
    blob_id = row["blob_id"]
    src_encoding = 'UTF-8'
    s3_url = f"s3://softwareheritage/content/{blob_id}"
    try:
        with smart_open.open(s3_url, "rb", compression=".gz", transport_params={"client": s3}) as fin:
            content = fin.read().decode(src_encoding)
            row['content'] = content
            return pop_invalid_col(row)
            # return (blob_id, {"blob_id": blob_id, "content": content}, True, time.time())
    except Exception as e:
        row['content'] = ""
        return pop_invalid_col(row)
        # return (blob_id, {"blob_id": blob_id, "error": str(e)}, False, time.time())

def process_chunk(ds_chunk_iter, output_file_path, process_idx, progress_dict, speed_dict):
    """处理分片的进程函数（含速度监控）"""
    s3 = boto3.client("s3", config=Config(signature_version=UNSIGNED))
    chunk_file = f"{output_file_path}/part{process_idx}.jsonl"
    chunk = list(ds_chunk_iter)
    total = len(chunk)
    processed = 0
    
    # 速度跟踪变量
    last_update_time = time.time()
    last_processed = 0
    speeds = []
    
    with open(chunk_file, "a", encoding="utf-8") as output_file:
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(download_worker, row, s3): row for row in chunk}
            
            for future in as_completed(futures):
                data = future.result()
                json_line = json.dumps(data, ensure_ascii=False)
                output_file.write(json_line + "\n")
                processed += 1
                
                # 更新进度
                progress_dict[process_idx] = processed / total
                
                # 计算并更新速度
                current_time = time.time()
                time_elapsed = max(0.001, current_time - last_update_time)
                current_speed = (processed - last_processed) / time_elapsed
                speeds.append(current_speed)
                if len(speeds) > 3:  # 保留最近3个速度样本
                    speeds.pop(0)
                
                avg_speed = sum(speeds) / len(speeds) if speeds else current_speed
                speed_dict[process_idx] = avg_speed
                
                last_update_time = current_time
                last_processed = processed
    
    return chunk_file

def parallel_download_with_monitoring(ds_iter, output_file_path, total_count, num_processes=30, chunk_size=1000):
    """
    带完整监控的惰性并行下载
    
    参数:
    - ds_iter: 数据集迭代器
    - output_file_path: 输出文件路径
    - s3_config: S3配置
    - total_count: 总数据量
    - num_processes: 进程数
    - chunk_size: 分块大小
    """
    start_time = time.time()
    last_progress = 0
    last_speed_update = start_time
    speed_history = []
    
    with Manager() as manager:
        progress_dict = manager.dict({i: 0 for i in range(num_processes)})
        speed_dict = manager.dict({i: 0 for i in range(num_processes)})
        
        with ProcessPoolExecutor(max_workers=num_processes) as executor:
            chunk_gen = chunked_iterable(ds_iter, chunk_size * num_processes)
            futures = []
            process_idx = 0
            total_current = 0
            # 自定义进度条格式
            bar_format = "{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}, {postfix}]"
            
            with tqdm(total=total_count, desc="Downloading", bar_format=bar_format) as pbar:
                for big_chunk in chunk_gen:
                    sub_chunks = [iter(big_chunk[i::num_processes]) for i in range(num_processes)]
                    
                    for i, sub_chunk in enumerate(sub_chunks):
                        futures.append(
                            executor.submit(
                                process_chunk,
                                sub_chunk,
                                output_file_path,
                                process_idx % num_processes,
                                progress_dict,
                                speed_dict
                            )
                        )
                        process_idx += 1
                    
                    # 实时更新进度和速度
                    while True:
                        time.sleep(0.5)  # 控制更新频率
                        
                        # 计算总体进度
                        current_progress = total_current + sum(
                            int(progress * (chunk_size * num_processes / num_processes))
                            for i, progress in progress_dict.items()
                        )
                        # print(current_progress)
                        # 计算总体速度
                        current_speed = sum(speed_dict.values())
                        speed_history.append(current_speed)
                        if len(speed_history) > 10:
                            speed_history.pop(0)
                        avg_speed = sum(speed_history) / len(speed_history)
                        
                        # 计算ETA
                        remaining_items = total_count - current_progress
                        eta = remaining_items / avg_speed if avg_speed > 0 else 0
                        
                        # 更新进度条
                        delta = current_progress - last_progress
                        # print("delta: ", delta)
                        if delta > 0:
                            pbar.update(delta)
                            last_progress = current_progress
                        
                        pbar.set_postfix({
                            "speed": f"{avg_speed:.1f} files/s",
                            "ETA": str(timedelta(seconds=int(eta))),
                            "workers": f"{num_processes}p×5t"
                        })
                        # 判断future的状态，pop掉已经完成的future
                        for index, future in enumerate(futures):
                            if future.done():
                                total_current += chunk_size
                                futures.pop(index)
                        # 检查是否需要提交新任务
                        # print(len(futures), futures)
                        if len(futures) < num_processes * 2:  # 保持任务管道充足
                            break

                # 等待所有任务完成
                for future in as_completed(futures):
                    future.result()  # 只是为了确保完成
                    # 继续更新进度直到全部完成
    
    total_time = time.time() - start_time
    print(f"\nFinal Statistics:")
    print(f"Total time: {timedelta(seconds=int(total_time))}")
    print(f"Average speed: {total_count/total_time:.1f} files/sec")
    print(f"Peak speed: {max(speed_history or [0]):.1f} files/sec")
    print(f"Output file: {output_file_path}")


# 使用示例
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    
    parser.add_argument('-i', '--input_path')
    parser.add_argument('-o', '--output_path')

    args = parser.parse_args()
    # process(args.input_path, args.output_path)
    base_path = args.input_path
    
    # input_path = "/mnt/public/datasets/RefineCode-code-corpus-meta-with-blob-id"  # 假设这里有若干个.parquet 文件
    # output_path = "/mnt/public/datasets/RefineCode-code-corpus-with-source-code/output.jsonl"
    skip_lang = ['CSV', "JSON", "JSON5", "JSONLD", "JSON_with_Comments", "JSONiq"]
    # base_path = "/mnt/public/datasets/stack-v2-dedup-index/data/"
    for lang in tqdm(os.listdir(base_path)):
        if lang in skip_lang:
            continue
        input_path = os.path.join(base_path, lang)
        # output_path = f"/mnt/public/datasets/stack-v2-dedup-index-source-code/{lang}"
        output_path = f"{args.output_path}/{lang}"
        if os.path.exists(output_path):
            continue
        else:
            os.makedirs(output_path)
        print(f"{lang} 开始处理")
        ds = load_dataset(input_path, split="train")

        
        # ds是你的原始迭代器，不需要转换为列表
        parallel_download_with_monitoring(
            ds_iter=ds,  # 原始数据集迭代器
            output_file_path=output_path,
            total_count=len(ds),  # 总数据量
            num_processes=48,
            chunk_size=4000  # 每个进程处理1000条记录
        )
        print(f"{lang} 处理完成")