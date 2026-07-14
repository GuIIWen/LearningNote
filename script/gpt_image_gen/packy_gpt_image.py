#!/usr/bin/env python3
"""Call PackyAPI gpt-image-2 text-to-image and image edit endpoints.

Examples:
  PACKY_API_KEY=pk-... python3 scripts/packy_gpt_image.py generate \
    --prompt "A warm illustration of an orange cat wearing a scarf" \
    --size 1024x1024 --quality high --output cat.png

  PACKY_API_KEY=pk-... python3 scripts/packy_gpt_image.py edit \
    --image ./input.jpg \
    --prompt "Keep the main subject and add a small red DEMO stamp" \
    --size 1024x1024 --quality high --output edited.png
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://www.packyapi.com"
MODEL = "gpt-image-2"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Call PackyAPI gpt-image-2 Images API.",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("PACKY_API_KEY"),
        help="PackyAPI Sora group API key. Defaults to PACKY_API_KEY.",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"API base URL. Defaults to {DEFAULT_BASE_URL}.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Request timeout in seconds. Image generation can take minutes.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="Text-to-image.")
    add_common_image_args(generate)
    generate.add_argument("--prompt", required=True, help="Image prompt.")

    edit = subparsers.add_parser("edit", help="Image-to-image / image edit.")
    add_common_image_args(edit)
    edit.add_argument("--prompt", required=True, help="Edit prompt.")
    edit.add_argument("--image", required=True, help="Input image path.")
    edit.add_argument("--mask", help="Optional PNG mask path for local edits.")
    edit.add_argument(
        "--input-fidelity",
        choices=("high",),
        help="Use high to preserve source-image details during editing.",
    )

    args = parser.parse_args()
    if not args.api_key:
        parser.error("Missing API key. Pass --api-key or set PACKY_API_KEY.")
    if (
        getattr(args, "output_compression", None) is not None
        and not 0 <= args.output_compression <= 100
    ):
        parser.error("--output-compression must be between 0 and 100.")
    return args


def add_common_image_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--size",
        default="auto",
        help="Image size, e.g. auto, 1024x1024, 1536x1024, 3840x2160.",
    )
    parser.add_argument(
        "--quality",
        default="auto",
        choices=("low", "medium", "high", "auto"),
        help="Image quality.",
    )
    parser.add_argument(
        "--response-format",
        default="url",
        choices=("url", "b64_json"),
        help="Return image URL or base64 JSON.",
    )
    parser.add_argument(
        "--output-format",
        default="png",
        choices=("png", "jpeg"),
        help="Output format. PackyAPI recommends png or jpeg.",
    )
    parser.add_argument(
        "--output-compression",
        type=int,
        help="JPEG compression, 0-100. Only useful with --output-format jpeg.",
    )
    parser.add_argument(
        "--background",
        choices=("opaque",),
        help="Optional background. PackyAPI recommends default or opaque.",
    )
    parser.add_argument(
        "--moderation",
        choices=("auto", "low"),
        help="Safety moderation setting.",
    )
    parser.add_argument("--user", help="Optional end-user/business identifier.")
    parser.add_argument(
        "--output",
        help="Save returned image here. For url responses, downloads the URL.",
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="Print the full JSON response instead of only the primary result.",
    )


def build_common_payload(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": MODEL,
        "prompt": args.prompt,
        "n": 1,
        "size": args.size,
        "quality": args.quality,
        "response_format": args.response_format,
        "output_format": args.output_format,
    }
    optional_fields = (
        "output_compression",
        "background",
        "moderation",
        "user",
    )
    for field in optional_fields:
        value = getattr(args, field, None)
        if value is not None:
            payload[field] = value
    return payload


def call_json(
    url: str,
    api_key: str,
    payload: dict[str, Any],
    timeout: int,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    return read_json_response(request, timeout)


def call_multipart(
    url: str,
    api_key: str,
    fields: dict[str, Any],
    files: dict[str, Path],
    timeout: int,
) -> dict[str, Any]:
    body, content_type = encode_multipart(fields, files)
    request = Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": content_type,
            "Accept": "application/json",
        },
        method="POST",
    )
    return read_json_response(request, timeout)


def encode_multipart(
    fields: dict[str, Any],
    files: dict[str, Path],
) -> tuple[bytes, str]:
    boundary = f"----packy-gpt-image-{uuid.uuid4().hex}"
    chunks: list[bytes] = []

    for name, value in fields.items():
        if value is None:
            continue
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(
                    "utf-8",
                ),
                str(value).encode("utf-8"),
                b"\r\n",
            ],
        )

    for name, path in files.items():
        filename = path.name
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                (
                    f'Content-Disposition: form-data; name="{name}"; '
                    f'filename="{filename}"\r\n'
                ).encode("utf-8"),
                f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
                path.read_bytes(),
                b"\r\n",
            ],
        )

    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def read_json_response(request: Request, timeout: int) -> dict[str, Any]:
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise SystemExit(f"Request failed: {exc.reason}") from exc

    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        body = raw.decode("utf-8", errors="replace")
        raise SystemExit(f"Response is not valid JSON: {body[:1000]}") from exc


def handle_result(
    result: dict[str, Any],
    output: str | None,
    response_format: str,
    print_json: bool,
    timeout: int,
) -> None:
    if print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    data = result.get("data")
    if not isinstance(data, list) or not data:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    first = data[0]
    if not isinstance(first, dict):
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    revised_prompt = first.get("revised_prompt")
    if revised_prompt:
        print(f"revised_prompt: {revised_prompt}", file=sys.stderr)

    if response_format == "b64_json":
        b64_json = first.get("b64_json")
        if not isinstance(b64_json, str):
            raise SystemExit("No b64_json field found in response.")
        image_bytes = base64.b64decode(b64_json)
        if output:
            save_file(output, image_bytes)
            print(output)
        else:
            print(b64_json)
        return

    image_url = first.get("url")
    if not isinstance(image_url, str):
        raise SystemExit("No url field found in response.")
    if output:
        download_file(image_url, Path(output), timeout)
        print(output)
    else:
        print(image_url)


def download_file(url: str, output: Path, timeout: int) -> None:
    request = Request(url, headers={"Accept": "*/*"})
    try:
        with urlopen(request, timeout=timeout) as response:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(response.read())
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Download failed with HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise SystemExit(f"Download failed: {exc.reason}") from exc


def validate_file(path_text: str, label: str) -> Path:
    path = Path(path_text)
    if not path.is_file():
        raise SystemExit(f"{label} does not exist or is not a file: {path}")
    return path


def save_file(output: str, content: bytes) -> None:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def main() -> int:
    args = parse_args()
    base_url = args.base_url.rstrip("/")

    if args.command == "generate":
        payload = build_common_payload(args)
        result = call_json(
            f"{base_url}/v1/images/generations",
            args.api_key,
            payload,
            args.timeout,
        )
    elif args.command == "edit":
        fields = build_common_payload(args)
        if args.input_fidelity:
            fields["input_fidelity"] = args.input_fidelity

        files = {"image": validate_file(args.image, "--image")}
        if args.mask:
            files["mask"] = validate_file(args.mask, "--mask")

        result = call_multipart(
            f"{base_url}/v1/images/edits",
            args.api_key,
            fields,
            files,
            args.timeout,
        )
    else:
        raise SystemExit(f"Unsupported command: {args.command}")

    handle_result(
        result=result,
        output=args.output,
        response_format=args.response_format,
        print_json=args.print_json,
        timeout=args.timeout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
