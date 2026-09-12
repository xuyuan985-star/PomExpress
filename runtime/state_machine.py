import time
import uuid
from enum import Enum

from runtime import db

State = Enum("State", [
    "INIT",
    "CHECK_WORLD_STATE",
    "NAVIGATING",
    "PORTAL_TRANSITION",
    "PORTAL_TRANSITION_FAILED",
    "VERIFYING",
    "INTERACTING",
    "EVENT_INTERRUPT",
    "RECOVERING",
    "DONE",
    "ABORT",
])

Event = Enum("Event", [
    "START",
    "ROOM_MATCH",
    "ROOM_MISMATCH",
    "PORTAL_EXPECTED",
    "PORTAL_DETECTED",
    "PORTAL_FAILED",
    "PORTAL_RECOVERED",
    "TARGET_VISIBLE",
    "TARGET_VERIFIED",
    "INTERACT_OK",
    "INTERACT_AGAIN",
    "EVENT_INTERRUPTED",
    "RECOVER_OK",
    "RECOVER_FAILED",
    "ABORT_REQUEST",
])

TRANSITIONS = {
    State.INIT: {Event.START: (State.CHECK_WORLD_STATE, "enter_check_world_state")},
    State.CHECK_WORLD_STATE: {
        Event.ROOM_MATCH: (State.NAVIGATING, "start_navigation"),
        Event.ROOM_MISMATCH: (State.RECOVERING, "recover_world_state"),
        Event.ABORT_REQUEST: (State.ABORT, "abort"),
    },
    State.NAVIGATING: {
        Event.PORTAL_EXPECTED: (State.PORTAL_TRANSITION, "enter_portal_transition"),
        Event.TARGET_VISIBLE: (State.VERIFYING, "enter_verifying"),
        Event.EVENT_INTERRUPTED: (State.EVENT_INTERRUPT, "interrupt"),
        Event.ABORT_REQUEST: (State.ABORT, "abort"),
    },
    State.PORTAL_TRANSITION: {
        Event.PORTAL_DETECTED: (State.PORTAL_TRANSITION, "continue_transition"),
        Event.ROOM_MATCH: (State.NAVIGATING, "resume_navigation"),
        Event.PORTAL_FAILED: (State.PORTAL_TRANSITION_FAILED, "enter_portal_failed"),
        Event.ABORT_REQUEST: (State.ABORT, "abort"),
    },
    State.PORTAL_TRANSITION_FAILED: {
        Event.PORTAL_RECOVERED: (State.PORTAL_TRANSITION, "retry_portal"),
        Event.ROOM_MATCH: (State.NAVIGATING, "resume_navigation"),
        Event.ABORT_REQUEST: (State.ABORT, "abort"),
    },
    State.VERIFYING: {
        Event.TARGET_VERIFIED: (State.INTERACTING, "interact"),
        Event.ROOM_MISMATCH: (State.RECOVERING, "recover_world_state"),
        Event.ABORT_REQUEST: (State.ABORT, "abort"),
    },
    State.INTERACTING: {
        Event.INTERACT_OK: (State.DONE, "done"),
        Event.INTERACT_AGAIN: (State.INTERACTING, "interact_again"),
        Event.ROOM_MISMATCH: (State.RECOVERING, "recover_world_state"),
        Event.ABORT_REQUEST: (State.ABORT, "abort"),
    },
    State.EVENT_INTERRUPT: {
        Event.RECOVER_OK: (State.NAVIGATING, "resume_navigation"),
        Event.ABORT_REQUEST: (State.ABORT, "abort"),
    },
    State.RECOVERING: {
        Event.RECOVER_OK: (State.NAVIGATING, "resume_navigation"),
        Event.RECOVER_FAILED: (State.ABORT, "abort"),
        # 审查 P1-4：RECOVERING 缺 ABORT_REQUEST 边——当前无发布点（不可达），
        # 但任何中止路径在恢复态触发即 ValueError 崩溃（fail-closed 补边）
        Event.ABORT_REQUEST: (State.ABORT, "abort"),
    },
}


class StateMachine:
    def __init__(self, execution_id=None, target_id=None, room=None, logger=None):
        self.execution_id = execution_id or str(uuid.uuid4())[:8]
        self.target_id = target_id
        self.state = State.INIT
        self.history = []
        self.logger = logger
        self._enter("INIT", "init", "machine created", "NONE")
        db.start_progress(self.execution_id, target_id or "", room or "")

    def _enter(self, new_state, action, reason, prev):
        new_name = new_state if isinstance(new_state, str) else new_state.name
        self.history.append((prev, new_name, time.time(), reason))
        db.record_state_observation(self.target_id or "", new_name, "state_machine")
        if self.logger:
            self.logger(prev, new_name, action, reason)

    def on(self, event, reason=""):
        if self.state in (State.DONE, State.ABORT):
            return self.state
        table = TRANSITIONS.get(self.state, {})
        if event not in table:
            raise ValueError(f"非法状态迁移: {self.state.name} --[{event.name}]--> ?")
        next_state, action = table[event]
        prev = self.state.name
        self.state = next_state
        self._enter(next_state, action, reason, prev)
        return self.state
