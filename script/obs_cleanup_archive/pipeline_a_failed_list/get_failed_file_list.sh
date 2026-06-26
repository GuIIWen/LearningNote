#!/bin/bash
# =====================================================================
# 【脚本作用】
#   批量下载每个 hsms 任务下的 object_migrate_result_list/（失败迁移结果清单），
#   并把 obsutil 运行时生成的 .obsutil_checkpoint* 元数据归集到统一目录。
#   属于 Pipeline A 的第 2 步，产出后续 03_query_cloud_lastmodified.py / 04_clean_and_export_tsv.py 的输入。
#
# 【使用前需修改】
#   - BASE_PATH     : OBS 桶/前缀路径（默认 obs://obs-zyt-temp/hsms）
#   - ROOT_DIR      : 工作根目录（默认取当前目录 pwd）。其下会自动创建：
#                       download_data/  —— 各任务的失败清单下载落盘
#                       metadata_backup/—— 元数据归集
#   - ./obsutil     : 按【当前工作目录】下的 ./obsutil 调用，需 cd 到其所在目录。
#   - cp 参数       : -j 128 -p 5 为并发/分片参数，可按机器与网络调整。
#   - 前置条件      : 已配置 obsutil 凭证（~/.obsutilconfig）。
#
# 【输入 / 输出】
#   输入: 桶内各任务的 object_migrate_result_list/
#   输出: final_task_ids.txt、download_data/<task_id>/、metadata_backup/
# =====================================================================

# ================= 配置区 =================
BASE_PATH="obs://obs-zyt-temp/hsms"
ROOT_DIR=$(pwd)
DOWNLOAD_ROOT="${ROOT_DIR}/download_data"
META_ROOT="${ROOT_DIR}/metadata_backup"
# ==========================================

# 1. 强震地基：创建本地总目录
mkdir -p "$DOWNLOAD_ROOT" "$META_ROOT"

echo "=== 正在拉取最新的全量 32+ 任务 ID 清单 ==="
TASKS_FILE="${ROOT_DIR}/final_task_ids.txt"

# 核心修复：-limit=0 确保 32 个任务一个不丢，直接存盘
./obsutil ls "$BASE_PATH" -d -limit=0 | grep "^obs://" | awk '{print $1}' > "$TASKS_FILE"

echo "已成功锁定的任务数: $(wc -l < "$TASKS_FILE")"

# 2. 核心大循环
while read -r task_url; do
    task_id=$(basename "$task_url")
    RESULT_DIR="${task_url}object_migrate_result_list/"
    
    echo "------------------------------------------------------"
    echo "正在全力下载任务: $task_id"
    echo "------------------------------------------------------"
    
    # 建立这个任务专属的本地下载坑位
    local_task_dir="${DOWNLOAD_ROOT}/${task_id}"
    mkdir -p "$local_task_dir"
    
    # 【核心大招】直接递归 cp -r 扔过去。
    # -fr 参数会自动在本地创建详细的 .obs_record 运行记录（内含全量元数据）
    ./obsutil cp "$RESULT_DIR" "$local_task_dir/" -r -f -fr -j 128 -p 5
    
    # 实时盘点
    task_files=$(find "$local_task_dir" -type f | wc -l)
    echo "任务 $task_id 同步完成，本地现存文件: $task_files 个"

done < "$TASKS_FILE"

# 3. 统一提取元数据
echo "=== 开始统一提取元数据至 metadata_backup ==="
cp -r ${DOWNLOAD_ROOT}/*/.obsutil_checkpoint* "$META_ROOT/" 2>/dev/null || true

echo "==========================================="
echo " 终极同步结束！"
echo "本地实际总文件数：" $(find "$DOWNLOAD_ROOT" -type f | wc -l)
echo "==========================================="
