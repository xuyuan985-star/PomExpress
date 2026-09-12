"""版本单点：全项目版本号唯一来源。

取值顺序
    1. 已安装发行版的元数据（`pip install .` 后可用）——发行名 ``pom-express``；
    2. 项目根目录的 ``VERSION`` 文件（源码运行时的权威来源）；
    3. ``FALLBACK_VERSION`` 常量（兜底，保证界面永远显示可用版本号）。
"""
import importlib.metadata
from pathlib import Path

APP_NAME = "pom-express"

# 与 CHANGELOG 最新条目保持一致；改动版本时同步两处
FALLBACK_VERSION = "0.6.1"


def _version() -> str:
    try:
        return importlib.metadata.version(APP_NAME)
    except Exception:
        pass
    try:
        f = Path(__file__).resolve().parent.parent / "VERSION"
        if f.is_file():
            v = f.read_text(encoding="utf-8").strip()
            if v:
                return v
    except Exception:
        pass
    return FALLBACK_VERSION


APP_VERSION = _version()

# 知识包 schema 版本（与 runtime.knowledge_loader 同步——单点避免双写漂移）
KNOWLEDGE_SCHEMA_VERSION = 1

# 点位数据版本（archive 写入 points_meta.json）
POINTS_VERSION = "1.1"
