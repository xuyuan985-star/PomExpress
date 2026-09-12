"""运行时健康检查（Capability Check）：启动前确认各通道可用，失败可提前 pause。

输入健康分三级（v0.12.1）：
  L0 cursor_move SetCursorPos → GetCursorPos 回读（注入通道）
  L1 send_input SendInput 事件注入返回（事件级权限，UIPI）
  L2 game_response 按 ESC → 截图 → 画面变化/OCR 命中（游戏是否接受输入）
"""


def check_health(verbose=False, game_required=True, input_probe=False,
                 auto_activate=False):
    """7×24 防御：input_probe 控制 L2 按键注入探测（默认关——会向游戏按 ESC）；
    auto_activate 默认关——启动/周期刷新的健康检测不抢前台（用户明确：
    只在点开始任务后才拉游戏窗口）。gate 路径显式开启。"""
    result = {
        "window": False,
        "capture": False,
        "ocr": False,
        "foreground": False,
        "admin": False,
        "input": False,
        "input_l0": False,
        "input_l1": False,
        "input_l2": False,
    }
    errors = {}

    import time

    # . 管理员权限（SendInput/UIPI 前置条件）
    try:
        import ctypes
        result["admin"] = bool(ctypes.windll.shell32.IsUserAnAdmin())
        if not result["admin"]:
            errors["admin"] = "非管理员权限（SendInput 可能被 UIPI 拦截）"
    except Exception as e:
        errors["admin"] = f"{type(e).__name__}: {e}"

    # . 窗口锁定（driver.window 可见窗口枚举）
    try:
        from runtime.drivers.local.window import find_game_window
        game = find_game_window()
        result["window"] = game is not None
        if not game:
            errors["window"] = "未找到可见的游戏窗口"
        else:
            # 前台锁定：操作前游戏必须在前台（M1-A 输入前提）
            # 自动置顶：仅 auto_activate=True（真机任务 gate）时激活
            # GUI 启动的健康检查不激活（用户正在用别的窗口时不能被抢前台）
            import ctypes
            if auto_activate:
                try:
                    from runtime.win_capture import set_foreground_with_retry
                    set_foreground_with_retry(game["hwnd"])
                    time.sleep(0.3)
                except Exception:
                    pass
            fg = ctypes.windll.user32.GetForegroundWindow()
            result["foreground"] = fg == game["hwnd"]
            if not result["foreground"]:
                errors["foreground"] = f"游戏窗口不在前台 (0x{fg:x})"
    except Exception as e:
        errors["window"] = f"{type(e).__name__}: {e}"

    # . 截屏（PrintWindow 后台截图，Local 通道）
    if result["window"]:
        try:
            from runtime.drivers.local.vision import ScreenVision
            vision = ScreenVision()
            shot = vision.take_screenshot()
            result["capture"] = shot is not None and shot[0].size[0] > 0
            if not result["capture"]:
                errors["capture"] = "后台截图为空"
        except Exception as e:
            errors["capture"] = f"{type(e).__name__}: {e}"

    # . OCR（RapidOCR 模型可用）
    try:
        from runtime.drivers.local.vision import ScreenVision
        vision = ScreenVision()
        result["ocr"] = vision.ocr is not None
        if not result["ocr"]:
            errors["ocr"] = "OCR 引擎未初始化"
    except Exception as e:
        errors["ocr"] = f"{type(e).__name__}: {e}"

    # . 输入健康分级
    try:
        import ctypes
        from ctypes import wintypes

        class MOUSEINPUT(ctypes.Structure):
            # dwExtraInfo 必须 ULONG_PTR（64 位下 c_size_t）——c_ulong 会致
            # 结构大小错误 SendInput 拒收（与 win32_backend 同款 64 位 bug，
            # 之前就是它导致真机点击 L1 误报 uipi_block）
            _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong),
                        ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                        ("dwExtraInfo", ctypes.c_size_t)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", ctypes.c_ulong), ("mi", MOUSEINPUT)]

        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()

        # L0: 光标移动回读（GUI-2：探测后必须恢复原位——否则启动 GUI 鼠标跳左上角）
        # 审查：L0 会移动鼠标（SetCursorPos）——与 L2 按 ESC 同理，GUI 轮询
        # 健康检查必须零副作用（用户"光打开程序鼠标就跳来跳去"根因
        # 每 5s 刷新一次就抢一次鼠标）。仅 input_probe（真机 gate）时探测。
        result["input_l0"] = None    # 未测
        if input_probe:
            try:
                saved_pt = wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(saved_pt))
                r = user32.SetCursorPos(100, 100)
                pt = wintypes.POINT()
                time.sleep(0.1)
                user32.GetCursorPos(ctypes.byref(pt))
                result["input_l0"] = bool(r) and abs(pt.x - 100) < 50 and abs(pt.y - 100) < 50
                if not result["input_l0"]:
                    errors["input_l0"] = "光标回读不一致（注入通道异常）"
                # 恢复原鼠标位置（探测副作用最小化）
                user32.SetCursorPos(saved_pt.x, saved_pt.y)
            except Exception as e:
                errors["input_l0"] = f"{type(e).__name__}: {e}"

        # L1: SendInput 事件注入（UIPI 拦截时 ret=0）
        try:
            inp = INPUT()
            inp.type = 0
            inp.mi.dwFlags = 0x0001    # MOUSEEVENTF_MOVE，dx=dy=0 无副作用
            ret = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
            result["input_l1"] = ret == 1
            if not result["input_l1"]:
                errors["input_l1"] = "uipi_block: SendInput 被拒绝（需管理员权限）"
        except Exception as e:
            errors["input_l1"] = f"{type(e).__name__}: {e}"

        # L2: 游戏响应探测（InputProbe，仅游戏窗口存在时）
        # 判据：以"画面变化显著"为主，OCR 命中菜单词为辅。
        # 隐藏 Bug：L2 会向游戏注入 ESC 键——GUI 启动的 HealthWorker 也会跑
        # check_health，用户正在游戏时会被按 ESC 打断（菜单/剧情/战斗）。
        # 按键注入探测默认关闭（input_probe=False），仅真机 gate 时开启。
        # F10-006：即使 input_probe=True，也先 OCR 检查安全状态
        # 战斗/剧情中按 ESC 会打断玩家游戏进程，不可接受。
        result["input_l2"] = None    # 未测
        if input_probe and result["window"] and result["capture"]:
            try:
                from runtime.drivers.local.vision import ScreenVision
                vision = ScreenVision()
                import numpy as np
                img0, _, _ = vision.take_screenshot()
                arr0 = np.asarray(img0).astype(int)
                # F10-006：安全状态检查——战斗/剧情中按 ESC 会打断玩家
                pre_texts = [t for t, _ in vision.ocr_lines()]
                pre_text = "".join(pre_texts)
                # 战斗指示词
                COMBAT_KEYWORDS = ("ATB", "AP", "敌人", "回合", "伤害",
                                   "暴击", "技能", "Skill", "Buff", "Debuff",
                                   "Battle", "Combat", "战斗", "敌")
                # 剧情指示词
                STORY_KEYWORDS = ("剧情", "对话", "Dialogue", "Skip", "跳过",
                                  "Next", "下一", "继续", "Story")
                in_combat = any(k in pre_text for k in COMBAT_KEYWORDS)
                in_story = any(k in pre_text for k in STORY_KEYWORDS)
                if in_combat or in_story:
                    errors["input_l2"] = (
                        f"安全跳过：检测到{'战斗' if in_combat else '剧情'}"
                        f"状态，按 ESC 会打断玩家（关键词: "
                        f"{'combat' if in_combat else 'story'}）")
                    result["input_l2"] = None    # 未测——安全跳过
                else:
                    vision.auto.press_key("esc", wait_time=0.8)
                    time.sleep(1.2)
                    img1, _, _ = vision.take_screenshot()
                    arr1 = np.asarray(img1).astype(int)
                    diff = float(np.abs(arr1 - arr0).mean())
                    texts = [t for t, _ in vision.ocr_lines()]
                    hit = any(k in "".join(texts) for k in ["设置", "菜单", "esc", "ESC", "返回"])
                    result["input_l2"] = diff > 2.0 or hit
                    if not result["input_l2"]:
                        errors["input_l2"] = f"游戏未响应输入（画面变化 {diff:.1f}，OCR 未命中菜单词）"
            except Exception as e:
                result["input_l2"] = False
                errors["input_l2"] = f"{type(e).__name__}: {e}"

        # 汇总：未测（None）不算失败——GUI 轮询（L0/L2 未测）显示绿，
        # 真机 gate（全测）显示真实结果
        result["input"] = all(result.get(k) is not False
                              for k in ("input_l0", "input_l1", "input_l2"))
    except Exception as e:
        errors["input"] = f"{type(e).__name__}: {e}"

    # 依赖检查——ffmpeg / 磁盘空间 / 知识库
    try:
        import shutil
        result["ffmpeg"] = shutil.which("ffmpeg") is not None
        if not result["ffmpeg"]:
            errors["ffmpeg"] = "未找到 ffmpeg（抽帧/下载需要）"
    except Exception as e:
        result["ffmpeg"] = False
        errors["ffmpeg"] = f"{type(e).__name__}: {e}"
    try:
        import shutil
        from pathlib import Path
        usage = shutil.disk_usage(Path(__file__).resolve().parent.parent)
        free_mb = usage.free / (1024 * 1024)
        result["disk"] = free_mb > 500
        if not result["disk"]:
            errors["disk"] = f"磁盘剩余 {free_mb:.0f}MB < 500MB"
    except Exception as e:
        result["disk"] = False
        errors["disk"] = f"{type(e).__name__}: {e}"

    if verbose:
        for k, v in result.items():
            if v is None:
                continue
            mark = "OK" if v else "FAIL"
            print(f"  [{mark}] {k:8} {errors.get(k, '')}")
    return {"capability": result, "errors": errors, "all_ok": all(
        v for k, v in result.items() if k not in ("input_l2",))}
