import glob
import ray
import os
import argparse
import json

from tqdm import tqdm
from ray.data.aggregate import AggregateFn


ray.init()


def merge_repo(input_language_path, output_path, sub_dir_):

    ds = ray.data.read_parquet(input_language_path, columns=["repo_name", "path", "text"])  # 如果资源有限 需要控制并行度
    ds = ds.select_columns(["repo_name", "path", "text"])
    
    count_agg = AggregateFn(
        init=lambda k: [],
        accumulate_row=lambda counter, row: counter + [[str(row["path"]), str(row["text"])]],
        merge=lambda c1, c2: c1 + c2,
        name="filelist",
    )

    ds_grouped = ds.groupby("repo_name")
    result = ds_grouped.aggregate(count_agg)  
    result.write_parquet(os.path.join(output_path, sub_dir_))


if __name__ == "__main__":
    
    parse = argparse.ArgumentParser(
        prog="group by repo name",
        description="文件级代码合并repo",
    )

    parse.add_argument("-i", "--input_path", type=str, help="输入文件的路径, 子文件夹是各个语言的数据")
    parse.add_argument("-s", "--saved_path", type=str, help="保存路径")
    
    args = parse.parse_args()
    input_path = args.input_path
    saved_path = args.saved_path

    print(f"输入文件的路径：{input_path}")
    print(f"文件保存路径：{saved_path}")

    for sub_dir in tqdm(os.listdir(input_path)):
        print(sub_dir)
        input_sub_path = os.path.join(input_path, sub_dir)
        try:
            sub_dir_name = sub_dir.split("language=")[1]
        except:
            continue
        
        merge_repo(input_sub_path, saved_path, sub_dir_name)