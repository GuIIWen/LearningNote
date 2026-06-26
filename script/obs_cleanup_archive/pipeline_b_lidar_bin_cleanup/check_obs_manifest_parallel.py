#!/usr/bin/env python3
"""
【脚本作用】
    读取清单文件，把每行规范化成 obs:// URL，用 `obsutil ls <url> -limit=1 -bf=raw`
    多线程判定每个对象在桶内是否存在、并记录大小，分类输出 existing/missing/error。
    属于 Pipeline B 清理流水线【第 1 步：存在性核查】。【只查不删】。

【使用前需修改】（命令行参数，可用 -h 查看）
    ⚠️ DEFAULT_* 默认值按【原目录结构】SCRIPT_DIR/codex_lidar_cleanup/... 推导。
       脚本归档到本目录后默认路径已失效 —— 请用命令行参数显式覆盖，或在本目录下
       重建 codex_lidar_cleanup/ 工作目录并把数据放进去。
    - --input        : 输入清单（默认 .../codex_lidar_cleanup/all_files_merged.txt）
    - --output-dir   : 结果输出目录（默认 .../codex_lidar_cleanup/obs_check_results）
    - --obsutil      : obsutil 可执行文件绝对路径（默认 /root/obsutil/obsutil/obsutil）
    - --bucket       : 桶前缀，把相对 key 补成完整 obs:// URL（默认 obs://obs-zyt-temp）
    - --source-config / --config / --use-runtime-config : obsutil 凭证配置
    - --workers/--retries/--timeout/--limit/--dedupe : 并发与容错参数

【输入 / 输出】
    输入: 清单文件（每行可为 obs:// URL、%2F 编码路径、或 `...|FILE` 形式）
    输出: <output-dir>/existing_urls.txt(含\\t大小)、missing_urls.txt、
          missing_raw_records.txt、error_urls.txt、summary.txt
"""
import argparse
import concurrent.futures
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import unquote


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = str(SCRIPT_DIR / "codex_lidar_cleanup" / "all_files_merged.txt")
DEFAULT_OUTPUT_DIR = str(SCRIPT_DIR / "codex_lidar_cleanup" / "obs_check_results")
DEFAULT_OBSUTIL = "/root/obsutil/obsutil/obsutil"
DEFAULT_SOURCE_CONFIG = "/root/.obsutilconfig"
DEFAULT_BUCKET = "obs://obs-zyt-temp"


@dataclass
class CheckResult:
    status: str
    url: str
    detail: str = ""
    size_bytes: Optional[int] = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read a manifest, build OBS URLs under obs://obs-zyt-temp/, "
            "and check object existence in parallel with obsutil ls."
        )
    )
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Manifest file to read.")
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for exists/missing/error outputs.",
    )
    parser.add_argument("--obsutil", default=DEFAULT_OBSUTIL, help="Path to obsutil.")
    parser.add_argument(
        "--source-config",
        default=DEFAULT_SOURCE_CONFIG,
        help="Existing obsutil config to clone when --use-runtime-config is enabled.",
    )
    parser.add_argument(
        "--config",
        default="",
        help="Use this obsutil config directly. If empty, rely on obsutil default config.",
    )
    parser.add_argument(
        "--use-runtime-config",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Clone --source-config into the output directory and rewrite log paths there. "
            "Disabled by default so local runs can use the machine's default obsutil config."
        ),
    )
    parser.add_argument(
        "--bucket",
        default=DEFAULT_BUCKET,
        help="Bucket prefix, for example obs://obs-zyt-temp",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=32,
        help="Parallel obsutil ls workers.",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=2,
        help="Retry count for transient obsutil failures.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Timeout seconds for each obsutil stat call.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Only check the first N normalized entries. 0 means no limit.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=1000,
        help="Print progress every N completed checks.",
    )
    parser.add_argument(
        "--dedupe",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="De-duplicate normalized OBS URLs before checking.",
    )
    return parser.parse_args()


