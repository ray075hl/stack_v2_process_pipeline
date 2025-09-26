import os
import json
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from tqdm import tqdm
import glob

def check_text_size(text, max_size_mb=8):
    """
    检查文本大小是否超过指定MB数
    
    :param text: 要检查的文本字符串
    :param max_size_mb: 最大允许的MB数，默认为8
    :return: (是否超过, 实际字节数)
    """
    byte_size = len(text.encode('utf-8'))
    max_bytes = max_size_mb * 1024 * 1024
    
    is_over = byte_size > max_bytes
    return is_over, byte_size

def convert_value2str(sample):
    for key in sample:
        if sample[key] is None:
            sample[key] = ""
        elif isinstance(sample[key], str):
            pass
        else:
            sample[key] = str(sample[key])
    return sample  

def process_line(line):
    """处理单行JSONL"""
    try:
        sample = json.loads(line)
        del sample["detected_licenses"]
        del sample["star_events_count"]
        del sample["fork_events_count"]
        del sample["gha_license_id"]
        del sample["is_vendor"]
        del sample["length_bytes"]
        return json.dumps(sample)
    except Exception as e:
        print("error: ", e)
        print(line)
        return None

def process_single_file(input_path, output_dir, max_lines_per_file=10000, line_workers=4):
    """处理单个文件，分割为多个最大10000行的文件"""
    # 准备输出目录
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    # 获取输入文件名（不含扩展名）
    input_filename = Path(input_path).stem
    
    with open(input_path, 'r', encoding='utf-8') as infile:
        file_count = 0
        line_count = 0
        current_batch = []
        
        with ThreadPoolExecutor(max_workers=line_workers) as executor:
            for line in infile:
                processed_line = process_line(line)
                if processed_line is not None:
                    current_batch.append(processed_line)
                    line_count += 1
                
                # 当达到最大行数时，写入文件并重置
                if len(current_batch) >= max_lines_per_file:
                    # 生成输出文件名
                    output_filename = f"{input_filename}_part{file_count:04d}.jsonl"
                    output_filepath = output_path / output_filename
                    
                    # 写入当前批次
                    with open(output_filepath, 'w', encoding='utf-8') as outfile:
                        outfile.write('\n'.join(current_batch) + '\n')
                    
                    file_count += 1
                    current_batch = []
                    print(f"Created: {output_filename} with {len(current_batch)} lines")
            
            # 处理剩余的行
            if current_batch:
                output_filename = f"{input_filename}_part{file_count:04d}.jsonl"
                output_filepath = output_path / output_filename
                
                with open(output_filepath, 'w', encoding='utf-8') as outfile:
                    outfile.write('\n'.join(current_batch) + '\n')
                print(f"Created: {output_filename} with {len(current_batch)} lines")
    
    return input_path

def process_all_files(file_paths, output_dir, file_workers=4, **kwargs):
    """处理所有文件（文件级并行）"""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    
    with ProcessPoolExecutor(max_workers=file_workers) as executor:
        # 提交所有文件处理任务
        futures = [
            executor.submit(process_single_file, fp, output_dir, **kwargs)
            for fp in file_paths
        ]
        
        # 用进度条显示处理进度
        for future in tqdm(futures, desc="Processing files"):
            future.result()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    
    parser.add_argument('-i', '--input_path')
    parser.add_argument('-o', '--output_path')

    args = parser.parse_args()
    # 配置示例
    # base_path = '/mnt/public/datasets/stack-v2-dedup-index-source-code'
    base_path = args.input_path
    output_path = args.output_path
    for lang in os.listdir(base_path):

        input_path = f'{base_path}/{lang}/*.jsonl'
        input_files_ori = glob.glob(input_path)
        
        input_files = [p for p in input_files_ori if os.path.getsize(p) > 0]
        output_directory = f'{output_path}/{lang}'
        
        # 启动处理
        process_all_files(
            input_files,
            output_directory,
            file_workers=max(len(input_files), 1),  # 同时处理多个文件
            line_workers=16,                        # 每个文件用16个线程处理行
            max_lines_per_file=10000               # 每个输出文件最大10000行
        )