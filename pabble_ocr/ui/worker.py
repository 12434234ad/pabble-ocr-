from __future__ import annotations

from dataclasses import dataclass
from typing import List

from PySide6.QtCore import QObject, QThread, Signal

from pabble_ocr.config import AppConfig
from pabble_ocr.core.models import QueueItem
from pabble_ocr.core.runner import Runner, RunnerCallbacks


class Worker(QObject):
    log = Signal(str)
    item_updated = Signal(object)
    finished = Signal()

    def __init__(self, config: AppConfig, items: List[QueueItem]) -> None:
        super().__init__()
        self._runner = Runner(config, RunnerCallbacks(on_log=self._emit_log, on_item_update=self._emit_item))
        self._items = items

    def _emit_log(self, msg: str) -> None:
        self.log.emit(msg)

    def _emit_item(self, item: QueueItem) -> None:
        self.item_updated.emit(item)

    def start(self) -> None:
        try:
            self._runner.run(self._items)
        finally:
            self.finished.emit()

    def pause(self) -> None:
        self._runner.pause()

    def resume(self) -> None:
        self._runner.resume()

    def cancel_current(self) -> None:
        self._runner.cancel_current()

    def stop_all(self) -> None:
        self._runner.stop_all()


@dataclass
class WorkerHandle:
    thread: QThread
    worker: Worker