def ensure_runtime_config(source_config: Path, output_dir: Path) -> Path:
    if not source_config.exists():
        raise FileNotFoundError(f"obsutil config not found: {source_config}")

    logs_dir = output_dir / "obs_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    runtime_config = output_dir / "obsutil_runtime.config"
    sdk_log_path = logs_dir / "obssdk.log"
    util_log_path = logs_dir / "obsutil.log"

    replaced_sdk = False
    replaced_util = False
    output_lines = []

    with source_config.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")
            if line.startswith("sdkLogPath="):
                output_lines.append(f"sdkLogPath={sdk_log_path}")
                replaced_sdk = True
            elif line.startswith("utilLogPath="):
                output_lines.append(f"utilLogPath={util_log_path}")
                replaced_util = True
            else:
                output_lines.append(line)

    if not replaced_sdk:
        output_lines.append(f"sdkLogPath={sdk_log_path}")
    if not replaced_util:
        output_lines.append(f"utilLogPath={util_log_path}")

    runtime_config.write_text("\n".join(output_lines) + "\n", encoding="utf-8")
    return runtime_config


def normalize_manifest_line(raw_line: str, bucket: str) -> Optional[str]:
    line = raw_line.strip()
    if not line:
        return None

    payload = line
    encoded_start = line.find("%2F")
    obs_start = line.find("obs://")
    if obs_start >= 0:
        payload = line[obs_start:]
    elif encoded_start >= 0:
        payload = line[encoded_start:]

    payload = payload.split("|", 1)[0].strip()
    if not payload:
        return None

    decoded = unquote(payload)
    if decoded.startswith("obs://"):
        return decoded

    decoded = decoded.lstrip("/")
    if not decoded:
        return None

    return f"{bucket.rstrip('/')}/{decoded}"


def combined_output(process: subprocess.CompletedProcess[str]) -> str:
    parts = []
    if process.stdout:
        parts.append(process.stdout.strip())
    if process.stderr:
        parts.append(process.stderr.strip())
    return "\n".join(part for part in parts if part).strip()


def classify_failure(output: str) -> str:
    lower = output.lower()

    not_found_markers = (
        "404",
        "not found",
        "nosuchkey",
        "status code [404]",
        "errorcode=nosuchkey",
        "0 objects",
    )
    if any(marker in lower for marker in not_found_markers):
        return "missing"

    auth_markers = (
        "401",
        "403",
        "access denied",
        "signature",
        "authentication",
        "invalidaccesskeyid",
        "security token",
    )
    if any(marker in lower for marker in auth_markers):
        return "error"

    return "retryable"


def parse_ls_counts(output: str) -> tuple[Optional[int], Optional[int]]:
    file_count = None
    folder_count = None

    for raw_line in output.splitlines():
        line = raw_line.strip()
        lower = line.lower()
        if lower.startswith("file number:"):
            try:
                file_count = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif lower.startswith("folder number:"):
            try:
                folder_count = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass

    return file_count, folder_count


def parse_first_size_bytes(output: str) -> Optional[int]:
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line.endswith("B") and line[:-1].isdigit():
            try:
                return int(line[:-1])
            except ValueError:
                return None
    return None


def looks_like_object_path(url: str) -> bool:
    tail = url.rsplit("/", 1)[-1]
    return "." in tail


def check_url_exists(
    obsutil_path: str,
    config_path: str,
    url: str,
    timeout: int,
    retries: int,
) -> CheckResult:
    cmd = [
        obsutil_path,
        "ls",
        url,
        "-limit=1",
        "-bf=raw",
    ]
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
        except Exception as exc:  # pragma: no cover - defensive path
            last_output = str(exc)
            failure_kind = "retryable"
        else:
            output = combined_output(process)
            if process.returncode == 0:
                file_count, folder_count = parse_ls_counts(output)
                if (file_count or 0) > 0 or (folder_count or 0) > 0:
                    size_bytes = None
                    if looks_like_object_path(url):
                        size_bytes = parse_first_size_bytes(output)
                    return CheckResult(status="exists", url=url, size_bytes=size_bytes)
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


