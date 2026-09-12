"""RealExecutor — 步骤执行器核心（拆单体后，原 step_executor.py 主体）。

- 常量/策略函数已拆至 runtime/step_executor/policy.py
- 决策层只产 ActionIntent（不携带坐标）；观测坐标经 ObservationStore
  进入执行层（ executor 不依赖 observer 模块），换算属于执行细节。
"""
import time
from collections import deque
from pathlib import Path
from config.settings import data_path

from runtime.action_intent import ActionIntent, ActionMethod, ActionType
from runtime.errors import (ErrorCode, PERMANENT_CODES, SUBCLASS_BY_CODE,
                            code_of)
from runtime.execution import ExecutionResult
from runtime.naturalness import NaturalnessPolicy
from runtime.observation_store import ObservationStore

# （行为恢复）：depth-1 文件 parent²=repo root
ROOT = Path(__file__).resolve().parent.parent

# 从新包导入已拆分的策略常量
from runtime.step_executor.policy import (
    INPUT_ACTIONS,
    OBS_MAX_AGE,
    OBS_MIN_CONFIDENCE,
    THRESHOLD_INTERACT_TEMPLATE,
    THRESHOLD_PORTAL,
    THRESHOLD_VERIFY_SIGNAL,
    THRESHOLD_CLICK_TEMPLATE_HIT,
    STEP_THRESHOLD_DEFAULT,
    subclass_for,
    retryable_for,
    recovery_for,
)


