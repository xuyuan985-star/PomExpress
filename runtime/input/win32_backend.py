import ctypes
import time

from runtime.input.base import InputBackend, InputResult

# 可调 · Windows API 常量（协议常量，勿改值；集中定义避免方法内重复）
# KEYEVENTF_* 用于 SendInput KEYBDINPUT 的 dwFlags 字段（键盘事件）。
# MOUSEEVENTF_* 用于 SendInput MOUSEINPUT 的 dwFlags 字段（鼠标事件）。
# INPUT_KEYBOARD 用于 SendInput INPUT 结构的 type 字段（键盘事件类型）。
# 均为 Windows SDK 协议常量，参考 winuser.h。
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_MOVE = 0x0001
INPUT_KEYBOARD = 1


class Win32Backend(InputBackend):
    """原生 win32 输入后端（SendInput/SetCursorPos）。UIPI 拦截时 success=False, error=uipi_block。"""

    name = "win32"

    def __init__(self):
        self.user32 = ctypes.windll.user32
        self.user32.SetProcessDPIAware()
        # 输入状态快照——当前按住的键（异常/停止时可全量释放）
        self.pressed_keys = set()

    def release_all(self):
        """释放全部按住键（紧急停止/异常退出兜底）。"""
        for key in list(self.pressed_keys):
            # A-W4 修复：_send_kbd_input 在 SendInput 失败时 return False
            # 不抛异常——原 except 子句永远不会触发，keybd_event 兜底失效。
            # 改为检查返回值：False 时回退 keybd_event。
            sent = True
            try:
                if not self._send_kbd_input(key, KEYEVENTF_KEYUP):
                    sent = False
            except Exception:
                sent = False
            if not sent:
                # 兜底：SendInput 失败时回退 keybd_event（任何一条释放成功即可）
                try:
                    self.user32.keybd_event(key, 0, KEYEVENTF_KEYUP, 0)
                except Exception:
                    pass
        self.pressed_keys.clear()

    @staticmethod
    def _send_input(inputs):
        ret = ctypes.windll.user32.SendInput(
            len(inputs), ctypes.byref(inputs), ctypes.sizeof(inputs[0]))
        if ret != len(inputs):
            return False
        return True

    def click(self, x, y):
        r = self.move(x, y)
        if not r.success:
            return r
        time.sleep(0.05)

        class MOUSEINPUT(ctypes.Structure):
            # dwExtraInfo 必须 ULONG_PTR（64 位下 c_size_t）——c_ulong 会致
            # 结构大小错误，SendInput 拒绝（管理员下 move 成功 click 失败的根因）
            _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong),
                        ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                        ("dwExtraInfo", ctypes.c_size_t)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", ctypes.c_ulong), ("mi", MOUSEINPUT)]

        inputs = (INPUT * 2)()
        inputs[0].type = 0
        inputs[0].mi.dwFlags = MOUSEEVENTF_LEFTDOWN
        inputs[1].type = 0
        inputs[1].mi.dwFlags = MOUSEEVENTF_LEFTUP
        if not self._send_input(inputs):
            return InputResult(success=False, action="click", backend=self.name,
                               error="uipi_block: SendInput 被拒绝（需要管理员权限）")
        return InputResult(success=True, action="click", backend=self.name)

    def click_hold(self, x, y, duration):
        """长按点击：移动到 (x,y) → 按住 duration 秒 → 释放（长按回放用）。"""
        r = self.move(x, y)
        if not r.success:
            return r
        time.sleep(0.05)
        if not self.click_down(x, y).success:
            return InputResult(success=False, action="click_hold",
                               backend=self.name,
                               error="uipi_block: SendInput 被拒绝（需要管理员权限）")
        time.sleep(max(0.0, float(duration or 0)))
        up = self.click_up()
        if not up.success:
            return InputResult(success=False, action="click_hold",
                               backend=self.name, error=up.error)
        return InputResult(success=True, action="click_hold", backend=self.name)

    def click_down(self, x, y):
        """移动到 (x,y) 并按住左键（长按回放分段用——可中断）。"""
        r = self.move(x, y)
        if not r.success:
            return r
        time.sleep(0.05)

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                        ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                        ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", ctypes.c_ulong), ("mi", MOUSEINPUT)]

        # 不能用 (INPUT*1)(inp)——Structure 实例
        # 被当 sequence（len=2）→ TypeError。仿 click 用 (INPUT*1) 逐字段
        inputs = (INPUT * 1)()
        inputs[0].type = 0
        inputs[0].mi.dwFlags = MOUSEEVENTF_LEFTDOWN
        if not self._send_input(inputs):
            return InputResult(success=False, action="click_down",
                               backend=self.name,
                               error="uipi_block: SendInput 被拒绝（需要管理员权限）")
        return InputResult(success=True, action="click_down", backend=self.name)

    def click_up(self):
        """释放左键（长按回放分段用）。"""
        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                        ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                        ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", ctypes.c_ulong), ("mi", MOUSEINPUT)]

        inputs = (INPUT * 1)()
        inputs[0].type = 0
        inputs[0].mi.dwFlags = MOUSEEVENTF_LEFTUP
        if not self._send_input(inputs):
            return InputResult(success=False, action="click_up",
                               backend=self.name,
                               error="uipi_block: SendInput 被拒绝（需要管理员权限）")
        return InputResult(success=True, action="click_up", backend=self.name)

    def move(self, x, y):
        if not self.user32.SetCursorPos(int(x), int(y)):
            return InputResult(success=False, action="move", backend=self.name,
                               error="SetCursorPos 失败")
        return InputResult(success=True, action="move", backend=self.name)

    # 单次 mouse_event 位移上限（像素）——超过时分批发送。
    # 地图 参考值 ±30（mouse_event.py:246-249），游戏指针锁定模式下
    # 单次增量过大可能丢失焦点或被系统钳制。
    _MOVE_BATCH_LIMIT = 30

    def relative_move(self, dx, dy, multi_num=1.0):
        """相对鼠标位移（视角回放用）——mouse_event 增量事件。

        与 move(x, y) 绝对 SetCursorPos 不同：游戏指针锁定模式只认
        MOUSEEVENTF_MOVE 增量事件（绝对位置被忽略——0.6.0 视角修复实锤）。
        暴露给 replayer，避免它直接 ctypes.windll.user32.mouse_event
        绕过 backend 协议（CLAUDE.md 架构：所有输入走 backend）。

        P0-11（t28）：multi_num 校准系数 + 单次上限分批。
        - multi_num=1.0 保持原语义（不缩放）；>1.0 放大位移，<1.0 缩小。
        - 单次位移超过 ±30 像素时自动分批（避免指针锁定丢失）。
        - detail.batches 记录实际发送批次数量（测试可断言）。

        Args:
            dx: 期望水平位移（像素）。
            dy: 期望垂直位移（像素）。
            multi_num: 视角校准系数（默认 1.0 = 不缩放）。

        Returns:
            InputResult，detail 含 batches（批次总数）与 total_dx/total_dy
            （校准后总位移，供 replayer 调试）。
        """
        limit = self._MOVE_BATCH_LIMIT

        # 应用校准系数
        total_dx = int(dx * multi_num)
        total_dy = int(dy * multi_num)

        # 零位移短路（避免空 mouse_event 调用）
        if total_dx == 0 and total_dy == 0:
            return InputResult(success=True, action="relative_move",
                               backend=self.name,
                               detail={"batches": 0})

        batches = 0
        while True:
            # 本批次发送量（钳制到 ±limit）
            batch_dx = max(-limit, min(limit, total_dx))
            batch_dy = max(-limit, min(limit, total_dy))

            try:
                ctypes.windll.user32.mouse_event(
                    MOUSEEVENTF_MOVE, batch_dx, batch_dy, 0, 0)
            except Exception as e:
                return InputResult(success=False, action="relative_move",
                                   backend=self.name,
                                   error=f"mouse_event 失败: {e}")

            batches += 1
            total_dx -= batch_dx
            total_dy -= batch_dy

            if total_dx == 0 and total_dy == 0:
                break

        return InputResult(success=True, action="relative_move",
                           backend=self.name,
                           detail={"batches": batches,
                                   "total_dx": int(dx * multi_num),
                                   "total_dy": int(dy * multi_num)})

    @staticmethod
    def minimap_region(window_rect, offset=(125, 136, -1765, -914)):
        """P1-01：计算小地图箭头区域坐标（供测角模块使用）。

        地图 的 take_screenshot_arrow 用 offset=(125,136,-1765,-914)
        从整窗截图定位小地图箭头区域。offset 语义：
        (left_from_left, top_from_top, right_from_right, bottom_from_bottom)
        其中后两个为负值表示从右/下边缘向内偏移。

        Args:
            window_rect: (left, top, right, bottom) 窗口客户区矩形。
            offset: 四元组 (左偏移, 上偏移, 右偏移, 下偏移)。
                    正值=从左/上边缘起，负值=从右/下边缘起。

        Returns:
            (x1, y1, x2, y2) 裁剪区域坐标（绝对屏幕坐标）。
        """
        left, top, right, bottom = window_rect
        x1 = left + offset[0]
        y1 = top + offset[1]
        x2 = right + offset[2]   # offset[2] 为负，等价 right - |offset[2]|
        y2 = bottom + offset[3]  # offset[3] 为负
        return (x1, y1, x2, y2)

    def press_key(self, key, wait_time=0.2):
        """原子化按下+等待+释放（兼容旧 API；内部调 key_down/key_up）。

        A3 修复：拆 key_down/key_up 后，press_key 退化为复合调用
        保留原语义（sleep 期间异常也释放—— 兜底），不破坏既有调用方
        （runtime/orchestrator.py、runtime/step_executor.py、local/input.py
        等 47 处 press_key 调用全部不动）。
        """
        down = self.key_down(key)
        if not down.success:
            return down
        # A-W7 修复：wait_time <= 0 短路——按完立即释放，跳过 sleep。
        # 原版仍 time.sleep(0) 浪费一次 syscall；零延迟场景下 caller 期望
        # "按下即释放"语义（replayer chord 段间间隔、Test 2b duration=0）。
        if wait_time is None or float(wait_time) <= 0.0:
            up = self.key_up(key)
            return up if not up.success else InputResult(
                success=True, action="press_key", backend=self.name)
        try:
            time.sleep(float(wait_time))
        finally:
            up = self.key_up(key)
            # 失败也不覆盖 down.success（按下成功、释放失败属异常）
        return InputResult(success=True, action="press_key", backend=self.name)

    def release_key(self, key):
        """仅发送 keyup，不按 wait_time 等待（兼容旧 API）。"""
        # A3 修复：内部走 key_up 通道（SendInput KEYBDINPUT）。
        return self.key_up(key)

    def key_down(self, key):
        """仅发送 keydown（不 sleep、不释放）——组合键/长按修饰键回放用。

        A3 修复：拆出独立 API；replayer 组合键路径与 orchestrator
        长按修饰键路径可调本方法表达"按下 modifier→按下 key→抬起 key→
        抬起 modifier"。A5 修复：内部走 SendInput KEYBDINPUT 带 scan code
        （MapVirtualKey）+ 扩展键标志（KEYEVENTF_EXTENDEDKEY）。
        """
        vk, scan, is_extended = self._vk_scan(key)
        if vk is None:
            return InputResult(success=False, action="key_down", backend=self.name,
                               error=f"unknown_key:{key}")
        # A-W2 修复：删 `if scan: flags |= 0x0008`（与下方
        # _send_kbd_input 注释"优先 VK 模式"对齐——Windows 用 wVk 不用 wScan）。
        flags = 0
        if is_extended:
            flags |= KEYEVENTF_EXTENDEDKEY
        if not self._send_kbd_input(vk, flags, scan=scan):
            return InputResult(success=False, action="key_down", backend=self.name,
                               error="send_input_failed: SendInput KEYBDINPUT 被拒绝"
                                     "（需要管理员权限）")
        self.pressed_keys.add(vk)    # 按下状态登记
        return InputResult(success=True, action="key_down", backend=self.name)

    def key_up(self, key):
        """仅发送 keyup（不 sleep）——组合键/长按修饰键回放用。

        A3 修复：与 key_down 对偶；A5 修复：SendInput KEYBDINPUT 通道。
        """
        vk, scan, is_extended = self._vk_scan(key)
        if vk is None:
            return InputResult(success=False, action="key_up", backend=self.name,
                               error=f"unknown_key:{key}")
        flags = KEYEVENTF_KEYUP
        # A-W2 修复：删 SCANCODE 标志（VK 模式优先）
        if is_extended:
            flags |= KEYEVENTF_EXTENDEDKEY
        if not self._send_kbd_input(vk, flags, scan=scan):
            return InputResult(success=False, action="key_up", backend=self.name,
                               error="send_input_failed: SendInput KEYBDINPUT 被拒绝"
                                     "（需要管理员权限）")
        self.pressed_keys.discard(vk)    # 按下-释放状态对齐
        return InputResult(success=True, action="key_up", backend=self.name)

    # 内部：KEYBDINPUT 发送

    def _send_kbd_input(self, vk, flags, scan=0):
        """SendInput KEYBDINPUT 包装（ A5 修复）。

        结构 dwExtraInfo 必须 c_size_t（c_ulong 64 位下错位致 SendInput
        拒绝——见 CLAUDE.md）。KEYBDINPUT.wVk / wScan 按 flags 决定：
        KEYEVENTF_SCANCODE=0 时仅 wVk 有效，wScan 忽略；非零时仅 wScan
        有效，wVk 忽略。我们优先 VK 模式（flags 不带 SCANCODE），scan 仅
        记录为扩展键辅助信息（保持 VK 路径不破坏 DirectInput 兼容性）。
        """

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", ctypes.c_ushort),
                        ("wScan", ctypes.c_ushort),
                        ("dwFlags", ctypes.c_ulong),
                        ("time", ctypes.c_ulong),
                        ("dwExtraInfo", ctypes.c_size_t)]

        class _KbdINPUT(ctypes.Structure):
            _fields_ = [("type", ctypes.c_ulong),
                        ("ki", KEYBDINPUT)]

        inp = (_KbdINPUT * 1)()
        inp[0].type = INPUT_KEYBOARD
        inp[0].ki.wVk = vk
        inp[0].ki.wScan = scan
        inp[0].ki.dwFlags = flags
        inp[0].ki.time = 0
        inp[0].ki.dwExtraInfo = 0
        return self._send_input(inp)

    # 内部：VK / scan code 映射

    @staticmethod
    def _vk(key):
        """A4 修复：键名 → VK 码显式映射（消除单字符 alnum 兜底导致
        的 shift→S 之类误判）。覆盖：方向键/修饰键/功能键 F1-F12/小键盘
        numpad 0-9 + 运算符/常用符号（[, ], ;, ', \\, -, =, `, /, ., ,）。

        未知键返回 None（显式失败——上层 InputResult.success=False 兜底）。
        """
        return Win32Backend._vk_scan(key)[0]

    @staticmethod
    def _vk_scan(key):
        """键名 → (vk, scan_code, is_extended) 三元组（ A4/A5 修复）。

        scan_code = MapVirtualKeyW(vk, 0) 出的硬件扫描码；is_extended 标志
        来自 _EXTENDED_VKS 硬编码表（ A-ST1 修复：MapVirtualKeyW
        在不同 Windows 环境 MAPVK_VK_TO_VSC_EX 行为差异大，本环境
        `MapVirtualKeyW(vk, 1) & 0x100` 恒为 0，导致所有扩展键漏标
        KEYEVENTF_EXTENDEDKEY——右修饰键/方向键/编辑键/小键盘数字键
        全部被 Windows 当作主键处理）。

        return (vk, scan, is_extended)；vk=None 表示未知键（显式失败）。
        """
        # 主表（轮转 hot-path 缓存到闭包变量——避免每次重建 dict）
        VK = _VK_TABLE
        k = str(key).lower()
        vk = VK.get(k)
        # 单字符 alnum 兜底（保持与原 _vk 兼容——小写→大写）
        if vk is None and len(k) == 1 and k.isalnum():
            vk = ord(k.upper())
        if vk is None:
            return (None, 0, False)
        # scan code（MapVirtualKeyW 在 user32；无 ctypes 导出需用
        # winuser.MapVirtualKeyW——避免硬编码以保持 ABI 兼容）
        try:
            scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
        except Exception:
            scan = 0
        # 扩展键标志：硬编码表（ A-ST1 修复——见上方 docstring）
        is_extended = vk in _EXTENDED_VKS
        scan = int(scan or 0)
        # A-W3 修复：numpad_enter 与主回车共享 VK 0x0D，但硬件
        # 扫描码不同（主回车 0x1C / 小键盘回车 0xE01C = 扩展位+0x1C）。
        # 无 EXTENDEDKEY 标志时 Windows 视为同一键，无法区分。
        if k == "numpad_enter":
            is_extended = True
            scan = 0xE01C    # 扩展键扫描码约定：E0xx 与 KEYEVENTF_EXTENDEDKEY 配对
        return (vk, scan, is_extended)


