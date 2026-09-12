"""宝箱互动矩阵：定义六种宝箱/互动类型的步骤模式。

每种类型定义：
  - id: 类型标识（英文 key）
  - name: 中文名称
  - verify_signal: 验证信号模板（minimap 图标名）
  - verify_ocr: OCR 验证关键词列表
  - combat: 是否需要战斗（扑满/可破坏物）
  - requires_mechanism: 是否需要机关操作（解谜）
  - step_template: 该类型的 workflow 步骤模板（interact + verify 步骤）

六种类型：
  1. normal 普通 标准 interact + verify（宝箱图标消失）
  2. rich 丰厚 interact + verify（丰厚宝箱图标消失）
  3. precious 珍贵 interact + verify（珍贵宝箱图标消失）
  4. wrecker 扑满 interact(fighting) + verify（扑满击败）
  5. destructible 可破坏物 interact(attack) + verify（可破坏物摧毁）
  6. puzzle 解谜 requires_mechanism → trajectory + interact + verify

互动矩阵用于：
  - 地图 转换器：为 map 段末尾的 interact 步骤生成匹配的 verify
  - 知识包构建：chests.json 的 type 字段映射到互动矩阵
  - 执行器：根据 chest type 选择执行策略（战斗/机关/直点）
  - 验证器：根据 chest type 选择 verify 信号和 OCR 关键词
"""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class InteractionType:
    """单种宝箱/互动类型的定义。"""

    id: str
    name: str    # 中文名
    verify_signal: str    # 验证信号模板名（minimap 图标）
    verify_ocr: list[str]    # OCR 验证关键词
    combat: bool = False    # 是否需要战斗
    requires_mechanism: bool = False    # 是否需要机关操作
    mechanism_type: str = ""    # 机关类型（puzzle 才有）
    interact_key: str = "f"    # 交互键（f/e/fighting）
    interact_template: str = ""    # 交互模板（如 chest.png）
    default_threshold: float = 0.8
    default_scale_range: list[float] = field(default_factory=lambda: [0.9, 1.1])
    retry_count: int = 2
    verify_timeout: int = 30
    verify_expected: str = "vanished"    # 预期结果（vanished/defeated/destroyed）
    description: str = ""

    def build_interact_step(self, template: str = "",
                            threshold: float | None = None,
                            scale_range: list[float] | None = None,
                            retry: int | None = None) -> dict:
        """生成 interact 步骤。"""
        tpl = template or self.interact_template
        thr = threshold if threshold is not None else self.default_threshold
        sr = scale_range or self.default_scale_range
        rt = retry if retry is not None else self.retry_count

        step: dict[str, Any] = {"type": "interact"}
        if tpl:
            step["template"] = tpl
        step["threshold"] = thr
        step["scale_range"] = sr
        step["retry"] = rt

        # 战斗型：标注 combat + 交互键
        if self.combat:
            step["key"] = "fighting"
            step["combat"] = True
        elif self.requires_mechanism:
            # 解谜型：机关操作后交互
            step["requires_mechanism"] = self.mechanism_type
        else:
            # 普通型：F 键交互
            step["key"] = self.interact_key

        return step

    def build_verify_step(self, signal: str = "",
                          ocr: list[str] | None = None,
                          timeout: int | None = None) -> dict:
        """生成 verify 步骤。"""
        sig = signal or self.verify_signal
        oc = ocr or self.verify_ocr
        to = timeout if timeout is not None else self.verify_timeout

        step: dict[str, Any] = {
            "type": "verify",
            "signal": sig,
            "expected": self.verify_expected,
            "timeout": to,
        }
        if oc:
            step["ocr"] = oc
        return step

    def build_steps(self, template: str = "",
                    threshold: float | None = None,
                    scale_range: list[float] | None = None) -> list[dict]:
        """生成完整的 interact + verify 步骤序列。"""
        steps = [self.build_interact_step(template, threshold, scale_range)]
        steps.append(self.build_verify_step())
        return steps


# 互动矩阵：六种类型定义

INTERACTION_MATRIX: dict[str, InteractionType] = {
    "normal": InteractionType(
        id="normal",
        name="普通宝箱",
        verify_signal="minimap_chest_icon",
        verify_ocr=["宝箱", "任务", "委托"],
        interact_key="f",
        interact_template="chest.png",
        default_threshold=0.8,
        description="标准宝箱：模板匹配 → F 键交互 → 验证图标消失",
    ),
    "rich": InteractionType(
        id="rich",
        name="丰厚宝箱",
        verify_signal="minimap_rich_chest_icon",
        verify_ocr=["丰厚", "宝箱", "任务"],
        interact_key="f",
        interact_template="chest_rich.png",
        default_threshold=0.8,
        description="丰厚宝箱：金色边框 → F 键交互 → 验证丰厚图标消失",
    ),
    "precious": InteractionType(
        id="precious",
        name="珍贵宝箱",
        verify_signal="minimap_precious_chest_icon",
        verify_ocr=["珍贵", "宝箱", "任务"],
        interact_key="f",
        interact_template="chest_precious.png",
        default_threshold=0.85,
        description="珍贵宝箱：红色边框 → F 键交互 → 验证珍贵图标消失",
    ),
    "wrecker": InteractionType(
        id="wrecker",
        name="扑满",
        verify_signal="minimap_wrecker_icon",
        verify_ocr=["扑满", "战斗", "胜利"],
        combat=True,
        interact_key="fighting",
        interact_template="wrecker.png",
        default_threshold=0.7,
        verify_expected="defeated",
        description="扑满：需战斗击败 → 战技/自动战斗 → 验证扑满消失",
    ),
    "destructible": InteractionType(
        id="destructible",
        name="可破坏物",
        verify_signal="minimap_destructible_icon",
        verify_ocr=["破坏", "可破坏", "摧毁"],
        combat=True,
        interact_key="e",
        interact_template="destructible.png",
        default_threshold=0.7,
        verify_expected="destroyed",
        description="可破坏物：普攻/战技摧毁 → 验证可破坏物消失",
    ),
    "puzzle": InteractionType(
        id="puzzle",
        name="解谜宝箱",
        verify_signal="minimap_puzzle_chest_icon",
        verify_ocr=["机关", "解谜", "宝箱", "开关"],
        requires_mechanism=True,
        mechanism_type="puzzle",
        interact_key="f",
        interact_template="chest_puzzle.png",
        default_threshold=0.8,
        description="解谜宝箱：需机关操作（开关/压力板/启动装置）→ 交互 → 验证",
    ),
}

