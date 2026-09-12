import os
import sys
import threading
from pathlib import Path

# P0-004：配置读写并发安全（GUI reload 与 runtime get 可能并发）
_config_lock = threading.RLock()

# 目录分层：项目本体 / 本地副产物 必须分开。
#
# 三个「根」，混用会导致「清理残留时要在项目本体里逐个辨认」：
#   - RESOURCE_ROOT：**项目本体**里的只读资源（knowledge/、assets/、VERSION）。
#     打包后位于 sys._MEIPASS 解包目录，**不可写**（退出即删）。
#   - DATA_ROOT：**可写运行产物**（logs/、state/、memory/、failure_reports/、
#     reports/、settings/*.ini、runtime.db、knowledge/trajectories/、.env）。
#   - 源码模式下 DATA_ROOT 优先指向**本地副产物区**（见下），
#     使运行产物不再落进项目本体；该区不存在时退回项目根（旧行为）。
#
# 本地副产物区约定：项目根的同级目录 `<项目目录名>_local`
#   F:\dsh workspace\STAR\PomExpress        ← 项目本体
#   F:\dsh workspace\STAR\PomExpress_local  ← 工作副产物区
# 可用环境变量 POMEXPRESS_LOCAL_DIR 覆盖（指向别的目录）。
_FROZEN = bool(getattr(sys, "frozen", False))
_MEIPASS = getattr(sys, "_MEIPASS", None)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

if _FROZEN:
    DATA_ROOT = Path(sys.executable).resolve().parent
    RESOURCE_ROOT = Path(_MEIPASS) if _MEIPASS else DATA_ROOT
else:
    RESOURCE_ROOT = _PROJECT_ROOT
    _env_local = os.environ.get("POMEXPRESS_LOCAL_DIR")
    _local = Path(_env_local) if _env_local else _PROJECT_ROOT.parent / f"{_PROJECT_ROOT.name}_local"
    try:
        DATA_ROOT = _local if _local.is_dir() else _PROJECT_ROOT
    except OSError:
        DATA_ROOT = _PROJECT_ROOT

# 兼容既有读取点：历史代码里的 ROOT 绝大多数用于读只读资源，
# 故 ROOT 语义定为 RESOURCE_ROOT。**写数据一律用 DATA_ROOT / data_path()**，
# 不要用 ROOT——打包后那会写进会被删除的解包目录。
ROOT = RESOURCE_ROOT


def resource_path(rel):
    """只读资源统一入口（打包/源码环境都正确）。"""
    return RESOURCE_ROOT / rel


def data_path(rel):
    """可写数据统一入口（打包后落在 exe 同级目录）。"""
    return DATA_ROOT / rel


def app_icon_path():
    """应用图标路径（窗口/任务栏图标）。

    优先 .ico（内含 16~256 多尺寸，Windows 按场景与 DPI 自动选取），
    缺失时退回 .png。两者都由 logo.webp 转换而来。
    """
    ico = resource_path("assets/logo.ico")
    if ico.is_file():
        return ico
    return resource_path("assets/logo.png")


# QSettings 命名空间（全项目单点）。新增读取点一律用这两个常量，
# 不要再硬编码字符串。
QSETTINGS_ORG = "PomExpress"
QSETTINGS_APP = "Studio"


def _load_env():
    env = {}
    # 查找顺序：exe 同级（用户自备，打包后可用）→ 资源目录（随包分发）
    for env_file in (DATA_ROOT / ".env", RESOURCE_ROOT / ".env"):
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip())
    return env


_ENV = _load_env()
# 运行时覆盖（GUI 设置页写入，优先于 .env/环境变量——进程内生效）
_runtime_override = {}


def set_override(key, value):
    """GUI 设置页：运行时覆盖配置（不写 .env，当前进程立即生效）。"""
    with _config_lock:
        if value is None:
            _runtime_override.pop(key, None)
        else:
            _runtime_override[key] = str(value)


def get_override(key):
    with _config_lock:
        return _runtime_override.get(key)


def reload_config():
    """运行中重新加载配置（.env/环境变量刷新，无需重启）。

    P0-004：锁保护——reload 与 get 并发不读中间状态。
    """
    global _ENV
    with _config_lock:
        _ENV = _load_env()
    return _ENV


# 日志脱敏（traceback/日志可能携带 key/cookie 等敏感值）

_SECRET_PATTERNS = [
    (r"(sk-[A-Za-z0-9]{8,})", "sk-***"),
    (r"(Bearer\s+)[A-Za-z0-9._-]{8,}", r"\1***"),
    (r"(SESSDATA=[A-Za-z0-9%._-]{6,})", "SESSDATA=***"),
    (r"(BILIBILI_COOKIE[=:]\s*)[^\s,;]+", r"\1***"),
]


