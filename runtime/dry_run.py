import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runtime.capabilities import CapabilityRegistry
from runtime.events.schema import make_event
from runtime.knowledge_loader import KnowledgePackage
from runtime.state_machine import Event, State, StateMachine

ANOMALY_TARGET = "chest_D"
DRY_RUN_VERIFIES = "schema/graph/state_transition/event_flow/replay"
DRY_RUN_NOT_VERIFIES = "game_vision/input_reliability/real_movement/game_state"


def _emit(bus, execution_id, event_type, **kw):
    if bus is not None:
        bus.publish(make_event(event_type, execution_id, **kw))


def simulate_step(machine, step, pkg, sim_context, bus=None, execution_id=None,
                  fail_rate=0.0, rng=None):
    """fail_rate>0 时注入随机故障（实机失败路径可在 DryRun 暴露）。

    故障注入点：interact 点击（返回 False → 上层重试/失败路径）。
    """
    import random as _random
    rng = rng or _random.Random()
    kind = step["type"]

    def _inject_fail(reason):
        """7×24 防御：故障注入的状态迁移安全——EVENT_INTERRUPT 状态再次失败
        不能重复发 EVENT_INTERRUPTED（TRANSITIONS 无该边——非法迁移崩溃，
        stress_test 50 轮实测复现）。已中断状态直接走 RECOVER_OK 重试语义。"""
        _emit(bus, execution_id, "observation",
              detail=reason,
              context={"room": sim_context["room"], "observer": "template_match",
                       "confidence": 0.31})
        if machine.state.name != "EVENT_INTERRUPT":
            machine.on(Event.EVENT_INTERRUPTED, "injected fail")
        else:
            machine.on(Event.RECOVER_OK, "injected fail retry")
        return False

    if kind == "interact" and fail_rate > 0 and rng.random() < fail_rate:
        print(f"  [SIM] {machine.state.name}  → interact 故障注入 FAILED (fail_rate={fail_rate})")
        return _inject_fail(f"template:{step['template']} miss")
    if kind == "portal":
        portal = pkg.portal(step["portal_id"])
        print(f"  [SIM] {machine.state.name}  → portal step: {portal['id']} ({portal['from']}→{portal['to']})")
        _emit(bus, execution_id, "action_executed", detail=f"portal:{portal['id']}",
              context={"room": sim_context["room"], "reason": "portal_transition",
                       "source": "decision_layer", "naturalized": True})
        machine.on(Event.PORTAL_EXPECTED, f"portal {portal['id']} ahead")
        time.sleep(0.05)
        machine.on(Event.PORTAL_DETECTED, "loading screen")
        time.sleep(0.05)
        machine.on(Event.ROOM_MATCH, f"arrived {portal['to']}")
        sim_context["room"] = portal["to"]
        return True

    if kind == "state_check":
        state = sim_context["states"].get(step["state_id"], False)
        print(f"  [SIM] {machine.state.name}  → state_check {step['state_id']} = {state}")
        if not state:
            sim_context["states"][step["state_id"]] = True
            print("  [SIM]      state now True (simulated precondition trigger)")
        return True

    if kind == "move":
        target = step["target"]
        if sim_context["room"] == "room_B" and target == "lm_left_wing_end" and not sim_context.get("lm_end_ok"):
            sim_context["lm_end_ok"] = True
            print(f"  [SIM] {machine.state.name}  → move {target} FAILED (template miss)")
            _emit(bus, execution_id, "observation",
                  detail=f"template:{target} miss",
                  context={"room": sim_context["room"], "observer": "template_match", "confidence": 0.31})
            machine.on(Event.EVENT_INTERRUPTED, "move fail")
            machine.on(Event.RECOVER_OK, "retry after recovery")
            print(f"  [SIM] {machine.state.name}  → move {target} retry OK")
        else:
            print(f"  [SIM] {machine.state.name}  → move to landmark {target} OK")
            _emit(bus, execution_id, "action_executed", detail=f"move:{target}",
                  context={"room": sim_context["room"], "reason": "move_to_landmark",
                           "source": "decision_layer", "naturalized": True})
        return True

    if kind == "interact":
        print(f"  [SIM] {machine.state.name}  → interact template={step['template']} (click)")
        return True

    if kind == "verify":
        # verify 可注入失败——验证失败路径（恢复/重试）可覆盖
        if fail_rate > 0 and rng.random() < fail_rate:
            print(f"  [SIM] {machine.state.name}  → verify 故障注入 FAILED")
            # ×24 防御：同 _inject_fail 的状态迁移安全
            _emit(bus, execution_id, "observation",
                  detail=f"verify:{step.get('signal')} miss",
                  context={"room": sim_context["room"], "observer": "template_match",
                           "confidence": 0.31})
            if machine.state.name != "EVENT_INTERRUPT":
                machine.on(Event.EVENT_INTERRUPTED, "injected verify fail")
            else:
                machine.on(Event.RECOVER_OK, "injected verify fail retry")
            return False
        # verify 区分模拟/已验证——SIM 标记（不冒充 VERIFIED_SUCCESS）
        print(f"  [SIM] {machine.state.name}  → verify signal={step['signal']} "
              f"expected={step['expected']} (SIMULATED——非真实视觉验证)")
        return True

    if kind == "wait":
        print(f"  [SIM] {machine.state.name}  → wait {step.get('seconds', 1)}s")
        return True

    print(f"  [SIM] unknown step type: {kind}")
    return False


