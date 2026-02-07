from __future__ import annotations

import argparse
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pabble_ocr.tools.check_markdown_assets import check_markdown_assets
from pabble_ocr.utils.io import atomic_write_json, atomic_write_text
from pabble_ocr.utils.paths import resolve_path_maybe_windows


_MD_IMAGE_RE = re.compile(r"!\[[^\]]*]\(([^)]+)\)")
_HTML_IMG_SRC_RE = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*([\"'])([^\"']+)\1", flags=re.IGNORECASE)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_ref(raw: str) -> str:
    ref = str(raw or "").strip()
    if ref.startswith("<") and ref.endswith(">") and len(ref) > 2:
        ref = ref[1:-1].strip()
    if any(ch.isspace() for ch in ref):
        ref = ref.split()[0]
    return ref.replace("\\", "/")


def _safe_rel_path(rel: str) -> Path | None:
    s = str(rel or "").replace("\\", "/").strip().lstrip("/")
    if not s:
        return None
    p = Path(s)
    parts: list[str] = []
    for part in p.parts:
        if not part or part == ".":
            continue
        if part == "..":
            return None
        parts.append(part)
    if not parts:
        return None
    return Path(*parts)


def _resolve_task_dir(path_arg: str) -> Path:
    p = resolve_path_maybe_windows(path_arg)
    if not p.exists():
        raise RuntimeError(f"路径不存在：{p}")
    if p.is_file():
        if p.name.lower() != "merged_result.md":
            raise RuntimeError(f"请传任务目录或 merged_result.md：{p}")
        return p.parent
    return p


def _resolve_out_dir(task_dir: Path, out_arg: str | None) -> Path:
    if out_arg:
        return resolve_path_maybe_windows(out_arg, base_dir=task_dir.parent)
    return task_dir.with_name(f"{task_dir.name}_epub_pack")


def _prepare_out_dir(out_dir: Path, *, force: bool) -> None:
    if out_dir.exists():
        if not force:
            raise RuntimeError(f"导出目录已存在，请加 --force 覆盖：{out_dir}")
        if out_dir.is_file():
            out_dir.unlink()
        else:
            shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)


def _copy_assets(out_dir: Path, resolved_items: list[dict[str, str]]) -> tuple[int, list[str]]:
    copied = 0
    warnings: list[str] = []
    seen: set[str] = set()

    for item in resolved_items:
        src_raw = item.get("hit_path") or ""
        rel_raw = item.get("resolved_ref") or item.get("ref") or ""
        src = Path(src_raw)
        rel = _safe_rel_path(rel_raw)
        if rel is None:
            warnings.append(f"跳过非法相对路径：{rel_raw}")
            continue
        rel_key = rel.as_posix()
        if rel_key in seen:
            continue
        seen.add(rel_key)
        if not src.exists() or not src.is_file():
            warnings.append(f"源文件不存在：{src}")
            continue
        dst = out_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1

    return copied, warnings


def _rewrite_markdown_image_refs(md_path: Path, rewrite_map: dict[str, str]) -> list[dict[str, Any]]:
    if not rewrite_map:
        return []

    text = md_path.read_text(encoding="utf-8", errors="ignore")
    counts: dict[tuple[str, str], int] = {}

    def _mark(old: str, new: str) -> None:
        key = (old, new)
        counts[key] = counts.get(key, 0) + 1

    def _replace_md(match: re.Match[str]) -> str:
        raw_target = (match.group(1) or "").strip()
        normalized = _normalize_ref(raw_target)
        new_target = rewrite_map.get(normalized)
        if not new_target or new_target == raw_target:
            return match.group(0)
        _mark(normalized, new_target)
        return match.group(0).replace(raw_target, new_target, 1)

    def _replace_html(match: re.Match[str]) -> str:
        raw_src = (match.group(2) or "").strip()
        normalized = _normalize_ref(raw_src)
        new_src = rewrite_map.get(normalized)
        if not new_src or new_src == raw_src:
            return match.group(0)
        _mark(normalized, new_src)
        return match.group(0).replace(raw_src, new_src, 1)

    rewritten = _MD_IMAGE_RE.sub(_replace_md, text)
    rewritten = _HTML_IMG_SRC_RE.sub(_replace_html, rewritten)

    if rewritten != text:
        atomic_write_text(md_path, rewritten, encoding="utf-8")

    out: list[dict[str, Any]] = []
    for (old, new), c in sorted(counts.items(), key=lambda x: (x[0][0], x[0][1])):
        out.append({"from": old, "to": new, "count": c})
    return out


