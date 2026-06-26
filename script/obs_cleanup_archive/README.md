# OBS 数据清理工具集（归档）

本目录归档了两条基于 **obsutil** 的 OBS（华为云对象存储）数据脚本流水线。两条流水线相互独立，仅通过一份合并清单产生数据衔接。

| 流水线 | 目录 | 作用 | 是否删数据 |
|--------|------|------|-----------|
| **Pipeline A** | `pipeline_a_failed_list/` | 分析 hsms 桶的「失败迁移清单」：查云端修改时间 → 清洗去重 → 导出 TSV 报表 | ❌ 只分析 |
| **Pipeline B** | `pipeline_b_lidar_bin_cleanup/` | 清理 lidar `aggregation.bin` / `*.pos` / `.aggregation.bin.tmp*`：核查存在性+大小 → 生成待删清单 → 执行删除 → 统计释放空间 | ✅ 真删 |

> ⚠️ 整个工具集里**只有 `pipeline_b_lidar_bin_cleanup/delete_bin_files_from_list.py` 会真正执行删除**（`obsutil rm`），其余脚本全部只读。

---

## 目录结构

```
obs_cleanup_archive/
├── README.md
├── pipeline_a_failed_list/            # Pipeline A：失败迁移清单分析（只读）
│   ├── get_file_list.sh               # 步骤 0 ：分页拉取桶内全量对象清单
│   ├── get_failed_file_list.sh        # 步骤 0.5：批量下载各任务失败迁移结果清单 + 元数据
│   ├── get_meta_data.py               # 步骤 1 ：obsutil stat 查云端修改时间 → JSON 映射表
│   ├── parse_with_clean_meta.py       # 步骤 2 ：清洗去重排序 → TSV（新版，读单个大 JSON）
│   └── data_clean.py                  # 步骤 2 ：清洗去重排序 → TSV（旧版，读逐文件 JSON）
│
└── pipeline_b_lidar_bin_cleanup/      # Pipeline B：lidar bin 清理（含删除）
    ├── check_obs_manifest_parallel.py        # 步骤 1 ：全量存在性+大小核查
    ├── check_obs_bin_pos_tmp_incremental.py  # 步骤 2 ：增量核查 bin/pos/tmp（跳过已知）
    ├── prepare_bin_cleanup_from_existing.py  # 步骤 3 ：二分生成待删清单 / 保留清单
    ├── delete_bin_files_from_list.py         # 步骤 4 ：⚠️ 执行删除 obsutil rm
    ├── sum_deleted_file_sizes.py             # 步骤 5 ：统计释放字节数
    │
    ├── step1_get_prefixes_raw.py             # 早期/备选：Python 流式提取一级目录前缀
    ├── search_bin.py                         # 早期/备选：正则匹配 lidar 路径
    └── download_file_list.py                 # 早期/备选：shell 管道提取一级目录前缀
```

每个脚本的**文件头**都有 docstring/注释块，写明：脚本作用、使用前需修改的变量或命令行参数、输入/输出。下文是对两条流水线的整体说明。

---

## 前置依赖

- **obsutil**：华为云对象存储命令行工具。Pipeline A 的脚本按 `./obsutil`（当前目录）调用，需 `cd` 到 obsutil 所在目录；Pipeline B 的脚本默认按 `/root/obsutil/obsutil/obsutil` 绝对路径调用，可用 `--obsutil` 覆盖。
- **obsutil 凭证**：机器上需先配置好 `~/.obsutilconfig`（或用 `--config`/`--source-config` 指定）。本归档**不包含任何凭证文件**。
- **Python 3.8+**：
  - Pipeline A 的清洗脚本依赖 `pandas`（`pip install pandas`）。
  - Pipeline B 全部脚本仅用标准库，无第三方依赖。
- **运行目录**：所有「需改路径」详见各脚本文件头，以及下文「⚠️ 路径注意事项」。

---

## Pipeline A：失败迁移清单分析（只读）

**目标**：从 hsms 桶拉取各任务的失败迁移清单，查出每条失败文件在云端的最后修改时间，清洗去重后输出每任务一份 TSV 报表。**全程不删除任何数据。**

**运行顺序**：

```
get_file_list.sh            # （可选）拉全量对象清单 → full_file_list.txt
        │
        ▼
get_failed_file_list.sh     # 拉任务清单 + 下载各任务 object_migrate_result_list/ + 元数据
        │                     → final_task_ids.txt / download_data/<task>/ / metadata_backup/
        ▼
get_meta_data.py            # 对每个 .txt 调 obsutil stat 取 LastModified
        │                     → clean_cloud_metadata.json   （key={task}_{file} → 时间）
        ▼
parse_with_clean_meta.py    # 解析 `路径|类型|原因` + 查 JSON 时间，去重排序
                              → task_analysis_tsv/analysis_<task>.tsv
```

