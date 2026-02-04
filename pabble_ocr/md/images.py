from __future__ import annotations

import base64
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests

from pabble_ocr.config import AppConfig
from pabble_ocr.core.models import FileTaskState
from pabble_ocr.core.state_store import save_state


logger = logging.getLogger(__name__)


_B64_RE = re.compile(r"^[A-Za-z0-9+/=\n\r]+$")


def _maybe_decode_inline_image(value: str) -> Optional[bytes]:
    if value.startswith("data:image/") and ";base64," in value:
        b64 = value.split(";base64,", 1)[1]
        try:
            return base64.b64decode(b64, validate=False)
        except Exception:
            return None

    # 服务端可能直接返回纯 base64（可能带换行/空白），这里做一次清洗后再解码。
    cleaned = re.sub(r"\s+", "", value or "")
    if len(cleaned) >= 64 and _B64_RE.match(value) and len(cleaned) % 4 == 0:
        try:
            return base64.b64decode(cleaned, validate=False)
        except Exception:
            return None

    return None


def _is_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def _should_omit_auth_header(url: str) -> bool:
    u = url.lower()
    if "bcebos.com" in u:
        return True
    if "authorization=bce-auth-v1" in u:
        return True
    return False


def download_images(
    *,
    config: AppConfig,
    output_dir: Path,
    state: FileTaskState,
    images: dict[str, str],
    max_retries: int,
    log: callable,
) -> None:
    downloaded = set(state.images_downloaded or [])

    session = requests.Session()
    session.trust_env = bool(getattr(config, "use_system_proxy", True))
    default_headers = {"Authorization": f"token {config.token}"} if config.token else {}

    for rel_path, ref in images.items():
        rel_path = rel_path.replace("\\", "/")
        if rel_path in downloaded:
            continue

        dst = output_dir / rel_path
        dst.parent.mkdir(parents=True, exist_ok=True)

        inline = _maybe_decode_inline_image(ref)
        if inline is not None:
            dst.write_bytes(inline)
            downloaded.add(rel_path)
            state.images_downloaded = sorted(downloaded)
            save_state(output_dir, state)
            continue

        resolved_ref = ref
        if not _is_url(resolved_ref):
            # 部分服务端返回相对路径（如 /xxx.png 或 outputImages/xxx.png），这里尝试基于 API_URL 补全。
            base = (config.api_url or "").strip()
            if base and isinstance(resolved_ref, str) and resolved_ref.strip():
                candidate = urljoin(base if base.endswith("/") else (base + "/"), resolved_ref.strip())
                if _is_url(candidate):
                    resolved_ref = candidate

        if not _is_url(resolved_ref):
            log(f"跳过未知图片引用：{rel_path} -> {ref!r}")
            continue

        headers = {} if _should_omit_auth_header(resolved_ref) else default_headers

        attempt = 0
        while True:
            attempt += 1
            try:
                r = session.get(resolved_ref, headers=headers, timeout=(config.connect_timeout_s, config.read_timeout_s))
                if r.status_code >= 400:
                    raise RuntimeError(f"HTTP {r.status_code}")
                dst.write_bytes(r.content)
                downloaded.add(rel_path)
                state.images_downloaded = sorted(downloaded)
                save_state(output_dir, state)
                break
            except Exception as e:
                if attempt <= max_retries:
                    time.sleep(min(10.0, 2 ** (attempt - 1) + 0.2))
                    continue
                log(f"图片下载失败：{rel_path} -> {resolved_ref} ({e})")
                break

        if os.name == "nt":
            try:
                dst.chmod(0o666)
            except Exception:
                pass
