#!/usr/bin/env python3
"""
【脚本作用】
    合并第 1、2 步产出的 existing_urls.txt，把对象二分为：
      - 删除目标（aggregation.bin / *.pos / .aggregation.bin.tmp*）→ 待删清单
      - 其余 → 保留清单
    并排除已删/已失败日志里的 URL，防止重复删除。
    属于 Pipeline B 清理流水线【第 3 步：生成待删清单】。【只读不删】。

【使用前需修改】（命令行参数，可用 -h 查看）
    ⚠️ DEFAULT_* 默认值按【原目录结构】SCRIPT_DIR/codex_lidar_cleanup/... 推导，
       归档后默认路径已失效 —— 请用命令行参数显式覆盖，或重建 codex_lidar_cleanup/ 布局。
    - --input        : existing_urls.txt 输入，可多次传（默认取 obs_check_results 与
                       obs_check_bin_pos_tmp 两份 existing_urls.txt）
    - --output-dir   : 输出目录（默认 .../codex_lidar_cleanup/cleanup_prepare）
    - --deleted-log  : 已删日志，用于排除（默认 .../cleanup_prepare/deleted_bin_files.log）
    - --failed-log   : 已失败日志，用于排除（默认 .../cleanup_prepare/failed_delete_bin_files.txt）
    - 删除目标判定 is_delete_target()：要清理别的文件名时改这里。

【输入 / 输出】
    输入: 各 existing_urls.txt（可选 `url\\t<size>` 形式）+ deleted/failed 日志
    输出: <output-dir>/delete_bin_files.txt(待删,含大小)、delete_bin_parent_dirs.txt、
          keep_non_bin_files.txt、delete_bin_summary.txt
"""
import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = str(SCRIPT_DIR / "codex_lidar_cleanup" / "cleanup_prepare")
DEFAULT_INPUTS = [
    SCRIPT_DIR / "codex_lidar_cleanup" / "obs_check_results" / "existing_urls.txt",
    SCRIPT_DIR / "codex_lidar_cleanup" / "obs_check_bin_pos_tmp" / "existing_urls.txt",
]
DEFAULT_DELETED_LOG = SCRIPT_DIR / "codex_lidar_cleanup" / "cleanup_prepare" / "deleted_bin_files.log"
DEFAULT_FAILED_LOG = SCRIPT_DIR / "codex_lidar_cleanup" / "cleanup_prepare" / "failed_delete_bin_files.txt"


@dataclass
class ParsedLine:
    url: str
    size_bytes: Optional[int]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Merge existing OBS result files, build a fresh deletion manifest for "
            "aggregation.bin / *.pos / .aggregation.bin.tmp*, exclude already deleted URLs, "
            "and keep a deduplicated non-delete list."
        )
    )
    parser.add_argument(
        "--input",
        action="append",
        dest="inputs",
        default=[],
        help="existing_urls.txt input; may be passed multiple times. Defaults include old and incremental result files.",
    )
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Output directory")
    parser.add_argument("--deleted-log", default=str(DEFAULT_DELETED_LOG), help="Deleted log to exclude")
    parser.add_argument("--failed-log", default=str(DEFAULT_FAILED_LOG), help="Failed delete log to exclude")
    return parser.parse_args()


def parse_existing_line(raw_line: str) -> Optional[ParsedLine]:
    line = raw_line.strip()
    if not line:
        return None

    if "\t" in line:
        url, size_text = line.split("\t", 1)
        url = url.strip()
        size_text = size_text.strip()
        if size_text.isdigit():
            return ParsedLine(url=url, size_bytes=int(size_text))
        return ParsedLine(url=url, size_bytes=None)

    return ParsedLine(url=line, size_bytes=None)


def is_delete_target(url: str) -> bool:
    tail = url.rsplit("/", 1)[-1]
    return (
        tail == "aggregation.bin"
        or tail.endswith(".pos")
        or tail.startswith(".aggregation.bin.tmp")
    )


def parent_dir(url: str) -> str:
    if "/" not in url:
        return url
    return url.rsplit("/", 1)[0]


def parse_url_only(raw_line: str) -> Optional[str]:
    parsed = parse_existing_line(raw_line)
    if parsed is None:
        return None
    return parsed.url


def load_excluded_urls(paths: list[Path]) -> set[str]:
    excluded: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8", buffering=1024 * 1024) as src:
            for raw_line in src:
                url = parse_url_only(raw_line)
                if url:
                    excluded.add(url)
    return excluded


