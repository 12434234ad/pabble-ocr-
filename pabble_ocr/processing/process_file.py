from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import Callable

from pabble_ocr.adapters.layout_parsing_client import LayoutParsingClient, build_layout_parsing_options
from pabble_ocr.config import AppConfig
from pabble_ocr.core.file_types import detect_file_type
from pabble_ocr.core.models import FileTaskState, QueueItem, SegmentState
from pabble_ocr.core.state_store import save_state
from pabble_ocr.md.postprocess import apply_markdown_image_width
from pabble_ocr.pdf.splitter import ensure_pdf_segments
from pabble_ocr.md.merge import merge_and_materialize, merge_best_effort
from pabble_ocr.md.images import download_images
from pabble_ocr.utils.io import atomic_write_json, atomic_write_text


LogFn = Callable[[str], None]
ProgressFn = Callable[[float, str], None]


class CanceledError(RuntimeError):
    pass


def _ocr_options_hash(config: AppConfig, *, include_pdf_image_rerun_options: bool = False) -> str:
    meta = {
        "apiUrl": (config.api_url or "").strip() or None,
        "restructureApiUrl": (config.restructure_api_url or "").strip() or None,
        "concatenatePages": config.concatenate_pages if config.concatenate_pages is not None else None,
        "layoutParsingOptions": build_layout_parsing_options(config),
        # 注意：PDF_IMAGE_OCR_* 仅用于“补漏页图片重跑”，不应触发“已完成分段全量重跑”。
        # 否则用户只改了补漏页范围，也会把所有 done 分段打回重跑。
    }
    if include_pdf_image_rerun_options:
        # 兼容旧版本哈希：旧实现把这三项并入 OCR 参数哈希。
        meta["pdfImageOcrPages"] = (getattr(config, "pdf_image_ocr_pages", "") or "").strip() or None
        meta["pdfImageOcrDpi"] = int(getattr(config, "pdf_image_ocr_dpi", 300) or 300)
        meta["pdfImageOcrMaxSidePx"] = int(getattr(config, "pdf_image_ocr_max_side_px", 5000) or 5000)
    raw = json.dumps(meta, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _is_ocr_hash_compatible(saved_hash: str | None, *, current_hash: str, legacy_hash: str) -> bool:
    h = (saved_hash or "").strip()
    if not h:
        return False
    return h == current_hash or h == legacy_hash


def _json_compact(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        return str(value)


_PAGE_SPEC_RE = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+)\s*)?$")
_FILE_PAGE_RANGE_RE = re.compile(r"(?:^|[_-])p(\d+)-(\d+)(?:$|[_-])", re.IGNORECASE)
_SEGMENT_SPEC_TOKEN_RE = re.compile(r"^\s*(\d{1,3})\s*$")
_SEGMENT_ID_CODE_RE = re.compile(r"^part_(\d{3})_", re.IGNORECASE)
_ABS_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_WIN_ABS_RE = re.compile(r"^[A-Za-z]:[\\\\/]")


def _parse_page_spec(value: str) -> set[int]:
    """
    解析页码表达式：`15`、`15,18-20`。
    返回 1-based 页码集合；非法片段会被忽略。
    """
    pages: set[int] = set()
    raw = (value or "").strip()
    if not raw:
        return pages
    for part in raw.split(","):
        p = part.strip()
        if not p:
            continue
        m = _PAGE_SPEC_RE.match(p)
        if not m:
            continue
        try:
            a = int(m.group(1))
            b = int(m.group(2)) if m.group(2) else a
        except Exception:
            continue
        if a <= 0 or b <= 0:
            continue
        lo, hi = (a, b) if a <= b else (b, a)
        # 轻量保护：避免意外填了超大范围导致卡死
        if hi - lo > 5000:
            continue
        for x in range(lo, hi + 1):
            pages.add(int(x))
    return pages


def _parse_segment_spec(value: str) -> set[str]:
    """
    解析分段表达式（首版）：`009`（兼容 `9`）。
    当前支持以逗号分隔多个单值，例如：`009,11`。
    返回 3 位分段号集合（如 `{"009", "011"}`）；非法输入抛 ValueError。
    """
    raw = (value or "").strip()
    if not raw:
        return set()

    out: set[str] = set()
    for token in raw.split(","):
        p = token.strip()
        if not p:
            continue
        m = _SEGMENT_SPEC_TOKEN_RE.match(p)
        if not m:
            raise ValueError(f"非法分段号：{p}（仅支持数字，如 009）")
        num = int(m.group(1))
        if num <= 0:
            raise ValueError(f"非法分段号：{p}（必须大于 0）")
        out.add(f"{num:03d}")
    if not out:
        raise ValueError("请输入至少一个分段号（如 009）")
    return out