def _build_rewrite_map(post_result: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    misses = list(post_result.get("missing_examples") or [])
    for item in misses:
        old_ref = _normalize_ref(str(item.get("ref") or ""))
        new_ref = _normalize_ref(str(item.get("suggestion") or ""))
        if not old_ref or not new_ref or old_ref == new_ref:
            continue
        out[old_ref] = new_ref
    return out


def _export_one(task_dir: Path, *, out_dir: Path, force: bool, rewrite_fallback: bool) -> int:
    src_md = task_dir / "merged_result.md"
    if not src_md.exists() or not src_md.is_file():
        raise RuntimeError(f"缺少 merged_result.md：{src_md}")

    if out_dir.resolve() == task_dir.resolve():
        raise RuntimeError("导出目录不能与任务目录相同。")

    _prepare_out_dir(out_dir, force=force)
    dst_md = out_dir / "merged_result.md"
    shutil.copy2(src_md, dst_md)

    pre_result, pre_resolved_items = check_markdown_assets(src_md)
    copied_assets, warnings = _copy_assets(out_dir, pre_resolved_items)

    post_result, _ = check_markdown_assets(dst_md)
    rewrites: list[dict[str, Any]] = []
    fallback_triggered = False

    if rewrite_fallback and int(post_result.get("missing_local_refs") or 0) > 0:
        fallback_triggered = True
        rewrite_map = _build_rewrite_map(post_result)
        rewrites = _rewrite_markdown_image_refs(dst_md, rewrite_map)
        if rewrites:
            post_result, _ = check_markdown_assets(dst_md)

    if int(post_result.get("missing_local_refs") or 0) > 0:
        warnings.append("导出后仍存在缺图，请先回源任务目录修复资源。")

    report = {
        "tool": "pabble_ocr.tools.export_epub_pack",
        "generated_at": _now_iso(),
        "input_task_dir": str(task_dir),
        "output_pack_dir": str(out_dir),
        "source_md": str(src_md),
        "pack_md": str(dst_md),
        "force": bool(force),
        "rewrite_fallback_enabled": bool(rewrite_fallback),
        "rewrite_fallback_triggered": bool(fallback_triggered),
        "rewrites": rewrites,
        "copied_asset_files": copied_assets,
        "warnings": warnings,
        "pre_check": pre_result,
        "post_check": post_result,
    }
    atomic_write_json(out_dir / "export_report.json", report)

    pre_missing = int(pre_result.get("missing_local_refs") or 0)
    post_missing = int(post_result.get("missing_local_refs") or 0)
    print(f"[ok] pack: {out_dir}")
    print(f"pre_missing={pre_missing}, post_missing={post_missing}, copied_asset_files={copied_assets}")
    print(f"report: {out_dir / 'export_report.json'}")
    if warnings:
        print(f"warnings={len(warnings)}")
    return 0 if post_missing == 0 else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="导出 md-epub 兼容包（生成 *_epub_pack，避免手工搬目录导致缺图）"
    )
    parser.add_argument(
        "path",
        type=str,
        help="任务输出目录（含 merged_result.md）；也可直接传 merged_result.md 路径",
    )
    parser.add_argument("--out", type=str, default="", help="自定义导出目录（默认：<task_dir>_epub_pack）")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的导出目录")
    parser.add_argument(
        "--no-rewrite-fallback",
        action="store_true",
        help="关闭导出后缺图时的最小路径改写兜底",
    )
    args = parser.parse_args(argv)

    try:
        task_dir = _resolve_task_dir(args.path)
        out_dir = _resolve_out_dir(task_dir, args.out or None)
        return _export_one(
            task_dir,
            out_dir=out_dir,
            force=bool(args.force),
            rewrite_fallback=not bool(args.no_rewrite_fallback),
        )
    except RuntimeError as e:
        print(f"[fail] {e}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
