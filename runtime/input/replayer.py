"""轨迹回放（TrajectoryRecorder 的逆操作）：按轨迹 JSON 重放按键/视角/点击。

坐标归一化（分辨率/全屏自适应）：
- 点击：轨迹存客户区归一化 (nx, ny) → 回放按当前客户区 + 客户区原点换算
- 视角：轨迹存归一化位移 (view_dx, view_dy) → 按当前客户区尺寸换算像素
录制/回放分辨率不一致时：log 提示（轨迹按比例缩放，近似可用）。
"""
import json
import logging
import math
import time
from pathlib import Path

from runtime.input.win32_backend import Win32Backend

# 回放时跳过的控制键。F10 是全局紧急停止热键——历史轨迹可能把它录了进去，
# 回放按下 F10 会触发急停（回放到该步就把自己停掉）。
# 录制端已不再录入（见 recorder.RESERVED_KEYS），此处兼容旧数据。
_RESERVED_REPLAY_KEYS = {"f10"}

_log = logging.getLogger("runtime.input.replayer")


class TrajectoryReplayer:
    def __init__(self, game_hwnd=None, backend=None, speed=1.0,
                 sensitivity=None, camera=None, multi_num=1.0):
        self.game_hwnd = game_hwnd
        self.backend = backend or Win32Backend()
        self.speed = speed
        # 回放时游戏内灵敏度（None=与录制相同）。多用户环境：录制者灵敏度
        # 与他人不同时，按 录制/回放 比例换算视角像素位移（视角角度一致）
        self.sensitivity = sensitivity
        self.camera = camera
        self.multi_num = multi_num
        self.events = []
        self.meta = {}
        self._abort = None    # 当前回放的中止回调（长按分段检查用）
        self._view_remain = [0.0, 0.0]    # 视角小数像素累积（慢速不归零）

    def load(self, path):
        """加载轨迹 JSON。损坏/截断/结构异常时 log warning + 返回 0。"""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
            _log.warning("轨迹加载失败 %s: %s: %s", path, type(e).__name__, e)
            self.events = []
            self.meta = {}
            return 0
        if not isinstance(data, dict):
            _log.warning("轨迹 JSON 根节点不是 dict（实际 %s）：%s",
                         type(data).__name__, path)
            self.events = []
            self.meta = {}
            return 0
        events = data.get("events", [])
        if not isinstance(events, list):
            _log.warning("轨迹 events 不是列表（实际 %s）：%s",
                         type(events).__name__, path)
            events = []
        # 非 dict 事件标记（replay 循环中跳过并 log）
        bad_count = sum(1 for e in events if not isinstance(e, dict))
        if bad_count:
            _log.warning("轨迹含 %d/%d 个非 dict 事件（将被跳过）：%s",
                         bad_count, len(events), path)
        self.events = events
        self.meta = {k: v for k, v in data.items() if k not in ("events",)}
        # 海量事件提示（不阻断，仅可见）
        if len(self.events) > 10_000:
            _log.info("轨迹事件数较大（%d 条），回放时间较长", len(self.events))
        return len(self.events)

    def _client_geometry(self):
        """当前客户区 (原点x, 原点y, 宽, 高)——回放坐标换算基准。"""
        if not self.game_hwnd:
            return (0, 0, 1920, 1080)
        import ctypes
        import ctypes.wintypes
        import win32gui
        ox, oy = win32gui.ClientToScreen(self.game_hwnd, (0, 0))
        rect = ctypes.wintypes.RECT()
        if not ctypes.windll.user32.GetClientRect(self.game_hwnd,
                                                  ctypes.byref(rect)):
            return (ox, oy, 1920, 1080)
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w <= 0 or h <= 0:
            w, h = 1920, 1080
        return (ox, oy, w, h)

    def replay(self, abort_check=None, progress=None):
        """按轨迹重放。abort_check 可中断；progress 回调 (i, total)。"""
        self._abort = abort_check
        self._view_remain = [0.0, 0.0]    # 重置视角余量（多次回放不残留）
        ox, oy, cw, ch = self._client_geometry()
        # 分辨率差异提示（录制时尺寸存 meta.client_w/h）
        rw = self.meta.get("client_w")
        rh = self.meta.get("client_h")
        if rw and rh and (rw != cw or rh != ch):
            _log.warning(
                "回放分辨率与录制时不同（录制 %sx%s / 当前 %sx%s）——"
                "轨迹按比例缩放，点击/视角为近似值", rw, rh, cw, ch)
        # 视角闭环回正：有相机且有 initial_heading 时，在回放入口做一次性回正。
        # 旧轨迹（无 initial_heading）或无相机时回落开环路径（向后兼容）。
        if self.camera is not None and self.meta.get("initial_heading") is not None:
            self._do_reset_view()
        # 可见性：数值静默兜底计数（防刷屏）——每类兜底只累计次数，
        # 回放结束时一次性汇总 log，而不是每事件 warning（损坏轨迹可能
        # 每事件都触发 → 上万条日志）。零控制流/数值语义变化：非法值仍
        # 归 0，只是现在会汇总可见。
        _fallback_counts = {
            "time_sleep": 0,
            "duration": 0,
            "view_dx": 0,
            "view_dy": 0,
        }
        # 游戏内灵敏度换算：视角角度 = 像素位移 × 灵敏度。
        # 录制灵敏度≠回放灵敏度时按比例缩放视角像素位移，保证视角角度一致。
        # float 转换/除零防护——损坏 trajectory 的
        # game_sensitivity 可能是非数字或 0，不能炸回放。
        gs = self.meta.get("game_sensitivity")
        sens_factor = 1.0
        if gs and self.sensitivity:
            try:
                g_f = float(gs)
                s_f = float(self.sensitivity)
                if s_f != 0 and g_f != s_f:
                    # 负灵敏度无物理意义——log warning 并按 1:1 处理
                    if g_f <= 0 or s_f <= 0:
                        _log.warning(
                            "回放灵敏度非正值（录制 %s / 回放 %s），"
                            "跳过换算（视角位移 ×1.0）",
                            gs, self.sensitivity)
                    else:
                        sens_factor = g_f / s_f
                        _log.info(
                            "游戏内灵敏度换算：录制 %s → 回放 %s（视角位移 ×%.3f）",
                            gs, self.sensitivity, sens_factor)
            except (TypeError, ValueError):
                _log.warning(
                    "回放灵敏度非数字（录制 %r / 回放 %r），"
                    "跳过换算（视角位移 ×1.0）",
                    gs, self.sensitivity)
        # 视角累积合并（0.6.0 采样率修复； F1 修复）：连续微小移动
        # （录制 3px 粒度）合并到 ≥3px 步长即发送——原 8px 阈值比录制粒度
        # px 严苛太多，83.6% 视角事件被积压器吞掉（player 视角"没动静"）。
        # 阈值降到 3px 与录制粒度对齐；time_sleep 间隔 ≥0.1s（注释一致）
        # 强制 flush 分段。点击/按键/末尾 flush 兜底。
        _v_acc = [0.0, 0.0]
        for i, ev in enumerate(self.events):
            if abort_check and abort_check():
                return False
            if progress:
                progress(i, len(self.events))
            # 非 dict 事件跳过（load 已警告，此处静默跳过防崩）
            if not isinstance(ev, dict):
                continue
            # 分段等待（0.6.0 F10 急停审查）：time_sleep 可能数秒~数十秒，
            # 整段 sleep 期间 abort 无法生效——分 0.1s 段检查中止；
            # 长等待同时发心跳（S3：单事件长 sleep 期间 watchdog 无事件）
            # A- 损坏 trajectory 可能 time_sleep=99999
            # 让 replayer sleep 数十小时（即使有 abort 兜底，无 abort 上下文
            # 仍会跑完）。硬截断 600s + log warning，避免失控。
            # 非数字 time_sleep / speed=0 防护——损坏数据
            # 不能炸回放（TypeError/ZeroDivisionError）。
            try:
                _ts_raw = float(ev.get("time_sleep", 0) or 0)
            except (TypeError, ValueError):
                _ts_raw = 0.0
                _fallback_counts["time_sleep"] += 1    # 可见性
            _speed = self.speed if self.speed else 1.0
            if _ts_raw < 0:
                _log.warning(
                    "回放 time_sleep 为负值（%s），截断为 0（事件 #%d）",
                    _ts_raw, i)
            _raw_sleep = max(0.0, _ts_raw) / _speed
            _WAIT_CAP = 600.0    # 单事件最大 sleep 秒数（≈10 分钟）
            if _raw_sleep > _WAIT_CAP:
                _log.warning(
                    "回放 time_sleep %s 超过硬上限 %ss，自动截断（trajectory 可能损坏）",
                    _raw_sleep, _WAIT_CAP)
                _wait = _WAIT_CAP
            else:
                _wait = _raw_sleep
            _deadline = time.time() + _wait
            _last_beat = 0.0
            while time.time() < _deadline:
                if abort_check is not None and abort_check():
                    return False
                if progress is not None and time.time() - _last_beat > 1.0:
                    progress(i, len(self.events))
                    _last_beat = time.time()
                time.sleep(min(0.1, _deadline - time.time()))
            if "key" in ev:
                if str(ev["key"]).lower() in _RESERVED_REPLAY_KEYS:
                    _log.warning("跳过控制键（不执行）: %s", ev["key"])
                    continue
                if _v_acc[0] or _v_acc[1]:
                    # A- key 事件前 flush 用 force_flush=True
                    # 让余量 ≥0.5px 也能 round 发 ±1 像素（避免 int 截断到 0 丢光）
                    self._replay_view(_v_acc[0], _v_acc[1], force_flush=True)
                    _v_acc = [0.0, 0.0]
                self._replay_key(ev["key"], ev.get("duration", 0.1))
            elif "click" in ev:
                if _v_acc[0] or _v_acc[1]:
                    # A- click 事件前 flush 同上
                    self._replay_view(_v_acc[0], _v_acc[1], force_flush=True)
                    _v_acc = [0.0, 0.0]
                # 非数字 nx/ny 兜底 0（损坏轨迹不炸回放）
                # NaN/inf 显式检测
                # 原代码 max(0.0, min(1.0, NaN)) 因 Python min/max 语义
                # 静默落到 (1.0, 1.0) 右边界且无 warning（NaN < 0 和 NaN > 1
                # 都 False，越界检查也失效）。现在：NaN/inf 视为非有限值，
                # 按 fail-closed 兜底到 (0, 0) + 可见 warning。
                try:
                    _nx_raw = float(ev.get("nx", 0) or 0)
                    _ny_raw = float(ev.get("ny", 0) or 0)
                    if not (math.isfinite(_nx_raw) and math.isfinite(_ny_raw)):
                        _log.warning(
                            "回放坐标非有限值 nx=%r ny=%r（兜底 (0,0)——"
                            "轨迹可能损坏），事件 #%d",
                            _nx_raw, _ny_raw, i)
                        nx, ny = 0.0, 0.0
                        _fallback_counts.setdefault("coord_nonfinite", 0)
                        _fallback_counts["coord_nonfinite"] += 1
                    else:
                        nx = max(0.0, min(1.0, _nx_raw))
                        ny = max(0.0, min(1.0, _ny_raw))
                        if _nx_raw < 0 or _nx_raw > 1 or _ny_raw < 0 or _ny_raw > 1:
                            _log.warning(
                                "回放坐标越界 nx=%.4f ny=%.4f（截断到 [0,1]），事件 #%d",
                                _nx_raw, _ny_raw, i)
                except (TypeError, ValueError):
                    _log.warning(
                        "回放坐标非数字 nx=%r ny=%r，兜底 (0,0)，事件 #%d",
                        ev.get("nx"), ev.get("ny"), i)
                    nx, ny = 0.0, 0.0
                # 长按回放：duration>0.05 时按住再释放（录制支持长按后）
                try:
                    duration = float(ev.get("duration") or 0)
                except (TypeError, ValueError):
                    duration = 0.0
                    _fallback_counts["duration"] += 1    # 可见性
                if duration > 0.05:
                    self._replay_click_hold(ox + int(nx * cw),
                                            oy + int(ny * ch), duration)
                else:
                    self._replay_click(ox + int(nx * cw), oy + int(ny * ch))
            elif "view_dx" in ev:
                # 游戏内灵敏度换算（sens_factor=录制/回放——视角角度一致）
                # 非数字 view_dx/view_dy 按 0 处理（损坏轨迹
                # 不炸回放）；view_dy 缺失时视为 0（历史格式兼容）。
                try:
                    _vdx = float(ev.get("view_dx") or 0)
                except (TypeError, ValueError):
                    _vdx = 0.0
                    _fallback_counts["view_dx"] += 1    # 可见性
                try:
                    _vdy = float(ev.get("view_dy") or 0)
                except (TypeError, ValueError):
                    _vdy = 0.0
                    _fallback_counts["view_dy"] += 1    # 可见性
                _v_acc[0] += _vdx * cw * sens_factor
                _v_acc[1] += _vdy * ch * sens_factor
                # 达到显著步长（≥3px，对齐录制粒度）或独立动作（间隔 ≥0.1s
                # 且余量 ≥1px）时发送—— F1 修复：8px→3px 解决 83.6%
                # 视角事件被吞。 F2 修复：time_sleep 阈值 0.3→0.1s。
                # A- time_sleep 单独触发时同时要求余量 ≥1px，
                # 否则 int 截断发 0 像素反而丢帧（小累积+长间隔场景）。
                _ts = ev.get("time_sleep", 0) or 0
                try:
                    _ts_f = float(_ts)
                except (TypeError, ValueError):
                    _ts_f = 0.0
                _big_step = abs(_v_acc[0]) >= 3 or abs(_v_acc[1]) >= 3
                _long_pause = _ts_f >= 0.1 and (
                    abs(_v_acc[0]) >= 1 or abs(_v_acc[1]) >= 1)
                if _big_step or _long_pause:
                    self._replay_view(_v_acc[0], _v_acc[1])
                    _v_acc = [0.0, 0.0]
        # 末尾 flush 残留累积（ F2 修复：force_flush=True 让小余量
        # 也能 round 出 ±1 像素，避免末帧小位移被 int 截断到 0 而丢光）
        if _v_acc[0] or _v_acc[1]:
            self._replay_view(_v_acc[0], _v_acc[1], force_flush=True)
        # 可见性：数值静默兜底汇总（防刷屏——一次性 warning 而非逐条）
        if any(_fallback_counts.values()):
            _parts = ", ".join(
                f"{_k}×{_v}" for _k, _v in _fallback_counts.items() if _v > 0)
            _total = sum(_fallback_counts.values())
            _log.warning(
                "回放发现 %d 处非数字数值兜底（按 0 处理，轨迹可能损坏）：%s",
                _total, _parts)
        return True

    def _do_reset_view(self):
        """回放前一次性视角回正。

        调用 runtime/angle/reset_view.py 的 reset_view，将相机视角校正到
        与 initial_heading 一致。失败时 log warning 并继续（回落开环，不阻断回放）。
        """
        try:
            from runtime.angle.reset_view import reset_view
            from runtime.angle.synthetic import render_arrow

            initial = self.meta.get("initial_heading")
            if initial is None:
                return

            # 渲染 initial_heading 角度的箭头图作为 baseline
            baseline_arrow = render_arrow(initial)

            result = reset_view(
                camera=self.camera,
                multi_num=self.multi_num,
                baseline_arrow=baseline_arrow,
                max_iterations=4,
                timeout_seconds=5.0,
                tolerance=1.0,
                iteration_delay=0.6,
            )
            if result.ok:
                _log.info(
                    "视角回正成功（%d 轮，误差 %.2f°）",
                    result.iterations, result.final_angle_error)
            else:
                _log.warning(
                    "视角回正失败：%s（误差 %.2f°）——回落开环路径继续回放",
                    result.reason, result.final_angle_error)
        except Exception as e:
            _log.warning("视角回正异常：%s——回落开环路径继续回放", e)

    def _is_known_key(self, norm):
        """A- 判 norm 是否为已知单键——先查 Win32Backend
        的 _VK_TABLE（含 "+"=0xBB / "_"=0xBD 等字符键），未命中再试
        alnum 兜底。命中 True → 当单键处理；否则走 chord split。
        """
        # Win32Backend 公开 _VK_TABLE（_vk_scan 主表）；MockBackend 不
        # 关心具体 vk 但 key_down("+") 会经 InputResult 失败路径——这里
        # 仅做"是否应当走单键"的判定，不实际发送
        try:
            from runtime.input.win32_backend import _VK_TABLE
            if norm in _VK_TABLE:
                return True
        except Exception:
            pass
        # alnum 兜底（与 _vk_scan 单字符分支对齐）
        return len(norm) == 1 and norm.isalnum()

    def _replay_key(self, key, duration):
        """按住 duration 秒再释放（keyDown → sleep → keyUp）。

        A3 修复：直接走 backend.key_down / key_up 通道，不再走
        press_key——避免 press_key 内复合调用产生多余的 sleep 边界
        （duration=0 时仍会走完 down+up，逻辑更清晰）。组合键 chord 路径
        （修饰键+普通键）后续接入：上层在事件格式扩展时可直接在 _replay_key
        解析 chord 并顺序调 key_down/key_up。
        """
        # key 名标准化：pynput 可能产出 "Key.shift_l"（_key_name 已去
        # Key. 前缀）——这里 str(key) 后 .lower 兼容字符串/枚举
        norm = str(key).lower().replace("key.", "")
        # chord 解析（ A3 扩展点）+ A- 若 norm 本身是
        # 已知单键（VK_TABLE 收录如 "+"=0xBB），优先当单键——避免把 "+"、
        # "ctrl++e" 中字符键 "+" 误判成 chord 分隔符。先查 backend 已知
        # 键（含 VK_TABLE + alnum 兜底），命中则 parts=[norm]；否则按
        # chord split（多键组合）。
        if self._is_known_key(norm):
            parts = [norm]
        elif "+" in norm:
            parts = [p.strip() for p in norm.split("+") if p.strip()]
        else:
            parts = [norm]
        # A- 跟踪已按下的键；任意阶段异常都在 finally
        # 倒序释放（不丢卡键）——chord 第 2 个键 key_down 抛 AttributeError
        # 时，前面已按下的键绝不能卡住。released_so_far 避免正常释放
        # 路径后 finally 再 release 一次（否则单键被 key_up 两次）。
        pressed_so_far = []
        released_so_far = set()
        try:
            # 全按下 → 等待末位键 duration → 倒序抬起
            for p in parts:
                rd = self.backend.key_down(p)
                if rd is not None and not getattr(rd, "success", True):
                    _log.warning("回放 key_down 失败 %s: %s",
                                 p, getattr(rd, "error", "?"))
                else:
                    # 仅在 key_down 成功时才加入已按下列表（失败的不需要释放）
                    pressed_so_far.append(p)
            # 非数字 duration 按 0 处理（损坏轨迹不炸回放）
            try:
                _dur_f = float(duration) if duration else 0.0
            except (TypeError, ValueError):
                _dur_f = 0.0
            if _dur_f > 0:
                # 分段 sleep 兼容 abort（与 _replay_click_hold 同模式）
                # A- 损坏 trajectory duration=99999 截断 600s
                _DUR_CAP = 600.0
                _safe_dur = min(_dur_f, _DUR_CAP)
                if _dur_f > _DUR_CAP:
                    _log.warning(
                        "回放 key duration %ss 超过硬上限 %ss，自动截断",
                        _dur_f, _DUR_CAP)
                _deadline = time.time() + max(0.0, _safe_dur)
                while time.time() < _deadline:
                    if self._abort is not None and self._abort():
                        break
                    time.sleep(min(0.05, _deadline - time.time()))
            # 倒序释放（与按下顺序相反，符合键盘 chord 礼仪）
            for p in reversed(pressed_so_far):
                try:
                    ru = self.backend.key_up(p)
                    released_so_far.add(p)
                except Exception as e2:
                    _log.warning("回放 key_up 异常 %s: %s", p, e2)
                    continue
                if ru is not None and not getattr(ru, "success", True):
                    _log.warning("回放 key_up 失败 %s: %s",
                                 p, getattr(ru, "error", "?"))
        except Exception as e:
            _log.warning("回放按键异常 %s: %s", key, e)
        finally:
            # A- 兜底：异常路径（key_down 抛 / sleep 抛 / key_up 抛）
            # 也保证倒序释放 pressed_so_far——绝不能卡键。仅释放未释放过的
            # 键（released_so_far 已记录正常释放过的部分）。
            for p in reversed(pressed_so_far):
                if p in released_so_far:
                    continue
                try:
                    self.backend.key_up(p)
                except Exception:
                    pass

    def _replay_click(self, x, y):
        try:
            r = self.backend.click(int(x), int(y))
            if r is not None and not getattr(r, "success", True):
                _log.warning("回放点击失败 (%s,%s): %s",
                             x, y, getattr(r, "error", "?"))
        except Exception as e:
            _log.warning("回放点击异常 (%s,%s): %s", x, y, e)

    def _replay_click_hold(self, x, y, duration):
        """长按回放：按住 duration 秒再释放（分段检查中止）。"""
        # A- 损坏 trajectory duration 硬截断 600s
        _DUR_CAP = 600.0
        try:
            _safe_dur = min(float(duration or 0), _DUR_CAP)
            if float(duration or 0) > _DUR_CAP:
                _log.warning("回放 click_hold duration %ss 超过硬上限 %ss，自动截断",
                             float(duration or 0), _DUR_CAP)
        except Exception:
            _safe_dur = 0.0
        try:
            if hasattr(self.backend, "click_hold"):
                # 分段按住：0.1s 一段检查 abort（F10 急停长按也能断）
                if hasattr(self.backend, "click_down"):
                    self.backend.click_down(int(x), int(y))
                    _deadline = time.time() + max(0.0, _safe_dur)
                    while time.time() < _deadline:
                        if self._abort is not None and self._abort():
                            break
                        time.sleep(min(0.1, _deadline - time.time()))
                    r = self.backend.click_up()
                    if r is not None and not getattr(r, "success", True):
                        _log.warning("回放长按释放失败 (%s,%s): %s",
                                     x, y, getattr(r, "error", "?"))
                else:
                    r = self.backend.click_hold(int(x), int(y), _safe_dur)
                    if r is not None and not getattr(r, "success", True):
                        _log.warning("回放长按失败 (%s,%s): %s",
                                     x, y, getattr(r, "error", "?"))
            else:
                r = self.backend.click(int(x), int(y))
                if r is not None and not getattr(r, "success", True):
                    _log.warning("回放长按点击失败 (%s,%s): %s",
                                 x, y, getattr(r, "error", "?"))
                _deadline = time.time() + max(0.0, _safe_dur)
                while time.time() < _deadline:
                    if self._abort is not None and self._abort():
                        break
                    time.sleep(min(0.1, _deadline - time.time()))
        except Exception as e:
            _log.warning("回放长按异常 (%s,%s): %s", x, y, e)

    def _replay_view(self, dx, dy, force_flush=False):
        """视角相对位移 → 真相对移动事件（0.6.0 修复：外部参考实现 同款）。

        实锤链路：pynput Controller.move = 读当前位置+位移 → SetCursorPos
        （假相对，绝对跳变）→ 指针锁定游戏不认 → 视角不动；我们此前
        GetCursorPos+SetCursorPos 同样无效。游戏指针锁定模式只认
        mouse_event(MOUSEEVENTF_MOVE) 增量事件——外部参考实现 锄大地转视角
        即此实现（mouse_event.py:237-262）。

        小数像素累积（ F2 修复）：int 截断（不舍入）保留余量到下
        帧；force_flush=True 用于末尾 flush——余量 ≥0.5px 用 round 强制发
        一次 ±1（避免末帧小余量被 int 截断到 0 而整体丢帧）。
        """
        # A- Python round 是 banker's rounding（0.5→0、
        # .5→2、-0.5→0、-1.5→-2），末帧 ±0.5px 残留被静默吞掉。改用
        # half-away-from-zero（向远离 0 的方向舍入）：±0.5→±1，±1.5→±2。
        # 配合下方 flush 路径，确保 force_flush 始终产生非零像素输出。
        def _round_half_away(x):
            if x >= 0.0:
                return int(x + 0.5)
            return -int(-x + 0.5)
        try:
            self._view_remain[0] += dx
            self._view_remain[1] += dy
            if force_flush:
                # 末尾 flush：余量过半像素就强制 round 发 1 次，不丢末帧
                ix = _round_half_away(self._view_remain[0])
                iy = _round_half_away(self._view_remain[1])
                # flush 完把余量清零（无下帧承接）
                self._view_remain[0] = 0.0
                self._view_remain[1] = 0.0
            else:
                ix = int(self._view_remain[0])
                iy = int(self._view_remain[1])
                self._view_remain[0] -= ix
                self._view_remain[1] -= iy
            if not ix and not iy:
                return
            # A- 走 backend.relative_move 而非直接 ctypes
            # mouse_event——让 MockBackend dry_run 不污染 OS；符合 CLAUDE.md
            # 输入链 SendInputBackend → Win32Backend 架构。
            r = self.backend.relative_move(ix, iy)
            if r is not None and not getattr(r, "success", True):
                _log.warning("回放视角相对移动失败 (%d,%d): %s",
                             ix, iy, getattr(r, "error", "?"))
        except Exception as e:
            _log.warning("回放视角移动失败: %s", e)