def dry_run(pkg_dir, target_ids=None, bus=None, execution_id=None,
            fail_rate=0.0, seed=None):
    """fail_rate>0：注入随机动作故障（ ——实机失败路径在 DryRun 可暴露）。"""
    import random
    rng = random.Random(seed)
    pkg = KnowledgePackage(Path(pkg_dir))
    print(f"== dry_run: {pkg.root.name} ==")
    print(f"   verifies: {DRY_RUN_VERIFIES}")
    print(f"   NOT verified: {DRY_RUN_NOT_VERIFIES}")

    caps = CapabilityRegistry()
    try:
        caps.check_requirements(pkg.meta.get("requires", []), knowledge_id=pkg.root.name)
    except Exception:
        # 能力检查失败带完整堆栈
        import logging
        logging.getLogger("runtime.dry_run").exception("capability check failed")
        print("  [ERROR] capability check failed")
        return 1

    # DS 审计 V3 解环：canonical 实现已迁入 runtime 侧
    # 不再从 ingest 离线管线 import（runtime→ingest 跨层边清零）。
    from runtime.knowledge_validation import validate

    errors, warnings = validate(pkg, verbose=False)
    if errors:
        for e in errors:
            print(f"  [ERROR] {e}")
        print("dry_run 中止: 知识包校验未通过")
        _emit(bus, execution_id, "run_finished", context={"result": "invalid"})
        return 1

    spawn = pkg.spawn_room()
    print(f"spawn_room = {spawn}")
    targets = target_ids or [c["id"] for c in pkg.chests]

    for cid in targets:
        sim_context = {"room": spawn, "states": {}, "lm_end_ok": False}

        def logger(prev, new, action, reason, _cid=cid):
            print(f"    {prev:>26} → {new:<26} {reason}")
            _emit(bus, execution_id, "state_changed",
                  from_state=prev, to_state=new, detail=reason,
                  context={"target": _cid, "action": action})

        machine = StateMachine(target_id=cid, room=spawn, logger=logger)
        print(f"\n== target {cid} ==")
        _emit(bus, execution_id, "target_progress",
              detail=f"start:{cid}", context={"target": cid, "status": "running"})
        machine.on(Event.START, "dry_run start")
        machine.on(Event.ROOM_MATCH, f"in {spawn}")
        wf = pkg.workflow(cid)
        if wf is None:
            # 真实点位无 workflow（只登记了坐标，执行流程未生成）——明确失败而非 TypeError
            print(f"  [SKIP] {cid} 无执行流程（workflow 未生成）")
            _emit(bus, execution_id, "target_progress",
                  detail=f"skip:{cid}:no_workflow",
                  context={"target": cid, "status": "failed",
                           "reason": "no_workflow", "category": "F3"})
            continue
        for i, step in enumerate(wf["steps"]):
            if machine.state in (State.DONE, State.ABORT):
                break
            simulate_step(machine, step, pkg, sim_context, bus=bus, execution_id=execution_id,
                          fail_rate=fail_rate, rng=rng)
            if machine.state in (State.DONE, State.ABORT):
                break
        if machine.state == State.NAVIGATING:
            machine.on(Event.TARGET_VISIBLE, "simulated target visible")
            machine.on(Event.TARGET_VERIFIED, "simulated verified")
            machine.on(Event.INTERACT_OK, "simulated interaction ok")
        ok = machine.state == State.DONE
        _emit(bus, execution_id, "target_progress",
              detail=f"finish:{cid}:{'ok' if ok else 'fail'}",
              context={"target": cid, "status": "done" if ok else "failed"})
        print(f"  [SIM] final state = {machine.state.name}  (exec {machine.execution_id})")
        if ok:
            print(f"  [RESULT] {cid} PASS (logical) — 仅验证 {DRY_RUN_VERIFIES}，不代表真机可用")
        else:
            print(f"  [RESULT] {cid} FAIL")
        for h in machine.history:
            print(f"    {h[0]:>26} → {h[1]:<26} {h[3]}")
    return 0


if __name__ == "__main__":
    sys.exit(dry_run(sys.argv[1], sys.argv[2:] or None))
