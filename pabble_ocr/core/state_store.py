from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pabble_ocr.core.models import FileTaskState, SegmentState
from pabble_ocr.utils.io import atomic_write_json


STATE_FILENAME = "task_state.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state(output_dir: Path) -> FileTaskState | None:
    path = output_dir / STATE_FILENAME
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        segments_raw = raw.get("segments", []) or []
        segments: list[SegmentState] = []
        for seg in segments_raw:
            if not isinstance(seg, dict):
                continue
            seg_known = {k: v for k, v in seg.items() if k in SegmentState.__dataclass_fields__}
            segments.append(SegmentState(**seg_known))
        raw["segments"] = segments
        return FileTaskState(**{k: v for k, v in raw.items() if k in FileTaskState.__dataclass_fields__})
    except Exception:
        return None


def init_or_load_state(
    *,
    input_path: Path,
    output_dir: Path,
    file_type: str,
) -> FileTaskState:
    existing = load_state(output_dir)
    if existing and existing.input_path == str(input_path):
        return existing

    return FileTaskState(
        input_path=str(input_path),
        output_dir=str(output_dir),
        file_type=file_type if file_type in ("pdf", "image") else "unknown",
        created_at=_now_iso(),
        updated_at=_now_iso(),
    )


def save_state(output_dir: Path, state: FileTaskState) -> Path:
    state.updated_at = _now_iso()
    payload: dict[str, Any] = asdict(state)
    path = output_dir / STATE_FILENAME
    atomic_write_json(path, payload)
    return path
