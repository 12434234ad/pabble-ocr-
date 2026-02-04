from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from pabble_ocr.config import AppConfig
from pabble_ocr.md.image_fragments import merge_image_fragments_for_page
from pabble_ocr.md.postprocess import apply_markdown_image_width
from pabble_ocr.utils.paths import resolve_path_maybe_windows


_PAGE_MARKER_RE = re.compile(r"(?=<!--\s*page\s*:\s*\d+\s*-->)", flags=re.IGNORECASE)
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_SEG_RANGE_RE = re.compile(r"_p(?P<start>\d{4})-(?P<end>\d{4})", flags=re.IGNORECASE)


def _split_pages(text: str) -> list[str]:
    t = text or ""
    if "<!--" not in t:
        return [t]
    parts = [p for p in _PAGE_MARKER_RE.split(t) if p]
    return parts if parts else [t]


def _page_no_of(text: str, default_no: int) -> int:
    m = re.search(r"<!--\s*page\s*:\s*(\d+)\s*-->", text, flags=re.IGNORECASE)
    if not m:
        return default_no
    try:
        return int(m.group(1))
    except Exception:
        return default_no


def _extract_images_from_page(text: str) -> list[str]:
    return [p.strip().replace("\\", "/") for p in _MD_IMAGE_RE.findall(text or "") if (p or "").strip()]

def _find_task_state(start_dir: Path) -> Path | None:
    cur = start_dir.resolve()
    for _ in range(6):
        cand = cur / "task_state.json"
        if cand.exists():
            return cand
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def _infer_pdf_from_task_state(base_dir: Path) -> Path | None:
    state_path = _find_task_state(base_dir)
    if state_path is None:
        return None
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    input_path = raw.get("input_path")
    if not isinstance(input_path, str) or not input_path.strip():
        return None
    pdf = resolve_path_maybe_windows(input_path.strip(), base_dir=state_path.parent)
    return pdf if pdf.exists() else None


def _infer_segment_start_from_md_name(md_path: Path) -> int | None:
    m = _SEG_RANGE_RE.search(md_path.name)
    if not m:
        return None
    try:
        return int(m.group("start"))
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="对已有 Markdown 做图片碎片合并（观感优先）")
    parser.add_argument("md", type=str, help="Markdown 路径（需能访问其引用的图片文件）")
    parser.add_argument(
        "--base-dir",
        type=str,
        default="",
        help="图片相对路径的基准目录（默认：md 所在目录）",
    )
    parser.add_argument("--pdf", type=str, default="", help="可选：用于裁剪的 PDF 路径（不填则尝试从 task_state.json 推断）")
    parser.add_argument("--inplace", action="store_true", help="原地覆盖 md（否则输出到 <md>.merged.md）")
    parser.add_argument("--width", type=int, default=0, help="MD_IMAGE_WIDTH_PERCENT（0=不处理）")
    parser.add_argument("--max-height", type=int, default=0, help="MD_IMAGE_MAX_HEIGHT_PX（0=不限制）")
    args = parser.parse_args(argv)

    md_path = Path(args.md)
    if not md_path.exists():
        raise SystemExit(f"md 不存在：{md_path}")

    base_dir = Path(args.base_dir).resolve() if (args.base_dir or "").strip() else md_path.parent.resolve()
    if not base_dir.exists():
        raise SystemExit(f"base-dir 不存在：{base_dir}")

    cfg = AppConfig(
        merge_image_fragments=True,
        markdown_image_width_percent=int(args.width or 0),
        markdown_image_max_height_px=int(args.max_height or 0),
    )

    pdf_path: Path | None = None
    if (args.pdf or "").strip():
        pdf_path = resolve_path_maybe_windows(args.pdf.strip(), base_dir=base_dir)
        if not pdf_path.exists():
            raise SystemExit(f"pdf 不存在：{pdf_path}")
    else:
        pdf_path = _infer_pdf_from_task_state(base_dir)

    seg_start = _infer_segment_start_from_md_name(md_path)

    original = md_path.read_text(encoding="utf-8", errors="ignore")
    pages = _split_pages(original)

    changed = False
    for i in range(len(pages)):
        page_no = _page_no_of(pages[i], i + 1)
        imgs = _extract_images_from_page(pages[i])
        before = pages[i]
        pdf_page_index = None
        if pdf_path is not None:
            if seg_start is not None:
                pdf_page_index = page_no - seg_start
            else:
                pdf_page_index = i
        after = merge_image_fragments_for_page(
            config=cfg,
            output_dir=base_dir,
            page_markdown=before,
            pruned_result=None,
            markdown_images=imgs,
            page_no=page_no,
            pdf_path=pdf_path,
            pdf_page_index=pdf_page_index,
        )
        after = apply_markdown_image_width(after, cfg)
        if after != before:
            pages[i] = after
            changed = True

    out_text = "".join(pages)
    out_path = md_path if bool(args.inplace) else md_path.with_suffix(md_path.suffix + ".merged.md")
    out_path.write_text(out_text, encoding="utf-8")

    if not changed:
        print("未检测到可合并的碎片图片（或缺少图片文件），输出未变化。")
    else:
        print(f"已输出：{out_path}")
        print(f"合并图目录：{(base_dir / 'images' / 'merged')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
