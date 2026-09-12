"""WorkflowOrchestrator — 编排器核心（拆单体后，原 orchestrator.py 主体）。

- EmergencyMonitor 随 run 启停；human_intervention → ABORT_REQUEST + 目标 interrupted。
- 失败（retry 用尽）→ EVENT_INTERRUPTED → 一次 recovery → 仍败 → fail_recorded + target failed。
- 只重试 retryable 失败（模板缺/低置信/非幂等动作不重试）。
- 紧急介入先按 esc + 释放按键（防卡键），再中断。
- / TargetRecord 跟踪每目标生命周期；run_mission 返回 (results, completed)。
- 状态机联动语义见企划 v0.12.2 §2.2。
- SessionWatchdog/TargetRecord 已拆至 runtime/orchestrator/ 包。
"""
import time
from pathlib import Path
from config.settings import data_path, knowledge_root

# （行为恢复）：depth-1 文件 parent²=repo root
ROOT = Path(__file__).resolve().parent.parent

from runtime.action_intent import ActionType
from runtime.errors import ErrorCode
from runtime.events.schema import make_event
from runtime.execution import ExecutionResult
from runtime.observation import Observation
from runtime.planner import Planner
from runtime.state_machine import Event, State, StateMachine

# workflow 步骤类型白名单
STEP_TYPES = {"move", "interact", "verify", "portal",
              "trajectory"}
# 单目标恢复/重试尝试硬上限
MAX_TARGET_ATTEMPTS = 3

# already_opened 三无判定的模板检测阈值。
# .8/0.7 → 0.6：already_opened 是"跳过 interact"的判定，必须 fail-closed
# 弱匹配（0.6-0.79 置信）视为"图标仍在"→ 不判已开；只有确凿消失才算已开。
ALREADY_OPENED_TEMPLATE_THRESHOLD = 0.6
ALREADY_OPENED_MINIMAP_THRESHOLD = 0.6
# OCR 兜底关键词（step.ocr 由互动矩阵 verify_ocr 注入，合并判定）
ALREADY_OPENED_FALLBACK_OCR = ("宝箱", "丰富", "珍稀", "丰厚", "珍贵",
                               "扑满", "破坏")

# S10：watchdog 判定"执行卡死"的事件静默上限
WATCHDOG_STALL_SECONDS = 120

# 从新包导入已拆分的组件
from runtime.orchestrator.watchdog import SessionWatchdog
from runtime.orchestrator.records import TargetRecord


