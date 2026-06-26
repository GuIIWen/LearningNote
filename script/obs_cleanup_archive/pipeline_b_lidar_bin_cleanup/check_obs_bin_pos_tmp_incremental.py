#!/usr/bin/env python3
"""
【脚本作用】
    在第 1 步基础上做【增量】核查：只针对 aggregation.bin / *.pos /
    .aggregation.bin.tmp* 三类目标，且自动跳过已出现在各历史结果文件里的 URL，
    避免重复请求。同样用 `obsutil ls -limit=1 -bf=raw` 判存在并记录大小。
    属于 Pipeline B 清理流水线【第 2 步：增量核查 bin/pos/tmp】。【只查不删】。

【使用前需修改】（命令行参数，可用 -h 查看）
    ⚠️ DEFAULT_* 默认值按【原目录结构】SCRIPT_DIR/codex_lidar_cleanup/... 推导，
       归档后默认路径已失效 —— 请用命令行参数显式覆盖，或重建 codex_lidar_cleanup/ 布局。
    - --input        : 输入清单（默认 .../codex_lidar_cleanup/all_files_merged.txt）
    - --output-dir   : 结果输出目录（默认 .../codex_lidar_cleanup/obs_check_bin_pos_tmp）
    - --obsutil      : obsutil 可执行文件绝对路径（默认 /root/obsutil/obsutil/obsutil）
    - --bucket       : 桶前缀（默认 obs://obs-zyt-temp）
    - --config       : obsutil 凭证配置（默认走机器 ~/.obsutilconfig）
    - --workers/--retries/--timeout/--limit/--progress-every : 并发与容错参数
    - 删除目标判定 is_target_url()：若要清理别的文件名，改该函数。

【输入 / 输出】
    输入: 清单文件 + DEFAULT_KNOWN_RESULT_FILES（用于跳过已知 URL）
    输出: <output-dir>/existing_urls.txt、missing_urls.txt、error_urls.txt、
          skipped_already_known_urls.txt、summary.txt
"""
import argparse
import concurrent.futures
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import unquote


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = str(SCRIPT_DIR / "codex_lidar_cleanup" / "all_files_merged.txt")
DEFAULT_OUTPUT_DIR = str(SCRIPT_DIR / "codex_lidar_cleanup" / "obs_check_bin_pos_tmp")
DEFAULT_OBSUTIL = "/root/obsutil/obsutil/obsutil"
DEFAULT_BUCKET = "obs://obs-zyt-temp"
DEFAULT_KNOWN_RESULT_FILES = [
    SCRIPT_DIR / "codex_lidar_cleanup" / "obs_check_results" / "existing_urls.txt",
    SCRIPT_DIR / "codex_lidar_cleanup" / "obs_check_results" / "missing_urls.txt",
    SCRIPT_DIR / "codex_lidar_cleanup" / "obs_check_results" / "error_urls.txt",
    SCRIPT_DIR / "codex_lidar_cleanup" / "cleanup_prepare" / "delete_bin_files.txt",
    SCRIPT_DIR / "codex_lidar_cleanup" / "cleanup_prepare" / "keep_non_bin_files.txt",
    SCRIPT_DIR / "codex_lidar_cleanup" / "cleanup_prepare" / "deleted_bin_files.log",
    SCRIPT_DIR / "codex_lidar_cleanup" / "cleanup_prepare" / "failed_delete_bin_files.txt",
]


@dataclass
class CheckResult:
    status: str
    url: str
    detail: str = ""
    size_bytes: Optional[int] = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Incrementally query only aggregation.bin / aggregation.bin.pos / "
            ".aggregation.bin.tmp* paths with obsutil ls, skipping URLs already present "
            "in previous result files, and record exact sizes for all existing objects."
        )
    )
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Manifest file to scan.")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Output directory.")
    parser.add_argument("--obsutil", default=DEFAULT_OBSUTIL, help="Path to obsutil.")
    parser.add_argument("--config", default="", help="Optional obsutil config path.")
    parser.add_argument("--bucket", default=DEFAULT_BUCKET, help="Bucket prefix.")
    parser.add_argument("--workers", type=int, default=32, help="Parallel obsutil ls workers.")
    parser.add_argument("--retries", type=int, default=2, help="Retry count.")
    parser.add_argument("--timeout", type=int, default=30, help="Timeout seconds per ls call.")
    parser.add_argument("--limit", type=int, default=0, help="Only process first N candidates after skip.")
    parser.add_argument("--progress-every", type=int, default=1000, help="Print progress every N completions.")
    return parser.parse_args()


