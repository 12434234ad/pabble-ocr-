from __future__ import annotations

import json
import re
from pathlib import Path

from pabble_ocr.config import AppConfig
from pabble_ocr.core.models import FileTaskState, SegmentState
from pabble_ocr.core.state_store import save_state
from pabble_ocr.md.image_fragments import merge_image_fragments_for_page
from pabble_ocr.md.images import download_images
from pabble_ocr.md.postprocess import apply_markdown_image_width
from pabble_ocr.utils.paths import resolve_path_maybe_windows
from pabble_ocr.utils.io import atomic_write_text


def _segment_md_path(output_dir: Path, seg: SegmentState) -> Path:
    return output_dir / "_parts" / f"{seg.segment_id}.md"


def _segment_images_path(output_dir: Path, seg: SegmentState) -> Path:
    return output_dir / "_parts" / f"{seg.segment_id}_images.json"


def _segment_pruned_path(output_dir: Path, seg: SegmentState) -> Path:
    return output_dir / "_parts" / f"{seg.segment_id}_pruned.json"


_PAGE_MARKER_RE = re.compile(r"(?=<!--\s*page\s*:\s*\d+\s*-->)", flags=re.IGNORECASE)


def _render_pages_markdown(*, pages: list[str], start_page: int, config: AppConfig) -> str:
    """
    与 processing/process_file.py 的渲染行为保持一致：
    - insert_page_numbers=true：每页前插入 marker + 可见页码行
    - 否则：按 page_separator 拼接；若 page_separator 为空则用空行分隔
    """
    blocks: list[str] = []
    for idx, text in enumerate(pages):
        if config.insert_page_numbers:
            page_no = start_page + idx
            blocks.append(f"<!-- page:{page_no} -->\n\n**第 {page_no} 页**\n\n{text}".rstrip())
        else:
            blocks.append((text or "").rstrip())
    sep = config.page_separator or ""
    if not sep:
        return "\n\n".join([b for b in blocks if b])
    return sep.join([b for b in blocks if b])


def _split_segment_pages(*, text: str, config: AppConfig) -> list[str]:
    t = text or ""
    # 优先按 `<!-- page:N -->` 拆页：这是本项目写入的“机器可读”标记，
    # 即使用户后续更改了 PAGE_SEPARATOR，也应能对已落盘 md 正确拆页。
    if "<!--" in t and "page:" in t:
        chunks = [c for c in _PAGE_MARKER_RE.split(t) if c]
        return chunks if len(chunks) > 1 else [t]

    sep = config.page_separator or ""
    if sep:
        return t.split(sep)
    return [t]


def _join_segment_pages(*, pages: list[str], config: AppConfig) -> str:
    # marker 拆分场景下直接拼接即可（每页块本身带 marker）
    if pages and pages[0].lstrip().lower().startswith("<!-- page:"):
        return "".join(pages)
    sep = config.page_separator or ""
    return sep.join(pages) if sep else "".join(pages)


def _apply_image_fragment_merge_for_segment(*, config: AppConfig, output_dir: Path, seg: SegmentState, text: str) -> str:
    if not bool(getattr(config, "merge_image_fragments", True)):
        return text or ""
    meta_path = _segment_pruned_path(output_dir, seg)
    if not meta_path.exists():
        return text or ""
    try:
        pages_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return text or ""
    if not isinstance(pages_meta, list) or not pages_meta:
        return text or ""

    # 优先使用 pruned.json 中落盘的每页 Markdown 快照（pageMarkdown）。
    # 这样即使 segment md 已被重写（例如已做过碎片合并），仍可基于“原始碎片引用”重复重跑合并逻辑。
    pages_from_meta: list[str] | None = None
    if all(isinstance(m, dict) and "pageMarkdown" in m for m in pages_meta):
        pages_from_meta = [str((m or {}).get("pageMarkdown") or "") for m in pages_meta if isinstance(m, dict)]

    pages = pages_from_meta if pages_from_meta is not None else _split_segment_pages(text=text or "", config=config)
    # pages_meta 的页数以服务端返回为准，避免因 page_separator 配置导致误分割
    if len(pages) < len(pages_meta):
        return text or ""

    changed = False
    # 优先使用该分段自身的 PDF（最接近原 PDF 效果）
    pdf_path = resolve_path_maybe_windows(seg.part_path, base_dir=output_dir)
    for i, meta in enumerate(pages_meta):
        if i >= len(pages):
            break
        if not isinstance(meta, dict):
            continue
        pruned = meta.get("prunedResult")
        if pruned is not None and not isinstance(pruned, dict):
            pruned = None
        imgs = meta.get("markdownImages") or []
        if not isinstance(imgs, list):
            imgs = []
        try:
            page_no = int(meta.get("pageNo") or 0)
        except Exception:
            page_no = 0
        before = pages[i]
        after = merge_image_fragments_for_page(
            config=config,
            output_dir=output_dir,
            page_markdown=before,
            pruned_result=pruned,
            markdown_images=[str(x) for x in imgs if isinstance(x, (str, int, float))],
            page_no=page_no if page_no > 0 else (i + 1),
            pdf_path=pdf_path if pdf_path.exists() else None,
            pdf_page_index=i,
        )
        if after != before:
            pages[i] = after
            changed = True

    if not changed:
        return text or ""

    # 若使用 meta 快照重建页面，则需要按当前配置重新拼接成 segment md（含页码/分隔符规则）。
    if pages_from_meta is not None:
        return _render_pages_markdown(pages=pages, start_page=int(seg.start_page), config=config)
    return _join_segment_pages(pages=pages, config=config)

