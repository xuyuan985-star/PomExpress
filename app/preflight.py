"""app/preflight.py — 冷启动前置检查（依赖/数据缺失 → 可见可操作提示）。

覆盖三种启动异常（每条都给出可见、可操作的提示，不崩溃、不静默）：
1. 运行环境缺失或 Python < 3.12（项目统一环境）
2. PySide6 缺失（系统 python 就没有，别让人用错解释器还以为程序坏了）
3. 知识包缺失/为空（knowledge/source/black_tower_test）

severity：
critical：拦在 GUI 前，退出码非 0（用户必须修复）
warning：打印但继续启动（正常启动路径不变）

不新增依赖：只用 stdlib（sys, os, pathlib, ctypes MessageBoxW, datetime）。
"""
from __future__ import annotations

import ctypes
import datetime
import os
import sys
from pathlib import Path
from typing import Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent

# 本地副产物区（与 config.settings 同一约定）：项目根同级 <项目名>_local。
# 运行环境 m7_venv 与运行产物都放在那里，项目本体内不再放副产物。
# 本模块只依赖 stdlib，故就地推导而不 import config（避免额外依赖面）。
_LOCAL_ROOT = ROOT.parent / f"{ROOT.name}_local"

# 项目统一环境的解释器位置（启动脚本按此路径创建与调用）
VENV_SCRIPTS = (_LOCAL_ROOT if _LOCAL_ROOT.is_dir() else ROOT) / "m7_venv" / "Scripts"
VENV_PYTHON = VENV_SCRIPTS / "python.exe"
# 知识包默认路径（与 gui/main_window.py 的知识目录注入同源）
KNOWLEDGE_PKG = ROOT / "knowledge" / "source" / "black_tower_test"
# 日志路径（与 app/__main__.py:26 / gui/run.py:191 同源）
# 日志目录必须可写：打包后 __file__ 指向退出即删的解包目录；
# 源码模式走副产物区（存在时）。
_DATA_ROOT = (Path(sys.executable).resolve().parent
              if getattr(sys, "frozen", False)
              else (_LOCAL_ROOT if _LOCAL_ROOT.is_dir() else ROOT))
LOG_DIR = _DATA_ROOT / "logs"
STARTUP_LOG = LOG_DIR / "startup_error.log"


# Helpers

def _show_messagebox(title: str, text: str, is_error: bool) -> bool:
    """Windows MessageBoxW——不依赖 PySide6，pythonw 下也能弹。

    失败时静默返回 False（不崩溃）。非 Windows 平台直接返回 False。
    """
    try:
        if os.name != "nt":
            return False
        MB_OK = 0x0
        MB_ICONERROR = 0x10
        MB_ICONWARNING = 0x30
        flags = (MB_ICONERROR if is_error else MB_ICONWARNING) | MB_OK
        ctypes.windll.user32.MessageBoxW(None, text, title, flags)
        return True
    except Exception:
        return False


