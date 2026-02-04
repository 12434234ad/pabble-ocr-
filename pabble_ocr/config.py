from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


def _default_output_dir() -> str:
    if os.name == "nt":
        return r"E:\output"
    return str(Path.home() / "output")


def _config_path() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "PabbleOCR" / "config.json"
    return Path.home() / ".config" / "pabble-ocr" / "config.json"


@dataclass(frozen=True)
class AppConfig:
    api_url: str = ""
    token: str = ""
    output_dir: str = _default_output_dir()
    pdf_chunk_pages: int = 80
    max_retries: int = 3
    connect_timeout_s: int = 10
    read_timeout_s: int = 120
    request_min_interval_ms: int = 0
    page_separator: str = "\n\n---\n\n"
    insert_page_numbers: bool = False
    use_doc_orientation_classify: Optional[bool] = None
    use_doc_unwarping: Optional[bool] = None
    use_chart_recognition: Optional[bool] = None
    use_layout_detection: Optional[bool] = None
    # 版面区域检测的重叠框过滤方式：large/small/union；None=跟随服务端默认
    layout_merge_bboxes_mode: Optional[str] = None
    layout_shape_mode: Optional[str] = None
    visualize: Optional[bool] = None
    restructure_pages: Optional[bool] = None
    merge_tables: Optional[bool] = None
    relevel_titles: Optional[bool] = None
    prettify_markdown: Optional[bool] = None
    show_formula_number: Optional[bool] = None
    concatenate_pages: Optional[bool] = None
    restructure_api_url: str = ""
    debug_dump_pages: bool = False
    # requests 默认会从环境变量/系统设置读取代理（HTTP_PROXY/HTTPS_PROXY 等）。
    # 某些代理会导致超时/证书问题；可关闭该行为以直连服务端。
    use_system_proxy: bool = True
    # ReadTimeout 往往表示“服务端可能仍在处理”，此时自动重试可能导致重复请求与重复计费。
    # 默认关闭 ReadTimeout 重试，用户可在配置文件里手动开启。
    retry_on_read_timeout: bool = False
    # LayoutParsing 可选参数：当 useLayoutDetection=false 时，可通过 promptLabel 指定任务类型
    # （例如 ocr / formula / table / chart）。留空则由服务端默认策略决定。
    prompt_label: Optional[str] = None
    # Markdown 图片展示缩放：将 `![](...)` 重写为 HTML `<img ... style="width:XX%; height:auto;">`
    # 以便在部分 Markdown/PDF/Word 转换链路中避免按“原始像素尺寸”渲染导致图片过大。
    # - 0：不做重写（保持服务端返回的 Markdown 原样）
    # - 1~100：按百分比设置宽度（建议 50~80）
    markdown_image_width_percent: int = 0
    # Markdown 图片最大高度（像素，0=不限制）。
    # 用于 EPUB 等“重排 + 分页”阅读器：当图片过高时可能被切成多页；限制高度可显著改善观感。
    markdown_image_max_height_px: int = 0
    # 观感优先：将同一张“大图”被切碎的多个图片块做本地合并，尽量还原原 PDF 观感。
    # 依赖服务端返回的 `prunedResult` 中的图片区域坐标；若缺失则自动跳过。
    merge_image_fragments: bool = True

    # PDF 偶发漏字补救：对指定页本地渲染为图片后以“图片模式”重跑 OCR，并用重跑结果替换该页输出。
    # 页码格式示例：`15`、`15,18-20`；留空表示关闭。
    pdf_image_ocr_pages: str = ""
    # 本地渲染 PDF 页为图片的 DPI（越大越清晰，但更慢/更占内存）；常用 200~400。
    pdf_image_ocr_dpi: int = 300
    # 渲染图片最大边长（像素）。用于避免超大页面/超高 DPI 导致内存暴涨。
    pdf_image_ocr_max_side_px: int = 5000

    def ensure_dirs(self) -> None:
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)


def load_config() -> AppConfig:
    path = _config_path()
    if not path.exists():
        return AppConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return AppConfig()
    known = {k: v for k, v in data.items() if k in AppConfig.__dataclass_fields__}
    return AppConfig(**known)


def save_config(config: AppConfig) -> Path:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path
