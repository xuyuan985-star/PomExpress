# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir）。

产物：dist/PomExpress/ 目录，双击 PomExpress.exe 即可启动（免装 Python）。

与源码模式的差异（由 config/settings.py 处理，见 RESOURCE_ROOT / DATA_ROOT）：
  - 只读资源（knowledge/、assets/、VERSION）打进包，位于 sys._MEIPASS
  - 可写数据（logs/、state/、memory/、failure_reports/、runtime.db、
    settings/*.ini、knowledge/ 的可写副本）落在 exe 同级目录
  - knowledge/ 首次运行会从包内播种一份可写副本到 exe 同级（轨迹录制与
    自定义地图需要可写）

不打包的内容及原因：
    - docs/        审计报告
"""
from PyInstaller.utils.hooks import collect_all

# 只读资源（打包进包内）
datas = [
    ("knowledge", "knowledge"),
    ("assets", "assets"),
    ("VERSION", "."),
]

binaries = []
hiddenimports = []

# 依赖的隐式数据/二进制（Qt 插件、OCR 模型、onnxruntime 动态库等）
for pkg in ("qfluentwidgets", "rapidocr", "onnxruntime", "pyautogui",
            "mss", "pynput", "ruamel.yaml", "keyboard", "psutil", "pyuac"):
    _d, _b, _h = collect_all(pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

# 显式补：运行时动态 import 的模块（静态分析看不到）
hiddenimports += [
    "app.launcher",
    "app.preflight",
    # GUI 以子进程方式调用（`--run <模块>` 桥），静态分析收不到，必须显式声明
    "app.validate_all",
    "ingest.compiler.map_converter",
    "ingest.compiler.validate_graph",
    "gui.run",
    "runtime.api.commands",
]

a = Analysis(
    ["app/__main__.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 明确排除开发期目录，避免被误收集（体积与收集时间）
    excludes=["tools", "tests", "docs", "pytest", "_pytest"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PomExpress",
    # exe 图标（资源管理器/任务栏/文件关联显示）：由 logo.webp 转换，
    # 内含 16~256 多档尺寸，Windows 按场景自动选取。
    icon="assets/logo.ico",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PomExpress",
)