def iter_normalized_urls(
    input_path: Path,
    bucket: str,
    dedupe: bool,
    limit: int,
) -> Iterable[tuple[int, str]]:
    seen = set() if dedupe else None
    yielded = 0

    with input_path.open("r", encoding="utf-8", buffering=1024 * 1024) as f:
        for raw_line_number, raw_line in enumerate(f, start=1):
            url = normalize_manifest_line(raw_line, bucket)
            if not url:
                continue

            if seen is not None:
                if url in seen:
                    continue
                seen.add(url)

            yield raw_line_number, url
            yielded += 1
            if limit and yielded >= limit:
                return


def write_summary(summary_path: Path, lines: list[str]) -> None:
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    start_time = datetime.now()

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    exists_path = output_dir / "existing_urls.txt"
    missing_path = output_dir / "missing_urls.txt"
    missing_raw_path = output_dir / "missing_raw_records.txt"
    error_path = output_dir / "error_urls.txt"
    summary_path = output_dir / "summary.txt"

    if not input_path.exists():
        print(f"[!] input manifest not found: {input_path}", file=sys.stderr)
        return 1
    if not os.path.exists(args.obsutil):
        print(f"[!] obsutil not found: {args.obsutil}", file=sys.stderr)
        return 1

    config_path = args.config.strip()
    runtime_config: Optional[Path] = None
    if args.use_runtime_config:
        runtime_config = ensure_runtime_config(Path(args.source_config), output_dir)
        config_path = str(runtime_config)

    with input_path.open("r", encoding="utf-8", buffering=1024 * 1024) as f:
        raw_input_lines = sum(1 for _ in f)

    print("======================================================================", flush=True)
    print(f"[{start_time.strftime('%H:%M:%S')}] Start OBS manifest existence check", flush=True)
    print("======================================================================", flush=True)
    print(f"Input manifest      : {input_path}", flush=True)
    print(f"Output directory    : {output_dir}", flush=True)
    print(f"obsutil             : {args.obsutil}", flush=True)
    print(f"Config mode         : {'runtime clone' if args.use_runtime_config else ('explicit' if config_path else 'default obsutil config')}", flush=True)
    if args.use_runtime_config and runtime_config is not None:
        print(f"Runtime config      : {runtime_config}", flush=True)
    elif config_path:
        print(f"Explicit config     : {config_path}", flush=True)
    print(f"Bucket prefix       : {args.bucket}", flush=True)
    print(f"Workers             : {args.workers}", flush=True)
    print(f"Retries             : {args.retries}", flush=True)
    print(f"Per-call timeout    : {args.timeout}s", flush=True)
    print(f"Manifest raw lines  : {raw_input_lines}", flush=True)
    if args.limit:
        print(f"Limit               : {args.limit}", flush=True)
    print(f"Dedupe              : {args.dedupe}", flush=True)
    print("----------------------------------------------------------------------", flush=True)

    scheduled = 0
    completed = 0
    exists_count = 0
    missing_count = 0
    error_count = 0
    existing_size_bytes = 0
    existing_size_known_count = 0
    last_raw_line_number = 0
    raw_line_map: dict[int, str] = {}

    pending: dict[concurrent.futures.Future[CheckResult], tuple[int, str]] = {}
    pending_limit = max(args.workers * 4, args.workers)

    with (
        exists_path.open("w", encoding="utf-8", buffering=1024 * 1024) as exists_file,
        missing_path.open("w", encoding="utf-8", buffering=1024 * 1024) as missing_file,
        missing_raw_path.open("w", encoding="utf-8", buffering=1024 * 1024) as missing_raw_file,
        error_path.open("w", encoding="utf-8", buffering=1024 * 1024) as error_file,
        concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor,
    ):
        def drain(wait_for_all: bool) -> None:
            nonlocal completed, exists_count, missing_count, error_count
            nonlocal existing_size_bytes, existing_size_known_count
            if not pending:
                return

            return_when = (
                concurrent.futures.ALL_COMPLETED
                if wait_for_all
                else concurrent.futures.FIRST_COMPLETED
            )
            done, _ = concurrent.futures.wait(pending, return_when=return_when)
            for future in done:
                raw_line_number, _ = pending.pop(future)
                try:
                    result = future.result()
                except Exception as exc:  # pragma: no cover - defensive path
                    result = CheckResult(status="error", url="<internal>", detail=str(exc))

                completed += 1
                if result.status == "exists":
                    exists_count += 1
                    if result.size_bytes is not None:
                        existing_size_bytes += result.size_bytes
                        existing_size_known_count += 1
                        exists_file.write(f"{result.url}\t{result.size_bytes}\n")
                    else:
                        exists_file.write(f"{result.url}\n")
                elif result.status == "missing":
                    missing_count += 1
                    missing_file.write(f"{result.url}\n")
                    raw_record = raw_line_map.get(raw_line_number, "")
                    if raw_record:
                        missing_raw_file.write(raw_record)
                        if not raw_record.endswith("\n"):
                            missing_raw_file.write("\n")
                else:
                    error_count += 1
                    error_file.write(f"{result.url}\t{result.detail}\n")

                raw_line_map.pop(raw_line_number, None)

                if completed % args.progress_every == 0:
                    print(
                        f"[progress] completed={completed} exists={exists_count} "
                        f"missing={missing_count} errors={error_count} "
                        f"last_input_line={raw_line_number}",
                        flush=True,
                    )

        with input_path.open("r", encoding="utf-8", buffering=1024 * 1024) as input_file:
            dedupe_seen = set() if args.dedupe else None
            normalized_count = 0

            for raw_line_number, raw_line in enumerate(input_file, start=1):
                url = normalize_manifest_line(raw_line, args.bucket)
                if not url:
                    continue

                if dedupe_seen is not None:
                    if url in dedupe_seen:
                        continue
                    dedupe_seen.add(url)

                raw_line_map[raw_line_number] = raw_line
                normalized_count += 1
                if args.limit and normalized_count > args.limit:
                    raw_line_map.pop(raw_line_number, None)
                    break

                last_raw_line_number = raw_line_number
                future = executor.submit(
                    check_url_exists,
                    args.obsutil,
                    config_path,
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
        f"config_mode={'runtime_clone' if args.use_runtime_config else ('explicit' if config_path else 'default')}",
        f"config_path={Path(config_path).resolve() if config_path else ''}",
        f"bucket_prefix={args.bucket}",
        f"workers={args.workers}",
        f"retries={args.retries}",
        f"timeout_seconds={args.timeout}",
        f"raw_input_lines={raw_input_lines}",
        f"scheduled_checks={scheduled}",
        f"completed_checks={completed}",
        f"existing_count={exists_count}",
        f"existing_size_known_count={existing_size_known_count}",
        f"existing_total_size_bytes={existing_size_bytes}",
        f"missing_count={missing_count}",
        f"error_count={error_count}",
        f"last_input_line={last_raw_line_number}",
        f"exists_output={exists_path.resolve()}",
        f"missing_output={missing_path.resolve()}",
        f"missing_raw_output={missing_raw_path.resolve()}",
        f"errors_output={error_path.resolve()}",
    ]
    write_summary(summary_path, summary_lines)

    print("----------------------------------------------------------------------", flush=True)
    print(f"Finished in         : {elapsed:.2f}s", flush=True)
    print(f"Scheduled checks    : {scheduled}", flush=True)
    print(f"Exists              : {exists_count}", flush=True)
    print(f"Exists size known   : {existing_size_known_count}", flush=True)
    print(f"Exists total bytes  : {existing_size_bytes}", flush=True)
    print(f"Missing             : {missing_count}", flush=True)
    print(f"Errors              : {error_count}", flush=True)
    print(f"Summary             : {summary_path}", flush=True)
    print(f"Exists output       : {exists_path}", flush=True)
    print(f"Missing output      : {missing_path}", flush=True)
    print(f"Missing raw output  : {missing_raw_path}", flush=True)
    print(f"Errors output       : {error_path}", flush=True)

    return 0 if error_count == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
