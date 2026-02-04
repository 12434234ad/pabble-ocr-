from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from pabble_ocr.config import AppConfig


@dataclass(frozen=True)
class ImageRegion:
    src: str
    bbox: tuple[float, float, float, float]  # x0,y0,x1,y1 (same unit as prunedResult)


_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)(?:\s*\{[^}]*\})?", flags=re.MULTILINE)
_HTML_IMG_RE = re.compile(
    r"<img\s+[^>]*?src=(?P<q>['\"])(?P<src>[^'\"]+)(?P=q)[^>]*/?>",
    flags=re.IGNORECASE | re.DOTALL,
)

# 常见服务端输出命名：img_in_image_box_{x0}_{y0}_{x1}_{y1}.jpg
_BBOX_IN_NAME_RE = re.compile(
    r"(?:^|/)(?:img_in_image_box)_(?P<a>\d+(?:\.\d+)?)_(?P<b>\d+(?:\.\d+)?)_(?P<c>\d+(?:\.\d+)?)_(?P<d>\d+(?:\.\d+)?)\.(?:png|jpg|jpeg|webp)$",
    flags=re.IGNORECASE,
)


def _normalize_src(value: str) -> str:
    v = (value or "").strip()
    if v.startswith("<") and v.endswith(">") and len(v) >= 2:
        v = v[1:-1].strip()
    v = v.replace("\\", "/")
    return v


def _src_for_markdown(src: str) -> str:
    s = (src or "").strip()
    if not s:
        return s
    # 与 apply_markdown_image_width 的处理保持一致：若含空白则用 <> 包裹
    return f"<{s}>" if any(ch.isspace() for ch in s) else s


def _iter_image_refs(markdown: str) -> Iterable[tuple[int, int, str, str, str]]:
    """
    迭代 Markdown/HTML 中的图片引用。
    返回：(start, end, kind, alt, src)
    - kind: "md" | "html"
    """
    text = markdown or ""
    for m in _MD_IMAGE_RE.finditer(text):
        alt = m.group(1) or ""
        src_raw = m.group(2) or ""
        yield (m.start(), m.end(), "md", alt, _normalize_src(src_raw))
    for m in _HTML_IMG_RE.finditer(text):
        yield (m.start(), m.end(), "html", "", _normalize_src(m.group("src") or ""))

def _html_imgs_to_markdown(text: str) -> str:
    if not text:
        return ""

    def _repl(m: re.Match[str]) -> str:
        src = _normalize_src(m.group("src") or "")
        return f"![]({_src_for_markdown(src)})"

    return _HTML_IMG_RE.sub(_repl, text)


def _bbox_from_any(value: Any) -> Optional[tuple[float, float, float, float]]:
    """
    尝试从常见形状里抽取 bbox（x0,y0,x1,y1）。
    支持：
    - [x0,y0,x1,y1]
    - [x0,y0,w,h]
    - {"x":..,"y":..,"w":..,"h":..} / {"left":..,"top":..,"right":..,"bottom":..}
    - points/poly/quad：[(x,y),...] 或 [x1,y1,x2,y2,...]
    """
    if value is None:
        return None

    def _to_f(v: Any) -> Optional[float]:
        try:
            if isinstance(v, bool):
                return None
            return float(v)
        except Exception:
            return None

    # dict 形式
    if isinstance(value, dict):
        if {"left", "top", "right", "bottom"}.issubset(value.keys()):
            x0 = _to_f(value.get("left"))
            y0 = _to_f(value.get("top"))
            x1 = _to_f(value.get("right"))
            y1 = _to_f(value.get("bottom"))
            if None not in (x0, y0, x1, y1):
                return (x0, y0, x1, y1)
        if {"x", "y", "w", "h"}.issubset(value.keys()):
            x = _to_f(value.get("x"))
            y = _to_f(value.get("y"))
            w = _to_f(value.get("w"))
            h = _to_f(value.get("h"))
            if None not in (x, y, w, h):
                return (x, y, x + w, y + h)
        if {"x", "y", "width", "height"}.issubset(value.keys()):
            x = _to_f(value.get("x"))
            y = _to_f(value.get("y"))
            w = _to_f(value.get("width"))
            h = _to_f(value.get("height"))
            if None not in (x, y, w, h):
                return (x, y, x + w, y + h)
        # 递归找 points
        for k in ("bbox", "box", "rect", "points", "quad", "poly", "polygon"):
            if k in value:
                b = _bbox_from_any(value.get(k))
                if b is not None:
                    return b
        return None

    # list/tuple 形式
    if isinstance(value, (list, tuple)):
        arr = list(value)
        # [(x,y), ...]
        if arr and all(isinstance(p, (list, tuple)) and len(p) >= 2 for p in arr):
            xs: list[float] = []
            ys: list[float] = []
            for p in arr:
                x = _to_f(p[0])
                y = _to_f(p[1])
                if x is None or y is None:
                    return None
                xs.append(x)
                ys.append(y)
            return (min(xs), min(ys), max(xs), max(ys))

        nums = [_to_f(v) for v in arr]
        if any(v is None for v in nums):
            return None
        n = [float(v) for v in nums if v is not None]
        if len(n) == 4:
            x0, y0, x1, y1 = n
            # 兼容 [x,y,w,h]
            if x1 >= 0 and y1 >= 0 and (x1 < x0 or y1 < y0):
                return None
            if x1 >= x0 and y1 >= y0:
                return (x0, y0, x1, y1)
            # 若疑似 w/h（极端情况）：x1<x0 或 y1<y0
            return (x0, y0, x0 + abs(x1), y0 + abs(y1))
        if len(n) >= 6 and len(n) % 2 == 0:
            xs = n[0::2]
            ys = n[1::2]
            return (min(xs), min(ys), max(xs), max(ys))
    return None


