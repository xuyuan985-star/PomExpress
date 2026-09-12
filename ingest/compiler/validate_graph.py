"""ingest.compiler.validate_graph — 向后兼容薄转发（canonical 实现已迁入 runtime）。

DS 审计 V3 解环：canonical 实现在 runtime/knowledge_validation.py——
校验逻辑只依赖同层 runtime.knowledge_loader（无跨层边）。本模块保留：
旧 import 路径（ingest.compiler.validate_graph.validate）——供离线管线沿用
`python -m ingest.compiler.validate_graph <knowledge_dir>` CLI 入口

新代码（runtime 侧）请直接 `from runtime.knowledge_validation import validate`。
"""
import sys
from pathlib import Path

# 本文件在 ingest/compiler/（depth 2）→ parent.parent = 项目根目录。
# （旧写法 parent.parent 少一层——当时 validate_graph 在 ingest/ 直属层；
# 迁入 compiler/ 子包后未同步，导致直跑 CLI 时 sys.path 缺根、import runtime 失败。）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from runtime.knowledge_validation import ALLOWED_STEP_TYPES, validate  # noqa: F401,E402

__all__ = ["ALLOWED_STEP_TYPES", "validate"]


def main():
    if len(sys.argv) < 2:
        print("用法: python -m ingest.compiler.validate_graph <knowledge_dir>")
        sys.exit(2)
    from runtime.knowledge_loader import KnowledgePackage
    pkg = KnowledgePackage(Path(sys.argv[1]))
    errors, _ = validate(pkg)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
