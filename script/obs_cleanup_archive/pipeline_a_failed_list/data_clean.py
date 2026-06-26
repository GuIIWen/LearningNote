"""
【脚本作用】
    多进程清洗失败清单：解析每行 `路径|类型|错误原因`，对齐 metadata_backup/ 下
    逐文件的元数据 JSON 取云端修改时间，做任务内去重 + 排序，导出 TSV 报表。
    属于 Pipeline A 的【旧版】清洗脚本（按逐文件 JSON 取时间）。
    新版见 04_clean_and_export_tsv.py（读单个大 JSON，更快）。二选一即可。

【使用前需修改】（见下方“配置区”）
    - DOWNLOAD_ROOT  : 本地下载根目录（download_data）
    - META_ROOT      : 元数据目录（02_download_failed_migrate_lists.sh 归集的 metadata_backup）
    - OUTPUT_TSV_DIR : TSV 报表输出目录（默认 task_analysis_tsv）
    - MAX_WORKERS    : 并发进程数（默认 8，按 CPU 调整）

【输入 / 输出】
    输入: DOWNLOAD_ROOT/<task_id>/**/*.txt + META_ROOT/<对应>.txt.json
    输出: OUTPUT_TSV_DIR/analysis_<task_id>.tsv
"""
import os
import re
from urllib.parse import unquote
import pandas as pd
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

# ================= 配置区 =================
DOWNLOAD_ROOT = "./download_data"
META_ROOT = "./metadata_backup"
OUTPUT_TSV_DIR = "./task_analysis_tsv"
MAX_WORKERS = 8  # 根据压测机CPU核心数调整（例如8、16、32）
# ==========================================

TIME_PATTERN = re.compile(r'LastModified:\s*([^\n\r]+)', re.IGNORECASE)

def process_single_task(task_id):
    """单任务独立清洗、内部去重和内部排序"""
    task_path = os.path.join(DOWNLOAD_ROOT, task_id)
    task_records = []
    
    # 递归扫描当前任务下的所有 txt 文件
    for root, dirs, files in os.walk(task_path):
        for file in files:
            if not file.endswith('.txt'):
                continue
                
            file_full_path = os.path.join(root, file)
            
            # 1. 动态对齐对应的元数据 JSON 获取云端最后修改时间
            rel_dir = os.path.relpath(root, DOWNLOAD_ROOT)
            json_file_path = os.path.join(META_ROOT, rel_dir, f"{file}.json")
            
            cloud_error_time = "UNKNOWN"
            if os.path.exists(json_file_path):
                try:
                    with open(json_file_path, 'r', encoding='utf-8', errors='ignore') as jf:
                        meta_content = jf.read()
                        time_match = TIME_PATTERN.search(meta_content)
                        if time_match:
                            cloud_error_time = time_match.group(1).strip()
                except:
                    pass

            # 2. 解析当前日志文件的每一行
            try:
                with open(file_full_path, 'r', encoding='utf-8', errors='ignore') as f:
                    for line in f:
                        line = line.strip()
                        if not line or '|' not in line:
                            continue
                        
                        parts = line.split('|')
                        if len(parts) >= 3:
                            raw_url_path = parts[0]
                            file_type = parts[1]
                            error_reason = parts[2]
                            
                            task_records.append({
                                "File_Path": unquote(raw_url_path),  # 还原 %2F -> /
                                "Type": file_type,
                                "Error_Reason": error_reason,
                                "Cloud_Failure_Time": cloud_error_time
                            })
            except Exception:
                pass

    # 3. 仅在任务内部进行数据提纯
    if task_records:
        df = pd.DataFrame(task_records)
        
        # 记录内部去重前的行数
        rows_before = len(df)
        
        # 【去重】仅在当前子任务内部，根据文件路径和错误原因去重，保留最后一次重试记录
        df = df.drop_duplicates(subset=["File_Path", "Error_Reason"], keep="last")
        
        # 【排序】仅在当前子任务内部，优先按错误原因归类，同原因下按云端失败时间先后排序
        df = df.sort_values(by=["Error_Reason", "Cloud_Failure_Time"])
        
        # 生成绝对路径
        output_path = os.path.abspath(os.path.join(OUTPUT_TSV_DIR, f"analysis_{task_id}.tsv"))
        df.to_csv(output_path, sep='\t', index=False, encoding='utf-8')
        
        rows_after = len(df)
        duplicated_count = rows_before - rows_after
        
        return task_id, rows_after, duplicated_count, output_path
    
    return task_id, 0, 0, None


if __name__ == '__main__':
    start_time = datetime.now()
    print(f"[{start_time.strftime('%H:%M:%S')}] 启动多进程并发清洗流（任务间完全独立）...")

    if not os.path.exists(OUTPUT_TSV_DIR):
        os.makedirs(OUTPUT_TSV_DIR)

    try:
        task_dirs = [d for d in os.listdir(DOWNLOAD_ROOT) if os.path.isdir(os.path.join(DOWNLOAD_ROOT, d))]
    except FileNotFoundError:
        print(f"错误: 无法在本地找到下载根目录 {DOWNLOAD_ROOT}，请检查路径。")
        exit(1)

    print(f"扫描到 {len(task_dirs)} 个任务目录，开始并行独立处理...\n")

    success_tasks = 0
    
    # 4. 调度多进程池
    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_single_task, tid): tid for tid in task_dirs}
        
        for future in as_completed(futures):
            tid, valid_rows, dup_rows, out_path = future.result()
            if out_path:
                success_tasks += 1
                # 严格打印每个任务的最终结果绝对路径、有效行数和内部去重数
                print(f" [✔] 任务 ID: {tid}")
                print(f"     ├─ 内部去重: 过滤了 {dup_rows} 条重复重试记录")
                print(f"     ├─ 最终有效: {valid_rows} 行")
                print(f"     └─ 结果路径: {out_path}\n")
            else:
                print(f" [.] 任务 ID: {tid} -> 无有效失败数据，已跳过。\n")

    end_time = datetime.now()
    print("-" * 80)
    print(f"清洗完成！总耗时: {(end_time - start_time).total_seconds():.2f} 秒")
    print(f"成功为 {success_tasks} 个任务生成了独立的内部去重排序 TSV 报表。")
    print(f"所有结果均已存入目录: {os.path.abspath(OUTPUT_TSV_DIR)}")
    print("-" * 80)
