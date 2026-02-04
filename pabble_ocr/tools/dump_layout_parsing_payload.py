from __future__ import annotations

import argparse
import json
from pathlib import Path

from pabble_ocr.adapters.layout_parsing_client import _b64_file, _build_payload_options
from pabble_ocr.config import load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="打印 layout-parsing 请求 payload（用于排查参数/图片畸变）")
    parser.add_argument("file", type=str, help="本地 PDF/图片路径")
    parser.add_argument("--fileType", type=int, choices=[0, 1], required=True, help="0=PDF, 1=图片")
    parser.add_argument("--omitFile", action="store_true", help="不输出 file(base64)，避免超大")
    args = parser.parse_args(argv)

    config = load_config()
    payload = {
        "fileType": int(args.fileType),
        **_build_payload_options(config),
    }

    p = Path(args.file)
    if not args.omitFile:
        payload["file"] = _b64_file(str(p))
    payload_meta = {
        "path": str(p),
        "exists": p.exists(),
        "size_bytes": p.stat().st_size if p.exists() else None,
        "b64_len": len(payload.get("file") or "") if not args.omitFile else None,
    }

    print(json.dumps({"payload": payload, "meta": payload_meta}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

