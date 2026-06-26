"""
【脚本作用】
    对本地已下载的每个失败清单 .txt 文件，调用 obsutil stat 查询其云端
    LastModified（最后修改时间），并发汇总成一份干净的 {key: 时间} 映射表 JSON。
    属于 Pipeline A 的第 3 步（元数据查询），【只查不删】。

【使用前需修改】（见下方“配置区”）
    - DOWNLOAD_ROOT    : 本地下载根目录（由 02_download_failed_migrate_lists.sh 产出的 download_data）
    - META_OUTPUT_FILE : 输出的时间映射表 JSON 路径（默认 clean_cloud_metadata.json）
    - OBS_BUCKET_BASE  : OBS 桶/前缀路径（默认 obs://obs-zyt-temp/hsms）
    - MAX_WORKERS      : 并发进程数（默认 32，按 CPU 调整）
    - ./obsutil        : 代码按【当前工作目录】下的 ./obsutil 调用，需 cd 到其所在目录；
                         且机器上需已配置好 obsutil 凭证（~/.obsutilconfig）。

【输入 / 输出】
    输入: DOWNLOAD_ROOT/<task_id>/**/*.txt
    输出: META_OUTPUT_FILE（key = {task_id}_{文件名}，value = 云端时间或 UNKNOWN）
"""
import os
import re
import json
import subprocess
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

# ================= 配置区 =================
DOWNLOAD_ROOT = "./download_data"
META_OUTPUT_FILE = "./clean_cloud_metadata.json"  # 最终生成的干净时间映射表
OBS_BUCKET_BASE = "obs://obs-zyt-temp/hsms"
MAX_WORKERS = 32  # 压测机性能强，直接开32并发狂轰云端
# ==========================================

TIME_PATTERN = re.compile(r'LastModified:\s*([^\n\r]+)', re.IGNORECASE)

def get_single_file_meta(task_id, rel_path_from_task, file_full_path):
    """单独查询一个文件的云端时间"""
    obs_file_url = f"{OBS_BUCKET_BASE}/{task_id}/{rel_path_from_task}"
    filename = os.path.basename(file_full_path)
    
    try:
        # 实时调取全局标准的 obsutil 查云端
        cmd = ["./obsutil", "stat", obs_file_url]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
        
        if result.returncode == 0:
            match = TIME_PATTERN.search(result.stdout)
            if match:
                cloud_time = match.group(1).strip()
                # 唯一标识 Key: task_id + 文件名
                return f"{task_id}_{filename}", cloud_time
    except Exception:
        pass
    return f"{task_id}_{filename}", "UNKNOWN"

if __name__ == '__main__':
    start_time = datetime.now()
    print(f"[{start_time.strftime('%H:%M:%S')}] 启动纯净元数据并发提取器...")

    if not os.path.exists("./obsutil"):
        print("错误：当前目录下未找到 ./obsutil 可执行文件！")
        exit(1)

    # 1. 扫描本地已下载的所有文件，构建查询队列
    tasks_todo = []
    for task_id in os.listdir(DOWNLOAD_ROOT):
        task_path = os.path.join(DOWNLOAD_ROOT, task_id)
        if not os.path.isdir(task_path):
            continue
            
        for root, _, files in os.walk(task_path):
            for file in files:
                if file.endswith('.txt'):
                    file_full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(file_full_path, task_path)
                    tasks_todo.append((task_id, rel_path, file_full_path))

    total_files = len(tasks_todo)
    print(f"共扫描到本地存在 {total_files} 个失败清单文件，开始并发请求云端元数据...")

    # 2. 多进程高并发查询
    metadata_map = {}
    success_count = 0
    
    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(get_single_file_meta, tid, rel, fp): (tid, re) for tid, rel, fp in tasks_todo}
        
        for i, future in enumerate(as_completed(futures), 1):
            key, cloud_time = future.result()
            metadata_map[key] = cloud_time
            if cloud_time != "UNKNOWN":
                success_count += 1
            
            # 每 100 个打印一次进度，免得刷屏
            if i % 100 == 0 or i == total_files:
                print(f" 进度: [{i}/{total_files}] | 成功获取有效时间: {success_count} 个")

    # 3. 写入最终的干净 JSON 映射文件
    with open(META_OUTPUT_FILE, 'w', encoding='utf-8') as jf:
        json.dump(metadata_map, jf, ensure_ascii=False, indent=4)

    end_time = datetime.now()
    print("-" * 60)
    print(f"元数据整理完成！总耗时: {(end_time - start_time).total_seconds():.2f} 秒")
    print(f"【干净元数据字典已生成】: {os.path.abspath(META_OUTPUT_FILE)}")
    print("-" * 60)