# 模块级扩展键 VK 表（ A-ST1 修复）：
# Windows 上需发 KEYEVENTF_EXTENDEDKEY 标志的 VK 集合。
# 来源：MSDN Virtual-Key Codes + 硬件扫描码 E0xx 区域。
# 含义：SendInput 不带 EXTENDEDKEY 时，Windows 将这些键当主键区，
# 右修饰键、方向键、编辑键、小键盘数字键会与左侧混淆。
_EXTENDED_VKS = frozenset({
    0x21,    # VK_PRIOR (Page Up)
    0x22,    # VK_NEXT (Page Down)
    0x23,    # VK_END
    0x24,    # VK_HOME
    0x25,    # VK_LEFT
    0x26,    # VK_UP
    0x27,    # VK_RIGHT
    0x28,    # VK_DOWN
    0x2D,    # VK_INSERT
    0x2E,    # VK_DELETE
    0x60,    # VK_NUMPAD0 （ 修复 0.6.0 ）
    0x61,    # VK_NUMPAD1
    0x62,    # VK_NUMPAD2
    0x63,    # VK_NUMPAD3
    0x64,    # VK_NUMPAD4
    0x65,    # VK_NUMPAD5
    0x66,    # VK_NUMPAD6
    0x67,    # VK_NUMPAD7
    0x68,    # VK_NUMPAD8
    0x69,    # VK_NUMPAD9
    0x6A,    # VK_MULTIPLY (numpad *)
    0x6B,    # VK_ADD (numpad +)
    0x6F,    # VK_DIVIDE (numpad /)
    0x6D,    # VK_SUBTRACT (numpad -)
    0xA1,    # VK_RSHIFT
    0xA3,    # VK_RCONTROL
    0xA5,    # VK_RMENU (alt_r)
    0xC0,    # VK_OEM_3 (`) — 注：OEM 键是否扩展由硬件决定；保守列入
    # A-RC5 修复：删 0xA7/0xA8（VK_BROWSER_BACK/FORWARD）
    # 这两个 VK 在 _VK_TABLE 中未收录（白名单也不收），留着会触发
    # test_key_table_consistency.test_extended_vks_subset_of_vk_table
    # 失败。浏览器键不在游戏内使用，删除无副作用。
})

