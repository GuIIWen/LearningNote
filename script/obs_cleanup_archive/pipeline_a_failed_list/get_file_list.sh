#!/bin/bash
# =====================================================================
# 【脚本作用】
#   用 marker 翻页法分页拉取 OBS 桶内全量对象清单（每页 1000 条），
#   拼接成一份完整的本地文件列表。属于 Pipeline A 的第 1 步。
#
# 【使用前需修改】
#   - BUCKET_PATH  : 要扫描的 OBS 桶/前缀路径（默认 obs://obs-zyt-temp/hsms）
#   - OUTPUT_FILE  : 全量清单输出文件名（默认 full_file_list.txt）
#   - ./obsutil    : 脚本按【当前工作目录】下的 ./obsutil 调用。
#                    运行前请 cd 到 obsutil 所在目录，或把 ./obsutil 改成绝对路径。
#   - 前置条件    : 机器上需已配置好 obsutil 凭证（~/.obsutilconfig）。
#
# 【输入 / 输出】
#   输入: 无（直接扫桶）
#   输出: full_file_list.txt（全量清单）、temp_list.txt（翻页临时文件）
# =====================================================================
# 设置起始路径和 Marker
BUCKET_PATH="obs://obs-zyt-temp/hsms"
MARKER=""
OUTPUT_FILE="full_file_list.txt"

# 清空旧文件
> $OUTPUT_FILE

while true; do
    # 每次获取 1000 条，并获取最后一条文件名作为新的 marker
    # 这里的 grep 和 awk 用来清洗 obsutil ls 的输出，只提取文件名
    ./obsutil ls $BUCKET_PATH -marker="$MARKER" -limit=1000 > temp_list.txt
    
    # 检查是否有内容输出
    if [ ! -s temp_list.txt ]; then
        break
    fi

    # 追加到总文件
    cat temp_list.txt >> $OUTPUT_FILE
    
    # 提取最后一行作为下一次的 marker
    MARKER=$(tail -n 1 temp_list.txt | awk '{print $1}')
    
    echo "Fetched up to: $MARKER"
done
