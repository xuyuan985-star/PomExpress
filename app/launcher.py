"""P0-002：启动器——环境检查 + 生命周期 + 命令路由。

入口职责收敛：__main__ → run(argv) → 具体命令。
"""
import sys
from config.version import APP_VERSION


def _check_python_version():
    # GUI 基础门槛 3.11+（pythonw 启动 GUI 本身不依赖 m7 语法）。
    # 注意：m7 任务中心进程内集成需 3.12+（PEP 701）——但那是任务启动时的
    # 单独检查（gui/tasks/runner.py），绝不能拦整个 GUI（3.11 环境下 GUI
    # 打不开 = 功能瘫痪）。
    if sys.version_info < (3, 11):
        raise RuntimeError(
            f"需要 Python 3.11+（当前 {sys.version_info.major}.{sys.version_info.minor}）")


def _check_venv():
    # 打包版自带解释器与依赖，不存在「未在虚拟环境中」的问题——跳过提示
    if getattr(sys, "frozen", False):
        return True
    # 虚拟环境检测（系统 Python 缺依赖风险提示）
    import os
    in_venv = sys.prefix != sys.base_prefix
    if not in_venv and os.name == "nt":
        print(f"[warn] 未检测到虚拟环境（当前: {sys.prefix}）"
              "——建议 .venv\\Scripts\\activate 后运行")
    return in_venv


def selftest():
    """启动自检报告（Config/Knowledge/Runtime/Vision）。"""
    from pathlib import Path
    ROOT = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(ROOT))
    print(f"帕姆巡宝 v{APP_VERSION} 自检报告")
    try:
        from config.settings import validate_config
        ok, problems = validate_config()
        print(f"  Config {'OK' if ok else 'FAIL'}"
              + (f"（{problems}）" if problems else ""))
    except Exception as e:
        print(f"  Config FAIL: {e}")
    try:
        from runtime.knowledge_loader import KnowledgePackage
        from config.settings import knowledge_root
        pkg = KnowledgePackage(knowledge_root() / "source" / "black_tower_test")
        print(f"  Knowledge OK（chests={len(pkg.chests or [])}）")
    except Exception as e:
        print(f"  Knowledge FAIL: {type(e).__name__}: {e}")
    try:
        import runtime.api.commands
        print("  Runtime OK")
    except Exception as e:
        print(f"  Runtime FAIL: {e}")
    try:
# 视觉栈 = 自研三件套（截图 / OCR / 模板匹配）。只 import 不加载模型
# （OCR 模型加载 1–2s，自检必须轻量）。
        # 只 import 不加载模型（OCR 模型加载 1–2s，自检必须轻量）。
        from runtime.drivers.local.vision import ScreenVision  # noqa: F401
        from runtime.input.template_backend import TemplateMatcher  # noqa: F401
        print("  Vision OK（截图 / OCR / 模板匹配）")
    except Exception as e:
        print(f"  Vision FAIL: {type(e).__name__}: {e}")
    return 0


def cleanup_temp():
    """临时文件统一清理（抽帧残留/临时 json/tmp）。"""
    from pathlib import Path
    ROOT = Path(__file__).resolve().parent.parent
    targets = []
    cap = ROOT / "ingest" / "raw" / "frames" / "capture"
    targets.append((cap, "f_*.jpg"))
    targets.append((cap, "*.tmp"))
    removed = 0
    for d, pat in targets:
        if d.exists():
            for f in d.glob(pat):
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass
    if removed:
        print(f"[cleanup] 移除 {removed} 个临时文件")
    return removed


def run(argv):
    """启动入口（argv 不含程序名）。返回退出码。"""
    # cleanup 是维护工具，不依赖 GUI/m7_venv，跳过前置检查
    # （否则用户在坏环境下连临时文件都清不掉）
    if "--cleanup" in argv:
        cleanup_temp()
        return 0
    # 冷启动前置检查（依赖/数据缺失 → 可见可操作提示，不静默）
    # 关键问题拦在 GUI 前（退出码 1），警告只打印但继续启动（正常路径不变）
    from app import preflight
    issues = preflight.run_all()
    if issues:
        rc = preflight.report(issues)
        if rc != 0:
            return rc
    _check_python_version()
    _check_venv()
    # 打包版首次启动：把只读知识包播种一份**可写副本**到 exe 同级目录。
    # 必须早于任何知识包读取——GUI（guides_view/world_graph）与录制器都读
    # 该副本；不做播种则自定义地图点位与已录轨迹在打包版里看不到。
    if getattr(sys, "frozen", False):
        try:
            from config.settings import knowledge_root
            knowledge_root()
        except Exception:
            pass
    if "--selftest" in argv:
        return selftest()
    if "--run" in argv:
        # 打包版不能用 `python -m <模块>`（sys.executable 就是本 exe，
        # 参数会被启动器忽略并开出一个新 GUI 窗口），故提供此桥：
        #   --run <模块名> [透传参数...]
        # 用 runpy 等价执行该模块的 __main__，供 GUI 以子进程方式调用
        # 内部命令（校验、地图导入等）时在打包版与源码版行为一致。
        i = argv.index("--run")
        module = argv[i + 1] if i + 1 < len(argv) else ""
        if not module:
            print("--run 需要模块名，例如：--run app.validate_all")
            return 2
        import runpy
        _saved_argv = sys.argv
        try:
            sys.argv = [module] + list(argv[i + 2:])
            runpy.run_module(module, run_name="__main__", alter_sys=True)
        except SystemExit as e:
            return int(e.code) if isinstance(e.code, int) else 0
        except Exception as e:
            print(f"--run {module} 失败: {type(e).__name__}: {e}")
            return 1
        finally:
            sys.argv = _saved_argv
        return 0
    if "--gate" in argv:
        # 本项仅作兼容提示，不再尝试执行
        print("--gate 依赖开发门禁工具，该项目已不再随源码分发。")
        return 2
    # 启动阶段进度（日志可定位卡在哪一步）
    from runtime.lifecycle import LIFECYCLE, AppState
    LIFECYCLE.set(AppState.INITIALIZING)
    # 异常/中断也保证清理（临时文件/注册资源）
    try:
        # 默认：GUI
        from gui import run as gui_run
        gui_run.main()
    finally:
        try:
            cleanup_temp()
        except Exception:
            pass
        from runtime.resource import _shutdown
        _shutdown()
    return 0