class RealExecutor:
    """真机执行器：基于 Local Driver（v0.12.1）。

决策层只产 ActionIntent（不携带坐标）；观测坐标经 ObservationStore
    进入执行层（ executor 不依赖 observer 模块），换算属于执行细节。
    """

    def __init__(self, pkg, bus=None, execution_id=None, natural_mode=True,
                 natural_seed=None, input_override=None, guard=None, abort_check=None,
                 min_action_interval=0.0):
        self.pkg = pkg
        self.bus = bus
        self.execution_id = execution_id
        self.naturalness = NaturalnessPolicy(enabled=natural_mode, seed=natural_seed)
        self.natural_mode = natural_mode
        self.obs_store = ObservationStore()
        self._driver = None
        self._input_override = input_override
        self.abort_check = abort_check
        self.min_action_interval = min_action_interval
        self._last_action_at = 0.0
        if guard is None:
            from runtime.guards.action_guard import ActionGuard
            guard = ActionGuard(strict=False)
        self.guard = guard
        self._entity_templates = None
        self._recent_events = deque(maxlen=100)
        self._method_handlers = {
            ActionMethod.TEMPLATE.value: self._execute_template,
        }
        self._core_methods = frozenset(self._method_handlers)

    @property
    def input(self):
        if self._input_override is not None:
            return self._input_override
        return self.driver.input

    def set_input_backend(self, backend):
        from runtime.input.base import InputBackendProtocol
        if backend is not None and not isinstance(backend, InputBackendProtocol):
            raise TypeError(f"输入后端未实现 InputBackendProtocol: {type(backend).__name__}")
        self._input_override = backend

    def register_method(self, name, handler):
        if name not in self._core_methods:
            raise ValueError(
                f"method 不在核心白名单: {name}（允许: {sorted(self._core_methods)}，"
                f"插件扩展须先审计进 ALLOWED_METHODS）")
        self._method_handlers[name] = handler

    @property
    def driver(self):
        if self._driver is None:
            from runtime.drivers.local import get_driver
            self._driver = get_driver()
        return self._driver

    def _emit(self, event_type, **kw):
        if self.bus is not None:
            from runtime.events.schema import make_event
            ev = make_event(event_type, self.execution_id, **kw)
            self.bus.publish(ev)
            self._recent_events.append(ev.id)
            return ev
        return None

    def _precondition_blocked(self, intent):
        if not intent.preconditions:
            return None
        return None

    def emergency_stop(self):
        try:
            self.input.press_key("esc", wait_time=0.3)
        except Exception:
            pass
        for k in ("w", "a", "s", "d", "shift"):
            try:
                self.input.release_key(k)
            except Exception:
                pass

    def entity_templates(self):
        if self._entity_templates is None:
            self._entity_templates = self.pkg.entity_templates()
        return self._entity_templates

    def resolve_template(self, entity_id):
        name = self.entity_templates().get(entity_id)
        if not name:
            return None
        name = Path(name).name
        if not name:
            return None
        path = self.pkg.templates_dir / name
        return str(path) if path.exists() else None

    def screenshot_path(self):
        # 截图落盘目录必须可写：打包后 __file__ 指向临时解包目录，故走 DATA_ROOT
        return self.driver.vision.screenshot_path(str(data_path("ingest/raw/frames/live")))

    def portal_transition(self, portal, wait_base, threshold=THRESHOLD_PORTAL, verify_timeout=10):
        trigger = portal["trigger"]
        result = self.interact_template(portal["id"], trigger["threshold"])
        if not result.success:
            return False
        wait = self.naturalness.transition_wait(wait_base)
        time.sleep(wait)
        vtmpl = trigger.get("verify_template") or "loading.png"
        if self.pkg.template_exists(vtmpl):
            return self.verify_signal(vtmpl, "vanished", verify_timeout)
        if trigger.get("allow_unverified_transition"):
            import logging
            logging.getLogger("runtime.step_executor").warning(
                "传送 %s 未验证（allow_unverified_transition=true 显式放行）", portal.get("id"))
            return True
        return ExecutionResult(
            success=False,
            error="portal_verify_template_missing",
            retryable=False,
            category="F2_VERIFY")

    def verify_signal(self, template, expected, timeout, threshold=THRESHOLD_VERIFY_SIGNAL,
                      abort_check=None):
        tpl = self._resolve_template_path(template)
        if tpl is None:
            return False
        vision = self.driver.vision
        if vision is None:
            return False
        # verify 三无分支：expected 语义归一——互动矩阵
        # （interaction_matrix.verify_expected）对战斗/破坏型目标产出
        # defeated/destroyed；它们与 vanished 同属"目标消失"语义，只有
        # present 是"目标出现"。原实现只认 vanished/present，矩阵生成的
        # workflow 用 defeated/destroyed 时 verify 必然超时失败。
        expect_absent = (expected != "present")
        deadline = time.time() + timeout
        delay = 0.2
        baseline_seen = None
        baseline_deadline = time.time() + min(2.0, timeout / 3)
        while time.time() < deadline:
            if abort_check and abort_check():
                return False
            found = vision.find_template(str(tpl), threshold) is not None
            if baseline_seen is None:
                if found:
                    baseline_seen = True
                elif time.time() > baseline_deadline:
                    baseline_seen = False
            if expect_absent:
                if baseline_seen is None:
                    pass
                elif not found:
                    if baseline_seen is False:
                        self._emit("verify_degraded",
                                   detail=f"verify:{template}:never_seen",
                                   context={"template": template,
                                            "reason": "signal never matched before vanish"
                                                      "（视频帧模板恒不中——验证降级）"})
                    return True
            elif found:    # expected == "present"
                return True
            time.sleep(delay)
            delay = min(delay * 1.5, 1.5)
        return False

    def _resolve_template_path(self, template):
        try:
            candidate = (self.pkg.templates_dir / template).resolve()
        except Exception:
            return None
        if self.pkg.templates_dir.resolve() not in candidate.parents:
            return None
        return candidate

    def map_transfer(self, portal, abort_check=None):
        import time
        steps = portal.get("steps") or []
        if not steps:
            return False
        auto_map_open = not (steps and isinstance(steps[0], dict)
                             and steps[0].get("action") == "map")
        if auto_map_open:
            hotkey = self._hotkey_map()
            r = self.input.press_key(hotkey, wait_time=1.2)
            if not r.success:
                return False
            if abort_check and abort_check():
                return False
        for step in steps:
            if not isinstance(step, dict):
                return False
            action = step.get("action") or "template"
            if action == "template":
                tpl_ref = step.get("template", "")
                threshold = step.get("threshold", STEP_THRESHOLD_DEFAULT)
                path = self._resolve_map_template(tpl_ref)
                if path is None:
                    if step.get("optional"):
                        continue
                    return False
                hit = self._click_template_hit(path, threshold)
                if not hit:
                    return False
                time.sleep(0.8)
                if abort_check and abort_check():
                    return False
            elif action == "map":
                hotkey = step.get("hotkey") or self._hotkey_map()
                r = self.input.press_key(hotkey, wait_time=1.2)
                if not r.success:
                    return False
                if abort_check and abort_check():
                    return False
            elif action == "orientation":
                tpl_ref = step.get("template", "")
                threshold = step.get("threshold", STEP_THRESHOLD_DEFAULT)
                path = self._resolve_map_template(tpl_ref)
                if path is None:
                    import logging
                    logging.getLogger("runtime.step_executor").warning(
                        "orientation 模板缺失 %s，跳过", tpl_ref)
                    continue
                hit = self._click_template_hit(path, threshold)
                if not hit and not step.get("optional", True):
                    return False
                time.sleep(0.6)
                if abort_check and abort_check():
                    return False
            elif action in ("region", "transfer_point", "transfer"):
                tpl_ref = step.get("template", "")
                threshold = step.get("threshold", STEP_THRESHOLD_DEFAULT)
                path = self._resolve_map_template(tpl_ref)
                if path is None:
                    return False
                hit = self._click_template_hit(path, threshold)
                if not hit:
                    return False
                time.sleep(step.get("delay", 0.8))
                if abort_check and abort_check():
                    return False
            elif action in ("space", "esc", "f", "e", "enter"):
                key = step.get("key", action)
                r = self.input.press_key(key, wait_time=float(step.get("wait", 0.5)))
                if not r.success:
                    return False
                if abort_check and abort_check():
                    return False
            elif action == "b":
                hotkey = step.get("hotkey") or self._hotkey_b()
                r = self.input.press_key(hotkey, wait_time=float(step.get("wait", 0.5)))
                if not r.success:
                    return False
                if abort_check and abort_check():
                    return False
            elif action == "await":
                sec = float(step.get("seconds", step.get("wait", 2.0)))
                _deadline = time.time() + sec
                while time.time() < _deadline:
                    if abort_check and abort_check():
                        return False
                    time.sleep(min(0.2, max(0.0, _deadline - time.time())))
            elif action == "sleep":
                sec = float(step.get("seconds", step.get("wait", 0.5)))
                time.sleep(sec)
                if abort_check and abort_check():
                    return False
            else:
                import logging
                logging.getLogger("runtime.step_executor").warning(
                    "map_transfer 未知 action=%s，跳过", action)
                continue
        load_wait = float(portal.get("load_wait", 8))
        self._wait_loading(load_wait, abort_check)
        return True

    def _hotkey_map(self):
        try:
            from runtime.platform.windows.game_launcher import game_setting
            v = game_setting("hotkey_map")
            if v:
                return str(v)
        except Exception:
            pass
        return "m"

    def _hotkey_b(self):
        try:
            from runtime.platform.windows.game_launcher import game_setting
            v = game_setting("hotkey_b")
            if v:
                return str(v)
        except Exception:
            pass
        return "b"

    def _resolve_map_template(self, template_ref):
        if template_ref.startswith("map:"):
            name = template_ref[len("map:"):]
            pic = (Path(__file__).resolve().parent.parent
                   / "assets" / "map_templates" / name)
            return str(pic) if pic.exists() else None
        return self._resolve_template_path(template_ref)

    def _click_template_hit(self, path, threshold=THRESHOLD_CLICK_TEMPLATE_HIT):
        from runtime.input.template_backend import TemplateMatcher
        try:
            tm = TemplateMatcher(threshold=threshold)
            hit = tm.locate(path)
        except Exception:
            return False
        if hit is None:
            return False
        _, cx, cy = hit
        r = self.input.click(cx, cy)
        return bool(r.success)

    def _wait_loading(self, seconds, abort_check=None):
        import time
        from runtime.pixel_diff import images_different
        vision = getattr(getattr(self, "driver", None), "vision", None)
        deadline = time.time() + seconds
        last = None
        stable_rounds = 0
        while time.time() < deadline:
            if abort_check and abort_check():
                return
            time.sleep(1.5)
            if vision is None:
                continue
            try:
                shot = vision.take_screenshot()
                cur = shot[0] if shot else None
            except Exception:
                cur = None
            if cur is not None and last is not None:
                changed, _ = images_different(last, cur)
                if not changed:
                    stable_rounds += 1
                    if stable_rounds >= 2:
                        return
                else:
                    stable_rounds = 0
            if cur is not None:
                last = cur
