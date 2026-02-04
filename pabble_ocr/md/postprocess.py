from __future__ import annotations

import re

from pabble_ocr.config import AppConfig

_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)(?!\s*\{)")
_DIV_WRAPPED_MD_IMAGE_RE = re.compile(
    r"(?P<div_open><div\b[^>]*>)\s*"
    r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)]+)\)\s*(?P<attrs>\{[^}]*\})?\s*"
    r"(?P<div_close></div>)",
    flags=re.IGNORECASE | re.DOTALL,
)
_HTML_IMG_TAG_RE = re.compile(r"<img\b(?P<attrs>[^>]*?)(?P<slash>/?)>", flags=re.IGNORECASE | re.DOTALL)
_HTML_STYLE_ATTR_RE = re.compile(r"\sstyle=(?P<q>['\"])(?P<style>.*?)(?P=q)", flags=re.IGNORECASE | re.DOTALL)
_MD_ATTR_STYLE_RE = re.compile(r"\bstyle\s*=\s*(?P<q>['\"])(?P<style>.*?)(?P=q)", flags=re.IGNORECASE | re.DOTALL)


def apply_markdown_image_width(text: str, config: AppConfig) -> str:
    """
    部分 Markdown 预览/导出链路会按图片“原始像素尺寸”渲染，导致图片在页面中异常巨大。
    这里提供一个纯客户端的后处理：把 `![](...)` 转成 `<img ... style="width:XX%; height:auto;">`。
    """
    width_percent = int(getattr(config, "markdown_image_width_percent", 0) or 0)
    max_height_px = int(getattr(config, "markdown_image_max_height_px", 0) or 0)
    width_percent = max(0, min(100, width_percent))
    max_height_px = max(0, max_height_px)

    def _style() -> str:
        if width_percent <= 0 and max_height_px <= 0:
            return ""
        styles: list[str] = ["max-width:100%", "height:auto", "object-fit:contain", "display:block", "margin:0 auto"]
        if width_percent > 0:
            styles[0] = f"max-width:{width_percent}%"
        if max_height_px > 0:
            styles.append(f"max-height:{max_height_px}px")
        return "; ".join(styles)

    def _extract_src(raw: str) -> str:
        v = (raw or "").strip()
        # 去掉可能的 <...> 包裹
        if v.startswith("<") and v.endswith(">") and len(v) >= 2:
            v = v[1:-1].strip()
        # 处理 `path "title"` / `path 'title'` 的情况：仅取第一个 token 作为 src
        if " " in v or "\t" in v or "\n" in v:
            v = v.split()[0]
        return v

    def _extract_style_from_md_attrs(raw: str) -> str:
        if not raw:
            return ""
        m = _MD_ATTR_STYLE_RE.search(raw)
        return (m.group("style") or "").strip() if m else ""

    def _escape_html_attr(v: str) -> str:
        # alt/src/style 仅用于写回 HTML 属性：做最小转义避免破坏标签
        return (v or "").replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")

    # 修复“HTML block 中的 Markdown 图片”兼容性：
    # CommonMark/markdown-it 通常不会解析 `<div> ... ![](...) ... </div>` 里的 Markdown，
    # 这会导致某些转换链路（例如 md-epub）丢图。这里将其改写为纯 HTML `<img>`，确保不丢。
    def _div_repl(m: re.Match[str]) -> str:
        div_open = m.group("div_open") or "<div>"
        div_close = m.group("div_close") or "</div>"
        alt = m.group("alt") or ""
        src_raw = _extract_src(m.group("src") or "")
        style_from_md = _extract_style_from_md_attrs(m.group("attrs") or "")
        style = style_from_md or _style()

        alt_html = _escape_html_attr(alt)
        src_html = _escape_html_attr(src_raw)
        if style:
            style_html = _escape_html_attr(style)
            img = f'<img src="{src_html}" alt="{alt_html}" style="{style_html}" />'
        else:
            img = f'<img src="{src_html}" alt="{alt_html}" />'
        return f"{div_open}\n{img}\n{div_close}"

    def _repl(m: re.Match[str]) -> str:
        if width_percent <= 0 and max_height_px <= 0:
            return m.group(0)
        alt = m.group(1) or ""
        src_raw = _extract_src(m.group(2) or "")

        # Pandoc Markdown 默认启用 link_attributes（pandoc 3.x：+link_attributes），
        # 可将图片属性写成：![](path){ style="..." }，最终会落到 EPUB 的 HTML/CSS。
        # 这样可避免上游工具把 HTML <img> 清洗回 Markdown 时丢掉 style。
        target = f"<{src_raw}>" if any(ch.isspace() for ch in src_raw) else src_raw
        style = _style()
        # 仍输出标准 Markdown 图片语法，便于 Pandoc/其他链路继续识别为“图片”。
        return f"![{alt}]({target}){{ style=\"{style}\" }}"

    updated = text or ""

    updated = _DIV_WRAPPED_MD_IMAGE_RE.sub(_div_repl, updated)

    # 若已经是 HTML <img>，仅在“未带 style 且用户启用缩放”时补一个 style，避免重复叠加。
    def _html_img_repl(m: re.Match[str]) -> str:
        if width_percent <= 0 and max_height_px <= 0:
            return m.group(0)
        attrs = m.group("attrs") or ""
        if _HTML_STYLE_ATTR_RE.search(attrs):
            return m.group(0)
        style = _style()
        if not style:
            return m.group(0)
        slash = m.group("slash") or ""
        return f'<img{attrs} style="{_escape_html_attr(style)}"{slash}>'

    updated = _HTML_IMG_TAG_RE.sub(_html_img_repl, updated)
    updated = _MD_IMAGE_RE.sub(_repl, updated)
    return updated
