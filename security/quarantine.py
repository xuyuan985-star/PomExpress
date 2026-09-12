"""报告与日志脱敏。

用途
    导出问题报告或写入日志前，把 Windows 用户名路径替换为占位符，
    避免把本机用户名带进对外可见的文件。

约定
    - `sanitize_text`：单个字符串的替换（用户名路径 → `C:\\Users\\<USER>`）。
    - `sanitize_mapping`：对 dict / list / tuple 递归应用。

实现说明
    使用 `str.replace` 而非正则：Windows 路径含 `\\U` 等反斜杠序列，
    即便用 `re.escape`（3.7+）不转义反斜杠也会产出非法正则（bad escape）。
"""
from __future__ import annotations

from pathlib import Path


def sanitize_text(text):
    """把当前用户的主目录路径替换为占位符。非字符串或空值原样返回。"""
    if not text:
        return text
    try:
        user = Path.home().name
        if user:
            text = text.replace("C:\\Users\\" + user, "C:\\Users\\<USER>")
            text = text.replace("C:/Users/" + user, "C:/Users/<USER>")
    except Exception:
        # 脱敏失败不应影响主流程；保留原文继续
        pass
    return text


def sanitize_mapping(mapping):
    """递归处理 dict / list / tuple 中的字符串。"""
    if isinstance(mapping, dict):
        return {k: sanitize_mapping(v) for k, v in mapping.items()}
    if isinstance(mapping, (list, tuple)):
        return [sanitize_mapping(v) for v in mapping]
    if isinstance(mapping, str):
        return sanitize_text(mapping)
    return mapping
