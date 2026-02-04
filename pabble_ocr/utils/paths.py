from __future__ import annotations

import os
import re
from pathlib import Path


_WIN_ABS_RE = re.compile(r"^(?P<drive>[A-Za-z]):[\\/](?P<rest>.*)$")
_ILLEGAL_NAME_CHARS_RE = re.compile(r'[<>:"/\\\\|?*\\x00-\\x1F]+')
_TRAILING_DOTS_SPACES_RE = re.compile(r"[. ]+$")


def resolve_path_maybe_windows(path: str | Path, *, base_dir: Path | None = None) -> Path:
    """
    将路径解析为本机可访问的 Path：
    - Windows：原样使用（E:\\...）
    - Linux/WSL：若传入 Windows 盘符路径，尝试映射到 /mnt/<drive>/<rest>
    - 相对路径：若提供 base_dir，则相对于 base_dir
    """
    if isinstance(path, Path):
        p = path
    else:
        p = Path(str(path))

    if p.is_absolute():
        # 在 Linux 下，Windows 盘符路径通常会被识别为相对路径（例如 'E:\\x'），需手动处理
        s = str(path)
        m = _WIN_ABS_RE.match(s)
        if m and os.name != "nt":
            drive = m.group("drive").lower()
            rest = m.group("rest").replace("\\", "/")
            return Path("/mnt") / drive / rest
        return p

    if base_dir is not None:
        return (base_dir / p).resolve()
    return p.resolve()


def safe_stem(value: str, *, fallback: str = "file", max_len: int = 80) -> str:
    """
    将任意字符串转成适合作为目录/文件 stem 的名字（跨平台尽量安全）：
    - 去掉 Windows 不允许的字符与控制字符
    - 去掉结尾的点/空格
    - 为空时用 fallback
    """
    v = (value or "").strip()
    v = _ILLEGAL_NAME_CHARS_RE.sub("_", v)
    v = _TRAILING_DOTS_SPACES_RE.sub("", v)
    if not v:
        v = fallback
    v = v[: max(1, int(max_len))]
    # Windows 保留名简单规避（CON/PRN/AUX/NUL/COM1.. /LPT1..）
    upper = v.upper()
    reserved = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
    if upper in reserved:
        v = f"_{v}"
    return v


def unique_dir(preferred: Path, *, max_tries: int = 9999) -> Path:
    """
    若 preferred 已存在，生成一个不冲突的目录名：preferred_001 / preferred_002 ...
    """
    base = preferred
    for i in range(1, max_tries + 1):
        cand = base.with_name(f"{base.name}_{i:03d}")
        if not cand.exists():
            return cand
    # 兜底：极端情况下使用时间戳
    return base.with_name(f"{base.name}_{os.getpid()}")