def _segment_code_from_segment_id(segment_id: str) -> str | None:
    m = _SEGMENT_ID_CODE_RE.match((segment_id or "").strip())
    if not m:
        return None
    return m.group(1)


def _infer_pdf_page_range_from_filename(pdf_path: Path) -> tuple[int, int] | None:
    """
    从文件名推断“原始文档绝对页码范围”，例如：
    - part_007_p0391-0455.pdf -> (391, 455)
    """
    stem = (pdf_path.stem or "").strip()
    if not stem:
        return None
    m = _FILE_PAGE_RANGE_RE.search(stem)
    if not m:
        return None
    try:
        a = int(m.group(1))
        b = int(m.group(2))
    except Exception:
        return None
    if a <= 0 or b <= 0:
        return None
    return (a, b) if a <= b else (b, a)


def _map_local_page_to_inferred_absolute_page(*, local_page_no: int, inferred_file_page_range: tuple[int, int] | None) -> int | None:
    if local_page_no <= 0 or not inferred_file_page_range:
        return None
    start, end = inferred_file_page_range
    absolute = int(start) + int(local_page_no) - 1
    if absolute < int(start) or absolute > int(end):
        return None
    return absolute


def _segment_matches_rerun_pages(
    *,
    seg: SegmentState,
    rerun_pages: set[int],
    inferred_file_page_range: tuple[int, int] | None,
) -> bool:
    if not rerun_pages:
        return False

    seg_start = int(seg.start_page)
    seg_end = int(seg.end_page)
    if seg_start > seg_end:
        seg_start, seg_end = seg_end, seg_start

    # 1) 直接按“当前输入 PDF 的页码”匹配（默认语义）。
    for p in rerun_pages:
        if seg_start <= int(p) <= seg_end:
            return True

    # 2) 若输入文件名含原始绝对页段（如 part_007_p0391-0455），同时支持按绝对页码匹配。
    if not inferred_file_page_range:
        return False
    abs_start = _map_local_page_to_inferred_absolute_page(local_page_no=seg_start, inferred_file_page_range=inferred_file_page_range)
    abs_end = _map_local_page_to_inferred_absolute_page(local_page_no=seg_end, inferred_file_page_range=inferred_file_page_range)
    if abs_start is None or abs_end is None:
        return False
    if abs_start > abs_end:
        abs_start, abs_end = abs_end, abs_start
    for p in rerun_pages:
        if abs_start <= int(p) <= abs_end:
            return True
    return False


def _render_pdf_page_to_png(*, pdf_path: Path, page_index: int, dpi: int, max_side_px: int, out_path: Path) -> bool:
    """
    使用 QtPdf 将 PDF 页渲染为 PNG（用于“图片模式重跑”）。
    - dpi: 渲染精度（72=1x）
    - max_side_px: 最大边长限制，避免内存暴涨
    """
    try:
        from PySide6.QtCore import QSize
        from PySide6.QtPdf import QPdfDocument
    except Exception:
        return False

    def _safe_int(v: object) -> int | None:
        try:
            return int(v)  # type: ignore[arg-type]
        except Exception:
            return None

    def _is_load_ok(doc: QPdfDocument, st: object) -> bool:
        try:
            if st == QPdfDocument.Error.None_:  # type: ignore[attr-defined]
                return True
        except Exception:
            pass
        value_attr = getattr(st, "value", None)
        if value_attr is not None:
            if callable(value_attr):
                try:
                    value_attr = value_attr()
                except Exception:
                    value_attr = None
            vi = _safe_int(value_attr) if value_attr is not None else None
            if vi == 0:
                return True
        name = getattr(st, "name", None)
        if isinstance(name, str) and name.lower() in {"none_", "noerror", "none"}:
            return True
        si = _safe_int(st)
        return si == 0

    try:
        dpi_n = max(72, int(dpi or 0))
        max_side = max(512, int(max_side_px or 0))

        doc = QPdfDocument()
        st = doc.load(str(pdf_path))
        if not _is_load_ok(doc, st):
            return False

        page_count = _safe_int(getattr(doc, "pageCount", lambda: 0)()) or 0
        if page_index < 0 or page_index >= page_count:
            return False

        size_pt = doc.pagePointSize(int(page_index))
        w_pt = float(getattr(size_pt, "width", lambda: 0.0)())
        h_pt = float(getattr(size_pt, "height", lambda: 0.0)())
        if w_pt <= 0 or h_pt <= 0:
            return False

        scale = float(dpi_n) / 72.0
        w = max(1, int(round(w_pt * scale)))
        h = max(1, int(round(h_pt * scale)))
        if w > max_side or h > max_side:
            s2 = min(max_side / float(w), max_side / float(h))
            w = max(1, int(round(w * s2)))
            h = max(1, int(round(h * s2)))

        img = doc.render(int(page_index), QSize(int(w), int(h)))
        if img is None or img.isNull():
            return False
        out_path.parent.mkdir(parents=True, exist_ok=True)
        return bool(img.save(str(out_path)))
    except Exception:
        return False


