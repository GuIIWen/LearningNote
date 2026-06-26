#!/usr/bin/env python3
"""
【脚本作用】 ⚠️ 这是整条流水线里【唯一真正执行删除】的脚本 ⚠️
    逐个删除 delete_bin_files.txt 里列出的对象：`obsutil rm <url>`。
    成功一个就把该行从待删清单【原子移除】并记入 deleted 日志；
    失败则记入 failed 日志、待删清单保持不变（下次可重试）。
    因此可随时中断、重跑，自动从剩余待删清单续删，不重不漏。
    属于 Pipeline B 清理流水线【第 4 步：执行删除】。

【使用前需修改】（命令行参数，可用 -h 查看）
    ⚠️ DEFAULT_* 默认值按【原目录结构】SCRIPT_DIR/codex_lidar_cleanup/... 推导，
       归档后默认路径已失效 —— 请用命令行参数显式覆盖，或重建 codex_lidar_cleanup/ 布局。
    - --input        : 待删清单（默认 .../cleanup_prepare/delete_bin_files.txt）
    - --output-dir   : 结果输出目录（默认 .../codex_lidar_cleanup/cleanup_prepare）
    - --obsutil      : obsutil 可执行文件绝对路径（默认 /root/obsutil/obsutil/obsutil）
    - --config       : obsutil 凭证配置（默认走机器 ~/.obsutilconfig）
    - --workers      : 并发删除线程数（默认 1；删大对象时谨慎调大）
    - --force        : 传 -f 给 obsutil rm（强制/跳过确认）
    - --limit        : 只删前 N 条（试跑用）
    - --timeout      : 单次 rm 超时秒数（默认 60）

【输入 / 输出】
    输入: delete_bin_files.txt（`url` 或 `url\\t<size>`）
    输出（向 --output-dir）: deleted_bin_files.log、failed_delete_bin_files.txt、
          delete_bin_run_summary.txt；并就地改写 delete_bin_files.txt（移除已删行）
"""
import argparse
import concurrent.futures
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = str(SCRIPT_DIR / "codex_lidar_cleanup" / "cleanup_prepare" / "delete_bin_files.txt")
DEFAULT_OUTPUT_DIR = str(SCRIPT_DIR / "codex_lidar_cleanup" / "cleanup_prepare")
DEFAULT_OBSUTIL = "/root/obsutil/obsutil/obsutil"
FLUSH_EVERY_BYTES = 100 * 1024


@dataclass
class DeleteEntry:
    url: str
    size_bytes: Optional[int]
    raw_line: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Delete URLs listed in delete_bin_files.txt one by one. "
            "Successful deletions are removed from the pending file; failures are recorded separately."
        )
    )
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Path to delete_bin_files.txt")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Directory for result files")
    parser.add_argument("--obsutil", default=DEFAULT_OBSUTIL, help="Path to obsutil")
    parser.add_argument("--config", default="", help="Optional obsutil config path")
    parser.add_argument("--limit", type=int, default=0, help="Delete only the first N pending entries")
    parser.add_argument("--timeout", type=int, default=60, help="Timeout seconds per delete")
    parser.add_argument("--workers", type=int, default=1, help="Parallel delete workers")
    parser.add_argument("--force", action="store_true", help="Pass -f to obsutil rm")
    return parser.parse_args()


def parse_delete_line(raw_line: str) -> Optional[DeleteEntry]:
    line = raw_line.rstrip("\n")
    if not line.strip():
        return None
    if "\t" in line:
        url, size_text = line.split("\t", 1)
        url = url.strip()
        size_text = size_text.strip()
        size_bytes = int(size_text) if size_text.isdigit() else None
        return DeleteEntry(url=url, size_bytes=size_bytes, raw_line=line)
    return DeleteEntry(url=line.strip(), size_bytes=None, raw_line=line)


def combined_output(process: subprocess.CompletedProcess[str]) -> str:
    parts = []
    if process.stdout:
        parts.append(process.stdout.strip())
    if process.stderr:
        parts.append(process.stderr.strip())
    return "\n".join(part for part in parts if part).strip()


def run_delete(
    obsutil_path: str,
    config_path: str,
    url: str,
    timeout: int,
    force: bool,
) -> tuple[bool, str]:
    cmd = [obsutil_path, "rm", url]
    if force:
        cmd.append("-f")
    if config_path:
        cmd.append(f"-config={config_path}")

    try:
        process = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout}s"
    except Exception as exc:
        return False, str(exc)

    output = combined_output(process)
    success = process.returncode == 0
    return success, output


def rewrite_pending_file(path: Path, lines: list[str]) -> None:
    fd, temp_path = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            for line in lines:
                tmp.write(line)
                if not line.endswith("\n"):
                    tmp.write("\n")
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise


