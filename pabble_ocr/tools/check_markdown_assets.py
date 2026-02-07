from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from pabble_ocr.utils.paths import resolve_path_maybe_windows


_ABS_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_WIN_ABS_RE = re.compile(r"^[A-Za-z]:[\\/]")
_HTML_IMG_SRC_RE = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*([\"'])([^\"']+)\1", flags=re.IGNORECASE)
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*]\(([^)]+)\)")


def _extract_refs(text: str) -> list[str]:
    out: list[str] = []
    for m in _MD_IMAGE_RE.finditer(text or ""):
        out.append((m.group(1) or "").strip())
    for m in _HTML_IMG_SRC_RE.finditer(text or ""):
        out.append((m.group(2) or "").strip())
    return out


def _normalize_ref(raw: str) -> str:
    ref = str(raw or "").strip()
    if ref.startswith("<") and ref.endswith(">") and len(ref) > 2:
        ref = ref[1:-1].strip()
    # 兼容 `path "title"` 形式，仅取 src token
    if any(ch.isspace() for ch in ref):
        ref = ref.split()[0]
    return ref.replace("\\", "/")


def _is_external_or_anchor(ref: str) -> bool:
    if not ref:
        return True
    if ref.startswith(("#", "/", "\\")):
        return True
    if ref.startswith("../"):
        return True
    if _ABS_SCHEME_RE.match(ref) or _WIN_ABS_RE.match(ref):
        return True
    return False


def _resolve_local_ref(task_dir: Path, ref: str) -> tuple[Path | None, str | None]:
    rel = _normalize_ref(ref)
    if not rel or _is_external_or_anchor(rel):
        return (None, None)

    # 优先级：root -> _parts -> images
    direct_candidates = [
        (task_dir / rel, rel),
        (task_dir / "_parts" / rel, f"_parts/{rel}"),
        (task_dir / "images" / rel, f"images/{rel}"),
    ]

    # 历史兼容：imgs/* 或 merged/* 实际常落在 images/ 下
    if rel.startswith("imgs/") or rel.startswith("merged/"):
        direct_candidates.append((task_dir / "images" / rel, f"images/{rel}"))

    for path, normalized in direct_candidates:
        try:
            if path.exists() and path.is_file():
                return (path, normalized)
        except Exception:
            continue
    return (None, None)


def check_markdown_assets(md_path: Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    task_dir = md_path.parent
    text = md_path.read_text(encoding="utf-8", errors="ignore")

    refs = _extract_refs(text)
    checked = 0
    hit = 0
    skipped = 0
    misses: list[dict[str, str]] = []
    resolved_items: list[dict[str, str]] = []

    for raw in refs:
        ref = _normalize_ref(raw)
        if not ref:
            continue
        if _is_external_or_anchor(ref):
            skipped += 1
            continue
        checked += 1
        hit_path, resolved = _resolve_local_ref(task_dir, ref)
        if hit_path is not None:
            hit += 1
            resolved_items.append(
                {
                    "ref": ref,
                    "resolved_ref": resolved or ref,
                    "hit_path": str(hit_path),
                }
            )
            continue
        suggestion = ""
        if ref.startswith("imgs/") or ref.startswith("merged/"):
            suggestion = f"images/{ref}"
        misses.append({"ref": ref, "suggestion": suggestion})

    result: dict[str, Any] = {
        "md": str(md_path),
        "task_dir": str(task_dir),
        "total_refs": len(refs),
        "checked_local_refs": checked,
        "resolved_local_refs": hit,
        "missing_local_refs": len(misses),
        "skipped_external_or_anchor": skipped,
        "missing_examples": misses[:200],
    }
    return result, resolved_items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查 Markdown 图片引用是否都能命中本地资源（EPUB 转换前建议先跑）")
    parser.add_argument("md", type=str, help="Markdown 文件路径（建议 merged_result.md）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 结果")
    args = parser.parse_args(argv)

    md_path = resolve_path_maybe_windows(args.md)
    if not md_path.exists() or not md_path.is_file():
        raise SystemExit(f"Markdown 不存在：{md_path}")

    result, _ = check_markdown_assets(md_path)
    misses = list(result.get("missing_examples") or [])

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"md: {md_path}")
        print(f"total_refs={int(result.get('total_refs') or 0)}")
        print(f"checked_local_refs={int(result.get('checked_local_refs') or 0)}")
        print(f"resolved_local_refs={int(result.get('resolved_local_refs') or 0)}")
        print(f"missing_local_refs={int(result.get('missing_local_refs') or 0)}")
        print(f"skipped_external_or_anchor={int(result.get('skipped_external_or_anchor') or 0)}")
        if misses:
            print("missing examples (up to 20):")
            for item in misses[:20]:
                ref = item.get("ref") or ""
                sug = item.get("suggestion") or ""
                if sug:
                    print(f"- {ref}  (suggest: {sug})")
                else:
                    print(f"- {ref}")

    return 0 if int(result.get("missing_local_refs") or 0) == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