def _run_with_heartbeat(*, fn: Callable[[], object], log: LogFn, title: str, interval_s: int = 15) -> object:
    stop = threading.Event()
    started = time.time()

    def _beat() -> None:
        while not stop.wait(interval_s):
            elapsed = int(time.time() - started)
            log(f"{title}（已等待 {elapsed}s）")

    t = threading.Thread(target=_beat, daemon=True)
    t.start()
    try:
        return fn()
    finally:
        stop.set()
        t.join(timeout=1.0)


def _wait_if_paused(is_paused: Callable[[], bool], is_canceled: Callable[[], bool]) -> None:
    while is_paused():
        if is_canceled():
            raise CanceledError()
        time.sleep(0.1)


def _segment_md_path(output_dir: Path, seg: SegmentState) -> Path:
    return output_dir / "_parts" / f"{seg.segment_id}.md"


def _segment_images_path(output_dir: Path, seg: SegmentState) -> Path:
    return output_dir / "_parts" / f"{seg.segment_id}_images.json"

def _segment_pruned_path(output_dir: Path, seg: SegmentState) -> Path:
    return output_dir / "_parts" / f"{seg.segment_id}_pruned.json"


def _segment_has_reusable_outputs(*, output_dir: Path, seg: SegmentState) -> bool:
    # 仅要求分段 Markdown 存在即可参与合并；images/pruned 允许缺失（兼容历史产物）。
    return _segment_md_path(output_dir, seg).exists()


def _prefix_images_to_parts(images: dict[str, str]) -> dict[str, str]:
    """
    将服务端返回的相对图片路径（通常形如 imgs/xxx.jpg）落盘到输出目录的 `_parts/` 下，
    以保证分段 Markdown（位于 `_parts/*.md`）能直接通过相对路径访问图片。
    """
    out: dict[str, str] = {}
    for k, v in (images or {}).items():
        rel = str(k or "").strip().replace("\\", "/")
        if not rel:
            continue
        # 绝对引用不改写（理论上 images dict 的 key 应该都是相对路径，这里做兜底）
        if _ABS_SCHEME_RE.match(rel) or _WIN_ABS_RE.match(rel) or rel.startswith(("/", "\\", "../")):
            out[rel] = v
            continue
        if rel.startswith("./"):
            rel = rel[2:]
        if rel.startswith("_parts/"):
            out[rel] = v
        else:
            out[f"_parts/{rel}"] = v
    return out

def _namespace_image_rel_path(*, segment_id: str, rel_path: str) -> str:
    """
    将图片相对路径做“分段命名空间”以避免跨分段重名覆盖：
    - imgs/xxx.jpg -> imgs/<segment_id>/xxx.jpg
    - images/xxx.png -> images/<segment_id>/xxx.png
    幂等：已包含该 segment_id 的不重复改写。
    """
    rel = (rel_path or "").strip().replace("\\", "/")
    if not rel:
        return rel
    # 绝对引用不改写
    if _ABS_SCHEME_RE.match(rel) or _WIN_ABS_RE.match(rel) or rel.startswith(("/", "\\", "../")):
        return rel
    if rel.startswith("./"):
        rel = rel[2:]
    if rel.startswith("_parts/"):
        rel = rel[len("_parts/") :]

    if "/" not in rel:
        return f"{segment_id}/{rel}"
    top, rest = rel.split("/", 1)
    if rest.startswith(f"{segment_id}/"):
        return rel
    return f"{top}/{segment_id}/{rest}"

