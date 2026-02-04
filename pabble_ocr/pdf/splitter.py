from __future__ import annotations

import logging
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from pabble_ocr.core.models import FileTaskState, SegmentState


logger = logging.getLogger(__name__)

def _pdf_total_pages(pdf_path: Path) -> int:
    reader = PdfReader(str(pdf_path))
    total = len(reader.pages)
    if total <= 0:
        raise RuntimeError("PDF 页数为 0")
    return total


def _is_full_pdf_segment(segments: list[SegmentState], pdf_path: Path) -> bool:
    if len(segments) != 1:
        return False
    seg = segments[0]
    if not seg.segment_id.startswith("pdf_full_"):
        return False
    # part_path 可能是绝对路径或相对路径；尽量用“指向原始 PDF”来判断
    try:
        return Path(seg.part_path).resolve() == pdf_path.resolve()
    except Exception:
        return str(seg.part_path) == str(pdf_path)


def ensure_pdf_segments(*, state: FileTaskState, pdf_path: Path, output_dir: Path, chunk_pages: int) -> list[SegmentState]:
    # 允许在“原本未切分（pdf_full_）且尚未完成”的情况下，通过调小 chunk_pages 重新切分，
    # 以降低单次请求耗时与 ReadTimeout 风险。该行为不会影响已完成分段（此分支仅在无已完成分段时触发）。
    if state.segments:
        if not any(s.done for s in state.segments):
            chunk_pages_n = max(1, int(chunk_pages))
            try:
                total = _pdf_total_pages(pdf_path)
            except Exception:
                total = 0
            if total > 0 and total > chunk_pages_n and _is_full_pdf_segment(state.segments, pdf_path):
                logger.info("resegment pdf_full -> parts: total=%s, chunk_pages=%s", total, chunk_pages_n)
                state.segments = []
            else:
                return state.segments
        else:
            return state.segments

    parts_dir = output_dir / "_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)

    reader = PdfReader(str(pdf_path))
    total = len(reader.pages)
    if total <= 0:
        raise RuntimeError("PDF 页数为 0")

    chunk_pages = max(1, int(chunk_pages))
    if total <= chunk_pages:
        seg_id = f"pdf_full_p0001-{total:04d}"
        state.segments = [
            SegmentState(
                segment_id=seg_id,
                start_page=1,
                end_page=total,
                part_path=str(pdf_path),
            )
        ]
        return state.segments

    segments: list[SegmentState] = []
    idx = 0
    for start0 in range(0, total, chunk_pages):
        idx += 1
        end0 = min(total - 1, start0 + chunk_pages - 1)
        start_page = start0 + 1
        end_page = end0 + 1

        seg_id = f"part_{idx:03d}_p{start_page:04d}-{end_page:04d}"
        part_path = parts_dir / f"{seg_id}.pdf"
        if not part_path.exists():
            writer = PdfWriter()
            for i in range(start0, end0 + 1):
                writer.add_page(reader.pages[i])
            with open(part_path, "wb") as f:
                writer.write(f)

        segments.append(
            SegmentState(
                segment_id=seg_id,
                start_page=start_page,
                end_page=end_page,
                part_path=str(part_path.relative_to(output_dir)),
            )
        )

    state.segments = segments
    return segments