class WorkflowOrchestrator:
    def __init__(self, pkg, bus=None, execution_id=None, 
                 natural_mode=True, stop_check=None):
        self.pkg = pkg
        self.bus = bus
        self.execution_id = execution_id
        self.natural_mode = natural_mode
        self._executor = None
        self._machine = None
        self._monitor = None
        self._watchdog = None
        self._records = {}
        self._foreground_retried = False
        self._last_beat_time = None
        self.foreground_check = True
        self._stop_check = stop_check
        self.planner = Planner()
        self.observer = None
        self.battle_strategy = "auto"
        # 修2/修5：chest 完成状态 pkg_key（与 MissionController._pkg_key 一致
        # 知识目录 sha256 前 12 位）；map_runner 白名单/断点续跑配置
        self._pkg_key_cached = None
        self._map_runner_cfg = None

    @property
    def executor(self):
        if self._executor is None:
            from runtime.step_executor import RealExecutor
            import hashlib
            seed = int(hashlib.sha256(
                (self.execution_id or "default").encode()).hexdigest()[:8], 16)
            self._executor = RealExecutor(self.pkg, self.bus, self.execution_id,
                                          self.natural_mode, seed,
                                          abort_check=self._aborted)
        return self._executor

    def _emit(self, event_type, **kw):
        if self.bus is not None:
            self.bus.publish(make_event(event_type, self.execution_id, **kw))

    # Emergency

    def start_emergency(self):
        try:
            from runtime.drivers.local.window import find_game_window
            from runtime.safety import EmergencyMonitor
            game = find_game_window()
            if game is None:
                return
            self._monitor = EmergencyMonitor(self.bus, self.execution_id, game["hwnd"])
            self._monitor.start()
            self.executor.monitor = self._monitor
        except Exception:
            import logging
            logging.getLogger("runtime.orchestrator").exception(
                "EmergencyMonitor 启动失败——继续执行但无人机介入保护")
            self._monitor = None
            try:
                self._emit("fail_recorded",
                           detail="F3:emergency_monitor_failed",
                           context={"category": "F3", "target": None,
                                    "error": "emergency_monitor_failed"})
            except Exception:
                pass

    def start_watchdog(self):
        self._watchdog = SessionWatchdog(self.bus, self.execution_id)
        self._watchdog.start()

    def stop_emergency(self):
        if self._monitor is not None:
            self._monitor.stop()
            self._monitor = None
        if getattr(self, "_watchdog", None) is not None:
            self._watchdog.stop()
            self._watchdog = None

    # 主流程

    def _pkg_key(self):
        """知识包身份键——与 MissionController._pkg_key 一致（路径 sha256 前 12 位）。"""
        if self._pkg_key_cached is None:
            import hashlib
            root = getattr(self.pkg, "root", None) or knowledge_root() / "source" / "black_tower_test"
            self._pkg_key_cached = hashlib.sha256(str(root).encode()).hexdigest()[:12]
        return self._pkg_key_cached

    @staticmethod
    def _interaction_type_for(chest):
        """按 chest 数据映射到互动矩阵类型。

        DS 审计 ：显式 type/chest_type → rarity（chests.json 真实 schema）
        → verify_signal/template 线索推断 → 回落 normal（fail-closed）。
        rarity 优先于信号推断——显式声明永远胜过猜测；无线索回落 normal。
        """
        from runtime.interaction_matrix import (
            get_interaction_type, resolve_type_from_chest)
        chest = chest or {}
        rarity = chest.get("rarity") or ""
        if rarity:
            t = get_interaction_type(rarity)
            if t is not None:
                return t
        try:
            return resolve_type_from_chest(chest)
        except Exception:
            from runtime.interaction_matrix import INTERACTION_MATRIX
            return INTERACTION_MATRIX.get("normal")

    def _map_runner(self):
        """按 config.settings 的 MAP_ALLOWLIST/MAP_ALLOWLIST_MODE/MAP_FORBIDDEN 构造 MapRunner。"""
        if self._map_runner_cfg is None:
            from runtime.map_runner import MapRunner
            from config.settings import get as _cfg_get
            allowlist = None
            amode = False
            forbidden = None
            raw_allow = _cfg_get("MAP_ALLOWLIST", "") or ""
            if raw_allow.strip():
                allowlist = [s.strip() for s in raw_allow.split(",") if s.strip()]
            amode_raw = (_cfg_get("MAP_ALLOWLIST_MODE", "0") or "0").strip()
            try:
                amode = bool(int(amode_raw))
            except Exception:
                amode = amode_raw.lower() in ("1", "true", "yes", "on")
            raw_forbid = _cfg_get("MAP_FORBIDDEN", "") or ""
            if raw_forbid.strip():
                forbidden = [s.strip() for s in raw_forbid.split(",") if s.strip()]
            knowledge_dir = str(getattr(self.pkg, "root", ROOT))
            self._map_runner_cfg = MapRunner(
                knowledge_dir, allowlist=allowlist,
                allowlist_mode=amode, forbidden=forbidden)
        return self._map_runner_cfg

    def _chest_already_opened(self, step):
        """verify 首帧"三无"判定：模板无命中 + OCR 无宝箱关键词 + 小地图无图标 → 视为已开。


        - 阈值 0.8/0.7 → 0.6（ALREADY_OPENED_TEMPLATE_THRESHOLD 等常量）
          already_opened 是跳过判定，弱命中（0.6-0.79）按"图标仍在"处理（fail-closed）。
        - OCR 关键词并入 step.ocr（互动矩阵 verify_ocr 注入的专属词：
          机关/解谜/开关、战斗/胜利、任务/委托等）——只查兜底词会漏检。
        """
        executor = self.executor
        vision = getattr(getattr(executor, "driver", None), "vision", None)
        if vision is None:
            return False
        try:
            # . 模板无命中：signal 模板 locate 返回 None（宝箱图标消失）
            signal = step.get("signal") or ""
            template_hit = True    # 信号模板缺失时保守——按未消失处理
            if signal:
                tpl_name = signal + ".png"
                if not self.pkg.template_exists(tpl_name):
                    template_hit = True
                else:
                    from runtime.input.template_backend import TemplateMatcher
                    tpl_path = str(self.pkg.templates_dir / tpl_name)
                    hit = TemplateMatcher(
                        threshold=ALREADY_OPENED_TEMPLATE_THRESHOLD).locate(tpl_path)
                    template_hit = hit is not None
            if template_hit:
                return False
            # . OCR 无宝箱关键词：兜底词 ∪ step.ocr（互动矩阵注入）
            try:
                texts = [t for t, _ in vision.ocr_lines()]
            except Exception:
                # BUG-EXEC-003 修复 ：OCR 异常 → fail-closed（拒绝跳过）。
                # 原代码 texts=[] → joined="" → 无关键词命中 → 若模板/小地图
                # 也缺命中则返回 True（已开 → 跳过），这是 fail-OPEN——判不出
                # 是否已开时按"未开"处理 = 继续动作，正是 B1 修复要根除的风险。
                # 现在：OCR 失败 → 返回 False（不跳过），让后续步骤正常执行。
                import logging
                logging.getLogger("runtime.orchestrator").warning(
                    "_chest_already_opened: OCR 异常，fail-closed（拒绝跳过）")
                return False
            joined = "".join(texts)
            kws = set(ALREADY_OPENED_FALLBACK_OCR)
            for kw in (step.get("ocr") or []):
                if isinstance(kw, str) and kw:
                    kws.add(kw)
            for kw in kws:
                if kw in joined:
                    return False
            # . 小地图无图标
            minimap_cfg = step.get("minimap")
            minimap_hit = False
            if isinstance(minimap_cfg, dict) and minimap_cfg.get("template"):
                mtpl_name = minimap_cfg["template"]
                if self.pkg.template_exists(mtpl_name):
                    from runtime.input.template_backend import TemplateMatcher
                    mtpl_path = str(self.pkg.templates_dir / mtpl_name)
                    minimap_hit = (TemplateMatcher(
                        threshold=ALREADY_OPENED_MINIMAP_THRESHOLD).locate(mtpl_path)
                        is not None)
            if minimap_hit:
                return False
            return True
        except Exception:
            return False

    def run_target(self, target_id):
        if self._stop_check is not None and self._stop_check():
            return False
        record = self._records.get(target_id) or TargetRecord(target_id)
        self._records[target_id] = record
        record.status = "running"
        record.attempts += 1
        record.mark_start()
        try:
            return self._run_target_inner(target_id, record)
        except Exception:
            import logging
            logging.getLogger("runtime.orchestrator").exception(
                "run_target crashed: %s", target_id)
            record.status = "failed"
            record.last_error = "orchestrator_crash"
            record.category = "F1_EXEC"
            try:
                snap = {"state": self._machine.state.name
                        if self._machine is not None else None,
                        "target": target_id}
                import json as _json
                from pathlib import Path as _P
                crash_dir = data_path("failure_reports") / "orchestrator_crash"
                crash_dir.mkdir(parents=True, exist_ok=True)
                (crash_dir / f"{target_id}_{int(time.time() * 1000)}.json").write_text(
                    _json.dumps(snap, ensure_ascii=False, indent=2),
                    encoding="utf-8")
            except Exception:
                pass
            self._emit("fail_recorded",
                       detail=f"F1_EXEC:crash:{target_id}",
                       context={"category": "F1_EXEC", "target": target_id,
                                "error": "orchestrator_crash"})
            self._emit("target_progress",
                       context={"target": target_id, "status": "failed",
                                "reason": "orchestrator_crash", "category": "F1_EXEC"})
            if self._machine is not None and self._machine.state not in (State.DONE, State.ABORT):
                self._machine.on(Event.ABORT_REQUEST, "orchestrator crash")
            self._interrupted(target_id)
            return False
        finally:
            record.mark_finish()

    def _run_target_inner(self, target_id, record):
        wf = self.pkg.workflow(target_id)
        if wf is None:
            from pathlib import Path as _P
            traj_dir = data_path("knowledge") / "trajectories"
            if (traj_dir / f"{target_id}.json").exists():
                wf = {"target_id": target_id,
                      "steps": [{"type": "trajectory",
                                 "file": f"{target_id}.json"}]}
            else:
                record.status = "failed"
                record.last_error = "workflow_not_found"
                record.category = "F3"
                self._emit("fail_recorded",
                           detail=f"F3:no_workflow:{target_id}",
                           context={"category": "F3", "target": target_id,
                                    "error": "workflow_not_found"})
                return False
        steps = wf.get("steps", [])
        # BUG-EXEC-002 修复 ：非 dict step 防护——原代码 s.get("type")
        # 对非 dict（None / str / list）抛 AttributeError。现加 isinstance 检查：
        # 非 dict step 被跳过（不当 trajectory 处理），不崩栈。
        if not any(isinstance(s, dict) and s.get("type") == "trajectory" for s in steps):
            for chest in (self.pkg.chests or []):
                if chest.get("id") == target_id and chest.get("trajectory"):
                    steps = [{"type": "trajectory",
                              "file": chest["trajectory"]}] + steps
                    break

        # 修2：步骤分发前接 interaction_matrix 按 chest rarity 选策略
        chest_data = self.pkg.chest(target_id) if hasattr(self.pkg, "chest") else None
        if chest_data:
            itype = self._interaction_type_for(chest_data)
            if itype:
                for s in steps:
                    if s.get("type") == "interact" and "key" not in s:
                        if itype.combat:
                            # DS 审计 ：combat 键按矩阵定义（wrecker→fighting，
                            # destructible→e 普攻），不用硬编码 fighting。
                            s["key"] = itype.interact_key or "fighting"
                            if itype.id == "wrecker":
                                s["combat"] = True
                        elif itype.requires_mechanism:
                            s["requires_mechanism"] = itype.mechanism_type
                        else:
                            s["key"] = itype.interact_key
                    if s.get("type") == "verify":
                        if not s.get("signal") and itype.verify_signal:
                            s["signal"] = itype.verify_signal
                        if not s.get("ocr") and itype.verify_ocr:
                            s["ocr"] = list(itype.verify_ocr)
                        # DS 审计 ：expected 按矩阵定义补齐（wrecker→defeated、
                        # destructible→destroyed），与 _step_executor_core
                        # verify 三无分支的"消失"语义归一对齐。
                        if not s.get("expected") and itype.verify_expected:
                            s["expected"] = itype.verify_expected

        def logger(prev, new, action, reason):
            self._emit("state_changed", from_state=prev, to_state=new,
                       detail=reason, context={"target": target_id, "action": action})

        self._machine = StateMachine(self.execution_id, target_id, logger=logger)
        self._machine.on(Event.START, "orchestrator start")
        self._machine.on(Event.ROOM_MATCH, "fixed position (M1-A)")

        for idx, step in enumerate(steps):
            if self._stop_check is not None and self._stop_check():
                record.status = "failed"
                record.last_error = "user_stopped"
                self._emit("target_progress",
                           context={"target": target_id, "status": "failed",
                                    "reason": "user stopped", "category": "F3"})
                return False
            if self._emergency_paused():
                # BUG-EXEC-001 修复 ：早退路径补 failed 状态
                # 事件流（_human_interrupted → target_progress status=failed）
                # 与 record.status 此前矛盾（record 仍 "running"）。
                record.status = "failed"
                record.last_error = "human_interrupt"
                self._human_interrupted(target_id)
                return False
            if self._stall_detected():
                record.status = "failed"
                record.last_error = "stall_detected"
                self._stall_abort(target_id)
                return False
            if not self._handle_battle_if_needed():
                # BUG-EXEC-001 修复：battle_unresolved 早退也补 failed 状态
                record.status = "failed"
                record.last_error = "battle_unresolved"
                self._emit("target_progress",
                           context={"target": target_id, "status": "failed",
                                    "reason": "battle_unresolved",
                                    "category": "F4_VISION"})
                self._interrupted(target_id)
                return False
            window_problem = self._window_lost()
            if window_problem == "window_not_foreground" and not self._foreground_retried:
                try:
                    from runtime.win_capture import set_foreground_with_retry
                    from runtime.drivers.local.window import find_game_window
                    game = find_game_window()
                    if game:
                        set_foreground_with_retry(game["hwnd"])
                except Exception:
                    pass
                self._foreground_retried = True
                window_problem = self._window_lost()
            if window_problem:
                self._system_failure(target_id, window_problem)
                return False
            result = self._run_step(step, idx, wf)
            if not result.success:
                retry = int(step.get("retry", 1) or 1)
                recovered = False
                if result.retryable:
                    for attempt in range(retry):
                        record.retry_count += 1
                        if not self._interruptible_wait(
                                min(2 ** attempt, 1.5)):
                            break
                        self._retry_transition()
                        result = self._run_step(step, idx, wf)
                        if result.success:
                            recovered = True
                            break
                if not recovered:
                    category = result.category
                    record.status = "failed"
                    record.last_error = result.error or "step_failed"
                    record.category = category
                    self._emit("fail_recorded",
                               detail=f"{category}:step:{step.get('type')}:{target_id}",
                               context={"category": category, "target": target_id,
                                        "step": step.get("type"),
                                        "error": result.error or "step_failed"})
                    self._emit("target_progress",
                               context={"target": target_id, "status": "failed",
                                        "reason": f"step[{idx}] {step.get('type')} failed",
                                        "category": category})
                    self._machine.on(Event.ABORT_REQUEST, "step failed")
                    self._interrupted(target_id)
                    return False
            self._progress(target_id, idx, len(steps))

        record.status = "succeeded"
        self._emit("target_progress", context={"target": target_id, "status": "done"})
        return True

    def _interruptible_wait(self, seconds):
        import time
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self._stop_check is not None and self._stop_check():
                return False
            if self._emergency_paused() or self._stall_detected():
                return False
            time.sleep(0.1)
        return True

    # 三大策略

    def _handle_battle_if_needed(self, max_rounds=6):
        executor = self.executor
        try:
            vision = executor.driver.vision
            for i in range(max_rounds):
                texts = []
                try:
                    for t, _ in vision.ocr_lines():
                        texts.append(t)
                except Exception:
                    return True
                joined = "".join(texts)
                in_battle = any(k in joined for k in ("回合", "波次", "战斗"))
                settled = any(k in joined for k in ("胜利", "失败", "挑战完成"))
                if not in_battle and not settled:
                    return True
                if in_battle:
                    strategy = self.battle_strategy
                    if strategy == "kill":
                        key = self._game_key("hotkey_technique", "e")
                        executor.input.press_key(key, wait_time=0.6)
                    else:
                        key = self._game_key("hotkey_auto_battle", "v")
                        executor.input.press_key(key, wait_time=0.6)
                    self._emit("state_changed", detail="battle:engage",
                               context={"action": "battle",
                                        "strategy": strategy, "key": key})
                if not self._interruptible_wait(3):
                    return False
            return False
        except Exception:
            return True

    def _game_key(self, key_name, default):
        try:
            from config.settings import game_setting
            v = game_setting(key_name)
            if v:
                return str(v)
        except Exception:
            pass
        return default

    def _check_map_locked(self):
        executor = self.executor
        try:
            vision = executor.driver.vision
            texts = []
            for t, _ in vision.ocr_lines():
                texts.append(t)
            joined = "".join(texts)
            for k in ("尚未解锁", "未解锁", "需完成", "解锁该区域",
                      "暂时无法", "无法传送"):
                if k in joined:
                    return k
        except Exception:
            pass
        return None

    def _check_mechanism(self):
        executor = self.executor
        try:
            vision = executor.driver.vision
            texts = []
            for t, _ in vision.ocr_lines():
                texts.append(t)
            joined = "".join(texts)
            for k in ("机关", "开关", "启动装置", "压力板", "解谜"):
                if k in joined:
                    return k
        except Exception:
            pass
        return None

    def _retry_transition(self):
        st = self._machine.state
        if st in (State.VERIFYING, State.INTERACTING):
            self._machine.on(Event.INTERACT_AGAIN, "retry")
        else:
            if st != State.EVENT_INTERRUPT:
                self._machine.on(Event.EVENT_INTERRUPTED, "step fail")
            self._machine.on(Event.RECOVER_OK, "retry")

    def _run_step(self, step, idx, wf):
        if not isinstance(step, dict):
            self._emit("fail_recorded", detail=f"F3:bad_step:{idx}",
                       context={"category": "F3", "target": wf.get("target_id"),
                                "error": f"bad_step:{type(step).__name__}"})
            return ExecutionResult(success=False, error=f"bad_step:{type(step).__name__}",
                                   retryable=False, category="F3")
        step_type = step.get("type")
        if step_type not in STEP_TYPES:
            self._emit("fail_recorded", detail=f"F3:unknown_step:{step_type}",
                       context={"category": "F3", "target": wf.get("target_id"),
                                "error": f"unknown_step:{step_type}"})
            return ExecutionResult(success=False, error=f"unknown_step:{step_type}",
                                   retryable=False, category="F3")
        if step_type == "move":
            return self._step_move(step)
        if step_type == "portal":
            return self._step_portal(step)
        if step_type == "trajectory":
            return self._step_trajectory(step)
        if step_type == "interact":
            return self._step_interact(step, wf)
        if step_type == "verify":
            return self._step_verify(step)
        return ExecutionResult(success=False, error=f"unhandled_step:{step_type}",
                               retryable=False, category="F3")

    def _step_move(self, step):
        lm_id = step.get("target")
        if not lm_id:
            return ExecutionResult(success=False, error="move:no_target",
                                   retryable=False, category="F3")
        return self.executor.interact_template(lm_id, threshold=0.8)

    def _step_trajectory(self, step):
        fname = step.get("file")
        if not fname:
            return ExecutionResult(success=False, error="trajectory:no_file",
                                   retryable=False, category="F3")
        from pathlib import Path
        traj_dir = data_path("knowledge") / "trajectories"
        tpath = traj_dir / fname
        if not tpath.exists():
            return ExecutionResult(success=False, error=f"trajectory:missing:{fname}",
                                   retryable=False, category="F3")
        try:
            from runtime.input.replayer import TrajectoryReplayer
            from runtime.drivers.local.window import find_game_window
            if self._stop_check is not None and self._stop_check():
                return ExecutionResult(success=False, error="trajectory:stopped",
                                       retryable=False, category="F1")
            game = find_game_window()
            hwnd = game["hwnd"] if game else None
            replay_sens = None
            try:
                from config.settings import get as _cfg_get
                _v = _cfg_get("REPLAY_SENSITIVITY", "")
                if _v:
                    f = float(_v)
                    if f > 0:
                        replay_sens = f
            except Exception:
                pass
            rp = TrajectoryReplayer(game_hwnd=hwnd, sensitivity=replay_sens)
            rp.load(str(tpath))
            monitor = getattr(self, "_monitor", None)
            if monitor is not None:
                try:
                    monitor.suspend_mouse()
                except Exception:
                    pass

            def _progress(i, total):
                if self._last_beat_time is None or \
                        time.monotonic() - self._last_beat_time >= 15:
                    self._last_beat_time = time.monotonic()
                    self._emit("state_changed",
                               detail=f"trajectory_progress:{i}/{total}",
                               context={"target": getattr(
                                   self, "_current_target", None),
                                   "file": fname})
            try:
                ok = rp.replay(
                    abort_check=(self._abort_condition
                                 if hasattr(self, "_abort_condition")
                                 else (self._stop_check
                                       if self._stop_check is not None
                                       else (lambda: False))),
                    progress=_progress)
            finally:
                if monitor is not None:
                    try:
                        monitor.resume_mouse()
                    except Exception:
                        pass
            if not ok:
                return ExecutionResult(success=False, error="trajectory:aborted",
                                       retryable=False, category="F1")
            self._emit("state_changed", detail="trajectory_done",
                       context={"target": getattr(self, "_current_target", None),
                                "file": fname})
            return ExecutionResult(success=True)
        except Exception as e:
            return ExecutionResult(success=False,
                                   error=f"trajectory:err:{type(e).__name__}:{e}",
                                   retryable=False, category="F1")

    def _abort_condition(self):
        if self._stop_check is not None and self._stop_check():
            return True
        return self._emergency_paused() or self._stall_detected()

    def _step_portal(self, step):
        pid = step.get("portal_id")
        portal = self.pkg.portal(pid) if pid else None
        if portal is None:
            return ExecutionResult(success=False, error=f"portal_not_found:{pid}",
                                   retryable=False, category="F3")
        if portal.get("kind") == "map_transfer":
            ok = self.executor.map_transfer(
                portal, abort_check=self._abort_condition)
        else:
            ok = self.executor.portal_transition(
                portal, wait_base=portal.get("load_wait", 8))
        if isinstance(ok, ExecutionResult):
            return ok
        if ok is True:
            locked = self._check_map_locked()
            if locked:
                self._emit("state_changed", detail=f"map_locked:{locked}",
                           context={"action": "map_locked",
                                    "portal": pid, "hint": locked})
                return ExecutionResult(
                    success=False, error=f"map_locked:{locked}",
                    retryable=False, category="F3")
            return ExecutionResult(success=True, category="F2")
        return ExecutionResult(success=False, error="portal_failed",
                               retryable=True, category="F2_VERIFY")

    def _step_interact(self, step, wf):
        tid = wf.get("target_id")
        # 修2：verify 首帧三无判 already_opened 直接跳 interact
        verify_step = None
        for s in wf.get("steps", []):
            if s.get("type") == "verify":
                verify_step = s
                break
        if verify_step and self._chest_already_opened(verify_step):
            self._emit("state_changed", detail="already_opened",
                       context={"action": "already_opened", "target": tid})
            self._machine.on(Event.TARGET_VISIBLE, "already opened")
            self._machine.on(Event.TARGET_VERIFIED, "already opened")
            return ExecutionResult(success=True, category="F2")
        mech = self._check_mechanism()
        if mech:
            self._emit("state_changed", detail=f"mechanism:{mech}",
                       context={"action": "mechanism", "target": tid,
                                "hint": mech})
            return ExecutionResult(
                success=False, error=f"requires_mechanism:{mech}",
                retryable=False, category="F3")
        result = self.executor.interact_template(
            tid, threshold=step.get("threshold", 0.8),
            max_retries=1)
        if not result.success:
            return result
        self._machine.on(Event.TARGET_VISIBLE, "target visible")
        self._machine.on(Event.TARGET_VERIFIED, "interact clicked")
        return result

    def _step_verify(self, step):
        signal = step.get("signal")
        if not signal:
            return ExecutionResult(
                success=False, error="verify_no_signal",
                retryable=False, category="F2_VERIFY")
        template = f"{signal}.png"
        if not self.pkg.template_exists(template):
            return ExecutionResult(
                success=False, error="verify_template_missing",
                retryable=False, category="F2_VERIFY")
        expected = step.get("expected", "vanished")
        timeout = step.get("timeout", 30)
        ok = self.executor.verify_signal(
            template, expected, timeout,
            abort_check=self._abort_condition)
        if ok:
            if self._machine.state == State.INTERACTING:
                self._machine.on(Event.INTERACT_OK, "verify passed")
        return ExecutionResult(
            success=ok, error=None if ok else "verify_timeout",
            retryable=not ok, category="F2")

    # 观察→规划→执行

    def observe_act(self, target, observer=None):
        obs = (observer or self.observer)
        if obs is None:
            return ExecutionResult(success=False, error="no_observer",
                                   retryable=False, category="F3")
        observation = obs.observe()
        if not isinstance(observation, Observation):
            return ExecutionResult(success=False, error="observer:bad_return",
                                   retryable=False, category="F3")
        intent = self.planner.decide(observation, target)
        if intent.action == ActionType.WAIT.value:
            self._emit("observation",
                       context={"observer": observation.source,
                                "target": target,
                                **observation.to_context()})
        return self.executor.execute_intent(intent)

    # 辅助

    def _progress(self, target_id, idx, total):
        self._emit("target_progress",
                   context={"target": target_id, "status": "running",
                            "step": idx + 1, "total": total})

    def _interrupted(self, target_id):
        self._emit("pause_requested", context={"reason": "target_failed",
                                               "detail": target_id})

    # 会话级

    def _ensure_foreground(self):
        if not self.foreground_check:
            return True
        try:
            from runtime.drivers.local.window import find_game_window
            from runtime.win_capture import set_foreground_with_retry
            game = find_game_window()
            if game is None:
                return False
            set_foreground_with_retry(game["hwnd"])
            import ctypes
            fg = ctypes.windll.user32.GetForegroundWindow()
            return fg == game["hwnd"]
        except Exception:
            return False

    def _replay_only(self, target_ids):
        from pathlib import Path as _P
        traj_dir = data_path("knowledge") / "trajectories"
        for tid in target_ids:
            wf = self.pkg.workflow(tid)
            if wf is not None:
                steps = wf.get("steps") or []
                if not steps or not all(
                        isinstance(s, dict) and s.get("type") == "trajectory"
                        for s in steps):
                    return False
            elif not (traj_dir / f"{tid}.json").exists():
                return False
        return True

    def run_mission(self, target_ids, emergency=True):
        if self._stop_check is not None and self._stop_check():
            self._emit("state_changed", detail="stopped_at_start",
                       context={"action": "user_stopped"})
            return {}, []
        if emergency:
            self.start_emergency()
        self.start_watchdog()
        try:
            replay_only = self._replay_only(target_ids)
            self._foreground_retried = False
            self._ensure_foreground()
            if not replay_only:
                self._ensure_game_ready()
            else:
                self._emit("state_changed",
                           detail="replay_only:skip_ready",
                           context={"action": "replay_only"})
            # 修2：跑图前接 map_runner 白名单过滤 + checkpoint 断点
            try:
                runner = self._map_runner()
                if runner.map_files:
                    self._emit("state_changed",
                               detail=f"map_runner:{len(runner.map_files)} maps",
                               context={"action": "map_runner_init"})
            except Exception:
                pass
            target_ids = list(dict.fromkeys(target_ids))
            results = {}
            for tid in target_ids:
                if self._aborted():
                    break
                if self._records.get(tid) and self._records[tid].attempts >= MAX_TARGET_ATTEMPTS:
                    record = self._records[tid]
                    record.status = "failed"
                    record.last_error = "max_attempts_exceeded"
                    record.category = "F3"
                    self._emit("fail_recorded",
                               detail=f"F3:max_attempts:{tid}",
                               context={"category": "F3", "target": tid,
                                        "error": "max_attempts_exceeded"})
                    self._emit("target_progress",
                               context={"target": tid, "status": "failed",
                                        "reason": "max_attempts_exceeded",
                                        "category": "F3"})
                    results[tid] = False
                    continue
                results[tid] = self.run_target(tid)
            # 修2：run_mission 收尾对成功目标调 chest_state.mark_done 落盘
            # DS 审计 ：失败不误记由 rec.status == "succeeded" 守卫 + GUI
            # main_window 双写前 is_done 幂等守卫共同保证；此处失败仅打日志
            # 落盘失败不污染 results/completed（收尾落盘是 best-effort）。
            try:
                from runtime import chest_state
                pkg_key = self._pkg_key()
                for tid in target_ids:
                    rec = self._records.get(tid)
                    if rec is not None and rec.status == "succeeded":
                        if chest_state.is_done(pkg_key, tid):
                            continue    # GUI 双写已落盘——幂等跳过
                        chest_data = self.pkg.chest(tid) if hasattr(self.pkg, "chest") else None
                        rarity = (chest_data or {}).get("rarity", "unknown")
                        room = (chest_data or {}).get("room")
                        chest_state.mark_done(pkg_key, tid, rarity=rarity,
                                              room=room, workflow=f"{tid}.json")
            except Exception:
                import logging
                logging.getLogger("runtime.orchestrator").warning(
                    "chest_state.mark_done 落盘失败", exc_info=True)
            completed = [tid for tid in target_ids
                         if self._records.get(tid) is not None
                         and self._records[tid].status == "succeeded"]
            return results, completed
        finally:
            self.stop_emergency()

    def session_summary(self):
        return {tid: r.to_dict() for tid, r in self._records.items()}

    # 系统状态

    _READY_POPUP = ("稍后再看", "前情提要", "跳过剧情")
    _READY_EXCEPTION = ("重新连接", "连接失败", "连接中断", "重试")
    _READY_BATTLE = ("波次", "回合", "胜利", "失败", "挑战者")
    _READY_MENU = ("设置", "返回标题", "退出游戏")

    def _ensure_game_ready(self, max_rounds=6):
        executor = self.executor
        try:
            for i in range(max_rounds):
                if self._stop_check is not None and self._stop_check():
                    raise RuntimeError("任务开始前被停止（界面归一化中）")
                self._emit("state_changed",
                           detail=f"ready:checking:{i + 1}/{max_rounds}",
                           context={"action": "ready_check"})
                try:
                    texts = [t for t, _ in executor.driver.vision.ocr_lines()]
                except AttributeError:
                    return
                except Exception:
                    return
                joined = "".join(texts)
                if any(k in joined for k in self._READY_POPUP):
                    self._emit("state_changed", detail="ready:popup_dismissed",
                               context={"action": "ready_popup"})
                    executor.click_text("稍后再看", max_retries=1)
                    self._interruptible_wait(2)
                    continue
                if any(k in joined for k in self._READY_EXCEPTION):
                    self._emit("state_changed", detail="ready:relogin",
                               context={"action": "ready_relogin"})
                    executor.click_text("确定", max_retries=1)
                    self._interruptible_wait(20)
                    continue
                if any(k in joined for k in self._READY_BATTLE):
                    self._emit("state_changed", detail="ready:battle_exit",
                               context={"action": "ready_battle"})
                    executor.input.press_key("esc", wait_time=2)
                    continue
                if any(k in joined for k in self._READY_MENU):
                    self._emit("state_changed", detail="ready:menu_exit",
                               context={"action": "ready_menu"})
                    executor.input.press_key("esc", wait_time=2)
                    continue
                self._emit("state_changed", detail="ready:ok",
                           context={"action": "ready_ok", "rounds": i + 1})
                return
            raise RuntimeError(
                "游戏画面未就绪：战斗/菜单/弹窗多次尝试未能退出（"
                f"{max_rounds} 轮）——请手动回到主城后重试")
        except RuntimeError:
            raise
        except Exception as e:
            import logging
            logging.getLogger("runtime.orchestrator").warning(
                "界面归一化异常（按就绪处理）: %s", e)

    def _window_lost(self):
        try:
            from runtime.drivers.local.window import find_game_window
            game = find_game_window()
            if game is None:
                return "window_lost"
            w, h = game["client"]
            if w < 500 or h < 500:
                return f"window_too_small:{w}x{h}"
            if self.foreground_check:
                import ctypes
                fg = ctypes.windll.user32.GetForegroundWindow()
                if fg != game["hwnd"]:
                    return "window_not_foreground"
        except Exception as e:
            return f"window_check_error:{type(e).__name__}"
        return None

    def _stall_detected(self):
        return getattr(self, "_watchdog", None) is not None and self._watchdog.tripped

    def _stall_abort(self, target_id):
        record = self._records.get(target_id)
        if record:
            record.status = "failed"
            record.last_error = "execution_stall"
            record.category = "F3"
        self._emit("target_progress",
                   context={"target": target_id, "status": "failed",
                            "reason": "execution_stall", "category": "F3"})
        if self._machine.state not in (State.DONE, State.ABORT):
            self._machine.on(Event.ABORT_REQUEST, "watchdog stall")
        self._interrupted(target_id)

    def _system_failure(self, target_id, reason):
        record = self._records.get(target_id)
        if record:
            record.status = "failed"
            record.last_error = reason
            record.category = "F3_WINDOW"
        self._emit("fail_recorded",
                   detail=f"F3_WINDOW:{reason}:{target_id}",
                   context={"category": "F3_WINDOW", "target": target_id,
                            "error": reason})
        self._emit("target_progress",
                   context={"target": target_id, "status": "failed",
                            "reason": reason, "category": "F3_WINDOW"})
        if self._machine.state not in (State.DONE, State.ABORT):
            self._machine.on(Event.ABORT_REQUEST, "system unavailable")
        self._interrupted(target_id)

    def _aborted(self):
        if self._stop_check is not None and self._stop_check():
            return True
        return self._machine is not None and self._machine.state == State.ABORT

    def _emergency_paused(self):
        return self._monitor is not None and self._monitor.is_paused()

    def _human_interrupted(self, target_id):
        try:
            self.executor.emergency_stop()
        except Exception:
            import logging
            logging.getLogger("runtime.orchestrator").critical(
                "EMERGENCY STOP 失败——按键/鼠标可能未释放！", exc_info=True)
            try:
                backend = getattr(self.executor.input, "backend", None) \
                    or getattr(self.executor.input, "backends", None)
                if hasattr(self.executor.input, "release_all"):
                    self.executor.input.release_all()
            except Exception:
                pass
        self._emit("target_progress",
                   context={"target": target_id, "status": "failed",
                            "reason": "human_interrupt", "category": "EMERGENCY"})
        if self._machine.state not in (State.DONE, State.ABORT):
            self._machine.on(Event.ABORT_REQUEST, "human intervention")
        self._interrupted(target_id)