def _namespace_page_markdown_and_images(
    *,
    segment_id: str,
    markdown_text: str,
    markdown_images: dict[str, str],
) -> tuple[str, dict[str, str]]:
    """
    同步改写：page markdown + markdown_images key，使“最终落盘路径”与“Markdown 引用”一致。
    """
    text = markdown_text or ""
    imgs = markdown_images or {}
    if not imgs:
        return text, {}

    mapping: dict[str, str] = {}
    out_images: dict[str, str] = {}
    for old, ref in imgs.items():
        old_norm = str(old or "").strip().replace("\\", "/")
        if not old_norm:
            continue
        new_norm = _namespace_image_rel_path(segment_id=segment_id, rel_path=old_norm)
        out_images[new_norm] = ref
        if new_norm != old_norm:
            mapping[old_norm] = new_norm
            mapping[old_norm.replace("/", "\\")] = new_norm

    if mapping:
        for src in sorted(mapping.keys(), key=len, reverse=True):
            text = text.replace(src, mapping[src])
    return text, out_images

def _write_failed_segment_placeholder(*, output_dir: Path, seg: SegmentState, config: AppConfig) -> None:
    # 写占位文件，保证失败时也能落盘可读信息，便于定位缺页与续跑。
    start = int(seg.start_page)
    end = int(seg.end_page)
    err = (seg.last_error or "").strip() or "unknown"
    title = f"**第 {start} 页（识别失败占位）**" if config.insert_page_numbers else "**识别失败占位**"
    md = "\n".join(
        [
            f"<!-- page:{start} -->" if config.insert_page_numbers else "",
            "",
            title,
            "",
            f"> 分段：`{seg.segment_id}`（页范围 {start}-{end}）",
            f"> 错误：{err}",
            "",
            "> 建议：提高 `READ_TIMEOUT_S` 后重试；或调小 `PDF_CHUNK_PAGES` 重新切分后重试（若当前仅 1 个 `pdf_full` 分段且未完成，会自动重新切分）。该分段成功后会自动覆盖本占位内容。",
            "",
        ]
    ).strip()
    atomic_write_text(_segment_md_path(output_dir, seg), md, encoding="utf-8")
    atomic_write_json(_segment_images_path(output_dir, seg), {})
    atomic_write_json(_segment_pruned_path(output_dir, seg), [])


def _pages_dir(output_dir: Path) -> Path:
    return output_dir / "pages"


def _page_md_path(output_dir: Path, page_no: int) -> Path:
    return _pages_dir(output_dir) / f"page_{page_no:04d}.md"


def _page_images_path(output_dir: Path, page_no: int) -> Path:
    return _pages_dir(output_dir) / f"page_{page_no:04d}_images.json"

def _render_pages_markdown(*, pages: list[str], start_page: int, config: AppConfig) -> str:
    blocks: list[str] = []
    for idx, text in enumerate(pages):
        if config.insert_page_numbers:
            page_no = start_page + idx
            # 同时提供：机器可读的注释标记 + 人眼可见的页码行
            # 仅插入 HTML 注释在多数 Markdown 预览/转换链路里会被隐藏或丢弃，用户会误以为“没有页码”。（见 issue: INSERT_PAGE_NUMBERS）
            blocks.append(f"<!-- page:{page_no} -->\n\n**第 {page_no} 页**\n\n{text}".rstrip())
        else:
            blocks.append((text or "").rstrip())
    sep = config.page_separator or ""
    if not sep:
        return "\n\n".join([b for b in blocks if b])
    return sep.join([b for b in blocks if b])


def _debug_dump_request_options(
    *,
    config: AppConfig,
    output_dir: Path,
    seg: SegmentState,
    file_type: str,
    ocr_hash: str,
    options: dict[str, object],
    input_path: Path,
) -> None:
    if not bool(getattr(config, "debug_dump_pages", False)):
        return
    try:
        payload = {
            "segmentId": seg.segment_id,
            "fileType": file_type,
            "inputPath": str(input_path),
            "apiUrl": (config.api_url or "").strip() or None,
            "restructureApiUrl": (config.restructure_api_url or "").strip() or None,
            "ocrOptionsHash": ocr_hash,
            "layoutParsingOptions": options,
        }
        atomic_write_json(output_dir / "_parts" / f"{seg.segment_id}_request_options.json", payload)
    except Exception:
        pass