# 模块级 VK 表（按 Windows VK 码；扩到 A4 范围）
_VK_TABLE = {
    # 编辑 / 控制
    "esc": 0x1B, "escape": 0x1B,
    "tab": 0x09, "space": 0x20, "enter": 0x0D, "return": 0x0D,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
    "insert": 0x2D, "home": 0x24, "end": 0x23,
    "page_up": 0x21, "pageup": 0x21,
    "page_down": 0x22, "pagedown": 0x22,
    "caps_lock": 0x14, "capslock": 0x14,
    "num_lock": 0x90, "numlock": 0x90,
    "scroll_lock": 0x91, "scrolllock": 0x91,
    "print_screen": 0x2C, "printscreen": 0x2C,
    "pause": 0x13,
    # 修饰键（左右分开——recorder 侧 _key_name 可能产出 shift_l/shift_r）
    "shift": 0x10, "shift_l": 0xA0, "shift_r": 0xA1,
    "ctrl": 0x11, "ctrl_l": 0xA2, "ctrl_r": 0xA3,
    "alt": 0x12, "alt_l": 0xA4, "alt_r": 0xA5,
    "win": 0x5B, "win_l": 0x5B, "win_r": 0x5C,
    "menu": 0x5D,    # 右键菜单
    # 方向键
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    # 功能键 F1-F12
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
    "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
    "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    # 常用单字符（明确列出避免单字符 alnum 兜底）——绝大部分由 len=1 alnum
    # 兜底命中；下列用于 alnum 之外符号
    "[": 0xDB, "]": 0xDD, ";": 0xBA, "'": 0xDE, "\\": 0xDC,
    "-": 0xBD, "=": 0xBB, "`": 0xC0, "/": 0xBF,
    ".": 0xBE, ",": 0xBC,
    # A-RC2 修复：补 "+" 字符键（与 "=" 同 VK 0xBB；pynput 在
    # US 键盘 shift+`=` 产出 key.char = "+"）+ "_"（shift+`-`，0xBD）。
    # A-RC5 修复：补 "*"（pynput 产 char = "*"，与 numpad_multiply
    # 共享 VK 0x6A）+ "alt_gr"（国际键盘右 Alt = alt_r = 0xA5）。
    "+": 0xBB, "_": 0xBD, "*": 0x6A, "alt_gr": 0xA5,
    # 字母（与单字符 alnum 兜底等价；显式列出便于 whitelist 校验）
    "w": 0x57, "a": 0x41, "s": 0x53, "d": 0x44,
    "e": 0x45, "f": 0x46, "r": 0x52, "v": 0x56, "x": 0x58,
    "m": 0x4D,
    "q": 0x51, "t": 0x54, "y": 0x59, "u": 0x55, "i": 0x49, "o": 0x4F,
    "p": 0x50, "g": 0x47, "h": 0x48, "j": 0x4A, "k": 0x4B, "l": 0x4C,
    "z": 0x5A, "c": 0x43, "b": 0x42, "n": 0x4E,
    # 小键盘 numpad（numlock 关闭时为方向/Page；此处按 numlock 开映射）
    "numpad_0": 0x60, "numpad_1": 0x61, "numpad_2": 0x62, "numpad_3": 0x63,
    "numpad_4": 0x64, "numpad_5": 0x65, "numpad_6": 0x66, "numpad_7": 0x67,
    "numpad_8": 0x68, "numpad_9": 0x69,
    "numpad_multiply": 0x6A, "numpad_add": 0x6B, "numpad_separator": 0x6C,
    "numpad_subtract": 0x6D, "numpad_decimal": 0x6E,
    "numpad_divide": 0x6F, "numpad_enter": 0x0D,    # VK_RETURN 兼
}
