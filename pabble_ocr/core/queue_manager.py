from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pabble_ocr.core.file_types import detect_file_type
from pabble_ocr.core.models import QueueItem
from pabble_ocr.core.state_store import load_state
from pabble_ocr.utils.paths import safe_stem, unique_dir


@dataclass
class QueueBuildResult:
    items: list[QueueItem]
    skipped: list[Path]


def build_queue_items(input_paths: list[Path], output_root: Path) -> QueueBuildResult:
    items: list[QueueItem] = []
    skipped: list[Path] = []

    for p in input_paths:
        if not p.exists() or not p.is_file():
            skipped.append(p)
            continue

        ft = detect_file_type(p)
        if ft == "unknown":
            skipped.append(p)
            continue

        preferred = output_root / safe_stem(p.stem)
        if preferred.exists():
            state = load_state(preferred)
            if state and state.input_path == str(p):
                out_dir = preferred
            else:
                out_dir = unique_dir(preferred)
        else:
            out_dir = preferred
        items.append(QueueItem(input_path=p, output_dir=out_dir))

    return QueueBuildResult(items=items, skipped=skipped)
