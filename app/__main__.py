"""P0-002：启动入口拆分——__main__ 只做参数路由。

职责分离：cli（参数）→ launcher（环境/生命周期）→ 具体命令。
审查 P0-4：SystemExit 是正常退出（sys.exit(0)），不能被当崩溃捕获。
"""
import sys
from pathlib import Path

# 崩溃/退出探针日志必须可写。
# 打包后：exe 同级目录（不能用 __file__——那是退出即删的解包目录）。
# 源码模式：本地副产物区（项目根同级 <项目名>_local），与 config.settings 的
# DATA_ROOT 约定一致；该区不存在时回退项目根（旧行为）。
# 此处就地推导而不 import config：__main__ 是最早执行的模块，
# 尽量不引入额外依赖面（逻辑与 config.settings 保持同步）。
if getattr(sys, "frozen", False):
    DATA_ROOT = Path(sys.executable).resolve().parent
else:
    _PROJ = Path(__file__).resolve().parent.parent
    _LOCAL = _PROJ.parent / f"{_PROJ.name}_local"
    DATA_ROOT = _LOCAL if _LOCAL.is_dir() else _PROJ


def main():
    # pythonw 启动（无控制台）时 stdout/stderr 为 None——print 会崩，重定向
    if sys.stdout is None:
        import io
        sys.stdout = io.StringIO()
    if sys.stderr is None:
        import io
        sys.stderr = io.StringIO()
    # 崩溃捕获（0xC0000005 等原生崩溃）：faulthandler 在 SIGSEGV 时 dump
    # 所有线程的 Python 栈到日志——pythonw 无控制台，必须显式落盘。
    try:
        import faulthandler
        import threading as _threading
        _crash_log = DATA_ROOT / "logs" / "crash_trace.log"
        _crash_log.parent.mkdir(parents=True, exist_ok=True)
        _f = open(_crash_log, "a", encoding="utf-8", buffering=1)
        import time as _time
        _f.write(f"\n===== process start {_time.strftime('%Y-%m-%d %H:%M:%S')} "
                 f"argv={sys.argv} =====\n")
        faulthandler.enable(file=_f, all_threads=True)

        def _thread_excepthook(args):
            try:
                with open(_crash_log, "a", encoding="utf-8") as _ef:
                    _ef.write(f"\n[thread {args.thread.name}] {args.exc_type.__name__}: "
                              f"{args.exc_value}\n")
                    import traceback as _tb
                    _tb.print_tb(args.exc_traceback, file=_ef)
            except Exception:
                pass

        _threading.excepthook = _thread_excepthook
    except Exception:
        pass
    # 退出路径探针（排查"窗口消失但无崩溃记录"）：atexit 只在正常退出执行，
    # os._exit / 原生崩溃不执行——可区分退出方式。
    try:
        import atexit as _atexit
        import time as _time2

        def _on_exit():
            try:
                with open(DATA_ROOT / "logs" / "exit_trace.log", "a",
                          encoding="utf-8") as _ef:
                    _ef.write(f"{_time2.strftime('%Y-%m-%d %H:%M:%S')} "
                              f"atexit fired — normal interpreter exit\n")
            except Exception:
                pass

        _atexit.register(_on_exit)
    except Exception:
        pass
    try:
        from app.launcher import run
        code = run(sys.argv[1:])
        sys.exit(code)
    except SystemExit:
        raise  # 正常退出——不捕获
    except Exception:
        # pythonw 下启动异常静默死——落盘可查
        import traceback
        try:
            err = DATA_ROOT / "logs" / "startup_error.log"
            err.parent.mkdir(parents=True, exist_ok=True)
            with open(err, "a", encoding="utf-8") as f:
                f.write("\n===== app entry =====\n")
                f.write(traceback.format_exc())
        except Exception:
            pass
        # 退出保护：异常退出前若有运行中 QThread（任务线程等），Qt 析构必
        # 0xC0000409——os._exit 跳过析构
        # 退出保护：查询全局 QThread 注册表（gui/thread_guard.py），
        # 与 gui/run.py 的 aboutToQuit 保护同一查询函数
        try:
            from gui.thread_guard import all_running_qthreads
            if all_running_qthreads():
                import os as _os
                _os._exit(0)
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