> `data_clean.py` 与 `parse_with_clean_meta.py` 二选一：前者从 `metadata_backup/` 逐文件读元数据（旧），后者从 `clean_cloud_metadata.json` 单文件读取（新、更快，推荐）。

**核心查询逻辑**（`get_meta_data.py`）：`obsutil stat <url>` → 正则 `LastModified:\s*(.+)` 提取修改时间；32 进程并发。

**需修改**：`BUCKET_PATH`/`BASE_PATH`、`DOWNLOAD_ROOT`、`META_OUTPUT_FILE`、`OBS_BUCKET_BASE`、`MAX_WORKERS`，以及 `./obsutil` 的实际位置（详见各文件头）。

---

## Pipeline B：lidar bin 清理（含删除）

**目标**：清理桶内 lidar 目录下的 `aggregation.bin`、`*.pos`、`.aggregation.bin.tmp*` 三类对象。

**运行顺序（核心 5 步）**：

```
all_files_merged.txt                       # 输入清单（失败迁移清单合并而来，~167万行）
        │
        ▼ ① check_obs_manifest_parallel.py        obsutil ls -limit=1 -bf=raw
        │                                          → obs_check_results/{existing,missing,error}_urls.txt
        ▼ ② check_obs_bin_pos_tmp_incremental.py  只查 bin/pos/tmp，跳过已知 URL（增量）
        │                                          → obs_check_bin_pos_tmp/{existing,missing,...}
        ▼ ③ prepare_bin_cleanup_from_existing.py  二分：删除目标 / 保留；排除已删/已失败
        │                                          → delete_bin_files.txt(+size) / keep_non_bin_files.txt
        ▼ ④ delete_bin_files_from_list.py   ⚠️    obsutil rm（逐个删，原子缩短待删清单）
        │                                          → deleted_bin_files.log / failed_delete_bin_files.txt
        ▼ ⑤ sum_deleted_file_sizes.py              → deleted_size_summary.txt（释放总字节）
```

**核心查询逻辑**（①②）：`obsutil ls <url> -limit=1 -bf=raw` → 解析 `File number:`/`Folder number:` 判存在，正则 `(\d+)B` 取大小；404/NoSuchKey→missing，401/403→error，其它→重试。32 线程并发。

**删除逻辑**（④，重点）：
- 逐个执行 `obsutil rm <url>`（可 `-f`），默认单线程，`--workers` 可并发。
- **可断点续跑**：每删成功一个，就把该行从 `delete_bin_files.txt` 原子移除（`mkstemp`+`os.replace`）并记入 `deleted_bin_files.log`；失败的记入 `failed_delete_bin_files.txt`、待删清单不变。中断后重跑自动从剩余清单续删，不重不漏。
- ③ 生成清单时也会排除 `deleted_bin_files.log`、`failed_delete_bin_files.txt` 里的 URL，双重防重。

**早期/备选脚本**（`step1_get_prefixes_raw.py`、`search_bin.py`、`download_file_list.py`）：早期「直接扫桶」方案，基于 `obsutil ls -limit=0` 流式扫描 `frames-video-s3/` 前缀。**已被上面「基于清单」的 5 步流水线取代**，仅作参考保留。

---

## ⚠️ 路径注意事项（归档后必读）

Pipeline B 的 5 个核心脚本里，所有 `DEFAULT_*` 默认路径都是按**原工作目录结构** `SCRIPT_DIR/codex_lidar_cleanup/...` 推导的（`SCRIPT_DIR` = 脚本所在目录）。**脚本被归档到本目录后，这些默认路径已失效**。两种解决方式：

1. **推荐：用命令行参数显式覆盖**（所有路径都支持 `--input` / `--output-dir` / `--deleted-log` 等，详见各脚本 `-h`）。
2. 或在本目录下重建原始工作布局：建一个 `codex_lidar_cleanup/` 目录，把 `all_files_merged.txt` 等数据放进去，让默认路径重新生效。

Pipeline A 的脚本则使用**相对当前工作目录**的路径（`./download_data`、`./obsutil` 等），运行前 `cd` 到含 obsutil 与数据的工作目录即可。

---

## 历史运行记录（仅供参考）

以下数字来自原工程里的 summary 文件，仅用于说明规模，不代表本归档目录内含数据：

- 输入清单 `all_files_merged.txt`：约 **167 万**行
- 步骤②增量核查：调度 **101.9 万**次，存在 **57.3 万**个（约 410 TB），missing 44.6 万，error 0
- 步骤③生成待删清单：**21.8 万**个删除目标（含 261 个 `.tmp`），保留 2.6 万个，待删总量约 **156.8 TB**
- 步骤④删除：实际删除 **218,374** 个，失败 0，剩余 0
