import copy
import yaml
from pathlib import Path

DEFAULT_CAPABILITIES = {
    "runtime_version": "0.1",
    "capabilities": {
        "room_detect": {"status": "mock"},
        "portal_transition": {"status": "mock"},
        "visual_move": {"status": "experimental"},
        "combat": {"status": "manual"},
        "puzzle": {"status": "disabled"},
        "home_return": {"status": "not_implemented"},
        "ocr": {"status": "ready"},
        "template_match": {"status": "ready"},
        },
}


class CapabilityRegistry:
    def __init__(self, path=None):
        self.path = Path(path) if path else Path(__file__).parent.parent / "runtime" / "capabilities.yaml"
        self.data = self._load()

    def _load(self):
        if self.path.exists():
            loaded = yaml.safe_load(self.path.read_text(encoding="utf-8"))
            # YAML 顶层必须是 dict（list/标量 → 明确错误而非 update 崩）
            if loaded is not None and not isinstance(loaded, dict):
                raise CapabilityError(
                    f"capability yaml 顶层应为对象，实际 {type(loaded).__name__}: {self.path}")
            # 深拷贝——嵌套 capabilities 不被默认值/后续修改污染
            merged = copy.deepcopy(DEFAULT_CAPABILITIES)
            merged.update(copy.deepcopy(loaded or {}))
            # 审查 P1：capabilities 键为非 dict（list 等）→ setdefault.update
            # 抛 AttributeError——显式校验
            if "capabilities" in merged and not isinstance(
                    merged["capabilities"], dict):
                raise CapabilityError(
                    f"capability yaml 的 capabilities 应为对象，"
                    f"实际 {type(merged['capabilities']).__name__}")
            merged.setdefault("capabilities", {}).update(
                copy.deepcopy(DEFAULT_CAPABILITIES["capabilities"]))
            return merged
        return copy.deepcopy(DEFAULT_CAPABILITIES)

    def status(self, capability):
        return self.data["capabilities"].get(capability, {}).get("status", "disabled")

    def is_ready(self, capability):
        return self.status(capability) in ("ready", "experimental")

    def check_requirements(self, requires, knowledge_id="unknown"):
        missing = [c for c in requires if not self.is_ready(c)]
        if missing:
            raise CapabilityError(
                f"knowledge[{knowledge_id}] requires {missing}, "
                f"runtime capabilities insufficient — abort before execution"
            )
        return True

    def summary(self):
        # 审查：与 status 一致——条目缺 status 字段不 KeyError
        return {k: (v.get("status", "disabled") if isinstance(v, dict) else "disabled")
                for k, v in self.data["capabilities"].items()}


class CapabilityError(Exception):
    pass
