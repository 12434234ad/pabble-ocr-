from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QComboBox,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QFileDialog,
    QCheckBox,
)
from PySide6.QtCore import Qt

from pabble_ocr.config import AppConfig


def _encode_escapes(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")


def _decode_escapes(value: str) -> str:
    v = value or ""
    v = v.replace("\\r", "\r").replace("\\n", "\n").replace("\\t", "\t")
    v = v.replace("\\\\", "\\")
    return v


class SettingsDialog(QDialog):
    def __init__(self, config: AppConfig, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self._config = config

        self.apply_preset_img_recomment = QPushButton("应用：img recomment")
        self.apply_preset_img_recomment.clicked.connect(self._apply_preset_img_recomment)

        self.api_url = QLineEdit(config.api_url)
        self.token = QLineEdit(config.token)
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.output_dir = QLineEdit(config.output_dir)

        choose_btn = QPushButton("选择...")
        choose_btn.clicked.connect(self._choose_output_dir)
        out_row = QHBoxLayout()
        out_row.addWidget(self.output_dir)
        out_row.addWidget(choose_btn)
        out_wrap = QWidget()
        out_wrap.setLayout(out_row)

        self.pdf_chunk_pages = QSpinBox()
        self.pdf_chunk_pages.setRange(1, 1000)
        self.pdf_chunk_pages.setValue(int(config.pdf_chunk_pages))

        self.pdf_rerun_segments = QLineEdit((getattr(config, "pdf_rerun_segments", "") or "").strip())
        self.pdf_rerun_segments.setPlaceholderText("示例：009（兼容输入 9，留空=关闭）")

        self.pdf_image_ocr_pages = QLineEdit((getattr(config, "pdf_image_ocr_pages", "") or "").strip())
        self.pdf_image_ocr_pages.setPlaceholderText("示例：15,18-20；分卷文件可填绝对页码如 391-455（留空=关闭）")

        self.pdf_image_ocr_dpi = QSpinBox()
        self.pdf_image_ocr_dpi.setRange(72, 600)
        self.pdf_image_ocr_dpi.setValue(int(getattr(config, "pdf_image_ocr_dpi", 300) or 300))

        self.pdf_image_ocr_max_side_px = QSpinBox()
        self.pdf_image_ocr_max_side_px.setRange(512, 12000)
        self.pdf_image_ocr_max_side_px.setValue(int(getattr(config, "pdf_image_ocr_max_side_px", 5000) or 5000))

        self.max_retries = QSpinBox()
        self.max_retries.setRange(0, 20)
        self.max_retries.setValue(int(config.max_retries))

        self.connect_timeout_s = QSpinBox()
        self.connect_timeout_s.setRange(1, 120)
        self.connect_timeout_s.setValue(int(config.connect_timeout_s))

        self.read_timeout_s = QSpinBox()
        self.read_timeout_s.setRange(5, 3600)
        self.read_timeout_s.setValue(int(config.read_timeout_s))

        self.request_min_interval_ms = QSpinBox()
        self.request_min_interval_ms.setRange(0, 5000)
        self.request_min_interval_ms.setValue(int(config.request_min_interval_ms))

        self.page_separator = QLineEdit(_encode_escapes(config.page_separator))
        self.insert_page_numbers = QCheckBox("插入页码（每页前插入注释标记 + 可见页码行）")
        self.insert_page_numbers.setChecked(bool(config.insert_page_numbers))

        self.markdown_image_width_percent = QSpinBox()
        self.markdown_image_width_percent.setRange(0, 100)
        self.markdown_image_width_percent.setValue(int(getattr(config, "markdown_image_width_percent", 0) or 0))

        self.markdown_image_max_height_px = QSpinBox()
        self.markdown_image_max_height_px.setRange(0, 4000)
        self.markdown_image_max_height_px.setValue(int(getattr(config, "markdown_image_max_height_px", 0) or 0))

        self.merge_image_fragments = QCheckBox("合并碎片图片（观感优先，尽量还原原PDF大图）")
        self.merge_image_fragments.setChecked(bool(getattr(config, "merge_image_fragments", True)))

        self.use_system_proxy = QCheckBox("使用系统/环境代理（HTTP(S)_PROXY 等）")
        self.use_system_proxy.setChecked(bool(config.use_system_proxy))

        self.use_doc_orientation_classify = QCheckBox("useDocOrientationClassify（半选=跟随服务端默认）")
        self.use_doc_orientation_classify.setTristate(True)
        self._set_tristate(self.use_doc_orientation_classify, config.use_doc_orientation_classify)

        self.use_doc_unwarping = QCheckBox("useDocUnwarping（半选=跟随服务端默认）")
        self.use_doc_unwarping.setTristate(True)
        self._set_tristate(self.use_doc_unwarping, config.use_doc_unwarping)

        self.use_chart_recognition = QCheckBox("useChartRecognition（半选=跟随服务端默认）")
        self.use_chart_recognition.setTristate(True)
        self._set_tristate(self.use_chart_recognition, config.use_chart_recognition)

        self.use_layout_detection = QCheckBox("useLayoutDetection（半选=跟随服务端默认）")
        self.use_layout_detection.setTristate(True)
        self._set_tristate(self.use_layout_detection, config.use_layout_detection)
        self.use_layout_detection.stateChanged.connect(self._sync_layout_detection_deps)

        self.prompt_label = QComboBox()
        self.prompt_label.addItem("（默认：跟随服务端）", "")
        self.prompt_label.addItem("ocr", "ocr")
        self.prompt_label.addItem("formula", "formula")
        self.prompt_label.addItem("table", "table")
        self.prompt_label.addItem("chart", "chart")
        cur_prompt = (getattr(config, "prompt_label", None) or "").strip().lower()
        idx_prompt = self.prompt_label.findData(cur_prompt)
        self.prompt_label.setCurrentIndex(idx_prompt if idx_prompt >= 0 else 0)

        self.layout_merge_bboxes_mode = QComboBox()
        self.layout_merge_bboxes_mode.addItem("（默认：跟随服务端）", "")
        self.layout_merge_bboxes_mode.addItem("large（保留外部最大框，减少碎片）", "large")
        self.layout_merge_bboxes_mode.addItem("small（保留内部小框，更细粒度）", "small")
        self.layout_merge_bboxes_mode.addItem("union（保留全部重叠框，可能更碎）", "union")
        cur_merge = (getattr(config, "layout_merge_bboxes_mode", None) or "").strip().lower()
        idx_merge = self.layout_merge_bboxes_mode.findData(cur_merge)
        self.layout_merge_bboxes_mode.setCurrentIndex(idx_merge if idx_merge >= 0 else 0)

        self.layout_shape_mode = QComboBox()
        self.layout_shape_mode.addItem("（默认：跟随服务端）", "")
        self.layout_shape_mode.addItem("rect（矩形框，避免透视裁剪畸变）", "rect")
        self.layout_shape_mode.addItem("quad（四边形框）", "quad")
        self.layout_shape_mode.addItem("poly（多边形框）", "poly")
        self.layout_shape_mode.addItem("auto（自动选择）", "auto")
        cur = (config.layout_shape_mode or "").strip().lower()
        idx = self.layout_shape_mode.findData(cur)
        self.layout_shape_mode.setCurrentIndex(idx if idx >= 0 else 0)

        self.visualize = QCheckBox("visualize（返回调试图 outputImages，半选=跟随服务端默认）")
        self.visualize.setTristate(True)
        self._set_tristate(self.visualize, config.visualize)

        self.restructure_pages = QCheckBox("restructurePages（半选=跟随服务端默认）")
        self.restructure_pages.setTristate(True)
        self._set_tristate(self.restructure_pages, config.restructure_pages)

        self.merge_tables = QCheckBox("mergeTables（半选=跟随服务端默认）")
        self.merge_tables.setTristate(True)
        self._set_tristate(self.merge_tables, config.merge_tables)

        self.relevel_titles = QCheckBox("relevelTitles（半选=跟随服务端默认）")
        self.relevel_titles.setTristate(True)
        self._set_tristate(self.relevel_titles, config.relevel_titles)

        self.prettify_markdown = QCheckBox("prettifyMarkdown（半选=跟随服务端默认）")
        self.prettify_markdown.setTristate(True)
        self._set_tristate(self.prettify_markdown, config.prettify_markdown)

        self.show_formula_number = QCheckBox("showFormulaNumber（半选=跟随服务端默认）")
        self.show_formula_number.setTristate(True)
        self._set_tristate(self.show_formula_number, config.show_formula_number)

        self.concatenate_pages = QCheckBox("concatenatePages（调用 /restructure-pages，半选=关闭）")
        self.concatenate_pages.setTristate(True)
        self._set_tristate(self.concatenate_pages, config.concatenate_pages)

        self.restructure_api_url = QLineEdit(config.restructure_api_url or "")
        self.restructure_api_url.setPlaceholderText("默认：由 API_URL 自动推导（/layout-parsing -> /restructure-pages）")

        self.debug_dump_pages = QCheckBox("调试：落盘每页 page_XXXX.md（默认关闭）")
        self.debug_dump_pages.setChecked(bool(config.debug_dump_pages))

        form = QFormLayout()
        form.addRow("PRESET", self.apply_preset_img_recomment)
        form.addRow("API_URL", self.api_url)
        form.addRow("TOKEN", self.token)
        form.addRow("OUTPUT_DIR", out_wrap)
        form.addRow("PDF_CHUNK_PAGES", self.pdf_chunk_pages)
        form.addRow("PDF_RERUN_SEGMENTS（分段重打）", self.pdf_rerun_segments)
        form.addRow("PDF_IMAGE_OCR_PAGES（补漏：指定页重跑）", self.pdf_image_ocr_pages)
        form.addRow("PDF_IMAGE_OCR_DPI", self.pdf_image_ocr_dpi)
        form.addRow("PDF_IMAGE_OCR_MAX_SIDE_PX", self.pdf_image_ocr_max_side_px)
        form.addRow("MAX_RETRIES", self.max_retries)
        form.addRow("CONNECT_TIMEOUT_S", self.connect_timeout_s)
        form.addRow("READ_TIMEOUT_S", self.read_timeout_s)
        form.addRow("REQUEST_MIN_INTERVAL_MS", self.request_min_interval_ms)
        form.addRow("PAGE_SEPARATOR（支持 \\n）", self.page_separator)
        form.addRow("INSERT_PAGE_NUMBERS", self.insert_page_numbers)
        form.addRow("MD_IMAGE_WIDTH_PERCENT（0=不处理，建议 50~80）", self.markdown_image_width_percent)
        form.addRow("MD_IMAGE_MAX_HEIGHT_PX（0=不限制，EPUB 建议 600~900）", self.markdown_image_max_height_px)
        form.addRow("MERGE_IMAGE_FRAGMENTS", self.merge_image_fragments)
        form.addRow("USE_SYSTEM_PROXY", self.use_system_proxy)
        form.addRow("useDocOrientationClassify", self.use_doc_orientation_classify)
        form.addRow("useDocUnwarping", self.use_doc_unwarping)
        form.addRow("useChartRecognition", self.use_chart_recognition)
        form.addRow("useLayoutDetection", self.use_layout_detection)
        form.addRow("promptLabel（useLayoutDetection=false 时可用）", self.prompt_label)
        form.addRow("layoutMergeBboxesMode", self.layout_merge_bboxes_mode)
        form.addRow("layoutShapeMode", self.layout_shape_mode)
        form.addRow("visualize", self.visualize)
        form.addRow("restructurePages", self.restructure_pages)
        form.addRow("mergeTables", self.merge_tables)
        form.addRow("relevelTitles", self.relevel_titles)
        form.addRow("prettifyMarkdown", self.prettify_markdown)
        form.addRow("showFormulaNumber", self.show_formula_number)
        form.addRow("concatenatePages", self.concatenate_pages)
        form.addRow("RESTRUCTURE_API_URL", self.restructure_api_url)
        form.addRow("DEBUG_DUMP_PAGES", self.debug_dump_pages)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.setLayout(layout)
        self._sync_layout_detection_deps()

    def _choose_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择输出目录", self.output_dir.text().strip() or "")
        if path:
            self.output_dir.setText(path)

    def get_config(self) -> AppConfig:
        return AppConfig(
            api_url=self.api_url.text().strip(),
            token=self.token.text().strip(),
            output_dir=self.output_dir.text().strip(),
            pdf_chunk_pages=int(self.pdf_chunk_pages.value()),
            pdf_rerun_segments=self.pdf_rerun_segments.text().strip(),
            pdf_image_ocr_pages=self.pdf_image_ocr_pages.text().strip(),
            pdf_image_ocr_dpi=int(self.pdf_image_ocr_dpi.value()),
            pdf_image_ocr_max_side_px=int(self.pdf_image_ocr_max_side_px.value()),
            max_retries=int(self.max_retries.value()),
            connect_timeout_s=int(self.connect_timeout_s.value()),
            read_timeout_s=int(self.read_timeout_s.value()),
            request_min_interval_ms=int(self.request_min_interval_ms.value()),
            page_separator=_decode_escapes(self.page_separator.text()),
            insert_page_numbers=bool(self.insert_page_numbers.isChecked()),
            markdown_image_width_percent=int(self.markdown_image_width_percent.value()),
            markdown_image_max_height_px=int(self.markdown_image_max_height_px.value()),
            merge_image_fragments=bool(self.merge_image_fragments.isChecked()),
            use_system_proxy=bool(self.use_system_proxy.isChecked()),
            use_doc_orientation_classify=self._get_tristate(self.use_doc_orientation_classify),
            use_doc_unwarping=self._get_tristate(self.use_doc_unwarping),
            use_chart_recognition=self._get_tristate(self.use_chart_recognition),
            use_layout_detection=self._get_tristate(self.use_layout_detection),
            prompt_label=self.prompt_label.currentData() or None,
            layout_merge_bboxes_mode=self.layout_merge_bboxes_mode.currentData() or None,
            layout_shape_mode=self.layout_shape_mode.currentData() or None,
            visualize=self._get_tristate(self.visualize),
            restructure_pages=self._get_tristate(self.restructure_pages),
            merge_tables=self._get_tristate(self.merge_tables),
            relevel_titles=self._get_tristate(self.relevel_titles),
            prettify_markdown=self._get_tristate(self.prettify_markdown),
            show_formula_number=self._get_tristate(self.show_formula_number),
            concatenate_pages=self._get_tristate(self.concatenate_pages),
            restructure_api_url=self.restructure_api_url.text().strip(),
            debug_dump_pages=bool(self.debug_dump_pages.isChecked()),
        )

    @staticmethod
    def _set_tristate(box: QCheckBox, value) -> None:
        if value is None:
            box.setCheckState(Qt.CheckState.PartiallyChecked)
        elif bool(value):
            box.setCheckState(Qt.CheckState.Checked)
        else:
            box.setCheckState(Qt.CheckState.Unchecked)

    @staticmethod
    def _get_tristate(box: QCheckBox):
        state = box.checkState()
        if state == Qt.CheckState.PartiallyChecked:
            return None
        if state == Qt.CheckState.Checked:
            return True
        return False

    def _sync_layout_detection_deps(self) -> None:
        """
        UI 约束：
        - promptLabel 仅在 useLayoutDetection=false 时有效
        - layoutMergeBboxesMode/layoutShapeMode 仅在 useLayoutDetection!=false 时有效
        """
        state = self.use_layout_detection.checkState()
        use_layout_detection = None if state == Qt.CheckState.PartiallyChecked else (state == Qt.CheckState.Checked)

        self.prompt_label.setEnabled(use_layout_detection is False)
        enable_layout_opts = use_layout_detection is not False
        self.layout_merge_bboxes_mode.setEnabled(enable_layout_opts)
        self.layout_shape_mode.setEnabled(enable_layout_opts)

    def _apply_preset_img_recomment(self) -> None:
        """
        img recomment（图片推荐预设）：
        - 优先保证“整页不漏识别”
        - 将可能导致裁剪/过滤偏差的选项设置为更稳的组合
        """
        self._set_tristate(self.use_layout_detection, True)
        self._set_tristate(self.use_doc_unwarping, False)
        self._set_tristate(self.use_doc_orientation_classify, False)

        idx_merge = self.layout_merge_bboxes_mode.findData("union")
        self.layout_merge_bboxes_mode.setCurrentIndex(idx_merge if idx_merge >= 0 else 0)

        idx_shape = self.layout_shape_mode.findData("rect")
        self.layout_shape_mode.setCurrentIndex(idx_shape if idx_shape >= 0 else 0)

        # promptLabel 仅在 useLayoutDetection=false 时有效；预设走 layoutDetection，因此重置为默认值。
        idx_prompt = self.prompt_label.findData("")
        self.prompt_label.setCurrentIndex(idx_prompt if idx_prompt >= 0 else 0)

        # 调试项不作为“默认常开”；需要排查时再手动打开即可。
        self._set_tristate(self.visualize, False)
        self.debug_dump_pages.setChecked(False)
        self._sync_layout_detection_deps()