# 类型别名映射（中文 → id）
TYPE_ALIASES: dict[str, str] = {
    "普通": "normal",
    "丰厚": "rich",
    "珍贵": "precious",
    "扑满": "wrecker",
    "可破坏物": "destructible",
    "可破坏": "destructible",
    "破坏物": "destructible",
    "解谜": "puzzle",
    "解谜宝箱": "puzzle",
    "机关": "puzzle",
    # 英文别名
    "chest": "normal",
    "rich_chest": "rich",
    "precious_chest": "precious",
    "wrecker": "wrecker",
    "destructible": "destructible",
    "puzzle": "puzzle",
    "mechanism": "puzzle",
}


def get_interaction_type(type_id: str) -> InteractionType | None:
    """按 id 获取互动类型定义。

    Args
        type_id: 类型 id（normal/rich/precious/wrecker/destructible/puzzle）
                 或中文别名（普通/丰厚/...）

    Returns
        InteractionType 或 None（未知类型）
    """
    # 直接匹配
    if type_id in INTERACTION_MATRIX:
        return INTERACTION_MATRIX[type_id]
    # 别名匹配
    alias = TYPE_ALIASES.get(type_id)
    if alias and alias in INTERACTION_MATRIX:
        return INTERACTION_MATRIX[alias]
    return None


def get_all_types() -> dict[str, InteractionType]:
    """获取全部互动类型。"""
    return INTERACTION_MATRIX


def resolve_type_from_chest(chest: dict) -> InteractionType:
    """从 chests.json 的点位数据推断互动类型。

    推断规则（优先级从高到低）：
    1. 显式 type 字段 → 直接映射
    2. verify_signal 含 wrecker/puzzle → 对应类型
    3. template 含 wrecker/puzzle/destruct → 对应类型
    4. 默认 normal
    """
    # . 显式 type
    ct = chest.get("type") or chest.get("chest_type") or ""
    if ct:
        t = get_interaction_type(ct)
        if t:
            return t

    # . verify_signal 推断
    sig = chest.get("verify_signal", "")
    if "wrecker" in sig.lower():
        return INTERACTION_MATRIX["wrecker"]
    if "puzzle" in sig.lower():
        return INTERACTION_MATRIX["puzzle"]
    if "destruct" in sig.lower():
        return INTERACTION_MATRIX["destructible"]
    if "rich" in sig.lower():
        return INTERACTION_MATRIX["rich"]
    if "precious" in sig.lower():
        return INTERACTION_MATRIX["precious"]

    # . template 推断
    tpl = chest.get("template", "")
    if "wrecker" in tpl.lower():
        return INTERACTION_MATRIX["wrecker"]
    if "puzzle" in tpl.lower():
        return INTERACTION_MATRIX["puzzle"]
    if "destruct" in tpl.lower():
        return INTERACTION_MATRIX["destructible"]
    if "rich" in tpl.lower():
        return INTERACTION_MATRIX["rich"]
    if "precious" in tpl.lower():
        return INTERACTION_MATRIX["precious"]

    # . 默认 normal
    return INTERACTION_MATRIX["normal"]


def build_workflow_for_chest(chest: dict, target_id: str = "") -> dict:
    """根据 chest 数据构建 workflow 1.3。

    Args
        chest: chests.json 中的点位 dict
        target_id: workflow target_id（默认用 chest["id"]）

    Returns
        workflow 1.3 dict
    """
    tid = target_id or chest.get("id", "")
    itype = resolve_type_from_chest(chest)
    template = chest.get("template", "")
    threshold = chest.get("threshold")
    scale_range = chest.get("scale_range")

    steps = []

    # 解谜型：先机关操作（trajectory 回放），再交互
    if itype.requires_mechanism:
        # 机关操作步骤（trajectory 回放）
        traj = chest.get("trajectory")
        if traj:
            steps.append({
                "type": "move",
                "trajectory": traj,
                "mechanism": itype.mechanism_type,
            })

    # interact + verify
    steps.extend(itype.build_steps(
        template=template,
        threshold=threshold,
        scale_range=scale_range,
    ))

    return {
        "protocol": "1.3",
        "target_id": tid,
        "chest_type": itype.id,
        "steps": steps,
    }


def print_matrix():
    """打印互动矩阵（调试用）。"""
    print("=== 宝箱互动矩阵 ===")
    print(f"{'类型':<12} {'名称':<8} {'战斗':<4} {'机关':<4} "
          f"{'交互键':<6} {'验证信号':<30} {'验证OCR'}")
    print("-" * 100)
    for t in INTERACTION_MATRIX.values():
        print(f"{t.id:<12} {t.name:<8} {'是' if t.combat else '否':<4} "
              f"{'是' if t.requires_mechanism else '否':<4} "
              f"{t.interact_key:<6} {t.verify_signal:<30} "
              f"{','.join(t.verify_ocr[:3])}")


if __name__ == "__main__":
    print_matrix()
