from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QThread

from pabble_ocr.config import AppConfig, load_config, save_config
from pabble_ocr.core.models import QueueItem
from pabble_ocr.core.queue_manager import build_queue_items
from pabble_ocr.core.queue_store import load_queue, save_queue
from pabble_ocr.utils.logging_utils import setup_logging
from pabble_ocr.ui.settings_dialog import SettingsDialog
from pabble_ocr.ui.worker import Worker, WorkerHandle


class DropLabel(QLabel):
    def __init__(self, text: str, on_drop, parent=None) -> None:
        super().__init__(text, parent)
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("border: 2px dashed #888; padding: 18px;")
        self._on_drop = on_drop

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        urls = event.mimeData().urls()
        paths = [Path(u.toLocalFile()) for u in urls if u.isLocalFile()]
        self._on_drop(paths)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Pabble OCR (v0.1)")
        self.resize(980, 720)

        self._config: AppConfig = load_config()
        self._config.ensure_dirs()
        setup_logging(Path(self._config.output_dir) / "_logs")

        self._items: list[QueueItem] = load_queue()
        self._worker_handle: WorkerHandle | None = None

        self._build_ui()
        self._build_menu()
        self._refresh_table()

    def _build_menu(self) -> None:
        settings_action = QAction("设置", self)
        settings_action.triggered.connect(self._open_settings)
        self.menuBar().addAction(settings_action)

    def _build_ui(self) -> None:
        self.drop = DropLabel("拖拽 PDF/图片/文件夹到这里导入", self._add_paths)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["文件", "状态", "进度", "信息"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(220)

        btn_add_files = QPushButton("添加文件")
        btn_add_files.clicked.connect(self._pick_files)
        btn_add_folder = QPushButton("添加文件夹")
        btn_add_folder.clicked.connect(self._pick_folder)
        btn_remove = QPushButton("移除选中")
        btn_remove.clicked.connect(self._remove_selected)
        btn_clear = QPushButton("清空")
        btn_clear.clicked.connect(self._clear)

        btn_start = QPushButton("开始")
        btn_start.clicked.connect(self._start)
        btn_pause = QPushButton("暂停")
        btn_pause.clicked.connect(self._pause)
        btn_resume = QPushButton("继续")
        btn_resume.clicked.connect(self._resume)
        btn_cancel = QPushButton("取消当前")
        btn_cancel.clicked.connect(self._cancel_current)
        btn_retry = QPushButton("重试失败")
        btn_retry.clicked.connect(self._retry_failed)
        btn_restart = QPushButton("从头重跑(选中)")
        btn_restart.clicked.connect(self._restart_selected)

        btn_open_out = QPushButton("打开输出目录")
        btn_open_out.clicked.connect(self._open_output_root)
        btn_open_md = QPushButton("打开 merged_result.md")
        btn_open_md.clicked.connect(self._open_selected_md)

        row1 = QHBoxLayout()
        for b in (btn_add_files, btn_add_folder, btn_remove, btn_clear):
            row1.addWidget(b)
        row1.addStretch(1)
        for b in (btn_open_out, btn_open_md):
            row1.addWidget(b)

        row2 = QHBoxLayout()
        for b in (btn_start, btn_pause, btn_resume, btn_cancel, btn_retry, btn_restart):
            row2.addWidget(b)
        row2.addStretch(1)

        root = QVBoxLayout()
        root.addWidget(self.drop)
        root.addLayout(row1)
        root.addLayout(row2)
        root.addWidget(self.table, 1)
        root.addWidget(QLabel("日志"))
        root.addWidget(self.log)

        w = QWidget()
        w.setLayout(root)
        self.setCentralWidget(w)

    def _append_log(self, msg: str) -> None:
        self.log.append(msg)

    def _is_running(self) -> bool:
        return self._worker_handle is not None

    def _pick_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "选择文件", "", "Files (*.pdf *.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff)")
        self._add_paths([Path(f) for f in files])

    def _pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹", "")
        if folder:
            self._add_paths([Path(folder)])

    def _collect_files(self, paths: list[Path]) -> list[Path]:
        out: list[Path] = []
        for p in paths:
            if p.is_dir():
                for child in sorted(p.rglob("*"), key=lambda x: str(x).lower()):
                    if child.is_file():
                        out.append(child)
            elif p.is_file():
                out.append(p)
        return out

    def _add_paths(self, paths: list[Path]) -> None:
        if self._is_running():
            QMessageBox.warning(self, "提示", "任务运行中，无法修改队列")
            return

        files = self._collect_files(paths)
        result = build_queue_items(files, Path(self._config.output_dir))
        if result.skipped:
            self._append_log(f"跳过不支持/不可读文件：{len(result.skipped)} 个")
        self._items.extend(result.items)
        save_queue(self._items)
        self._refresh_table()

    def _remove_selected(self) -> None:
        if self._is_running():
            QMessageBox.warning(self, "提示", "任务运行中，无法修改队列")
            return
        rows = sorted({r.row() for r in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            if 0 <= r < len(self._items):
                self._items.pop(r)
        save_queue(self._items)
        self._refresh_table()

    def _clear(self) -> None:
        if self._is_running():
            QMessageBox.warning(self, "提示", "任务运行中，无法修改队列")
            return
        self._items = []
        save_queue(self._items)
        self._refresh_table()

    def _retry_failed(self) -> None:
        if self._is_running():
            QMessageBox.warning(self, "提示", "任务运行中，无法重置状态")
            return
        for it in self._items:
            if it.status == "failed":
                it.status = "queued"
                it.progress = 0.0
                it.error = None
                it.message = ""
        save_queue(self._items)
        self._refresh_table()

    def _start(self) -> None:
        if self._is_running():
            return
        if not self._items:
            QMessageBox.information(self, "提示", "请先导入文件")
            return
        if not self._config.api_url or not self._config.token:
            QMessageBox.warning(self, "提示", "请先在“设置”中配置 API_URL 与 TOKEN")
            return

        thread = QThread(self)
        worker = Worker(self._config, self._items)
        worker.moveToThread(thread)
        thread.started.connect(worker.start)
        worker.log.connect(self._append_log)
        worker.item_updated.connect(self._on_item_updated)
        worker.finished.connect(self._on_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._worker_handle = WorkerHandle(thread=thread, worker=worker)
        thread.start()

    def _pause(self) -> None:
        if self._worker_handle:
            self._worker_handle.worker.pause()

    def _resume(self) -> None:
        if self._worker_handle:
            self._worker_handle.worker.resume()

    def _cancel_current(self) -> None:
        if self._worker_handle:
            self._worker_handle.worker.cancel_current()

    def _on_item_updated(self, item: QueueItem) -> None:
        for idx, it in enumerate(self._items):
            if str(it.input_path) == str(item.input_path) and str(it.output_dir) == str(item.output_dir):
                self._items[idx] = item
                self._update_row(idx, item)
                save_queue(self._items)
                break

    def _on_finished(self) -> None:
        self._append_log("队列处理结束")
        self._worker_handle = None
        save_queue(self._items)

    def _refresh_table(self) -> None:
        self.table.setRowCount(len(self._items))
        for i, it in enumerate(self._items):
            self._update_row(i, it)

    def _update_row(self, row: int, it: QueueItem) -> None:
        self.table.setItem(row, 0, QTableWidgetItem(it.input_path.name))
        self.table.setItem(row, 1, QTableWidgetItem(it.status))
        self.table.setItem(row, 2, QTableWidgetItem(f"{int(it.progress * 100)}%"))
        msg = it.message or (it.error or "")
        self.table.setItem(row, 3, QTableWidgetItem(msg))

    def _open_output_root(self) -> None:
        it = self._selected_item()
        if it and it.output_dir.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(it.output_dir)))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(self._config.output_dir))

    def _selected_item(self) -> QueueItem | None:
        rows = sorted({r.row() for r in self.table.selectedIndexes()})
        if not rows:
            return None
        r = rows[0]
        if 0 <= r < len(self._items):
            return self._items[r]
        return None

    def _open_selected_md(self) -> None:
        it = self._selected_item()
        if not it:
            QMessageBox.information(self, "提示", "请先选中一条任务")
            return
        md = it.output_dir / "merged_result.md"
        if not md.exists():
            QMessageBox.information(self, "提示", "尚未生成 merged_result.md")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(md)))

    def _restart_selected(self) -> None:
        if self._is_running():
            QMessageBox.warning(self, "提示", "任务运行中，无法重置输出")
            return
        it = self._selected_item()
        if not it:
            QMessageBox.information(self, "提示", "请先选中一条任务")
            return
        if QMessageBox.question(self, "确认", "将删除该任务的输出目录并从头重跑，确定？") != QMessageBox.StandardButton.Yes:
            return
        try:
            if it.output_dir.exists():
                shutil.rmtree(it.output_dir)
            it.status = "queued"
            it.progress = 0.0
            it.error = None
            it.message = ""
            save_queue(self._items)
            self._refresh_table()
        except Exception as e:
            QMessageBox.warning(self, "失败", f"重置失败：{e}")

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self._config, self)
        if dlg.exec() == dlg.DialogCode.Accepted:
            self._config = dlg.get_config()
            self._config.ensure_dirs()
            save_config(self._config)
            setup_logging(Path(self._config.output_dir) / "_logs")
            QMessageBox.information(self, "提示", "设置已保存")

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._is_running():
            if QMessageBox.question(self, "退出确认", "队列仍在运行，确定退出？") != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        event.accept()
