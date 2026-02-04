from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pabble_ocr.core.models import QueueItem
from pabble_ocr.utils.io import atomic_write_json


def _queue_path() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "PabbleOCR" / "queue.json"
    return Path.home() / ".config" / "pabble-ocr" / "queue.json"


def save_queue(items: list[QueueItem]) -> Path:
    path = _queue_path()
    payload: list[dict[str, Any]] = [
        {
            "input_path": str(it.input_path),
            "output_dir": str(it.output_dir),
            "status": it.status,
        }
        for it in items
    ]
    atomic_write_json(path, payload)
    return path


def load_queue() -> list[QueueItem]:
    path = _queue_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        items: list[QueueItem] = []
        for r in raw or []:
            p = Path(r.get("input_path", ""))
            out = Path(r.get("output_dir", ""))
            if not p.exists():
                continue
            items.append(QueueItem(input_path=p, output_dir=out, status=r.get("status", "queued")))
        return items
    except Exception:
        return []