def choose_preferred_size(current: Optional[int], candidate: Optional[int]) -> Optional[int]:
    if current is not None:
        return current
    return candidate


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    input_paths = [Path(p) for p in (args.inputs or [])]
    if not input_paths:
        input_paths = list(DEFAULT_INPUTS)

    missing_inputs = [str(path) for path in input_paths if not path.exists()]
    if missing_inputs:
        raise FileNotFoundError(f"input file not found: {missing_inputs[0]}")

    excluded_urls = load_excluded_urls([Path(args.deleted_log), Path(args.failed_log)])

    delete_files_path = output_dir / "delete_bin_files.txt"
    delete_dirs_path = output_dir / "delete_bin_parent_dirs.txt"
    keep_files_path = output_dir / "keep_non_bin_files.txt"
    summary_path = output_dir / "delete_bin_summary.txt"

    delete_sizes: dict[str, Optional[int]] = {}
    keep_urls: set[str] = set()
    total_lines = 0

    for input_path in input_paths:
        with input_path.open("r", encoding="utf-8", buffering=1024 * 1024) as src:
            for raw_line in src:
                parsed = parse_existing_line(raw_line)
                if parsed is None:
                    continue

                total_lines += 1
                if parsed.url in excluded_urls:
                    continue

                if is_delete_target(parsed.url):
                    delete_sizes[parsed.url] = choose_preferred_size(delete_sizes.get(parsed.url), parsed.size_bytes)
                else:
                    keep_urls.add(parsed.url)

    delete_urls_sorted = sorted(delete_sizes)
    delete_dir_urls_sorted = sorted({parent_dir(url) for url in delete_urls_sorted})
    keep_urls_sorted = sorted(keep_urls)

    delete_size_known_count = 0
    delete_total_size_bytes = 0
    tmp_delete_count = 0

    with delete_files_path.open("w", encoding="utf-8", buffering=1024 * 1024) as delete_files_out:
        for url in delete_urls_sorted:
            size_bytes = delete_sizes[url]
            if url.rsplit("/", 1)[-1].startswith(".aggregation.bin.tmp"):
                tmp_delete_count += 1
            if size_bytes is not None:
                delete_size_known_count += 1
                delete_total_size_bytes += size_bytes
                delete_files_out.write(f"{url}\t{size_bytes}\n")
            else:
                delete_files_out.write(f"{url}\n")

    with delete_dirs_path.open("w", encoding="utf-8", buffering=1024 * 1024) as delete_dirs_out:
        for dir_url in delete_dir_urls_sorted:
            delete_dirs_out.write(f"{dir_url}\n")

    with keep_files_path.open("w", encoding="utf-8", buffering=1024 * 1024) as keep_files_out:
        for url in keep_urls_sorted:
            keep_files_out.write(f"{url}\n")

    summary_lines = [
        "input_files=" + ",".join(str(path.resolve()) for path in input_paths),
        f"deleted_log={Path(args.deleted_log).resolve()}",
        f"failed_log={Path(args.failed_log).resolve()}",
        f"excluded_url_count={len(excluded_urls)}",
        f"total_input_lines={total_lines}",
        f"delete_file_count={len(delete_urls_sorted)}",
        f"tmp_delete_count={tmp_delete_count}",
        f"delete_parent_dir_count={len(delete_dir_urls_sorted)}",
        f"delete_size_known_count={delete_size_known_count}",
        f"delete_total_size_bytes={delete_total_size_bytes}",
        f"keep_file_count={len(keep_urls_sorted)}",
        f"delete_files_output={delete_files_path.resolve()}",
        f"delete_dirs_output={delete_dirs_path.resolve()}",
        f"keep_files_output={keep_files_path.resolve()}",
    ]
    summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    print(f"input_files           : {', '.join(str(path) for path in input_paths)}")
    print(f"excluded_url_count    : {len(excluded_urls)}")
    print(f"delete_file_count     : {len(delete_urls_sorted)}")
    print(f"tmp_delete_count      : {tmp_delete_count}")
    print(f"delete_parent_dir_cnt : {len(delete_dir_urls_sorted)}")
    print(f"delete_size_known_cnt : {delete_size_known_count}")
    print(f"delete_total_size_b   : {delete_total_size_bytes}")
    print(f"keep_file_count       : {len(keep_urls_sorted)}")
    print(f"delete_files_output   : {delete_files_path}")
    print(f"delete_dirs_output    : {delete_dirs_path}")
    print(f"keep_files_output     : {keep_files_path}")
    print(f"summary_output        : {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
