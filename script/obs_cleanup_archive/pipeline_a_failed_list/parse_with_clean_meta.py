"""
【脚本作用】
    多进程清洗失败清单（新版）：解析每行 `路径|类型|错误原因`，从步骤一生成的单个
    大 JSON 映射表里 O(1) 取云端修改时间，做任务内去重 + 排序，导出 TSV 报表。
    属于 Pipeline A 的第 4 步（清洗）。替代旧版 legacy_clean_and_export_tsv.py。

【使用前需修改】（见下方“配置区”）
    - DOWNLOAD_ROOT  : 本地下载根目录（download_data）
    - META_JSON_FILE : 步骤 3（03_query_cloud_lastmodified.py）产出的时间映射表（默认 clean_cloud_metadata.json）
    - OUTPUT_TSV_DIR : TSV 报表输出目录（默认 task_analysis_tsv）
    - MAX_WORKERS    : 并发进程数（默认 8，按 CPU 调整）

【输入 / 输出】
    输入: DOWNLOAD_ROOT/<task_id>/**/*.txt + META_JSON_FILE
    输出: OUTPUT_TSV_DIR/analysis_<task_id>.tsv

【运行顺序】必须先跑 03_query_cloud_lastmodified.py 生成 META_JSON_FILE，否则报错退出。
"""
import os
import json
from urllib.parse import unquote
import pandas as pd
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

# ================= 配置区 =================
DOWNLOAD_ROOT = "./download_data"
META_JSON_FILE = "./clean_cloud_metadata.json"
OUTPUT_TSV_DIR = "./task_analysis_tsv"
MAX_WORKERS = 8  # 涉及大量大文件磁盘读写，建议设置为 8 到 16
# ==========================================

def process_single_task_heavy(task_id, meta_map_path):
    """单任务独立进程清洗：内存秒查时间 + 极速去重排序"""
    task_path = os.path.join(DOWNLOAD_ROOT, task_id)
    if not os.path.exists(task_path):
        return task_id, 0, None
        
    # 每个子进程独立延迟加载 JSON，避免大对象跨进程传输的 IPC 开销
    with open(meta_map_path, 'r', encoding='utf-8') as jf:
        meta_map = json.load(jf)
        
    task_records = []
    
    # 递归扫描当前任务
    for root, _, files in os.walk(task_path):
        for file in files:
            if not file.endswith('.txt'):
                continue
                
            file_full_path = os.path.join(root, file)
            
            # 从内存字典中 O(1) 速度直接抓取真实的云端失败时间
            lookup_key = f"{task_id}_{file}"
            cloud_error_time = meta_map.get(lookup_key, "UNKNOWN")
            
            # 高效流式读取日志行
            try:
                with open(file_full_path, 'r', encoding='utf-8', errors='ignore') as f:
                    for line in f:
                        line = line.strip()
                        if not line or '|' not in line:
                            continue
                        parts = line.split('|')
                        if len(parts) >= 3:
                            task_records.append({
                                "File_Path": unquote(parts[0]), # 还原 %2F -> /
                                "Type": parts[1],
                                "Error_Reason": parts[2],
                                "Cloud_Failure_Time": cloud_error_time
                            })
            except Exception:
                pass

    if task_records:
        df = pd.DataFrame(task_records)
        
        # 【去重】仅在当前子任务内部针对业务路径和原因去重
        df = df.drop_duplicates(subset=["File_Path", "Error_Reason"], keep="last")
        
        # 【排序】内部排序
        df = df.sort_values(by=["Error_Reason", "Cloud_Failure_Time"])
        
        # 导出独立的 TSV 结果
        out_path = os.path.abspath(os.path.join(OUTPUT_TSV_DIR, f"analysis_{task_id}.tsv"))
        df.to_csv(out_path, sep='\t', index=False, encoding='utf-8')
        return task_id, len(df), out_path
        
    return task_id, 0, None


if __name__ == '__main__':
    start_time = datetime.now()
    print(f"[{start_time.strftime('%H:%M:%S')}] 启动步骤二：多进程并发清洗与独立去重流...")

    if not os.path.exists(OUTPUT_TSV_DIR):
        os.makedirs(OUTPUT_TSV_DIR)

    if not os.path.exists(META_JSON_FILE):
        print(f"错误：找不到步骤一生成的元数据映射表 {META_JSON_FILE}，请先运行步骤一！")
        exit(1)

    # 扫描任务目录
    try:
        task_dirs = [d for d in os.listdir(DOWNLOAD_ROOT) if os.path.isdir(os.path.join(DOWNLOAD_ROOT, d))]
    except FileNotFoundError:
        print(f"错误: 找不到本地下载根目录 {DOWNLOAD_ROOT}")
        exit(1)

    print(f"共发现 {len(task_dirs)} 个任务，正在调度 {MAX_WORKERS} 个并行清洗进程...")

    success_tasks = 0
    
    # 开启并发进程池
    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # 将元数据 JSON 的路径传进子进程，让子进程自己读，性能损耗最小
        futures = {executor.submit(process_single_task_heavy, tid, META_JSON_FILE): tid for tid in task_dirs}
        
        for future in as_completed(futures):
            tid, valid_rows, out_path = future.result()
            if out_path:
                success_tasks += 1
                print(f" [✔] 任务 ID: {tid} 清洗去重完成")
                print(f"     └─ 【结果绝对路径】: {out_path} (共 {valid_rows} 行)")
            else:
                print(f" [.] 任务 ID: {tid} 未检测到有效失败明细，已跳过")

    print("-" * 80)
    print(f"所有独立任务清洗成功！总耗时: {(datetime.now() - start_time).total_seconds():.2f} 秒")
    print(f"所有生成的 TSV 均保存在: {os.path.abspath(OUTPUT_TSV_DIR)}")
    print("-" * 80)
