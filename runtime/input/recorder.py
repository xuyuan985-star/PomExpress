"""轨迹录制（借鉴 外部参考实现 record.py：按键按下/释放 + 时间戳 → JSON）。

录制内容：
- 键盘按键（WASD/交互键）：按下→释放，记录 {key, time_sleep, duration}
- 鼠标视角移动：增量事件 {mouse_dx, mouse_dy, time_sleep}
- 鼠标左键点击：{click, x, y（游戏窗口内相对坐标）, time_sleep}
停止（F10 或 stop）→ 保存 JSON 到 knowledge/trajectories/。

回放（replayer.py）按同格式重放——轨迹 = 玩家一次手动操作的全记录。
"""
import json
import logging
import time
from pathlib import Path
from config.settings import data_path

_log = logging.getLogger("runtime.input.recorder")

# 录制按键白名单（锄大地/宝箱跑图常用键 + A4 扩展）
#
# A4 修复：扩到覆盖：
# - 字母全键（whitelist 字母含 w/a/s/d + 之前漏的 q/t/y/u/i/o/p/g/h/j/k/l/z/c/b/n）
# - 功能键 F1-F12（星铁菜单/截图/帮助键）
# - 修饰键 shift_l/shift_r/ctrl_l/ctrl_r/alt_l/alt_r（recorder 之前
# 把 shift_l/shift_r 直接当 shift 合并，但 pynput 实际可能区分左右）
# - 小键盘 numpad 0-9（数字盘常用：星铁切换角色 / 切换技能）
# - 方向键 up/down/left/right（菜单导航/对话选项）
# - 常用符号 [, ], ;, ', -, =, `, /, \, .
# - 编辑键 tab/backspace/enter/delete/insert/home/end/page_up/page_down
#
# 安全默认：未列出的键（cmd/win、prtsc、power 等）仍被过滤——玩家不会
# 在游戏内用这些键；如需可继续按相同模式 append。
KEY_WHITELIST = {
    # 字母（whitelist 字母全部 26 — alnum 兜底在 _vk 已支持，但白名单
    # 是前置过滤，提前允许避免 _diag_filtered 噪音）
    "w", "a", "s", "d", "e", "f", "r", "v", "x",
    "q", "t", "y", "u", "i", "o", "p", "g", "h", "j", "k", "l",
    "z", "c", "b", "n", "m",
    # 数字键（主键 0-9）
    "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
    # 编辑 / 控制
    "esc", "escape", "tab", "space", "enter", "return",
    "backspace", "delete", "del", "insert",
    "home", "end", "page_up", "pageup", "page_down", "pagedown",
    "caps_lock", "capslock",
    # 修饰键（左右都开）
    "shift", "shift_l", "shift_r",
    "ctrl", "ctrl_l", "ctrl_r",
    "alt", "alt_l", "alt_r", "alt_gr",    # A-RC3：alt_gr（国际键盘右 Alt）
    # 方向键
    "up", "down", "left", "right",
    # 功能键（注意：f10 是紧急停止热键，见 RESERVED_KEYS，不在此列）
    "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f11", "f12",
    # 小键盘
    "numpad_0", "numpad_1", "numpad_2", "numpad_3", "numpad_4",
    "numpad_5", "numpad_6", "numpad_7", "numpad_8", "numpad_9",
    "numpad_add", "numpad_subtract", "numpad_multiply", "numpad_divide",
    # A-RC7 修复：numpad_enter 玩家可能在星铁用小键盘回车（部分
    # 快捷键）；whitelist 之前缺。回放端 A-W3 已加特殊路径
    # VK 0x0D 共用主回车，scan 0xE01C + KEYEVENTF_EXTENDEDKEY 区分。
    "numpad_enter",
    # 常用符号
    "[", "]", ";", "'", "-", "=", "`", "/", "\\", ".", ",",
    # A-RC2 修复：补 "+" 字符键（pynput 在 US 键盘 shift+`=` 产出
    # key.char = "+" / vk=0xBB，whitelist 漏掉 → 玩家在聊天/对话框按 +
    # 完全录不到）。同时补 "_"（pynput 也产 key.char = "_"）。
    # A-RC1 修复：补 "*"（小键盘 * 实际产 key.char = "*"）。
    "+", "_", "*",
}