def redact_secrets(text):
    """脱敏任意文本（日志/异常信息输出前调用）。"""
    if not text:
        return text
    import re
    for pat, repl in _SECRET_PATTERNS:
        text = re.sub(pat, repl, text)
    return text


def install_log_redaction():
    """给 root logger 挂脱敏 Filter（全局生效，无需逐处调用）。

    P1-005：同时挂到已有 handler 上——handler 级过滤覆盖 exc_info
    格式化输出（traceback 里的 key 也会被清洗）。
    """
    import logging

    class _RedactFilter(logging.Filter):
        def filter(self, record):
            try:
                msg = str(record.msg)
                # 审查 P1：any() 对非空列表恒真——直接按 msg 非空处理
                if msg:
                    record.msg = redact_secrets(msg)
                if record.args:
                    record.args = tuple(
                        redact_secrets(str(a)) if isinstance(a, str) else a
                        for a in record.args)
                if record.exc_info and record.exc_info[1]:
                    try:
                        import traceback
                        tb_text = "".join(
                            traceback.format_exception(*record.exc_info))
                        cleaned = redact_secrets(tb_text)
                        if cleaned != tb_text:
                            record.exc_text = cleaned
                    except Exception:
                        pass
            except Exception:
                pass
            return True

    root = logging.getLogger()
    root.addFilter(_RedactFilter())
    # P1-005：handler 级也挂（traceback 走 exc_text 路径时兜底）
    for h in list(root.handlers):
        h.addFilter(_RedactFilter())


def get(key, default=None):
    # BUG-015：优先级固定为 系统环境变量 > .env > 默认值（部署可覆盖本地配置，
    # 属有意设计）。reload_config 只刷新 .env 层——系统环境不变是预期行为。
    # 运行时覆盖（GUI 设置页）优先于一切
    with _config_lock:
        if key in _runtime_override:
            return _runtime_override[key]
        return os.environ.get(key) or _ENV.get(key) or default


def default_map():
    """GUI 默认加载的地图（可通过 env/GUI 设置覆盖）。

    默认 08_custom：早期攻略视频转化出的地图点位已全部移除，目前只有
    自定义地图（由真实录制轨迹同步而来）的数据是可执行的。
    """
    return get("DEFAULT_MAP", "02_herta_space_station")


def validate_config():
    """启动阶段配置校验（缺字段/非法值提前暴露，而非运行中才崩）。

    返回 (ok, [问题列表])。
    关键参数范围限制（min/max 越界即报）。
    """
    problems = []
    interval = get("MIN_ACTION_INTERVAL", "")
    if interval:
        try:
            if float(interval) < 0:
                problems.append("MIN_ACTION_INTERVAL 不能为负")
        except ValueError:
            problems.append(f"MIN_ACTION_INTERVAL 非法数值: {interval}")
    # 关键数值参数范围
    threshold = get("TEMPLATE_THRESHOLD", "")
    if threshold:
        try:
            v = float(threshold)
            if not (0.0 < v <= 1.0):
                problems.append(f"TEMPLATE_THRESHOLD={v} 应在 (0, 1]（0-1 置信度）")
        except ValueError:
            problems.append(f"TEMPLATE_THRESHOLD 非法数值: {threshold}")
    return (not problems), problems


def knowledge_root():
    """知识包根 —— **可写副本**。

    knowledge/ 整体随包分发（只读资源），但其中两类内容必须可写：
      - knowledge/trajectories/  用户手动录制的轨迹
      - knowledge/guides/maps/08_custom/  自定义地图点位（guides_loader 动态同步）
    故打包环境首次调用时把只读资源**播种**到 DATA_ROOT/knowledge 并后续一律
    使用该副本；源码模式下两者本就是同一路径，行为不变。
    """
    d = data_path("knowledge")
    if not d.exists():
        seed = resource_path("knowledge")
        try:
            if seed.is_dir() and seed.resolve() != d.resolve():
                import shutil
                shutil.copytree(seed, d)
        except OSError:
            pass
    return d if d.exists() else resource_path("knowledge")


def knowledge_data_root():
    """知识包内**可写**子目录的根（用户录制的轨迹写在这里）。

    与 knowledge_root() 分开的原因：knowledge/ 整体是只读资源，但
    knowledge/trajectories/ 存放用户手动录制的轨迹，必须可写；打包后
    写到解包目录会在退出时丢失，故指向 DATA_ROOT。
    """
    return data_path("knowledge")


def runtime_db_path():
    """运行时数据库（可写数据——打包后落在 exe 同级）。"""
    return data_path("runtime.db")


def game_setting(key, default=None):
    """游戏相关配置（GAME_PATH / HOTKEY_*）的统一读取入口。

    原先这些键读自 m7 的 config.yaml；m7 已从项目移除，
    改为读本项目配置（`.env` 或设置页写入），键名见 `.env.example`。
    """
    v = get(key)
    return str(v) if v else default