def _log_startup(issues: list) -> None:
    """关键问题落盘（pythonw 无控制台——日志可查）。"""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(STARTUP_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n===== {datetime.datetime.now().isoformat()} preflight =====\n")
            for sev, msg in issues:
                f.write(f"[{sev}] {msg}\n")
    except Exception:
        pass


def _is_under_project_venv() -> bool:
    """当前解释器是否运行在项目运行环境（VENV_SCRIPTS）下。"""
    try:
        exe_path = sys.executable or ""
        if not exe_path:
            return False
        exe = Path(exe_path).resolve()
        return exe.parent == VENV_SCRIPTS.resolve()
    except Exception:
        return False


# Checks

def check_python_env() -> Optional[Tuple[str, str]]:
    """检查 1：运行环境存在性 + Python 版本。

    返回 (severity, message) 或 None（OK）。
    """
    # 打包版（PyInstaller）解释器与依赖随包分发，不存在「项目运行环境缺失」，
    # 也没有 m7_venv —— 若不跳过会误报 critical 弹框拦住启动。
    if getattr(sys, "frozen", False):
        return None
    py_ver = sys.version_info
    py_str = f"{py_ver.major}.{py_ver.minor}.{py_ver.micro}"
    in_venv = _is_under_project_venv()
    venv_exists = VENV_PYTHON.exists()

    issues: list[Tuple[str, str]] = []

    # 1a: Python 版本（< 3.12 是项目标准外的环境）
    if py_ver < (3, 12):
        issues.append(("critical",
            f"Python 版本过低：{py_str}（需 3.12+）\n"
            f"当前解释器：{sys.executable}\n\n"
            "修复：双击启动脚本（自动重建运行环境）"))

    # 1b: 运行环境存在性 / 当前解释器来源
    if not in_venv:
        if not venv_exists:
            issues.append(("critical",
                f"项目运行环境不存在：{VENV_SCRIPTS}\n"
                f"当前解释器：{sys.executable}（{py_str}）\n\n"
                "修复：双击启动脚本（自动重建）"))
        else:
            issues.append(("warning",
                f"未运行在项目运行环境下。\n"
                f"当前解释器：{sys.executable}（{py_str}）\n"
                f"预期环境：{VENV_PYTHON}（存在，但当前进程未使用）\n\n"
                "影响：依赖可能与项目要求不一致；建议双击启动脚本。"))

    if not issues:
        return None
    if len(issues) == 1:
        return issues[0]
    severity = "critical" if any(s == "critical" for s, _ in issues) else "warning"
    msg = "\n\n---\n\n".join(m for _, m in issues)
    return (severity, msg)


def check_pyside6() -> Optional[Tuple[str, str]]:
    """检查 2：PySide6 是否可导入（系统 python 就没有，别让人用错解释器）。"""
    try:
        import PySide6.QtCore  # noqa: F401
    except Exception as e:
        return ("critical",
                f"PySide6 未安装（GUI 无法启动）\n\n"
                f"错误：{type(e).__name__}: {e}\n\n"
                f"当前解释器：{sys.executable}\n\n"
                "修复：用项目唯一环境启动——\n"
                f"  {VENV_PYTHON.with_name('pythonw.exe')} -m app\n"
                f"或安装：{VENV_PYTHON} -m pip install PySide6")
    return None


def check_knowledge_package() -> Optional[Tuple[str, str]]:
    """检查 3：知识包是否存在且非空（缺失只影响任务执行/世界图，不拖垮 GUI）。"""
    if not KNOWLEDGE_PKG.is_dir():
        return ("warning",
                f"知识包目录不存在：{KNOWLEDGE_PKG}\n\n"
                "影响：任务执行/世界图将不可用（GUI 仍可启动）。\n"
                "修复：确认项目完整下载（knowledge/ 随仓库分发）。")
    try:
        entries = list(KNOWLEDGE_PKG.iterdir())
    except Exception as e:
        return ("warning",
                f"知识包目录无法读取：{KNOWLEDGE_PKG}\n"
                f"错误：{type(e).__name__}: {e}\n\n"
                "影响：任务执行/世界图将不可用。")
    if not entries:
        return ("warning",
                f"知识包目录为空：{KNOWLEDGE_PKG}\n\n"
                "影响：任务执行/世界图将不可用。\n"
                "修复：确认项目完整下载。")
    return None


# Aggregation + Reporting

def run_all() -> list[Tuple[str, str]]:
    """执行全部前置检查。返回 [(severity, message), ...]，OK 项不返回。"""
    issues: list[Tuple[str, str]] = []
    for fn in (check_python_env, check_pyside6, check_knowledge_package):
        try:
            r = fn()
        except Exception as e:
            # 检查本身失败也可见（不静默）
            r = ("warning", f"前置检查 {fn.__name__} 异常：{type(e).__name__}: {e}")
        if r is not None:
            issues.append(r)
    return issues


def report(issues: list[Tuple[str, str]]) -> int:
    """打印 issues + 关键问题弹对话框，返回退出码（0=OK, 1=critical 拦下）。"""
    critical = [(s, m) for s, m in issues if s == "critical"]
    warnings = [(s, m) for s, m in issues if s == "warning"]

    # 打印全部（console 可见；pythonw 下 stdout/stderr 已重定向到 StringIO，
    # 关键问题另落盘到日志——见 _log_startup）
    for _, m in warnings:
        print(f"[warn] {m}")
    for _, m in critical:
        print(f"[error] {m}")

    if not critical:
        return 0

    # 关键问题：落盘 + 弹对话框（best-effort）
    _log_startup(issues)
    combined = "\n\n---\n\n".join(m for _, m in critical)
    _show_messagebox("帕姆巡宝 启动失败", combined, is_error=True)
    return 1
