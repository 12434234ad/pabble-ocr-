from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable

from pabble_ocr.config import AppConfig
from pabble_ocr.core.file_types import detect_file_type
from pabble_ocr.core.models import QueueItem
from pabble_ocr.core.state_store import init_or_load_state, save_state
from pabble_ocr.processing.process_file import CanceledError, ensure_output_dir, process_queue_item


logger = logging.getLogger(__name__)


@dataclass
class RunnerCallbacks:
    on_log: Callable[[str], None]
    on_item_update: Callable[[QueueItem], None]


class Runner:
    def __init__(self, config: AppConfig, callbacks: RunnerCallbacks) -> None:
        self._config = config
        self._callbacks = callbacks
        self._pause = Event()
        self._cancel_current = Event()
        self._stop_all = Event()

    def pause(self) -> None:
        self._pause.set()

    def resume(self) -> None:
        self._pause.clear()

    def cancel_current(self) -> None:
        self._cancel_current.set()

    def stop_all(self) -> None:
        self._stop_all.set()

    def run(self, items: list[QueueItem]) -> None:
        for item in items:
            if self._stop_all.is_set():
                item.status = "canceled"
                item.message = "队列已停止"
                self._callbacks.on_item_update(item)
                continue

            self._cancel_current.clear()
            self._run_one(item)

    def _wait_if_paused(self) -> None:
        while self._pause.is_set() and not self._stop_all.is_set():
            time.sleep(0.1)

    def _run_one(self, item: QueueItem) -> None:
        item.status = "running"
        item.progress = 0.0
        item.error = None
        item.message = "开始处理"
        self._callbacks.on_item_update(item)

        try:
            ensure_output_dir(item.output_dir)
            ft = detect_file_type(item.input_path)
            state = init_or_load_state(input_path=item.input_path, output_dir=item.output_dir, file_type=ft)
            save_state(item.output_dir, state)

            def log(msg: str) -> None:
                self._callbacks.on_log(f"[{item.input_path.name}] {msg}")

            def progress(p: float, msg: str) -> None:
                item.progress = max(0.0, min(1.0, p))
                item.message = msg
                self._callbacks.on_item_update(item)

            process_queue_item(
                config=self._config,
                item=item,
                state=state,
                is_paused=lambda: self._pause.is_set(),
                is_canceled=lambda: self._cancel_current.is_set() or self._stop_all.is_set(),
                log=log,
                progress=progress,
            )

            item.status = "completed"
            item.progress = 1.0
            item.message = "完成"
            self._callbacks.on_item_update(item)
        except CanceledError:
            item.status = "canceled"
            item.message = "已取消"
            self._callbacks.on_item_update(item)
        except Exception as e:
            logger.exception("处理失败：%s", item.input_path)
            item.status = "failed"
            err = str(e).strip()
            item.error = err or "失败"
            item.message = err or "失败"
            self._callbacks.on_item_update(item)
