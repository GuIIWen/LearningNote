"""
【脚本作用】
    用原生 Linux 管道（obsutil ls | grep | awk | uniq）直接把 frames-video-s3/ 下的
    一级子目录前缀流式去重写盘，Python 几乎不参与逐行计算，内存占用极低。
    属于 Pipeline B 的【早期/备选】直接扫桶方案（legacy_scan_prefixes_python.py 的 shell 管道版）。

【使用前需修改】（见下方“配置区”）
    - OBSUTIL_EXEC     : obsutil 可执行文件绝对路径（默认 /root/obsutil/obsutil/obsutil）
    - BASE_PATH        : 要扫描的 OBS 前缀（默认 .../frames-video-s3）
    - PREFIX_OUTPUT_FILE : 一级目录前缀输出文件（默认 ./search_results/first_layer_prefixes.txt）
    - shell_cmd 中的切片关键字 'frames-video-s3/' : BASE_PATH 变了需同步修改。

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
    print(f"======================================================================", flush=True)
    print(f"[{start_time.strftime('%H:%M:%S')}] 启动内核级一级目录提取器（彻底干掉 Python 内存积压）", flush=True)
    print(f"======================================================================", flush=True)

    if not os.path.exists(OBSUTIL_EXEC):
        print(f" [!] 错误：在路径 {OBSUTIL_EXEC} 下未找到可执行文件！", flush=True)
        sys.exit(1)

    os.makedirs(os.path.dirname(PREFIX_OUTPUT_FILE), exist_ok=True)

    # 核心大招：拼装一条纯原生的 Linux 管道命令
    # 1. obsutil 狂喷原始路径
    # 2. grep 只留标准路径行
    # 3. awk 瞬间以 "frames-video-s3/" 为切片，抓取第一级子目录名
    # 4. uniq 动态去重（因为 obsutil 吐出的数据天然按前缀有序，uniq 就能实现流式去重，不需要 sort 排序，极省内存）
    shell_cmd = (
        f"{OBSUTIL_EXEC} ls {BASE_PATH}/ -limit=0 2>/dev/null | "
        f"grep '^obs://' | "
        f"awk -F 'frames-video-s3/' '{{print $2}}' | awk -F '/' '{{print $1}}' | "
        f"uniq > {PREFIX_OUTPUT_FILE}"
    )

    print(f"已经在后台打通 Linux 内核高速管道流...", flush=True)
    print(f"当前由系统内核直接处理数据并实时写盘，Python 进程处于绝对休眠状态（内存占用近乎为0）。", flush=True)
    print(f"你可以新开窗口运行 `watch -n 1 ls -lh {PREFIX_OUTPUT_FILE}` 查看前缀文件的暴涨情况...\n", flush=True)

    # 拉起原生 shell 执行，Python 不参与任何逐行文本计算
    process = subprocess.Popen(shell_cmd, shell=True)
    process.wait()

    end_time = datetime.now()
    print("-" * 75, flush=True)
    print(f" 阶段一物理打桩大捷！", flush=True)
    print(f"1. 运行总耗时 : {(end_time - start_time).total_seconds():.2f} 秒")
    print(f"2. 【最终一级目录静态清单】已稳稳落盘: {os.path.abspath(PREFIX_OUTPUT_FILE)}")
    print("-" * 75, flush=True)
