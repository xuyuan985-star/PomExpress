"""统一时间格式（ISO8601 UTC）——日志/JSON/事件统一出口。"""
import re
import time
from datetime import datetime, timezone


def iso_utc(ts=None):
    """时间戳 → ISO8601 UTC 字符串（如 2026-08-09T09:00:00Z）。"""
    dt = datetime.fromtimestamp(ts or time.time(), tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def now_iso():
    return iso_utc()


def utc_timestamp():
    return time.time()


def sanitize_filename(name, fallback="unnamed"):
    """文件名清洗——Windows 非法字符替换，长度截断。

    非法：\\ / : * ? " < > |
    """
    cleaned = re.sub(r'[\\/:*?"<>|]', "_", str(name)).strip()
    cleaned = cleaned.rstrip(". ")
    cleaned = cleaned[:120] or fallback
    return cleaned
