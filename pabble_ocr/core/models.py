from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional


TaskStatus = Literal["queued", "running", "paused", "completed", "failed", "canceled"]


@dataclass
class QueueItem:
    input_path: Path
    output_dir: Path
    status: TaskStatus = "queued"
    progress: float = 0.0
    message: str = ""
    error: Optional[str] = None


@dataclass
class SegmentState:
    segment_id: str
    start_page: int  # 1-based
    end_page: int  # 1-based, inclusive
    part_path: str
    done: bool = False
    attempts: int = 0
    elapsed_s: Optional[float] = None
    last_http_status: Optional[int] = None
    last_error: Optional[str] = None
    # 用于断点续跑场景：当 OCR 相关参数变化时自动触发重跑，避免用户手动删除 task_state.json。
    ocr_options_hash: Optional[str] = None


@dataclass
class FileTaskState:
    version: int = 1
    input_path: str = ""
    file_type: Literal["pdf", "image", "unknown"] = "unknown"
    created_at: str = ""
    updated_at: str = ""
    output_dir: str = ""
    segments: list[SegmentState] = field(default_factory=list)
    images_downloaded: list[str] = field(default_factory=list)
    merged_md_done: bool = False
