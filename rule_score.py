import ray
import sys
import argparse
import os
import glob 
import json


ROOT_PATH = os.getcwd()

sys.path.append(ROOT_PATH)
ray.init(runtime_env={"working_dir": ROOT_PATH})


from utils.preprocessing import get_program_lang, get_doc_type
from pipeline.compute_quality_signals import ComputeCodeQualitySignal
from pipeline.compute_filtering import CodeFilter


ccqs = ComputeCodeQualitySignal()
code_filter = CodeFilter()


context_key = 'content'

def estimate_size_from_content_batch(batch, encoding='utf-8'):
    """
    通过字符串内容估算文件大小
    :param content: 字符串内容
    :param encoding: 文件编码方式
    :return: 估算的文件大小(字节)
    """
    def calc_content_size(row):
        content = row[context_key]
        if encoding.lower() in ('utf-8', 'utf8'):
            # UTF-8编码: ASCII字符1字节，其他字符2-4字节
            return len(content.encode('utf-8'))
        elif encoding.lower() in ('utf-16', 'utf16'):
            # UTF-16编码: 大部分字符2字节，某些4字节
            return len(content.encode('utf-16-le'))  # 小端序，不带BOM
        else:
            # 其他编码如ASCII/latin-1: 每个字符1字节
            return len(content.encode(encoding))
        
    batch['file_size_in_byte'] = batch.apply(calc_content_size, axis=1)
    return batch


def get_program_lang_batch(batch):

    def get_program_lang_func(row):
        lang = get_program_lang(row['filename'], row['ext'])
        return lang
    
    batch['program_lang'] = batch.apply(get_program_lang_func, axis=1)
    return batch

def get_filename_suffix_batch(batch):
    
    def get_ext(row):
        filename = row['filename']
        name, ext = os.path.splitext(filename)
        ext = ext[1:]
        return ext
    
    batch['ext'] = batch.apply(get_ext, axis=1)
    return batch
    
def compute_qs(row, ccqs: ComputeCodeQualitySignal):
    try:
        final_result = ccqs.evaluate(
                        text=row[context_key],
                        filename=row['filename'],
                        lang=row['lang'],
                        ext=row['ext'],
                        file_size_in_byte=row['file_size_in_byte'],
                        program_lang=row['program_lang'],
                        doc_type=row['doc_type'],
                    )
        # final_result 的类型是str
        return json.loads(final_result)['quality_signal']
    except Exception as e:
        return {}


def get_rule_quality_signal_batch(batch):        
    batch['quality_signal'] = batch.apply(compute_qs, axis=1,args=(ccqs,))
    return batch

def filter_code(row, code_filter: CodeFilter):
    try:
        final_result = code_filter.evaluate(
                        doc_type=row['doc_type'],
                        lang=row['lang'],
                        program_lang=row['program_lang'],
                        quality_signal=row['quality_signal']
                    )
        
        # final_result的类型是str
        final_result_dict = json.loads(final_result)
        return final_result_dict["effective"], final_result_dict["hit_map"]
    except Exception as e:
        return "0", {}

def add_filter_tag_batch(batch):
    batch[['effective','hit_map']] = batch.apply(filter_code, axis=1 ,args=(code_filter,), result_type='expand')
    return batch
        

if __name__ == "__main__":

    parse = argparse.ArgumentParser(
        prog="rule based code data clean program",
        description="代码数据规则过滤",
    )

    parse.add_argument("-p", "--pattern", type=str, help="输入文件的pattern")
    parse.add_argument("-s", "--saved_path", type=str, help="打标后文件的保存路径")

    '''
    python rule_score.py \
        --pattern "/mnt/public/datasets/stack-v2-dedup-index-source-code/*.jsonl" \
        --saved_path xxxxx
    '''
    # 解析入参
    args = parse.parse_args()
    input_path = args.pattern
    saved_path = args.saved_path

    print(f"输入文件的路径：{input_path}")
    print(f"文件保存路径：{saved_path}")

    if os.path.exists(saved_path):
        pass
    else:
        os.makedirs(saved_path)
    
    # 读取文件
    ori_filelist = glob.glob(input_path)
    # 过滤空文件
    filelist = [p for p in ori_filelist if os.path.getsize(p) > 0]
    all_data = ray.data.read_json(filelist)
    all_data = all_data.drop_columns(cols=["detected_licenses"])
    all_data = all_data.drop_columns(cols=["gha_license_id"])
    # 增加属性
    all_data = all_data.add_column("doc_type", lambda x: 'code')
    all_data = all_data.add_column("lang", lambda x: 'en')
    
    
    all_data = all_data.map_batches(get_filename_suffix_batch, batch_format='pandas')
    all_data = all_data.map_batches(get_program_lang_batch, batch_format='pandas')
    all_data = all_data.map_batches(estimate_size_from_content_batch, batch_format='pandas')

    print('quality score')
    all_data = all_data.map_batches(get_rule_quality_signal_batch, batch_format='pandas')

    print('filter')
    all_data = all_data.map_batches(add_filter_tag_batch, batch_format='pandas')

    # 根据标识进行过滤
    all_data = all_data.filter(lambda row: row["effective"] == "1")  # 只保留可用的数据
    # 将content 字段rename 为text 方便后续的处理
    all_data = all_data.rename_columns({"content": "text"})
    all_data.write_parquet(saved_path, partition_cols=["language"])  # 按语言来进行存储 "language" 字段是stack v2 自带的
    
