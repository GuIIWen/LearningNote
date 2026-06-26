"""
【脚本作用】
    流式扫描桶（obsutil ls -limit=0），按正则匹配 lidar 雷达帧路径，URL 解码后去重落盘。
    属于 Pipeline B 的【早期/备选】直接扫桶方案（已被 codex 清理流水线取代，保留作参考）。

【使用前需修改】（见下方“配置区”）
    - OBSUTIL_EXEC  : obsutil 可执行文件绝对路径（默认 /root/obsutil/obsutil/obsutil）
    - BASE_PATH     : 要扫描的 OBS 前缀（默认 .../frames-video-s3）
    - OUTPUT_FILE   : 命中结果输出文件（默认 ./search_results/matched_files_list.txt）
    - MATCH_PATTERN : 命中正则（默认匹配 uid/.../(scam_recalib/frames/lidar/|frames/lidar/)）；
                      要捞别的路径时改这里。

【输入 / 输出】
    输入: 无（直接扫桶）
    输出: OUTPUT_FILE
"""
import os
import re
import sys
import subprocess
from datetime import datetime
from urllib.parse import unquote

# ================= 配置区 =================
OBSUTIL_EXEC = "/root/obsutil/obsutil/obsutil"
BASE_PATH = "obs://obs-zyt-temp/jfs/auto.prod.sz/data/frames-video-s3"
OUTPUT_FILE = "./search_results/matched_files_list.txt"
# ==========================================

MATCH_PATTERN = re.compile(r'uid:\d+/uid/.*?(?:scam_recalib/frames/lidar/|frames/lidar/)')

if __name__ == '__main__':
    start_time = datetime.now()
    print(f"======================================================================", flush=True)
    print(f"[{start_time.strftime('%H:%M:%S')}] 启动流式亿级绞杀器 (高频心跳日志版)...", flush=True)
    print(f"======================================================================", flush=True)
    print(f"确定调用路径: {OBSUTIL_EXEC}", flush=True)

    if not os.path.exists(OBSUTIL_EXEC):
        print(f" [!] 错误：在路径 {OBSUTIL_EXEC} 下未找到可执行文件！", flush=True)
        sys.exit(1)

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        pass

    print(f"正在向华为云发起深度拉流连接，亿级大桶初始化可能需要十几秒，请稍候...", flush=True)
    cmd = [OBSUTIL_EXEC, "ls", f"{BASE_PATH}/", "-limit=0"]
    
    # 开启底层管道
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    total_scanned = 0
    matched_count = 0
    raw_lines_count = 0
    seen_keys = set()

    with open(OUTPUT_FILE, 'a', encoding='utf-8', buffering=1024*1024) as af:
        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            
            line_str = line.strip()
            if not line_str:
                continue
                
            raw_lines_count += 1
            
            # 【心跳日志 1】只要云端开始回显任何东西，前 20 行杂质全量实时打印出来，让你看到连接状态
            if raw_lines_count <= 20:
                print(f" [云端初始化握手回显] {line_str}", flush=True)
                if raw_lines_count == 20:
                    print(" [系统通知] 握手及元数据流建立完毕，开始进入亿级全量流式清洗，每扫描 1 万条上报一次...", flush=True)
            
            # 捕获异常
            if "Error:" in line_str or "auth" in line_str.lower():
                print(f"\n [!!! 华为云 OBSUTIL 报错提示 !!!] -> {line_str}\n", flush=True)
                sys.exit(1)
                
            if not line_str.startswith("obs://"):
                continue
                
            total_scanned += 1
            clean_line = re.sub(r'^obs://[^/]+/', '', line_str)
            
            if MATCH_PATTERN.search(clean_line):
                decoded_key = unquote(clean_line)
                bucket_name = line_str.split('/')[2]
                final_url = f"obs://{bucket_name}/{decoded_key}"
                
                if final_url not in seen_keys:
                    seen_keys.add(final_url)
                    af.write(f"{final_url}\n")
                    matched_count += 1
            
            # 【心跳日志 2】降低门槛，每扫 1 万条文件就疯狂打印一次进度，彻底告别假死
            if total_scanned % 10000 == 0:
                print(f" -> [高频对账] 已成功扫描云端对象: {total_scanned} 个 | 累计命中并落盘雷达帧: {matched_count} 条", flush=True)

    end_time = datetime.now()
    print("-" * 75, flush=True)
    print(f" 运行报告：", flush=True)
    print(f"1. 总耗时 : {(end_time - start_time).total_seconds():.2f} 秒", flush=True)
    print(f"2. 云端共扫描文件总数 : {total_scanned} 个", flush=True)
    print(f"3. 最终命中的雷达唯一路径 : {matched_count} 行", flush=True)
    print(f"4. 结果绝对路径: {os.path.abspath(OUTPUT_FILE)}", flush=True)
    print("-" * 75, flush=True)
