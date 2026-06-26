"""
【脚本作用】
    流式扫描桶（obsutil ls -limit=0），用 Python 逐行提取 frames-video-s3/ 下的一级
    子目录前缀并去重落盘。属于 Pipeline B 的【早期/备选】直接扫桶方案（已被 codex 清理
    流水线的“基于清单”方案取代，保留作参考）。

【使用前需修改】（见下方“配置区”）
    - OBSUTIL_EXEC     : obsutil 可执行文件绝对路径（默认 /root/obsutil/obsutil/obsutil）
    - BASE_PATH        : 要扫描的 OBS 前缀（默认 .../frames-video-s3）
    - PREFIX_OUTPUT_FILE : 一级目录前缀输出文件（默认 ./search_results/first_layer_prefixes.txt）
    - keyword 'frames-video-s3/' : 切片关键字；若 BASE_PATH 变了需同步改切片逻辑。

【输入 / 输出】
    输入: 无（直接扫桶）
    输出: PREFIX_OUTPUT_FILE
"""
import os
import sys
import subprocess
from datetime import datetime

# ================= 配置区 =================
OBSUTIL_EXEC = "/root/obsutil/obsutil/obsutil"
BASE_PATH = "obs://obs-zyt-temp/jfs/auto.prod.sz/data/frames-video-s3"
PREFIX_OUTPUT_FILE = "./search_results/first_layer_prefixes.txt"
# ==========================================

if __name__ == '__main__':
    start_time = datetime.now()
    print("======================================================================", flush=True)
    print(f"[{start_time.strftime('%H:%M:%S')}] 启动前缀提取器（自适应换行与尾随空格修正版）", flush=True)
    print("======================================================================", flush=True)

    if not os.path.exists(OBSUTIL_EXEC):
        print(f" [!] 错误：在路径 {OBSUTIL_EXEC} 下未找到可执行文件！", flush=True)
        sys.exit(1)

    os.makedirs(os.path.dirname(PREFIX_OUTPUT_FILE), exist_ok=True)

    # 纯净拉流，不加任何 shell 管道，直接用列表形式启动，确保 0 内存积压
    cmd = [OBSUTIL_EXEC, "ls", f"{BASE_PATH}/", "-limit=0"]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    total_lines = 0
    unique_prefixes = set()

    # buffering=1 开启行缓冲模式
    with open(PREFIX_OUTPUT_FILE, 'w', encoding='utf-8', buffering=1) as pf:
        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            
            if not line:
                continue
                
            line_str = line.strip()
            
            # 过滤掉第2行的元数据行、表头、以及Next marker等系统杂质
            if not line_str.startswith("obs://"):
                continue

            total_lines += 1
            
            # 【精准提取】彻底解决尾随空格和列数错位问题
            # 逻辑：直接通过字符串裁切找到路径中 'frames-video-s3/' 的位置，紧跟其后的就是一级目录
            keyword = "frames-video-s3/"
            if keyword in line_str:
                parts = line_str.split(keyword)[1].split('/')
                if len(parts) > 0:
                    first_dir = parts[0].strip() # strip() 瞬间干掉所有尾随空格隐患
                    
                    if first_dir and first_dir not in unique_prefixes:
                        unique_prefixes.add(first_dir)
                        pf.write(f"{first_dir}\n")
                        
                        # 每发现 2000 个新前缀，控制台立刻打印对账
                        if len(unique_prefixes) % 2000 == 0:
                            print(f" -> [目录捕获] 已安全落盘有效一级目录: {len(unique_prefixes)} 个 (已扫描路径流水: {total_lines} 条)...", flush=True)

    end_time = datetime.now()
    print("-" * 75, flush=True)
    print(" 一级目录提取大捷！", flush=True)
    print(f"1. 过滤云端有效路径行数 : {total_lines} 行")
    print(f"2. 最终成功落盘的一级目录 : {len(unique_prefixes)} 个")
    print(f"3. 运行总耗时           : {(end_time - start_time).total_seconds():.2f} 秒")
    print(f"4. 【最终清单绝对路径】   : {os.path.abspath(PREFIX_OUTPUT_FILE)}")
    print("-" * 75, flush=True)