def normalize_line_to_url(raw_line: str, bucket: str) -> Optional[str]:
    line = raw_line.strip()
    if not line:
        return None

    payload = line
    obs_start = line.find("obs://")
    encoded_start = line.find("%2F")
    if obs_start >= 0:
        payload = line[obs_start:]
    elif encoded_start >= 0:
        payload = line[encoded_start:]

    payload = payload.split("|", 1)[0].strip()
    if not payload:
        return None

    if "\t" in payload:
        payload = payload.split("\t", 1)[0].strip()

    decoded = unquote(payload)
    if decoded.startswith("obs://"):
        return decoded

    decoded = decoded.lstrip("/")
    if not decoded:
        return None
    return f"{bucket.rstrip('/')}/{decoded}"


def is_target_url(url: str) -> bool:
    tail = url.rsplit("/", 1)[-1]
    return (
        tail == "aggregation.bin"
        or tail.endswith(".pos")
        or tail.startswith(".aggregation.bin.tmp")
    )


def build_known_result_files(output_dir: Path) -> list[Path]:
    return [
        *DEFAULT_KNOWN_RESULT_FILES,
        output_dir / "existing_urls.txt",
        output_dir / "missing_urls.txt",
        output_dir / "error_urls.txt",
    ]


def load_already_seen_urls(bucket: str, known_files: list[Path]) -> set[str]:
    seen: set[str] = set()
    for path in known_files:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8", buffering=1024 * 1024) as f:
            for raw_line in f:
                url = normalize_line_to_url(raw_line, bucket)
                if url:
                    seen.add(url)
    return seen


def combined_output(process: subprocess.CompletedProcess[str]) -> str:
    parts = []
    if process.stdout:
        parts.append(process.stdout.strip())
    if process.stderr:
        parts.append(process.stderr.strip())
    return "\n".join(part for part in parts if part).strip()


def classify_failure(output: str) -> str:
    lower = output.lower()
    if any(marker in lower for marker in ("404", "not found", "nosuchkey", "status code [404]", "errorcode=nosuchkey", "0 objects")):
        return "missing"
    if any(marker in lower for marker in ("401", "403", "access denied", "signature", "authentication", "invalidaccesskeyid", "security token")):
        return "error"
    return "retryable"


def parse_ls_counts(output: str) -> tuple[Optional[int], Optional[int]]:
    file_count = None
    folder_count = None
    for raw_line in output.splitlines():
        line = raw_line.strip().lower()
        if line.startswith("file number:"):
            try:
                file_count = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("folder number:"):
            try:
                folder_count = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
    return file_count, folder_count


def parse_first_size_bytes(output: str) -> Optional[int]:
    direct_size_pattern = re.compile(r"(?<!\d)(\d+)B(?!\d)")
    total_size_pattern = re.compile(r"total size of prefix .*?:\s*(\d+)B", re.IGNORECASE)

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        direct_match = direct_size_pattern.search(line)
        if direct_match:
            try:
                return int(direct_match.group(1))
            except ValueError:
                pass

        total_match = total_size_pattern.search(line)
        if total_match:
            try:
                return int(total_match.group(1))
            except ValueError:
                pass
    return None