def _extract_regions_from_pruned_result(pruned_result: dict[str, Any], known_srcs: set[str]) -> list[ImageRegion]:
    regions: list[ImageRegion] = []
    if not pruned_result or not known_srcs:
        return regions

    def _match_src(raw: str) -> Optional[str]:
        v = _normalize_src(raw)
        if v in known_srcs:
            return v
        # 有些服务端会在 prunedResult 里给绝对/相对混用，这里尽量做尾缀匹配
        for k in known_srcs:
            if v.endswith(k):
                return k
        return None

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            matched: list[str] = []
            for v in node.values():
                if isinstance(v, str):
                    m = _match_src(v)
                    if m:
                        matched.append(m)

            bbox: Optional[tuple[float, float, float, float]] = None
            if matched:
                # 尝试在同级 dict 中找 bbox
                for key in ("bbox", "box", "rect", "points", "quad", "poly", "polygon"):
                    if key in node:
                        bbox = _bbox_from_any(node.get(key))
                        if bbox is not None:
                            break
                if bbox is None:
                    # 兜底：整层 dict 递归找
                    bbox = _bbox_from_any(node)

            if matched and bbox is not None:
                x0, y0, x1, y1 = bbox
                if x1 > x0 and y1 > y0:
                    for src in matched:
                        regions.append(ImageRegion(src=src, bbox=(float(x0), float(y0), float(x1), float(y1))))

            for v in node.values():
                _walk(v)
            return
        if isinstance(node, list):
            for v in node:
                _walk(v)

    _walk(pruned_result)

    # 去重（同一 src 可能被多处引用）
    seen = set()
    uniq: list[ImageRegion] = []
    for r in regions:
        if r.src in seen:
            continue
        seen.add(r.src)
        uniq.append(r)
    return uniq


def _extract_regions_from_bbox_in_name(known_srcs: set[str]) -> list[ImageRegion]:
    regions: list[ImageRegion] = []
    for src in sorted(known_srcs):
        m = _BBOX_IN_NAME_RE.search(_normalize_src(src))
        if not m:
            continue
        a = float(m.group("a"))
        b = float(m.group("b"))
        c = float(m.group("c"))
        d = float(m.group("d"))
        # 约定：多数为 x0,y0,x1,y1；兜底支持 x,y,w,h
        x0, y0, x1, y1 = a, b, c, d
        if x1 < x0 or y1 < y0:
            x0, y0, x1, y1 = a, b, a + abs(c), b + abs(d)
        if x1 > x0 and y1 > y0:
            regions.append(ImageRegion(src=src, bbox=(x0, y0, x1, y1)))
    return regions


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0 = max(ax0, bx0)
    iy0 = max(ay0, by0)
    ix1 = min(ax1, bx1)
    iy1 = min(ay1, by1)
    iw = max(0.0, ix1 - ix0)
    ih = max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, (ax1 - ax0)) * max(0.0, (ay1 - ay0))
    area_b = max(0.0, (bx1 - bx0)) * max(0.0, (by1 - by0))
    denom = area_a + area_b - inter
    return float(inter / denom) if denom > 0 else 0.0


