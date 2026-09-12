"""安全工具层：报告与日志脱敏。

对外统一从此处导出，避免各模块自行实现脱敏逻辑。
"""
from security.quarantine import (  # noqa: F401
    sanitize_mapping,
    sanitize_text,
)