def check_url(obsutil_path: str, config_path: str, url: str, timeout: int, retries: int) -> CheckResult:
    cmd = [obsutil_path, "ls", url, "-limit=1", "-bf=raw"]
    if config_path:
        cmd.append(f"-config={config_path}")

    last_output = ""
    for attempt in range(retries + 1):
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
            last_output = f"timeout after {timeout}s"
            failure_kind = "retryable"
        except Exception as exc:
            last_output = str(exc)
            failure_kind = "retryable"
        else:
            output = combined_output(process)
            if process.returncode == 0:
                file_count, folder_count = parse_ls_counts(output)
                if (file_count or 0) > 0 or (folder_count or 0) > 0:
                    return CheckResult(status="exists", url=url, size_bytes=parse_first_size_bytes(output))
                return CheckResult(status="missing", url=url, detail=output)
            last_output = output
            failure_kind = classify_failure(last_output)
            if failure_kind == "missing":
                return CheckResult(status="missing", url=url, detail=last_output)
            if failure_kind == "error":
                return CheckResult(status="error", url=url, detail=last_output)

        if attempt < retries:
            time.sleep(min(2 ** attempt, 5))

    return CheckResult(status="error", url=url, detail=last_output)


def main() -> int:
    args = parse_args()
    start_time = datetime.now()

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    exists_path = output_dir / "existing_urls.txt"
    missing_path = output_dir / "missing_urls.txt"
    skipped_path = output_dir / "skipped_already_known_urls.txt"
    error_path = output_dir / "error_urls.txt"
    summary_path = output_dir / "summary.txt"

    if not input_path.exists():
        print(f"[!] input manifest not found: {input_path}", file=sys.stderr)
        return 1
    if not os.path.exists(args.obsutil):
        print(f"[!] obsutil not found: {args.obsutil}", file=sys.stderr)
        return 1

    known_result_files = build_known_result_files(output_dir)
    already_seen = load_already_seen_urls(args.bucket, known_result_files)

    print("======================================================================", flush=True)
    print(f"[{start_time.strftime('%H:%M:%S')}] Start incremental bin/pos/tmp OBS check", flush=True)
    print("======================================================================", flush=True)
    print(f"Input manifest      : {input_path}", flush=True)
    print(f"Output directory    : {output_dir}", flush=True)
    print(f"obsutil             : {args.obsutil}", flush=True)
    print(f"Config              : {args.config or '<default>'}", flush=True)
    print(f"Bucket prefix       : {args.bucket}", flush=True)
    print(f"Workers             : {args.workers}", flush=True)
    print(f"Retries             : {args.retries}", flush=True)
    print(f"Per-call timeout    : {args.timeout}s", flush=True)
    print(f"Already known URLs  : {len(already_seen)}", flush=True)
    if args.limit:
        print(f"Limit               : {args.limit}", flush=True)
    print("----------------------------------------------------------------------", flush=True)

    candidate_seen: set[str] = set()
    candidates: list[tuple[int, str]] = []
    skipped_count = 0
    raw_input_lines = 0
    target_line_count = 0

    with (
        input_path.open("r", encoding="utf-8", buffering=1024 * 1024) as src,
        skipped_path.open("w", encoding="utf-8", buffering=1024 * 1024) as skipped_file,
    ):
        for raw_line_number, raw_line in enumerate(src, start=1):
            raw_input_lines += 1
            url = normalize_line_to_url(raw_line, args.bucket)
            if not url or not is_target_url(url):
                continue

            target_line_count += 1
            if url in already_seen or url in candidate_seen:
                skipped_count += 1
                skipped_file.write(f"{url}\n")
                continue

            candidate_seen.add(url)
            candidates.append((raw_line_number, url))
            if args.limit and len(candidates) >= args.limit:
                break

    scheduled = 0
    completed = 0
    exists_count = 0
    missing_count = 0
    error_count = 0
    existing_size_known_count = 0
    existing_total_size_bytes = 0
    last_input_line = 0

    pending: dict[concurrent.futures.Future[CheckResult], tuple[int, str]] = {}
    pending_limit = max(args.workers * 4, args.workers)

    with (
        exists_path.open("a", encoding="utf-8", buffering=1024 * 1024) as exists_file,
        missing_path.open("a", encoding="utf-8", buffering=1024 * 1024) as missing_file,
        error_path.open("a", encoding="utf-8", buffering=1024 * 1024) as error_file,
        concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor,
    ):
        def drain(wait_for_all: bool) -> None:
            nonlocal completed, exists_count, missing_count, error_count
            nonlocal existing_size_known_count, existing_total_size_bytes
            if not pending:
                return
            return_when = concurrent.futures.ALL_COMPLETED if wait_for_all else concurrent.futures.FIRST_COMPLETED
            done, _ = concurrent.futures.wait(pending, return_when=return_when)
            for future in done:
                raw_line_number, _ = pending.pop(future)
                try:
                    result = future.result()
                except Exception as exc:
                    result = CheckResult(status="error", url="<internal>", detail=str(exc))

                completed += 1
                if result.status == "exists":
                    exists_count += 1
                    if result.size_bytes is not None:
                        existing_size_known_count += 1
                        existing_total_size_bytes += result.size_bytes
                        exists_file.write(f"{result.url}\t{result.size_bytes}\n")
                    else:
                        exists_file.write(f"{result.url}\n")
                elif result.status == "missing":
                    missing_count += 1
                    missing_file.write(f"{result.url}\n")
                else:
                    error_count += 1
                    error_file.write(f"{result.url}\t{result.detail}\n")

                if completed % args.progress_every == 0:
                    print(
                        f"[progress] completed={completed} exists={exists_count} "
                        f"missing={missing_count} errors={error_count} "
                        f"last_input_line={raw_line_number}",
                        flush=True,
                    )

        for raw_line_number, url in candidates:
            last_input_line = raw_line_number
            future = executor.submit(
                check_url,
                args.obsutil,
                args.config.strip(),
                url,
                args.timeout,
                args.retries,
            )
            pending[future] = (raw_line_number, url)
            scheduled += 1
            if len(pending) >= pending_limit:
                drain(wait_for_all=False)

        drain(wait_for_all=True)

    end_time = datetime.now()
    elapsed = (end_time - start_time).total_seconds()

    summary_lines = [
        f"start_time={start_time.isoformat()}",
        f"end_time={end_time.isoformat()}",
        f"elapsed_seconds={elapsed:.2f}",
        f"input_manifest={input_path.resolve()}",
        f"output_dir={output_dir.resolve()}",
        f"obsutil={args.obsutil}",
        f"config_path={Path(args.config).resolve() if args.config else ''}",
        f"bucket_prefix={args.bucket}",
        f"workers={args.workers}",
        f"retries={args.retries}",
        f"timeout_seconds={args.timeout}",
        f"raw_input_lines={raw_input_lines}",
        f"target_line_count={target_line_count}",
        f"already_known_count={len(already_seen)}",
        f"skipped_count={skipped_count}",
        f"scheduled_checks={scheduled}",
        f"completed_checks={completed}",
        f"existing_count={exists_count}",
        f"existing_size_known_count={existing_size_known_count}",
        f"existing_total_size_bytes={existing_total_size_bytes}",
        f"missing_count={missing_count}",
        f"error_count={error_count}",
        f"last_input_line={last_input_line}",
        f"exists_output={exists_path.resolve()}",
        f"missing_output={missing_path.resolve()}",
        f"skipped_output={skipped_path.resolve()}",
        f"errors_output={error_path.resolve()}",
    ]
    summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    print("----------------------------------------------------------------------", flush=True)
    print(f"Target lines        : {target_line_count}", flush=True)
    print(f"Skipped known       : {skipped_count}", flush=True)
    print(f"Scheduled checks    : {scheduled}", flush=True)
    print(f"Exists              : {exists_count}", flush=True)
    print(f"Exists size known   : {existing_size_known_count}", flush=True)
    print(f"Exists total bytes  : {existing_total_size_bytes}", flush=True)
    print(f"Missing             : {missing_count}", flush=True)
    print(f"Errors              : {error_count}", flush=True)
    print(f"Summary             : {summary_path}", flush=True)
    print(f"Exists output       : {exists_path}", flush=True)
    print(f"Missing output      : {missing_path}", flush=True)
    print(f"Skipped output      : {skipped_path}", flush=True)
    print(f"Errors output       : {error_path}", flush=True)
    return 0 if error_count == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
