#!/usr/bin/env python3
"""
【脚本作用】
    统计已删除对象释放的总字节：优先用 deleted_bin_files.log 里内嵌的大小，
    缺失时从 existing_urls.txt 反查补全，最后给出汇总。
    属于 Pipeline B 清理流水线【第 5 步：统计释放空间】。【只读不删】。

【使用前需修改】（命令行参数，可用 -h 查看）
    ⚠️ DEFAULT_* 默认值按【原目录结构】SCRIPT_DIR/codex_lidar_cleanup/... 推导，
       归档后默认路径已失效 —— 请用命令行参数显式覆盖，或重建 codex_lidar_cleanup/ 布局。
    - --deleted-log  : 已删日志（默认 .../cleanup_prepare/deleted_bin_files.log）
    - --output-dir   : 输出目录（默认 .../codex_lidar_cleanup/cleanup_prepare）
    - --size-source  : 用于反查补全大小的 existing_urls.txt，可多次传；
                       默认取 obs_check_results 与 obs_check_bin_pos_tmp 两份。

【输入 / 输出】
    输入: deleted_bin_files.log + 各 existing_urls.txt（补全大小用）
    输出: <output-dir>/deleted_size_summary.txt、deleted_missing_size_urls.txt
"""
import argparse
from pathlib import Path
from typing import Optional


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DELETED_LOG = str(SCRIPT_DIR / "codex_lidar_cleanup" / "cleanup_prepare" / "deleted_bin_files.log")
DEFAULT_OUTPUT_DIR = str(SCRIPT_DIR / "codex_lidar_cleanup" / "cleanup_prepare")
DEFAULT_SIZE_SOURCES = [
    SCRIPT_DIR / "codex_lidar_cleanup" / "obs_check_results" / "existing_urls.txt",
    SCRIPT_DIR / "codex_lidar_cleanup" / "obs_check_bin_pos_tmp" / "existing_urls.txt",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Sum file sizes for deleted URLs. Prefer sizes already embedded in deleted_bin_files.log; "
            "optionally backfill missing sizes from existing_urls result files."
        )
    )
    parser.add_argument("--deleted-log", default=DEFAULT_DELETED_LOG, help="deleted_bin_files.log path")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Output directory")
    parser.add_argument(
        "--size-source",
        action="append",
        dest="size_sources",
        default=[],
        help="existing_urls.txt source for backfill; may be passed multiple times",
    )
    return parser.parse_args()


def parse_url_and_size(raw_line: str) -> tuple[Optional[str], Optional[int]]:
    line = raw_line.strip()
    if not line:
        return None, None

    if "\t" in line:
        url, size_text = line.split("\t", 1)
        url = url.strip()
        size_text = size_text.strip()
        return url, int(size_text) if size_text.isdigit() else None

    return line, None


def load_size_map(paths: list[Path]) -> dict[str, int]:
    size_map: dict[str, int] = {}
    for path in paths:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8", buffering=1024 * 1024) as src:
            for raw_line in src:
                url, size_bytes = parse_url_and_size(raw_line)
                if url and size_bytes is not None and url not in size_map:
                    size_map[url] = size_bytes
    return size_map


def main() -> int:
    args = parse_args()
    deleted_log_path = Path(args.deleted_log)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not deleted_log_path.exists():
        raise FileNotFoundError(f"deleted log not found: {deleted_log_path}")

    size_source_paths = [Path(p) for p in (args.size_sources or [])]
    if not size_source_paths:
        size_source_paths = list(DEFAULT_SIZE_SOURCES)
    size_map = load_size_map(size_source_paths)

    summary_path = output_dir / "deleted_size_summary.txt"
    missing_size_path = output_dir / "deleted_missing_size_urls.txt"

    total_deleted_count = 0
    unique_deleted_count = 0
    size_known_in_log_count = 0
    size_backfilled_count = 0
    size_missing_count = 0
    total_size_bytes = 0
    seen_urls: set[str] = set()
    missing_urls: list[str] = []

    with deleted_log_path.open("r", encoding="utf-8", buffering=1024 * 1024) as src:
        for raw_line in src:
            url, size_bytes = parse_url_and_size(raw_line)
            if not url:
                continue

            total_deleted_count += 1
            if url in seen_urls:
                continue
            seen_urls.add(url)
            unique_deleted_count += 1

            if size_bytes is not None:
                size_known_in_log_count += 1
                total_size_bytes += size_bytes
                continue

            backfilled = size_map.get(url)
            if backfilled is not None:
                size_backfilled_count += 1
                total_size_bytes += backfilled
            else:
                size_missing_count += 1
                missing_urls.append(url)

    with missing_size_path.open("w", encoding="utf-8", buffering=1024 * 1024) as out:
        for url in missing_urls:
            out.write(f"{url}\n")

    summary_lines = [
        f"deleted_log={deleted_log_path.resolve()}",
        "size_sources=" + ",".join(str(path.resolve()) for path in size_source_paths if path.exists()),
        f"total_deleted_log_lines={total_deleted_count}",
        f"unique_deleted_count={unique_deleted_count}",
        f"size_known_in_log_count={size_known_in_log_count}",
        f"size_backfilled_count={size_backfilled_count}",
        f"size_missing_count={size_missing_count}",
        f"total_size_bytes={total_size_bytes}",
        f"missing_size_output={missing_size_path.resolve()}",
    ]
    summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    print(f"deleted_log           : {deleted_log_path}")
    print(f"size_sources          : {', '.join(str(path) for path in size_source_paths)}")
    print(f"total_deleted_lines   : {total_deleted_count}")
    print(f"unique_deleted_count  : {unique_deleted_count}")
    print(f"size_known_in_log_cnt : {size_known_in_log_count}")
    print(f"size_backfilled_count : {size_backfilled_count}")
    print(f"size_missing_count    : {size_missing_count}")
    print(f"total_size_bytes      : {total_size_bytes}")
    print(f"summary_output        : {summary_path}")
    print(f"missing_size_output   : {missing_size_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