def process_queue_item(
    *,
    config: AppConfig,
    item: QueueItem,
    state: FileTaskState,
    is_paused: Callable[[], bool],
    is_canceled: Callable[[], bool],
    log: LogFn,
    progress: ProgressFn,
) -> None:
    if is_canceled():
        raise CanceledError()

    ensure_output_dir(item.output_dir)
    ensure_output_dir(item.output_dir / "_parts")

    ft = detect_file_type(item.input_path)
    if ft == "unknown":
        raise RuntimeError("不支持的文件类型")

    client = LayoutParsingClient(config)
    ocr_hash = _ocr_options_hash(config)
    ocr_hash_legacy = _ocr_options_hash(config, include_pdf_image_rerun_options=True)
    opts = build_layout_parsing_options(config)
    opt_desc = _json_compact(opts)

    if ft == "image":
        if not state.segments:
            state.segments = [
                SegmentState(
                    segment_id="image_001_p0001-0001",
                    start_page=1,
                    end_page=1,
                    part_path=str(item.input_path),
                )
            ]
            save_state(item.output_dir, state)

        seg = state.segments[0]
        if seg.done:
            if not _is_ocr_hash_compatible(seg.ocr_options_hash, current_hash=ocr_hash, legacy_hash=ocr_hash_legacy):
                log("检测到 OCR 参数变化：将重新调用 API（避免沿用旧结果）。")
                seg.done = False
                seg.last_error = None
                seg.elapsed_s = None
                state.merged_md_done = False
                save_state(item.output_dir, state)
            else:
                # 允许“仅调整本地渲染/合并逻辑（例如 Markdown 图片宽度）”后重新生成 merged_result.md，
                # 避免再次调用 OCR 接口。
                progress(0.9, "已完成（跳过识别），重新合并输出（如需应用新的 OCR 参数，请删除输出目录内 task_state.json 后重跑）")
                merge_and_materialize(config=config, output_dir=item.output_dir, state=state, log=log)
                progress(1.0, "输出完成")
                return

        _wait_if_paused(is_paused, is_canceled)
        if is_canceled():
            raise CanceledError()

        seg.attempts += 1
        seg.ocr_options_hash = ocr_hash
        save_state(item.output_dir, state)
        _debug_dump_request_options(
            config=config,
            output_dir=item.output_dir,
            seg=seg,
            file_type="image",
            ocr_hash=ocr_hash,
            options=opts,
            input_path=item.input_path,
        )

        try:
            log(f"调用 API（图片），参数：{opt_desc}（READ_TIMEOUT_S={config.read_timeout_s}s）")
            t0 = time.time()
            result = _run_with_heartbeat(
                fn=lambda: client.layout_parsing(file_path=str(item.input_path), file_type=1),
                log=log,
                title="等待服务端响应（图片）",
            )
            seg.elapsed_s = time.time() - t0
            save_state(item.output_dir, state)

            pages: list[str] = []
            images: dict[str, str] = {}
            pruned_pages: list[dict[str, object]] = []
            for j, p in enumerate(result.pages or [], start=0):
                md_text, md_images = _namespace_page_markdown_and_images(
                    segment_id=seg.segment_id,
                    markdown_text=p.markdown_text,
                    markdown_images=p.markdown_images,
                )
                page_text = apply_markdown_image_width(md_text, config)
                pages.append(page_text)
                images.update(md_images)
                pruned_pages.append(
                    {
                        "pageNo": 1 + j,
                        "prunedResult": p.pruned_result or None,
                        "markdownImages": sorted([str(k) for k in (md_images or {}).keys()]),
                        "pageMarkdown": page_text,
                    }
                )

            if config.debug_dump_pages:
                ensure_output_dir(_pages_dir(item.output_dir))
                atomic_write_text(_page_md_path(item.output_dir, 1), pages[0] if pages else "")
            text = _render_pages_markdown(pages=pages, start_page=1, config=config) if pages else ""
            atomic_write_text(_segment_md_path(item.output_dir, seg), text)

            atomic_write_json(_segment_images_path(item.output_dir, seg), images)
            if config.debug_dump_pages:
                atomic_write_json(_page_images_path(item.output_dir, 1), images)

            # 为后续“碎片图片合并”落盘每页 prunedResult（若服务端未返回则为空）
            atomic_write_json(_segment_pruned_path(item.output_dir, seg), pruned_pages)

            if images:
                log(f"下载图片：{len(images)} 个")
                download_images(
                    config=config,
                    output_dir=item.output_dir,
                    state=state,
                    images=_prefix_images_to_parts(images),
                    max_retries=config.max_retries,
                    log=log,
                )

            seg.done = True
            seg.last_error = None
            save_state(item.output_dir, state)
            progress(0.9, "识别完成，开始合并与落盘图片")

            merge_and_materialize(config=config, output_dir=item.output_dir, state=state, log=log)
            progress(1.0, "输出完成")
            return
        except Exception as e:
            seg.last_error = str(e)
            save_state(item.output_dir, state)
            raise

    if ft == "pdf":
        segments = ensure_pdf_segments(state=state, pdf_path=item.input_path, output_dir=item.output_dir, chunk_pages=config.pdf_chunk_pages)
        save_state(item.output_dir, state)
        rerun_pages = _parse_page_spec(getattr(config, "pdf_image_ocr_pages", "") or "")
        rerun_segment_spec_raw = (getattr(config, "pdf_rerun_segments", "") or "").strip()
        rerun_segments: set[str] = set()
        if rerun_segment_spec_raw:
            try:
                rerun_segments = _parse_segment_spec(rerun_segment_spec_raw)
            except ValueError as e:
                raise RuntimeError(f"PDF_RERUN_SEGMENTS 输入非法：{e}")
            if rerun_pages:
                raise RuntimeError("冲突配置：PDF_RERUN_SEGMENTS 与 PDF_IMAGE_OCR_PAGES 不能同时填写，请仅保留一种重跑方式。")

        rerun_dpi = int(getattr(config, "pdf_image_ocr_dpi", 300) or 300)
        rerun_max_side = int(getattr(config, "pdf_image_ocr_max_side_px", 5000) or 5000)
        inferred_file_page_range = _infer_pdf_page_range_from_filename(item.input_path)
        done_count = sum(1 for s in segments if bool(s.done))
        if done_count > 0:
            log(f"检测到可续跑状态：{done_count}/{len(segments)} 分段已完成。")
        else:
            log("未检测到已完成分段：将从头开始处理（若预期续跑，请检查是否命中原输出目录内 task_state.json）。")
        if rerun_pages and inferred_file_page_range:
            s, e = inferred_file_page_range
            log(f"补漏页匹配：检测到文件名页段 {s}-{e}，支持按原始绝对页码命中。")
        if rerun_segments:
            target_desc = ",".join(sorted(rerun_segments))
            matched_segment_ids = [
                s.segment_id for s in segments if (_segment_code_from_segment_id(s.segment_id) or "") in rerun_segments
            ]
            if not matched_segment_ids:
                raise RuntimeError(f"未匹配到目标分段：{target_desc}")
            log(f"指定分段重打模式：目标分段={target_desc}")
            log(f"指定分段重打模式：命中分段={', '.join(matched_segment_ids)}")

        total = len(segments)
        for i, seg in enumerate(segments, start=1):
            _wait_if_paused(is_paused, is_canceled)
            if is_canceled():
                raise CanceledError()

            if rerun_segments:
                seg_code = _segment_code_from_segment_id(seg.segment_id)
                if (seg_code or "") not in rerun_segments:
                    if not seg.done and not (seg.last_error or "").strip():
                        if _segment_has_reusable_outputs(output_dir=item.output_dir, seg=seg):
                            seg.done = True
                            save_state(item.output_dir, state)
                            progress(
                                i / total,
                                f"分段重打模式：沿用历史产物并跳过非目标分段 {i}/{total}（{seg.start_page}-{seg.end_page}）",
                            )
                            continue
                    progress(i / total, f"分段重打模式：跳过非目标分段 {i}/{total}（{seg.start_page}-{seg.end_page}）")
                    continue
                log(f"分段重打模式：重跑目标分段 {i}/{total}（{seg.start_page}-{seg.end_page}）")
                if seg.done:
                    seg.done = False
                    seg.last_error = None
                    seg.elapsed_s = None
                    state.merged_md_done = False
                    save_state(item.output_dir, state)

            matched_rerun = False
            if rerun_pages:
                matched_rerun = _segment_matches_rerun_pages(
                    seg=seg,
                    rerun_pages=rerun_pages,
                    inferred_file_page_range=inferred_file_page_range,
                )
                # 补漏模式：仅处理命中分段；未命中的分段（无论 done 与否）都跳过。
                if not matched_rerun:
                    if not seg.done and not (seg.last_error or "").strip():
                        if _segment_has_reusable_outputs(output_dir=item.output_dir, seg=seg):
                            seg.done = True
                            save_state(item.output_dir, state)
                            progress(
                                i / total,
                                f"补漏模式：沿用历史产物并跳过未命中分段 {i}/{total}（{seg.start_page}-{seg.end_page}）",
                            )
                            continue
                    progress(i / total, f"补漏模式：跳过未命中分段 {i}/{total}（{seg.start_page}-{seg.end_page}）")
                    continue

            if seg.done:
                if rerun_pages:
                    log(f"检测到补漏页命中：将重跑分段 {i}/{total}（{seg.start_page}-{seg.end_page}），其余已完成分段保持跳过。")
                    seg.done = False
                    seg.last_error = None
                    seg.elapsed_s = None
                    state.merged_md_done = False
                    save_state(item.output_dir, state)
                elif not _is_ocr_hash_compatible(seg.ocr_options_hash, current_hash=ocr_hash, legacy_hash=ocr_hash_legacy):
                    log(f"检测到 OCR 参数变化：将重跑分段 {i}/{total}（{seg.start_page}-{seg.end_page}）。")
                    seg.done = False
                    seg.last_error = None
                    seg.elapsed_s = None
                    state.merged_md_done = False
                    save_state(item.output_dir, state)
                else:
                    # 允许“仅调整本地渲染/合并逻辑（例如 Markdown 图片尺寸）”后续跑：
                    # 不再调用 OCR，只对已落盘的分段 Markdown 做一次后处理（幂等），提升 Pandoc/EPUB 观感。
                    try:
                        md_path = _segment_md_path(item.output_dir, seg)
                        if md_path.exists():
                            old = md_path.read_text(encoding="utf-8")
                            new = apply_markdown_image_width(old, config)
                            if new != old:
                                atomic_write_text(md_path, new, encoding="utf-8")
                    except Exception:
                        pass
                    progress(i / total, f"跳过已完成分段 {i}/{total}（如需应用新的 OCR 参数，请删除输出目录内 task_state.json 后重跑）")
                    continue

            seg.attempts += 1
            seg.ocr_options_hash = ocr_hash
            save_state(item.output_dir, state)
            _debug_dump_request_options(
                config=config,
                output_dir=item.output_dir,
                seg=seg,
                file_type="pdf",
                ocr_hash=ocr_hash,
                options=opts,
                input_path=item.input_path,
            )

            part_p = Path(seg.part_path)
            part_abs = part_p if part_p.is_absolute() else (item.output_dir / seg.part_path)
            try:
                log(
                    f"调用 API（PDF 分段 {i}/{total}：{seg.start_page}-{seg.end_page}），参数：{opt_desc}（READ_TIMEOUT_S={config.read_timeout_s}s）"
                )
                t0 = time.time()
                result = _run_with_heartbeat(
                    fn=lambda: client.layout_parsing(file_path=str(part_abs), file_type=0),
                    log=log,
                    title=f"等待服务端响应（PDF 分段 {i}/{total}：{seg.start_page}-{seg.end_page}）",
                )
                seg.elapsed_s = time.time() - t0
                save_state(item.output_dir, state)
                # PDF 偶发漏字补救：对指定页本地渲染为图片后重跑（仅对命中的页增加额外请求）。
                if rerun_pages:
                    pages_list = list(result.pages or [])
                    for j in range(len(pages_list)):
                        local_page_no = int(seg.start_page) + j
                        absolute_page_no = _map_local_page_to_inferred_absolute_page(
                            local_page_no=local_page_no,
                            inferred_file_page_range=inferred_file_page_range,
                        )
                        matched_page_no: int | None = None
                        if local_page_no in rerun_pages:
                            matched_page_no = local_page_no
                        elif absolute_page_no is not None and absolute_page_no in rerun_pages:
                            matched_page_no = absolute_page_no
                        if matched_page_no is None:
                            continue
                        img_path = item.output_dir / "_parts" / f"{seg.segment_id}_page_{local_page_no:04d}_rerun.png"
                        ok = _render_pdf_page_to_png(
                            pdf_path=part_abs,
                            page_index=j,
                            dpi=rerun_dpi,
                            max_side_px=rerun_max_side,
                            out_path=img_path,
                        )
                        if not ok:
                            if absolute_page_no is not None and matched_page_no == absolute_page_no and local_page_no != matched_page_no:
                                log(
                                    f"补漏失败：无法渲染 PDF 第 {matched_page_no} 页为图片"
                                    f"（当前文件内页码 {local_page_no}，可能缺少 QtPdf 组件）"
                                )
                            else:
                                log(f"补漏失败：无法渲染 PDF 第 {matched_page_no} 页为图片（可能缺少 QtPdf 组件）")
                            continue
                        _wait_if_paused(is_paused, is_canceled)
                        if is_canceled():
                            raise CanceledError()
                        if absolute_page_no is not None and matched_page_no == absolute_page_no and local_page_no != matched_page_no:
                            log(f"补漏：重跑第 {matched_page_no} 页（当前文件内页码 {local_page_no}，图片模式，DPI={rerun_dpi}）")
                        else:
                            log(f"补漏：重跑第 {matched_page_no} 页（图片模式，DPI={rerun_dpi}）")
                        img_result = _run_with_heartbeat(
                            fn=lambda p=str(img_path): client.layout_parsing(file_path=p, file_type=1),
                            log=log,
                            title=f"等待服务端响应（补漏：第 {matched_page_no} 页）",
                        )
                        if not img_result.pages:
                            log(f"补漏失败：第 {matched_page_no} 页返回空结果")
                            continue
                        pages_list[j] = img_result.pages[0]
                    result = type(result)(pages=pages_list)
                # prunedResult 主要来自 layout-parsing；restructure-pages 可能不回传该字段
                pages_for_pruned = list(result.pages or [])
                if config.concatenate_pages:
                    log("调用 API（restructure-pages），参数：concatenatePages=true")
                    result = client.restructure_pages(pages=result.pages)

                pages_text: list[str] = []
                images: dict[str, str] = {}
                pruned_pages: list[dict[str, object]] = []
                for j, p in enumerate(result.pages, start=0):
                    page_no = seg.start_page + j
                    md_text, md_images = _namespace_page_markdown_and_images(
                        segment_id=seg.segment_id,
                        markdown_text=p.markdown_text,
                        markdown_images=p.markdown_images,
                    )
                    page_text = apply_markdown_image_width(md_text, config)
                    pages_text.append(page_text)
                    images.update(md_images)
                    if config.debug_dump_pages:
                        ensure_output_dir(_pages_dir(item.output_dir))
                        atomic_write_text(_page_md_path(item.output_dir, page_no), page_text)
                        atomic_write_json(_page_images_path(item.output_dir, page_no), md_images or {})

                    pruned = p.pruned_result or None
                    if pruned is None and j < len(pages_for_pruned):
                        pruned = pages_for_pruned[j].pruned_result or None
                    pruned_pages.append(
                        {
                            "pageNo": int(page_no),
                            "prunedResult": pruned,
                            "markdownImages": sorted([str(k) for k in (md_images or {}).keys()]),
                            "pageMarkdown": page_text,
                        }
                    )

                text = _render_pages_markdown(pages=pages_text, start_page=seg.start_page, config=config) if pages_text else ""
                atomic_write_text(_segment_md_path(item.output_dir, seg), text)
                atomic_write_json(_segment_images_path(item.output_dir, seg), images)
                atomic_write_json(_segment_pruned_path(item.output_dir, seg), pruned_pages)

                if images:
                    log(f"下载图片：{len(images)} 个")
                    download_images(
                        config=config,
                        output_dir=item.output_dir,
                        state=state,
                        images=_prefix_images_to_parts(images),
                        max_retries=config.max_retries,
                        log=log,
                    )

                seg.done = True
                seg.last_error = None
                save_state(item.output_dir, state)
                progress(i / total, f"分段完成 {i}/{total}（待合并/落盘图片）")
            except Exception as e:
                seg.done = False
                seg.last_error = str(e)
                save_state(item.output_dir, state)
                try:
                    _write_failed_segment_placeholder(output_dir=item.output_dir, seg=seg, config=config)
                except Exception:
                    pass
                progress(i / total, f"分段失败 {i}/{total}：{seg.last_error}")
                continue

        any_failed = any(not s.done for s in segments)
        if any_failed:
            # 产出 best-effort 的 merged_result.md（失败分段会有占位块），方便立刻拿到可读输出并定位缺页
            merge_best_effort(config=config, output_dir=item.output_dir, state=state, log=log)
            failed = [s for s in segments if not s.done]
            parts: list[str] = []
            for s in failed:
                err = (s.last_error or "").strip() or "unknown"
                parts.append(f"{s.segment_id}: {err}")
            detail = "; ".join(parts)
            if len(detail) > 800:
                detail = detail[:800] + "…"
            raise RuntimeError(f"存在失败分段（可稍后重试，已生成 merged_result.md 供定位）：{detail}")

        progress(0.9, "分段全部完成，开始合并与落盘图片")
        merge_and_materialize(config=config, output_dir=item.output_dir, state=state, log=log)
        progress(1.0, "输出完成")
        return

    raise RuntimeError("未知文件类型")


def ensure_output_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
