from __future__ import annotations

from pathlib import Path


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


def detect_file_type(path: Path) -> str:
    suf = path.suffix.lower()
    if suf == ".pdf":
        return "pdf"
    if suf in IMAGE_SUFFIXES:
        return "image"
    return "unknown"

