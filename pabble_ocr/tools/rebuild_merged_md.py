from __future__ import annotations

import argparse
import re
from pathlib import Path

from pabble_ocr.config import AppConfig, load_config
from pabble_ocr.core.state_store import load_state
from pabble_ocr.md.merge import merge_and_materialize, merge_best_effort
from pabble_ocr.utils.io import atomic_write_text
from pabble_ocr.utils.paths import resolve_path_maybe_windows


_SEG_RANGE_RE = re.compile(r"_p(?P<start>\d{4})-(?P<end>\d{4})", flags=re.IGNORECASE)


def _safe_sep(config: AppConfig) -> str:
    sep = getattr(config, "page_separator", "")
    if sep is None:
        return ""
    if isinstance(sep, str):
        return sep
    try:
        return str(sep)
    except Exception:
        return ""


def _normalize_target(path: Path) -> Path:
    if path.is_file() and path.name.lower() == "task_state.json":
        return path.parent
    if path.is_dir() and path.name == "_parts":
        return path.parent
    return path


def _iter_task_dirs(root: Path, *, recursive: bool) -> list[Path]:
    root = _normalize_target(root)
    if (root / "task_state.json").exists():
        return [root]
    if not recursive:
        raise RuntimeError("未找到 task_state.json；若传入的是输出根目录，请加 --recursive 扫描。")
    dirs: list[Path] = []
    for st in root.rglob("task_state.json"):
        if st.is_file():
            dirs.append(st.parent)
    uniq: dict[str, Path] = {}
    for d in dirs:
        uniq[str(d.resolve())] = d
    return [uniq[k] for k in sorted(uniq.keys())]


def _latest_parts_mtime(task_dir: Path) -> float:
    parts_dir = task_dir / "_parts"
    if not parts_dir.exists():
        return 0.0
    latest = 0.0
    for p in parts_dir.glob("*.md"):
        try:
            latest = max(latest, float(p.stat().st_mtime))
        except Exception:
            continue
    return latest


def _should_rebuild(task_dir: Path, *, force: bool, stale: bool) -> bool:
    merged = task_dir / "merged_result.md"
    if force:
        return True
    if not merged.exists():
        return True
    if not stale:
        return False
    try:
        merged_mtime = float(merged.stat().st_mtime)
    except Exception:
        return True
    return merged_mtime < (_latest_parts_mtime(task_dir) or 0.0)


def _manual_merge_from_parts(*, config: AppConfig, task_dir: Path) -> Path:
    parts_dir = task_dir / "_parts"
    if not parts_dir.exists():
        raise RuntimeError(f"缺少目录：{parts_dir}")
    md_files = [p for p in parts_dir.glob("*.md") if p.is_file() and p.name.lower() != "merged_result.md"]
    if not md_files:
        raise RuntimeError(f"未发现分段 md：{parts_dir}")

    def _key(p: Path) -> tuple[int, str]:
        m = _SEG_RANGE_RE.search(p.name)
        if not m:
            return (10**9, p.name.lower())
        try:
            start = int(m.group("start"))
        except Exception:
            start = 10**9
        return (start, p.name.lower())

    texts: list[str] = []
    for p in sorted(md_files, key=_key):
        texts.append(p.read_text(encoding="utf-8", errors="ignore"))

    sep = _safe_sep(config)
    merged_text = sep.join(texts)

    # merged_result.md 位于 output_dir 根目录；需要把分段 md 中的相对图片引用改写为 `_parts/...`
    # 直接复用 merge 模块已有的路径改写逻辑（避免重复实现/遗漏边界）。
    from pabble_ocr.md.merge import _rewrite_merged_md_image_paths  # noqa: PLC0415

    merged_text = _rewrite_merged_md_image_paths(output_dir=task_dir, text=merged_text)
    out_path = task_dir / "merged_result.md"
    atomic_write_text(out_path, merged_text, encoding="utf-8")
    return out_path


def _rebuild_one(*, config: AppConfig, task_dir: Path, strict: bool) -> Path:
    state = load_state(task_dir)
    if state and state.segments:
        done_all = all(bool(s.done) for s in state.segments)
        if strict or done_all:
            return merge_and_materialize(config=config, output_dir=task_dir, state=state, log=print)
        return merge_best_effort(config=config, output_dir=task_dir, state=state, log=print)
    return _manual_merge_from_parts(config=config, task_dir=task_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线重建 merged_result.md（不重跑 OCR）")
    parser.add_argument(
        "path",
        type=str,
        help="输出目录（含 task_state.json）、或其 _parts 目录、或 task_state.json 文件；也可传输出根目录并加 --recursive 扫描",
    )
    parser.add_argument("--recursive", action="store_true", help="扫描子目录中的 task_state.json 并批量重建")
    parser.add_argument("--force", action="store_true", help="即使 merged_result.md 已存在也强制重建")
    parser.add_argument("--stale", action="store_true", help="当 merged_result.md 早于任一 _parts/*.md 时重建")
    parser.add_argument("--strict", action="store_true", help="严格模式：仅当全部分段完成时合并（否则报错）")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要处理的目录，不实际写文件")
    args = parser.parse_args(argv)

    root = resolve_path_maybe_windows(args.path)
    if not root.exists():
        raise SystemExit(f"路径不存在：{root}")

    task_dirs = _iter_task_dirs(root, recursive=bool(args.recursive))
    if not task_dirs:
        print("未发现可处理的输出目录。")
        return 0

    config = load_config()

    total = 0
    rebuilt = 0
    skipped = 0
    failed = 0

    for d in task_dirs:
        total += 1
        if not _should_rebuild(d, force=bool(args.force), stale=bool(args.stale)):
            skipped += 1
            print(f"[skip] {d}")
            continue
        if args.dry_run:
            rebuilt += 1
            print(f"[plan] {d}")
            continue
        try:
            out = _rebuild_one(config=config, task_dir=d, strict=bool(args.strict))
            rebuilt += 1
            print(f"[ok] {out}")
        except Exception as e:
            failed += 1
            print(f"[fail] {d}: {e}")

    print(f"done. total={total}, rebuilt={rebuilt}, skipped={skipped}, failed={failed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