def _overlap_ratio_1d(a0: float, a1: float, b0: float, b1: float) -> float:
    inter = max(0.0, min(a1, b1) - max(a0, b0))
    denom = max(1e-9, min(a1 - a0, b1 - b0))
    return float(inter / denom)


def _union_over_sum(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ux0 = min(ax0, bx0)
    uy0 = min(ay0, by0)
    ux1 = max(ax1, bx1)
    uy1 = max(ay1, by1)
    union = max(0.0, (ux1 - ux0)) * max(0.0, (uy1 - uy0))
    area_a = max(0.0, (ax1 - ax0)) * max(0.0, (ay1 - ay0))
    area_b = max(0.0, (bx1 - bx0)) * max(0.0, (by1 - by0))
    denom = area_a + area_b
    return float(union / denom) if denom > 0 else 999.0


def _group_regions(regions: list[ImageRegion]) -> list[list[ImageRegion]]:
    if len(regions) < 2:
        return []

    xs = [r.bbox[0] for r in regions] + [r.bbox[2] for r in regions]
    ys = [r.bbox[1] for r in regions] + [r.bbox[3] for r in regions]
    span_x = max(xs) - min(xs)
    span_y = max(ys) - min(ys)
    span = max(span_x, span_y, 1e-6)

    # 经验阈值：适配 0~1 归一化坐标与像素坐标
    # 观感优先：允许更大的间隙（很多 PDF 图是多宫格/多子图，bbox 之间间隙会明显大于 2%）
    widths = [max(1e-6, r.bbox[2] - r.bbox[0]) for r in regions]
    heights = [max(1e-6, r.bbox[3] - r.bbox[1]) for r in regions]
    med_w = _median(widths)
    med_h = _median(heights)
    # 注意：这里宁可“偏激进合并”，也不要把同一图的上下/左右子图拆开（观感会明显变差）。
    # 同时保留 union_over_sum 的兜底约束，避免把相距很远的图片误合并。
    gap_thresh = max(span * 0.08, med_w * 0.40, med_h * 0.40)
    min_overlap = 0.22
    max_union_over_sum = 2.20
    max_union_over_sum_center = 2.60
    center_align_x = max(span * 0.03, med_w * 0.35)
    center_align_y = max(span * 0.03, med_h * 0.35)

    parent = list(range(len(regions)))

    def _find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def _union(i: int, j: int) -> None:
        ri = _find(i)
        rj = _find(j)
        if ri != rj:
            parent[rj] = ri

    for i in range(len(regions)):
        a = regions[i].bbox
        for j in range(i + 1, len(regions)):
            b = regions[j].bbox
            if _iou(a, b) > 0.02:
                _union(i, j)
                continue
            ax0, ay0, ax1, ay1 = a
            bx0, by0, bx1, by1 = b
            v_overlap = _overlap_ratio_1d(ay0, ay1, by0, by1)
            h_overlap = _overlap_ratio_1d(ax0, ax1, bx0, bx1)
            h_gap = max(0.0, max(bx0 - ax1, ax0 - bx1))
            v_gap = max(0.0, max(by0 - ay1, ay0 - by1))

            # 1) 明确同排/同列：优先合并（不让 union_over_sum 过早剪枝，避免“同图被拆成上下两段”）
            if v_overlap >= min_overlap and h_gap <= gap_thresh:
                _union(i, j)
                continue
            if h_overlap >= min_overlap and v_gap <= gap_thresh:
                _union(i, j)
                continue

            # 2) 尺寸差异/裁剪偏移时 overlap 可能很低：用“中心对齐 + 距离”兜底
            cx_a = (ax0 + ax1) / 2.0
            cy_a = (ay0 + ay1) / 2.0
            cx_b = (bx0 + bx1) / 2.0
            cy_b = (by0 + by1) / 2.0
            if v_gap <= gap_thresh * 1.5 and abs(cx_a - cx_b) <= center_align_x:
                if _union_over_sum(a, b) <= max_union_over_sum_center:
                    _union(i, j)
                    continue
            if h_gap <= gap_thresh * 1.5 and abs(cy_a - cy_b) <= center_align_y:
                if _union_over_sum(a, b) <= max_union_over_sum_center:
                    _union(i, j)
                    continue

            # 3) 最后再用 union_over_sum 作为安全阀：相距过远/对角线偏移的直接跳过
            if _union_over_sum(a, b) > max_union_over_sum:
                continue

    groups: dict[int, list[ImageRegion]] = {}
    for i, r in enumerate(regions):
        groups.setdefault(_find(i), []).append(r)

    # 仅返回真实“碎片”（>=2）
    return [g for g in groups.values() if len(g) >= 2]


def _bbox_union(regions: list[ImageRegion]) -> tuple[float, float, float, float]:
    x0 = min(r.bbox[0] for r in regions)
    y0 = min(r.bbox[1] for r in regions)
    x1 = max(r.bbox[2] for r in regions)
    y1 = max(r.bbox[3] for r in regions)
    return (x0, y0, x1, y1)


def _median(values: list[float]) -> float:
    if not values:
        return 1.0
    s = sorted(values)
    mid = len(s) // 2
    return float(s[mid]) if len(s) % 2 == 1 else float((s[mid - 1] + s[mid]) / 2.0)


def _compose_merged_image(*, output_dir: Path, merged_rel: str, regions: list[ImageRegion]) -> bool:
    """
    使用 Qt(QImage/QPainter) 将多个碎片图按 bbox 拼回一张。
    返回 True 表示生成成功。
    """
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage, QPainter
    except Exception:
        return False

    union = _bbox_union(regions)
    ux0, uy0, ux1, uy1 = union
    uw = max(1e-6, ux1 - ux0)
    uh = max(1e-6, uy1 - uy0)

    scales_x: list[float] = []
    scales_y: list[float] = []
    loaded: list[tuple[ImageRegion, QImage]] = []
    for r in regions:
        src_path = output_dir / _normalize_src(r.src)
        if not src_path.exists():
            continue
        img = QImage(str(src_path))
        if img.isNull():
            continue
        x0, y0, x1, y1 = r.bbox
        bw = max(1e-6, x1 - x0)
        bh = max(1e-6, y1 - y0)
        if img.width() > 0 and img.height() > 0:
            scales_x.append(float(img.width() / bw))
            scales_y.append(float(img.height() / bh))
        loaded.append((r, img))

    if len(loaded) < 2:
        return False

    sx = _median(scales_x)
    sy = _median(scales_y)
    # 用统一缩放，减少拼接缝隙（取均值更稳）
    s = float((sx + sy) / 2.0) if sx > 0 and sy > 0 else float(max(sx, sy, 1.0))

    canvas_w = max(1, int(round(uw * s)))
    canvas_h = max(1, int(round(uh * s)))
    # 过大的合并图通常意味着 bbox 不在同一坐标系，直接跳过避免 OOM
    if canvas_w * canvas_h > 60_000_000:  # ~60MP
        return False

    canvas = QImage(canvas_w, canvas_h, QImage.Format.Format_ARGB32)
    canvas.fill(Qt.GlobalColor.white)

    painter = QPainter(canvas)
    try:
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        for r, img in loaded:
            x0, y0, x1, y1 = r.bbox
            bw = max(1e-6, x1 - x0)
            bh = max(1e-6, y1 - y0)
            dx = int(round((x0 - ux0) * s))
            dy = int(round((y0 - uy0) * s))
            tw = max(1, int(round(bw * s)))
            th = max(1, int(round(bh * s)))
            if img.width() != tw or img.height() != th:
                img2 = img.scaled(tw, th, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            else:
                img2 = img
            painter.drawImage(dx, dy, img2)
    finally:
        painter.end()

    out_path = output_dir / _normalize_src(merged_rel)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    return bool(canvas.save(str(out_path)))

def _render_pdf_page_image(*, pdf_path: Path, page_index: int, width: int, height: int):
    """
    渲染 PDF 单页为 QImage。
    返回 QImage 或 None。
    """
    try:
        from PySide6.QtCore import QSize
        from PySide6.QtPdf import QPdfDocument
    except Exception:
        return None

    def _safe_int(v: object) -> Optional[int]:
        try:
            return int(v)  # type: ignore[arg-type]
        except Exception:
            return None

    def _is_load_ok(st: object) -> bool:
        """
        兼容不同 PySide6/QtPdf 绑定下 QPdfDocument.load 的返回值：
        - 有的版本返回 int(0=OK)
        - 有的版本返回枚举对象，但 int(enum) / int(QPdfDocument.Error) 可能抛 TypeError
        """
        # 1) 直接与 None_ 比较（最标准）
        try:
            if st == QPdfDocument.Error.None_:
                return True
        except Exception:
            pass

        # 2) enum-like: st.value 或 st.value() / name
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

        # 3) 最后再尝试 int(st)
        si = _safe_int(st)
        return si == 0

    try:
        doc = QPdfDocument()
        st = doc.load(str(pdf_path))
        if not _is_load_ok(st):
            return None

        page_count = _safe_int(getattr(doc, "pageCount", lambda: 0)()) or 0
        if page_index < 0 or page_index >= page_count:
            return None

        w = max(1, int(width))
        h = max(1, int(height))
        img = doc.render(int(page_index), QSize(w, h))
        if img is None or img.isNull():
            return None
        return img
    except Exception:
        # QtPdf 在某些环境/版本组合下会出现奇怪的类型错误；这里宁可降级（返回 None），也不要让整条流水线崩溃。
        return None


def _crop_from_pdf(
    *,
    output_dir: Path,
    merged_rel: str,
    pdf_path: Path,
    page_index: int,
    crop_bbox: tuple[float, float, float, float],
    render_w: int,
    render_h: int,
) -> bool:
    """
    从 PDF 渲染整页再按 bbox 裁剪，生成合并图（最接近原 PDF）。
    bbox 单位默认为像素坐标（与 render_w/render_h 对齐）。
    """
    try:
        from PySide6.QtCore import QRect
    except Exception:
        return False

    page_img = _render_pdf_page_image(pdf_path=pdf_path, page_index=page_index, width=render_w, height=render_h)
    if page_img is None:
        return False

    x0, y0, x1, y1 = crop_bbox
    x = max(0, int(round(min(x0, x1))))
    y = max(0, int(round(min(y0, y1))))
    w = max(1, int(round(abs(x1 - x0))))
    h = max(1, int(round(abs(y1 - y0))))
    if x + w > page_img.width():
        w = max(1, page_img.width() - x)
    if y + h > page_img.height():
        h = max(1, page_img.height() - y)

    cropped = page_img.copy(QRect(x, y, w, h))
    if cropped.isNull():
        return False

    out_path = output_dir / _normalize_src(merged_rel)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    return bool(cropped.save(str(out_path)))

def _rewrite_markdown_with_merged_images(
    markdown: str, *, replacements: dict[str, str]
) -> str:
    """
    将 `replacements` 中的 src（碎片）替换为 merged src，并删除同组的其它碎片引用。
    replacements: {fragment_src -> merged_src}
    """
    if not markdown or not replacements:
        return markdown or ""

    # 先找出现顺序（只处理 Markdown 语法；HTML img 暂不做删除，避免误伤）
    spans = list(_iter_image_refs(markdown))
    if not spans:
        return markdown

    # 记录每个 merged_src 的第一次出现位置，用于保留；其它 fragment 删除
    keep_first: dict[str, int] = {}
    to_remove: set[int] = set()
    to_replace: dict[int, tuple[str, str]] = {}  # idx -> (alt, merged_src)

    for idx, (_s, _e, kind, alt, src) in enumerate(spans):
        if kind != "md":
            continue
        frag = _normalize_src(src)
        merged = replacements.get(frag)
        if not merged:
            continue
        if merged not in keep_first:
            keep_first[merged] = idx
            to_replace[idx] = (alt, merged)
        else:
            to_remove.add(idx)

    if not to_replace and not to_remove:
        return markdown

    # 反向应用 edits，避免 span 偏移
    out = markdown
    for idx in sorted(set(to_replace.keys()) | to_remove, reverse=True):
        start, end, kind, alt, _src = spans[idx]
        if kind != "md":
            continue
        if idx in to_remove:
            out = out[:start] + "" + out[end:]
            continue
        if idx in to_replace:
            _alt, merged_src = to_replace[idx]
            tag = f"![{_alt}]({_src_for_markdown(merged_src)})"
            out = out[:start] + tag + out[end:]

    # 清理多余空行
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out


def merge_image_fragments_for_page(
    *,
    config: AppConfig,
    output_dir: Path,
    page_markdown: str,
    pruned_result: Optional[dict[str, Any]],
    markdown_images: Iterable[str],
    page_no: int,
    pdf_path: Optional[Path] = None,
    pdf_page_index: Optional[int] = None,
) -> str:
    """
    对单页 Markdown 做“碎片图片合并”：
    - 从 prunedResult 抽取图片区域 + bbox
    - 聚类合并后，生成合并图文件（本地）
    - 替换/删除页面 Markdown 中的碎片图片引用
    """
    if not bool(getattr(config, "merge_image_fragments", True)):
        return page_markdown or ""
    if not page_markdown:
        return page_markdown or ""

    # 即使用户未开启 markdown_image_width_*，也尽量兼容服务端返回的 <img> 语法
    page_markdown = _html_imgs_to_markdown(page_markdown)

    known = {_normalize_src(s) for s in (markdown_images or []) if isinstance(s, str) and s.strip()}
    if not known:
        return page_markdown or ""

    regions: list[ImageRegion] = []
    if isinstance(pruned_result, dict) and pruned_result:
        regions = _extract_regions_from_pruned_result(pruned_result, known)
    # 兜底：若服务端不返回 prunedResult，则从图片文件名中解析 bbox（如 img_in_image_box_...）
    if not regions:
        regions = _extract_regions_from_bbox_in_name(known)

    if len(regions) < 2:
        return page_markdown or ""
    groups = _group_regions(regions)
    if not groups:
        return page_markdown or ""

    # PDF 裁剪渲染尺寸：必须尽量对齐 bbox 坐标系，否则 crop 会裁到错误区域（常见表现：合并图发白/缺图）。
    # 优先使用 prunedResult 的 page width/height（若存在），否则退回到 bbox 最大值。
    def _safe_int(v: object) -> int:
        try:
            if isinstance(v, bool):
                return 0
            if isinstance(v, (int, float)):
                return int(v)
            s = str(v).strip()
            if not s:
                return 0
            return int(float(s))
        except Exception:
            return 0

    pr_w = _safe_int(pruned_result.get("width")) if isinstance(pruned_result, dict) else 0
    pr_h = _safe_int(pruned_result.get("height")) if isinstance(pruned_result, dict) else 0
    # 合理范围保护：避免异常值导致渲染巨图
    if not (256 <= pr_w <= 50_000 and 256 <= pr_h <= 50_000):
        pr_w = 0
        pr_h = 0

    if pr_w > 0 and pr_h > 0:
        render_w, render_h = pr_w, pr_h
    else:
        max_x = int(max(r.bbox[2] for r in regions) + 2)
        max_y = int(max(r.bbox[3] for r in regions) + 2)
        render_w = max(256, max_x)
        render_h = max(256, max_y)

    replacements: dict[str, str] = {}
    for g in groups:
        # 合并文件名用稳定 hash，确保幂等
        h = hashlib.sha1()
        h.update(str(page_no).encode("utf-8"))
        for r in sorted(g, key=lambda x: x.src):
            h.update(_normalize_src(r.src).encode("utf-8"))
            h.update((",%.6f,%.6f,%.6f,%.6f" % r.bbox).encode("utf-8"))
        digest = h.hexdigest()[:12]
        merged_rel = f"images/merged/page_{page_no:04d}_{digest}.png"

        out_path = output_dir / merged_rel
        need_build = not out_path.exists()
        if not need_build:
            # 经验兜底：若历史产物明显异常（常见于 render 尺寸与 bbox 坐标系不一致导致 crop 发白），尝试重建。
            # 仅在“可以从 PDF 裁剪”的情况下触发，避免无谓重算。
            try:
                area = _bbox_area(_bbox_union(g))
                if area > 200 * 200 and out_path.stat().st_size < 8_192:
                    need_build = pdf_path is not None and pdf_page_index is not None and Path(pdf_path).exists()
            except Exception:
                pass

        if need_build:
            ok = False
            if pdf_path is not None and pdf_page_index is not None and Path(pdf_path).exists():
                # QtPdf 在部分环境里可能不可用/不稳定：失败时自动退回“碎片拼接”，不要直接让整个任务崩溃。
                try:
                    ok = _crop_from_pdf(
                        output_dir=output_dir,
                        merged_rel=merged_rel,
                        pdf_path=Path(pdf_path),
                        page_index=int(pdf_page_index),
                        crop_bbox=_bbox_union(g),
                        render_w=render_w,
                        render_h=render_h,
                    )
                except Exception:
                    ok = False
            if not ok:
                ok = _compose_merged_image(output_dir=output_dir, merged_rel=merged_rel, regions=g)
            if not ok:
                continue

        for r in g:
            replacements[_normalize_src(r.src)] = merged_rel

    if not replacements:
        return page_markdown or ""

    return _rewrite_markdown_with_merged_images(page_markdown, replacements=replacements)
