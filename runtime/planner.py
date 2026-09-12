"""Planner（第 7 步：决策层——Observation → ActionIntent）。

禁止：点击 / OCR / Windows API——只做决策。
输入 Observation，输出 ActionIntent（frozen，零坐标）。
"""
from runtime.action_intent import ActionIntent, ActionMethod, ActionType
from runtime.observation import Observation
from runtime.world_state import WorldState


class Planner:
    """意图规划器。

    decide：观察 → 动作意图（文本/实体检测语义）。
    plan_interact / plan_wait：直接构造意图（workflow 步骤路径也可用）。
    plan：目标驱动（Sprint E-5）——状态校验 + 知识校验 + 步骤产出。
    """

    def __init__(self, default_threshold=0.8, max_retries=3, min_confidence=0.6):
        self.default_threshold = default_threshold
        self.max_retries = max_retries
        self.min_confidence = min_confidence    # -8：低于此置信不行动

    def decide(self, obs: Observation, target: str) -> ActionIntent:
        """观察 → 意图：目标文本出现在观测中且置信达门槛 → 点击意图；否则等待。

        不执行任何观测/输入（纯决策）。method 由观测来源决定：
        - 观测含 entities（已有观测位置）→ template 实体点击
        - 文本命中（OCR）→ text 点击
        -8：confidence 低于门槛 → WAIT（防 Edge 截图等非游戏画面误判）
        """
        if obs is None or obs.confidence < self.min_confidence:
            return self.plan_wait(
                "low confidence" if obs is not None else "no observation")
        # OCR 文本匹配防误触发——token 级精确优先；
        # 中文无分词时：短目标（<2字，如"门"）必须 token 精确，长目标才允许包含
        tokens = set()
        for line in (obs.text or []):
            tokens.update(str(line).split())
        if target in tokens:
            return self.plan_interact(target, method=ActionMethod.TEXT.value,
                                      reason="target detected in OCR text",
                                      confidence=obs.confidence)
        text = "".join(obs.text or [])
        if len(target) >= 2 and target in text:
            return self.plan_interact(target, method=ActionMethod.TEXT.value,
                                      reason="target detected in OCR text",
                                      confidence=obs.confidence)
        for ent in (obs.entities or []):
            # 实体置信度用统一阈值（不再硬编码 0.6）
            if ent.get("id") == target and ent.get("confidence", 0) >= self.min_confidence:
                return self.plan_interact(target, method=ActionMethod.TEMPLATE.value,
                                          reason="target entity located",
                                          confidence=ent.get("confidence", 0.0))
        return self.plan_wait("target missing")

    def plan_interact(self, target, method=None, reason="", confidence=0.0,
                      vision_verified=False, vision_confidence=0.0,
                      evidence_id=None, risk="low", idempotent=True):
        return ActionIntent(
            action=ActionType.INTERACT.value,
            target=target,
            method=method or ActionMethod.TEMPLATE.value,
            params={"threshold": self.default_threshold,
                    "max_retries": self.max_retries},
            reason=reason or "objective_interact",
            source="planner",
            # 幂等参数化——非幂等动作（确认/购买/领取）由
            # workflow 显式声明 idempotent:false 才禁止 retry
            idempotent=idempotent,
            # Sprint 视觉证明透传（observe_act 通道由 gate 写入）
            vision_verified=vision_verified,
            vision_confidence=vision_confidence,
            evidence_id=evidence_id,
            risk=risk,
        )

    def plan(self, state: WorldState, goal: str, pkg=None):
        """Sprint E-5：目标驱动规划。

        goal 映射 knowledge workflows；校验前置（room 匹配）后产出步骤意图。
        返回 {"plan": [ActionIntent...], "status": "planned|already_done|blocked"
              "reason": "..."}。
        """
        if state is None:
            return {"plan": [], "status": "blocked", "reason": "no_world_state"}
        workflow = None
        if pkg is not None:
            try:
                workflow = pkg.workflow(goal)    # 知识包按 target_id 单文件加载
            except Exception as e:
                # 加载失败 ≠ 不存在——分类（JSON 损坏/IO 错误）
                return {"plan": [], "status": "blocked",
                        "reason": f"workflow_load_failed:{type(e).__name__}:{e}"}
        if workflow is None:
            return {"plan": [], "status": "blocked", "reason": f"goal_unknown:{goal}"}
        # workflow 结构本地防护（外部数据不可信——不依赖 validate）
        if not isinstance(workflow, dict) or not isinstance(workflow.get("steps"), list):
            return {"plan": [], "status": "blocked",
                    "reason": f"invalid_workflow_schema:{goal}"}

        # 房间前置校验：workflow 声明 room → 当前状态必须匹配
        need_room = workflow.get("room")
        if need_room and state.room and need_room != state.room:
            return {"plan": [], "status": "blocked",
                    "reason": f"room_mismatch:need={need_room},have={state.room}"}
        if goal in state.completed:
            return {"plan": [], "status": "already_done", "reason": "completed"}

        # 步骤 → 意图（move/interact/portal/trajectory 语义；
        # verify 不产意图——由执行后校验闭环）
        plan = []
        for step in (workflow.get("steps") or []):
            # 步骤非 dict → 阻断（防 AttributeError）
            if not isinstance(step, dict):
                return {"plan": [], "status": "blocked",
                        "reason": "invalid_step_schema"}
            st = step.get("type")
            if st == "move":
                # move 必须产 ActionType.MOVE 意图（不能伪装 interact）
                plan.append(ActionIntent(
                    action=ActionType.MOVE.value,
                    target=step.get("target"),
                    method=ActionMethod.TEMPLATE.value,
                    params={"threshold": self.default_threshold,
                            "max_retries": self.max_retries},
                    reason=f"objective_navigate:{goal}",
                    source="planner",
                    idempotent=True))
            elif st == "interact":
                plan.append(self.plan_interact(
                    workflow.get("target_id") or step.get("target"),
                    method=ActionMethod.TEMPLATE.value,
                    reason=f"objective_interact:{goal}",
                    # workflow 显式声明 idempotent:false → 禁止 retry
                    idempotent=step.get("idempotent", True)))
            elif st == "portal":
                # 传送门步骤：产 interact 意图（executor.portal_transition
                # 内部走 interact_template(portal["id"], ...)——意图语义一致）。
                # params 带 portal_id 供执行链区分普通 interact 与传送。
                pid = step.get("portal_id") or step.get("target")
                plan.append(ActionIntent(
                    action=ActionType.INTERACT.value,
                    target=pid,
                    method=ActionMethod.TEMPLATE.value,
                    params={"threshold": self.default_threshold,
                            "max_retries": self.max_retries,
                            "portal_id": pid},
                    reason=f"objective_portal:{goal}",
                    source="planner",
                    idempotent=True))
            elif st == "trajectory":
                # 轨迹回放步骤：产回放意图（action="trajectory" 非 ActionType
                # 白名单——executor 需按 action 字段分派回放路径；target=
                # 轨迹文件名，params.file 冗余便于执行链快速定位）。
                fname = step.get("file")
                plan.append(ActionIntent(
                    action="trajectory",
                    target=fname,
                    method=ActionMethod.TEMPLATE.value,
                    params={"file": fname,
                            "threshold": self.default_threshold,
                            "max_retries": self.max_retries},
                    reason=f"objective_trajectory:{goal}",
                    source="planner",
                    idempotent=True))
        return {"plan": plan, "status": "planned", "reason": "ok"}

    def plan_wait(self, reason="wait"):
        return ActionIntent(
            action=ActionType.WAIT.value,
            target=None,
            method=ActionMethod.TEXT.value,
            reason=reason,
            source="planner",
            idempotent=False,
        )