def rewrite_pending_file_excluding(path: Path, original_lines: list[str], removed_lines: set[str]) -> None:
    if not removed_lines:
        return
    remaining_lines = [line for line in original_lines if line not in removed_lines]
    rewrite_pending_file(path, remaining_lines)


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not input_path.exists():
        print(f"[!] input file not found: {input_path}", file=sys.stderr)
        return 1
    if not os.path.exists(args.obsutil):
        print(f"[!] obsutil not found: {args.obsutil}", file=sys.stderr)
        return 1

    deleted_log_path = output_dir / "deleted_bin_files.log"
    failed_log_path = output_dir / "failed_delete_bin_files.txt"
    run_log_path = output_dir / "delete_bin_run_summary.txt"

    with input_path.open("r", encoding="utf-8", buffering=1024 * 1024) as f:
        pending_lines = [line.rstrip("\n") for line in f if line.strip()]

    original_pending_count = len(pending_lines)
    entries: list[DeleteEntry] = []
    for raw_line in pending_lines:
        entry = parse_delete_line(raw_line)
        if entry is not None:
            entries.append(entry)

    if args.limit > 0:
        entries = entries[:args.limit]

    print(f"start_time          : {datetime.now().isoformat()}", flush=True)
    print(f"pending_input       : {input_path}", flush=True)
    print(f"pending_total       : {original_pending_count}", flush=True)
    print(f"delete_this_run     : {len(entries)}", flush=True)
    print(f"obsutil             : {args.obsutil}", flush=True)
    print(f"config              : {args.config or '<default>'}", flush=True)
    print(f"workers             : {args.workers}", flush=True)
    print(f"force               : {args.force}", flush=True)

    deleted_count = 0
    failed_count = 0
    completed_count = 0
    deleted_bytes_since_flush = 0
    failed_bytes_since_flush = 0
    removed_raw_lines: set[str] = set()

    failed_file = failed_log_path.open("a", encoding="utf-8", buffering=1024 * 1024)
    deleted_file = deleted_log_path.open("a", encoding="utf-8", buffering=1024 * 1024)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(args.workers, 1)) as executor:
            future_map: dict[concurrent.futures.Future[tuple[bool, str]], DeleteEntry] = {}
            for entry in entries:
                future = executor.submit(
                    run_delete,
                    args.obsutil,
                    args.config.strip(),
                    entry.url,
                    args.timeout,
                    args.force,
                )
                future_map[future] = entry

            for future in concurrent.futures.as_completed(future_map):
                entry = future_map[future]
                success, output = future.result()
                completed_count += 1

                if success:
                    deleted_count += 1
                    removed_raw_lines.add(entry.raw_line)
                    deleted_line = f"{entry.raw_line}\n"
                    deleted_file.write(deleted_line)
                    deleted_bytes_since_flush += len(deleted_line.encode("utf-8"))
                    if deleted_bytes_since_flush >= FLUSH_EVERY_BYTES:
                        deleted_file.flush()
                        deleted_bytes_since_flush = 0
                    rewrite_pending_file_excluding(input_path, pending_lines, removed_raw_lines)
                else:
                    failed_count += 1
                    failed_line = f"{entry.raw_line}\t{output}\n"
                    failed_file.write(failed_line)
                    failed_bytes_since_flush += len(failed_line.encode("utf-8"))
                    if failed_bytes_since_flush >= FLUSH_EVERY_BYTES:
                        failed_file.flush()
                        failed_bytes_since_flush = 0

                remaining_count = original_pending_count - deleted_count
                print(
                    f"[{completed_count}/{len(entries)}] success={success} "
                    f"deleted={deleted_count} failed={failed_count} remaining={remaining_count} "
                    f"url={entry.url}",
                    flush=True,
                )
                if output:
                    print(output, flush=True)
    finally:
        deleted_file.flush()
        failed_file.flush()
        deleted_file.close()
        failed_file.close()

    remaining_count = original_pending_count - deleted_count
    summary_lines = [
        f"run_time={datetime.now().isoformat()}",
        f"input_file={input_path.resolve()}",
        f"run_count={len(entries)}",
        f"deleted_count={deleted_count}",
        f"failed_count={failed_count}",
        f"remaining_count={remaining_count}",
        f"deleted_log={deleted_log_path.resolve()}",
        f"failed_log={failed_log_path.resolve()}",
    ]
    run_log_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    print(f"deleted_count       : {deleted_count}", flush=True)
    print(f"failed_count        : {failed_count}", flush=True)
    print(f"remaining_count     : {remaining_count}", flush=True)
    print(f"deleted_log         : {deleted_log_path}", flush=True)
    print(f"failed_log          : {failed_log_path}", flush=True)
    print(f"run_summary         : {run_log_path}", flush=True)
    return 0 if failed_count == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
