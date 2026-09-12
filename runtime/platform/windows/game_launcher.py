"""游戏拉起：窗口不存在时自动启动游戏。

流程：启动游戏 exe → 轮询等窗口出现（最长 360s）→ 窗口就绪后尝试点
「点击进入」（模板 `assets/templates/click_enter.png`）。点进入失败不阻塞
（登录态/加载慢——用户手动处理）。

配置来源：项目自身配置（`.env` / 设置页），键见 `.env.example`：
  GAME_PATH             游戏可执行文件路径（不填则不做自动拉起）
  HOTKEY_MAP            地图键（默认 m）
  HOTKEY_TECHNIQUE      战斗技巧键（默认 e）
  HOTKEY_AUTO_BATTLE    自动战斗键（默认 v）
  HOTKEY_B              备用键（可不填）
缺失任何一项都只使对应功能降级，不影响其余流程。
"""
import subprocess
import time
from pathlib import Path

from config.settings import get as _cfg_get
from config.settings import resource_path
from runtime.win_capture import GAME_TITLE, find_game_window

# 游戏内热键的内置默认（原读 m7/config.yaml，现由项目配置覆盖）
HOTKEY_DEFAULTS = {
    "HOTKEY_MAP": "m",
    "HOTKEY_TECHNIQUE": "e",
    "HOTKEY_AUTO_BATTLE": "v",
}


def game_setting(key, default=None):
    """读游戏相关配置（`.env` / 设置页覆盖 / 内置默认）。

    key 用大写配置键名，如 `GAME_PATH`、`HOTKEY_MAP`。
    """
    v = _cfg_get(key)
    if v:
        return str(v)
    if default is not None:
        return default
    return HOTKEY_DEFAULTS.get(key)


def game_executable():
    """游戏可执行文件路径；未配置或文件不存在则返回 None。"""
    path = game_setting("GAME_PATH")
    if not path or not Path(str(path)).exists():
        return None
    return str(path)


def launch_game_process(executable):
    """启动游戏 exe（cmd start 带工作目录；失败 Popen 兜底）。

    参数化 argv 传递（不拼 shell 字符串、不用 shell=True——架构 lint 禁
    subprocess.call/shell=True，且消除注入面）。
    """
    folder = str(Path(executable).parent)
    try:
        subprocess.Popen(
            ["cmd", "/C", "start", "", "/D", folder, executable],
            cwd=folder, creationflags=subprocess.CREATE_NO_WINDOW)
        return True
    except Exception:
        pass
    try:
        subprocess.Popen(executable, cwd=folder)
        return True
    except Exception:
        return False


def wait_for_game_window(wait_seconds=360, poll_interval=5.0):
    """轮询等游戏窗口出现（find_game_window 可见+非最小化）。"""
    from runtime.win_capture import find_game_window
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        game = find_game_window(GAME_TITLE)
        if game is not None:
            return game
        time.sleep(poll_interval)
    return None


def click_enter_if_present(max_tries=5, interval=10.0):
    """游戏启动后的"点击进入"界面——点进入游戏。

    模板：`assets/templates/click_enter.png`（随项目分发）。
    模板匹配全屏（游戏全屏/窗口化均可）；失败不阻塞（登录态/加载慢）。
    返回是否点过。
    """
    tmpl = resource_path("assets") / "templates" / "click_enter.png"
    if not tmpl.exists():
        return False
    from runtime.input.template_backend import TemplateMatcher
    tm = TemplateMatcher(threshold=0.7)
    for _ in range(max_tries):
        try:
            hit = tm.locate(str(tmpl))
        except Exception:
            return False
        if hit is None:
            return False    # 未出现（可能已进入游戏/未到该界面）——不再等
        _, cx, cy = hit
        try:
            from runtime.input.win32_backend import Win32Backend
            r = Win32Backend().click(cx, cy)
            if r.success:
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def ensure_game_launched(wait_seconds=360, auto_enter=True):
    """总入口：窗口在 → 直接返回 (True, "already_running")；
    不在 → 启动 → 等窗口 → 点进入。失败返回 (False, 原因)。"""
    from runtime.win_capture import find_game_window
    if find_game_window(GAME_TITLE) is not None:
        return True, "already_running"
    exe = game_executable()
    if not exe:
        return False, "GAME_PATH 未配置或文件不存在（见 .env.example）"
    if not launch_game_process(exe):
        return False, f"游戏启动失败: {exe}"
    game = wait_for_game_window(wait_seconds=wait_seconds)
    if game is None:
        return False, f"等待游戏窗口超时（{wait_seconds}s）"
    entered = False
    if auto_enter:
        entered = click_enter_if_present()
    return True, ("started" if entered else "started_no_enter")