def _failed_segment_placeholder(*, seg: SegmentState, config: AppConfig) -> str:
    start = int(seg.start_page)
    end = int(seg.end_page)
    title = f"**第 {start} 页（识别失败占位）**" if config.insert_page_numbers else "**识别失败占位**"
    err = (seg.last_error or "").strip() or "unknown"
    lines = []
    if config.insert_page_numbers:
        lines.append(f"<!-- page:{start} -->")
        lines.append("")
    lines.extend(
        [
            title,
            "",
            f"> 分段：`{seg.segment_id}`（页范围 {start}-{end}）",
            f"> 错误：{err}",
            "",
            "> 建议：降低 `PDF_CHUNK_PAGES` 或提高 `READ_TIMEOUT_S` 后重试；该分段成功后会自动覆盖本占位内容。",
            "",
        ]
    )
    return "\n".join(lines).rstrip()

def merge_best_effort(
    *,
    config: AppConfig,
    output_dir: Path,
    state: FileTaskState,
    log: callable,
) -> Path:
    """
    生成 best-effort 的 merged_result.md：
    - 分段成功：拼接真实 Markdown
    - 分段失败/缺失：拼接占位块，保证用户仍能拿到可读输出并定位缺页
    """
    if not state.segments:
        raise RuntimeError("未发现分段结果")

    combined_images: dict[str, str] = {}

    for seg in state.segments:
        img_path = _segment_images_path(output_dir, seg)
        if not img_path.exists():
            continue
        try:
            combined_images.update(json.loads(img_path.read_text(encoding="utf-8")))
        except Exception:
            pass

    if combined_images:
        log(f"下载图片：{len(combined_images)} 个")
        download_images(
            config=config,
            output_dir=output_dir,
            state=state,
            images=combined_images,
            max_retries=config.max_retries,
            log=log,
        )

    parts: list[str] = []
    for seg in state.segments:
        md_path = _segment_md_path(output_dir, seg)
        if md_path.exists():
            raw = md_path.read_text(encoding="utf-8")
            merged = _apply_image_fragment_merge_for_segment(config=config, output_dir=output_dir, seg=seg, text=raw)
            styled = apply_markdown_image_width(merged, config)
            if styled != raw:
                atomic_write_text(md_path, styled, encoding="utf-8")
            parts.append(styled)
        else:
            parts.append(_failed_segment_placeholder(seg=seg, config=config))

    merged = config.page_separator.join([p for p in parts if p is not None])
    out_path = output_dir / "merged_result.md"
    atomic_write_text(out_path, merged, encoding="utf-8")

    state.merged_md_done = all(s.done for s in state.segments)
    save_state(output_dir, state)
    return out_path


def merge_and_materialize(
    *,
    config: AppConfig,
    output_dir: Path,
    state: FileTaskState,
    log: callable,
) -> Path:
    if not state.segments:
        raise RuntimeError("未发现分段结果")

    if any(not s.done for s in state.segments):
        raise RuntimeError("存在未完成分段，暂不合并")

    combined_images: dict[str, str] = {}

    for seg in state.segments:
        img_path = _segment_images_path(output_dir, seg)
        if not img_path.exists():
            continue
        try:
            combined_images.update(json.loads(img_path.read_text(encoding="utf-8")))
        except Exception:
            pass

    if combined_images:
        log(f"下载图片：{len(combined_images)} 个")
        download_images(
            config=config,
            output_dir=output_dir,
            state=state,
            images=combined_images,
            max_retries=config.max_retries,
            log=log,
        )

    parts: list[str] = []
    for seg in state.segments:
        md_path = _segment_md_path(output_dir, seg)
        if not md_path.exists():
            parts.append("")
            continue
        raw = md_path.read_text(encoding="utf-8")
        merged = _apply_image_fragment_merge_for_segment(config=config, output_dir=output_dir, seg=seg, text=raw)
        styled = apply_markdown_image_width(merged, config)
        if styled != raw:
            atomic_write_text(md_path, styled, encoding="utf-8")
        parts.append(styled)

    merged = config.page_separator.join([p for p in parts if p is not None])
    out_path = output_dir / "merged_result.md"
    atomic_write_text(out_path, merged, encoding="utf-8")

    state.merged_md_done = True
    save_state(output_dir, state)
    return out_path