# 保留键：程序自身使用，永不录入轨迹。
# F10 是全局紧急停止热键——若被录入轨迹，回放时会按下 F10 触发急停，
# 相当于「回放到这一步就把自己停掉」；录制时按 F10 结束录制也会把该次
# 按键写进轨迹。故在录制入口与回放入口双重拦截，不依赖白名单是否包含它。
RESERVED_KEYS = {"f10"}

# recorder.py 在 runtime/input/（depth 2）→ parent³=repo root。
TRAJ_DIR = data_path("knowledge") / "trajectories"

# 全局活动录制器（GUI 关闭时收尾——closeEvent 停录制防钩子残留）
_active_recorder = None


class TrajectoryRecorder:
    def __init__(self, game_hwnd=None, game_sensitivity=4, camera=None):
        self.game_hwnd = game_hwnd
        # 游戏内灵敏度（1-5，星穹铁道设置项）——录制时记入 JSON，回放换算/提示用
        self.game_sensitivity = game_sensitivity
        self.camera = camera
        self.events = []
        self._last_time = None
        self._key_down = {}
        self._mouse_down = None    # 鼠标按下状态（长按 duration 用）
        self._mouse_pos = None     # 鼠标上次位置（视角位移差分）
        self._view_acc = [0.0, 0.0]    # 视角小位移累积
        self._recording = False
        self._keyboard_listener = None
        self._mouse_listener = None
        self._started_at = None
        self._client_size = None    # 录制时游戏客户区 (w, h)——分辨率归一化基准
        self._event_hook = None    # 实时回调（HUD 显示用）——pynput 线程调用，须线程安全
        # 录制起始朝向（视角闭环用）。None 表示旧格式（回放时跳过回正）。
        self.initial_heading = None
        # 键盘诊断（0.6.0 排查：键盘 0 事件——区分钩子死 vs 白名单过滤）
        self._diag_keys = []        # 钩子收到的全部按键（含白名单外）
        self._diag_filtered = []    # 被白名单过滤的按键
        self._diag_releases = []    # 收到的 release（无对应 press）
        # A-RC6 修复：HUD 诊断防噪音——按"白名单外键种类变化"去重；
        # 配合 _diag_known_filtered 集合，新 key tag 立即 emit，同 key 类
        # s 内不重发（cmd/媒体键连按不刷屏），5s 后必重发一次（让累计
        # 计数可见）。_diag_last_t 控强制刷新窗口。
        self._diag_last_t = 0.0
        self._diag_known_filtered = set()

    def _diag_reset(self):
        self._diag_keys = []
        self._diag_filtered = []
        self._diag_releases = []
        self._diag_last_t = 0.0
        # A-RC6 修复：去重基础——上次发诊断时已知的白名单外键集合；
        # 新键入集合 → 立即 emit；同集合 + 5s 内不重发（避免 cmd/媒体
        # 键连按刷屏）；5s 过后必重发一次（保证后续新增 key_count 可见）。
        self._diag_known_filtered = set()

    def _diag_emit_debounced(self):
        """A-RC6 修复：诊断推送去重——按"白名单外键种类变化"判定。

        玩家按住 cmd/媒体键时 pynput 持续触发 press（OS 自动重复），
        每次都 emit 会让 HUD 刷屏，遮住真实事件。判定逻辑：
        - 当前 _diag_filtered 中出现新 key tag（如"wl:cmd"首次出现）
          → 立即 emit（新 key 类进 HUD，运维能看见）
        - 同 key 类连发：5s 内不重发；5s 后必重发一次（让累计计数可见）
        """
        cur_set = set(self._diag_filtered)
        new_keys = cur_set - self._diag_known_filtered
        now = time.time()
        if new_keys:
            self._diag_known_filtered = cur_set
            self._diag_last_t = now
            self._emit_hook({"type": "diag", "text": self._diag_report()})
            return
        if (now - self._diag_last_t) >= 5.0:
            self._diag_known_filtered = cur_set
            self._diag_last_t = now
            self._emit_hook({"type": "diag", "text": self._diag_report()})

    def _diag_report(self):
        """诊断摘要（HUD 显示）：钩子收到的按键 vs 白名单过滤 vs release 孤儿。"""
        if not self._diag_keys and not self._diag_releases:
            return "键盘钩子未收到任何按键！"
        parts = []
        if self._diag_keys:
            from collections import Counter
            c = Counter(self._diag_keys)
            parts.append("收到: " + ", ".join(f"{k}×{n}" for k, n in c.most_common(8)))
        if self._diag_filtered:
            # A-RC8 修复：filtered 列表里的键带 "wl:"/"vk:" 前缀
            # 分类——是 whitelist 漏录还是回放端 _VK_TABLE 不支持。区分后
            # 能定位"录不到"根因，避免误判全部为"白名单外"导致排查偏离。
            wl_miss = sorted({k.split(":", 1)[1] for k in self._diag_filtered
                              if k.startswith("wl:")})
            vk_miss = sorted({k.split(":", 1)[1] for k in self._diag_filtered
                              if k.startswith("vk:")})
            tag = []
            if wl_miss:
                tag.append(f"白名单外(可补): {wl_miss[:6]}")
            if vk_miss:
                tag.append(f"回放端未支持: {vk_miss[:6]}")
            if tag:
                parts.append("；".join(tag))
            else:
                # 兜底：旧格式（无 prefix）直接显示
                parts.append(f"白名单外: {sorted(set(self._diag_filtered))[:6]}")
        if self._diag_releases:
            parts.append(f"孤儿 release(无 press): {len(self._diag_releases)}")
        return "键盘: " + "；".join(parts)

    def set_event_hook(self, cb):
        """注册实时事件回调：每个事件记录后调用 cb(event_dict)。

        HUD 实时显示用——回调在 pynput 监听线程，实现必须线程安全
        （GameHudController.append_external 是线程安全入队）。
        """
        self._event_hook = cb

    def _emit_hook(self, event):
        if self._event_hook is not None:
            try:
                self._event_hook(event)
            except Exception:
                pass

    @property
    def recording(self):
        return self._recording

    def _client_rect(self):
        """游戏客户区（GetClientRect——不含边框，全屏=显示器客户区）。"""
        if not self.game_hwnd:
            return None
        import ctypes
        import ctypes.wintypes
        rect = ctypes.wintypes.RECT()
        if not ctypes.windll.user32.GetClientRect(self.game_hwnd,
                                                  ctypes.byref(rect)):
            return None
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w <= 0 or h <= 0:
            return None
        return (w, h)

    def start(self):
        """开始录制（3 秒后正式计数——给玩家切窗口时间）。

        pynput 未安装时 log warning 并返回 False（不崩）。
        """
        if self._recording:
            return False
        try:
            from pynput import keyboard, mouse
        except ImportError as e:
            _log.warning("pynput 未安装，无法录制: %s", e)
            return False
        self.events = []
        self._key_down = {}
        self._diag_reset()
        self._mouse_pos = None
        self._mouse_down = None    # .6.0 跨会话残留——上次按住时
        # 停止，下次录制首次 release 会配对旧状态产生幽灵点击
        self._view_acc = [0.0, 0.0]    # 视角小位移累积（连续慢移不丢）
        self._started_at = time.time() + 3.0
        self._last_time = self._started_at
        # 记录录制时的客户区尺寸——回放按当前尺寸归一化换算
        # （分辨率/全屏变化自适应——否则点击/视角全部偏移）
        self._client_size = self._client_rect()

        # 录制起始朝向：有相机时通过 get_angle 捕获，用于回放前视角回正。
        # 无相机或测角不可用时保持 None（旧格式，回放时跳过回正）。
        self.initial_heading = None
        if self.camera is not None:
            try:
                import numpy as np
                from runtime.angle.orientation import get_angle
                arrow_img = self.camera.take_arrow()
                # 构造完整截图（箭头放在自定义 ROI 位置，尺寸与 arrow_img 匹配）
                ah, aw = arrow_img.shape[:2]
                roi = (0, 0, aw, ah)
                screenshot = np.zeros((ah + 10, aw + 10, 3), dtype=np.uint8)
                screenshot[0:ah, 0:aw] = arrow_img
                result = get_angle(screenshot, roi=roi)
                if result.available:
                    self.initial_heading = result.angle
                    _log.info("录制起始朝向: %.1f°", result.angle)
            except Exception as e:
                _log.warning("录制起始朝向捕获失败: %s", e)

        self._keyboard_listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release)
        self._mouse_listener = mouse.Listener(
            on_move=self._on_move, on_click=self._on_click)
        self._keyboard_listener.start()
        self._mouse_listener.start()
        self._recording = True
        global _active_recorder
        _active_recorder = self
        return True

    def _on_press(self, key):
        if not self._recording:
            return
        t = time.time()
        if t < self._started_at:
            return
        k = self._key_name(key)
        if k in RESERVED_KEYS:
            # 控制键不入轨迹（录制结束时按的 F10 属此列）
            return
        # A-RC1 修复：pynput 在 Windows 实际产出走主键路径
        # numlock on 数字 → 字符 "0"-"9"（已 whitelisted）；
        # numlock on 运算符 → 字符 "+"/"-"/"*"/"/"（已 whitelisted via RC2）；
        # numlock off 方向/Page → Key.up/down/left/right/page_up/...（已 whitelisted）。
        # 因此 whitelist 中 numpad_* 14 项在生产永远不命中（dead code）
        # 保留作 _vk_scan 反查（未来 hand-crafted trajectory / keymap 工具用）。
        # 诊断：所有收到的按键都记录（区分钩子死 vs 白名单过滤）
        self._diag_keys.append(k)
        if k not in KEY_WHITELIST:
            # A-RC8 修复：分类过滤原因——回放端 _VK_TABLE 是否
            # 支持该键。两者皆无 → whitelist 漏录；whitelist 有但 VK 表
            # 无 → 回放端失同步。两类分别用不同 tag，HUD 排查更快。
            try:
                from runtime.input.win32_backend import _VK_TABLE as _VT
                _vk_ok = k in _VT or (len(k) == 1 and k.isalnum())
            except Exception:
                _vk_ok = False
            # 用 prefix 区分：'wl' = whitelist 漏；'vk' = whitelist 有但 VK 表无
            tag = "vk" if _vk_ok else "wl"
            self._diag_filtered.append(f"{tag}:{k}")
            self._diag_emit_debounced()
            return
        if k in self._key_down:
            return
        self._key_down[k] = t
        self._diag_emit_debounced()

    def _on_release(self, key):
        if not self._recording:
            return
        t = time.time()
        if t < self._started_at:
            return
        k = self._key_name(key)
        if k in RESERVED_KEYS:
            return
        # 诊断：release 无对应 press（钩子 press 丢失的证据）
        if k not in self._key_down:
            self._diag_releases.append(k)
            self._diag_emit_debounced()
            return
        down = self._key_down.pop(k)
        duration = round(t - down, 2)
        if duration < 0.05:
            duration = 0.05
        self.events.append({
            "key": k,
            "time_sleep": round(max(0.0, down - self._last_time), 2),
            "duration": duration,
        })
        # _last_time 表示「上一个事件的结束时刻」：按键事件的写入发生在释放时刻 t，
        # 故此处记录 t（而非按下时刻 down）。若记录 down，则长按键期间发生的事件
        # 会相对 down 计算，产生负值并把时长重复计入后续事件。
        self._last_time = max(self._last_time, t)
        self._emit_hook({"type": "key", "key": k, "duration": duration})

    def _on_move(self, x, y):
        if not self._recording:
            return
        t = time.time()
        if t < self._started_at:
            return
        if self._mouse_pos is None:
            self._mouse_pos = (x, y)
            return
        dx = x - self._mouse_pos[0]
        dy = y - self._mouse_pos[1]
        self._mouse_pos = (x, y)
        # 视角移动：小位移不丢弃——累积到阈值再记录（连续慢移鼠标
        # 每帧位移 <3px，原实现直接 return → 连续转视角完全录不到）
        self._view_acc[0] += dx
        self._view_acc[1] += dy
        if abs(self._view_acc[0]) < 3 and abs(self._view_acc[1]) < 3:
            return
        adx, ady = self._view_acc
        self._view_acc = [0.0, 0.0]
        # 归一化位移（相对客户区宽高）——分辨率/全屏变化时回放按当前尺寸换算
        w, h = self._client_size or (1, 1)
        self.events.append({
            "view_dx": round(adx / w, 4), "view_dy": round(ady / h, 4),
            "time_sleep": round(max(0.0, t - self._last_time), 2),
        })
        self._last_time = max(self._last_time, t)
        self._emit_hook({"type": "view",
                         "dx": round(adx / w, 4), "dy": round(ady / h, 4)})

    def _on_click(self, x, y, button, pressed):
        if not self._recording:
            return
        t = time.time()
        if t < self._started_at:
            return
        if str(button) != "Button.left":
            return
        if pressed:
            # 按下：记录位置与时间（release 时算 duration——支持长按）
            self._mouse_down = {"t": t, "x": x, "y": y}
            return
        # 释放：生成点击事件（带按住时长 duration——长按可回放）
        down = self._mouse_down
        self._mouse_down = None
        if down is None:
            return
        duration = round(t - down["t"], 2)
        if duration < 0.05:
            duration = 0.05
        # 归一化点击（客户区 0-1）——回放按当前客户区换算，分辨率自适应
        w, h = self._client_size or (1, 1)
        ox, oy = 0, 0
        if self.game_hwnd:
            import ctypes
            import ctypes.wintypes
            import win32gui
            # 客户区原点（屏幕绝对坐标）——鼠标 x 是屏幕坐标，须先减原点
            ox, oy = win32gui.ClientToScreen(self.game_hwnd, (0, 0))
            rect = ctypes.wintypes.RECT()
            if ctypes.windll.user32.GetClientRect(self.game_hwnd,
                                                  ctypes.byref(rect)):
                cw = rect.right - rect.left
                ch = rect.bottom - rect.top
                if cw > 0 and ch > 0:
                    w, h = cw, ch
        self.events.append({
            "click": True,
            "nx": round((down["x"] - ox) / w, 4),
            "ny": round((down["y"] - oy) / h, 4),
            "duration": duration,    # 按住时长（长按可回放）
            "time_sleep": round(max(0.0, down["t"] - self._last_time), 2),
        })
        # 同上：点击事件在释放时刻 t 写入，_last_time 记 t
        self._last_time = max(self._last_time, t)
        self._emit_hook({"type": "click",
                         "nx": round((down["x"] - ox) / w, 4),
                         "ny": round((down["y"] - oy) / h, 4),
                         "duration": duration})

    @staticmethod
    def _key_name(key):
        try:
            if hasattr(key, "char") and key.char:
                return key.char.lower()
            name = str(key).replace("Key.", "").lower()
            return name
        except Exception:
            return "?"

    def stop(self):
        """停止录制并返回事件列表。"""
        if not self._recording:
            return []
        self._recording = False
        try:
            # listener.stop 异常吞掉（录制停止不能被钩子故障打断）
            try:
                if self._keyboard_listener:
                    self._keyboard_listener.stop()
            except Exception:
                pass
            try:
                if self._mouse_listener:
                    self._mouse_listener.stop()
            except Exception:
                pass
            # .6.0 完善：等钩子线程退出（stop 异步——不 join 则事件可能
            # 在返回后被追加，回放读到的列表不完整）
            for _l in (self._keyboard_listener, self._mouse_listener):
                try:
                    if _l is not None and _l.is_alive():
                        _l.join(timeout=1.0)
                except Exception:
                    pass
        finally:
            # listener.stop 抛异常也保证注销（防僵尸引用）
            self._keyboard_listener = None
            self._mouse_listener = None
            global _active_recorder
            if _active_recorder is self:
                _active_recorder = None
        # 诊断摘要落日志（0.6.0 排查：键盘 0 事件——钩子死 vs 白名单）
        try:
            _log.warning("录制键盘诊断: %s", self._diag_report())
        except Exception:
            pass
        return self.events

    def save(self, name=None):
        """保存轨迹 JSON 到 knowledge/trajectories/。返回路径。

        默认命名：自定义-1、自定义-2…（按现有最大序号递增，可读可排序）。
        磁盘不可写/路径不存在/写中断时 log warning 并返回 None（不崩）。
        """
        if not self.events:
            return None
        try:
            TRAJ_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            _log.warning("轨迹目录创建失败 %s: %s", TRAJ_DIR, e)
            return None
        if name is None:
            # 默认命名规则：自定义-N（现有最大序号 + 1）
            max_n = 0
            for f in TRAJ_DIR.glob("自定义-*.json"):
                try:
                    n = int(f.stem.split("-")[1])
                    max_n = max(max_n, n)
                except Exception:
                    pass
            name = f"自定义-{max_n + 1}"
        path = TRAJ_DIR / f"{name}.json"
        payload = {
            "version": 1,
            "recorded_at": time.time(),
            "client_w": (self._client_size or (1920, 1080))[0],
            "client_h": (self._client_size or (1920, 1080))[1],
            # 游戏内灵敏度（1-5，星穹铁道设置项）——回放按比例换算视角位移
            "game_sensitivity": self.game_sensitivity,
            # 录制起始朝向（视角闭环回正用）。None 表示旧格式（回放时跳过回正）。
            "initial_heading": self.initial_heading,
            "events": self.events,
            "count": len(self.events),
        }
        try:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        except (OSError, IOError, json.JSONEncodeError) as e:
            _log.warning("轨迹保存失败 %s: %s: %s",
                         path, type(e).__name__, e)
            # 尝试清理可能写入的半成品文件
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass
            return None
        return path


def stop_active_recorder(save=True):
    """（S2-B）公共 API：停止模块级当前活动录制器（如有），可选保存轨迹。

    替代 main_window.closeEvent 内的 `runtime.input.recorder._active_recorder`
    私有 reach-in。返回 dict {stopped, saved_path, event_count} 或 None
    （无活动录制时）。
    """
    global _active_recorder
    rec = _active_recorder
    if rec is None:
        return None
    try:
        events = rec.stop()
    except Exception:
        # 即使 stop 异常也清空全局引用（防僵尸）
        _active_recorder = None
        return {"stopped": True, "saved_path": None, "event_count": 0}
    # 释放全局引用（recorder.stop 自身也会清，但兜底再清一次）
    if _active_recorder is rec:
        _active_recorder = None
    saved_path = None
    if save and events:
        try:
            saved_path = rec.save()
        except Exception:
            saved_path = None
    return {
        "stopped": True,
        "saved_path": saved_path,
        "event_count": len(events) if events else 0,
    }
