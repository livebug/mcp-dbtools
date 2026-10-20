"""服务日志读取（尾部 N 行 + 关键字过滤），供 REST API 与控制台使用。"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def fmt_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024
    return f"{size} B"


def tail_lines(path: str | Path, lines: int = 200, q: str | None = None) -> dict[str, Any]:
    """读取日志文件末尾 N 行（可选关键字过滤）。

    返回: {content, file, size, size_text, truncated, returned}
    日志文件有轮转上限（默认 10MB），整体读取成本可控；q 非空时先过滤再取尾部。
    """
    p = Path(path)
    n = min(max(1, lines), 2000)
    size = 0
    if not p.is_file():
        return {"content": "", "file": str(p), "size": 0, "size_text": "0 B", "truncated": False, "returned": 0}
    try:
        size = p.stat().st_size
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {
            "content": f"读取日志失败: {exc}",
            "file": str(p),
            "size": size,
            "size_text": fmt_size(size),
            "error": str(exc),
        }
    all_lines = text.splitlines()
    if q:
        ql = q.lower()
        all_lines = [ln for ln in all_lines if ql in ln.lower()]
    picked = all_lines[-n:]
    return {
        "content": "\n".join(picked),
        "file": str(p),
        "size": size,
        "size_text": fmt_size(size),
        "truncated": len(all_lines) > n,
        "returned": len(picked),
    }
