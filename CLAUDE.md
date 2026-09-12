# 帕姆巡宝 项目记忆

## 项目

**帕姆巡宝 PomExpress** — 崩坏：星穹铁道（Windows）宝箱收集自动化助手。
Python 3.14 + PySide6，半自动：使用者手动录一次跑图轨迹，之后反复回放复现；
执行链以**模板匹配 + 差分验证**为主。

不调用任何视觉语言模型，不依赖任何外部项目目录。

## 目录布局

项目本体与工作副产物**物理分离**——清理残留只需处理副产物区一个目录。

```
<parent>/PomExpress        项目本体（= 仓库，只放源码与数据）
<parent>/PomExpress_local  工作副产物（环境 / 运行产物 / 备份 / 构建产物）
```

`config/settings.py` 定义两个根，**写数据一律用 `data_path()`，不要用 `ROOT`**：

| 常量 | 源码模式 | 打包模式 | 放什么 |
|---|---|---|---|
| `RESOURCE_ROOT` | 项目本体 | `sys._MEIPASS`（退出即删） | 只读资源：`knowledge/` `assets/` `VERSION` |
| `DATA_ROOT` | 同级 `PomExpress_local`（不存在则回退项目根） | exe 同级 | 可写：`logs/` `state/` `settings/` `reports/` `runtime.db` `.env` |

`knowledge/` 是只读资源，但 `trajectories/` 与 `guides/maps/08_custom/` 需要可写，
故**首次运行把整份 `knowledge/` 播种到 `DATA_ROOT` 一份可写副本**，之后一律用副本。

## 架构

分层：`app`（入口）→ `gui`（PySide6）→ `runtime`（编排/执行/输入）→ `ingest`（离线管线）→ `knowledge`（数据）。
分层边界无机械门禁，改动时人工确认。

**可用链路（全自研，无外部依赖）**

| 环节 | 模块 |
|---|---|
| 截图 | `runtime/win_capture`（PrintWindow 后台 → 前台 mss 兜底） |
| 帧质检 | `runtime/vision_quality`（黑屏/白屏/静态/尺寸校验） |
| OCR | `runtime/ocr_engine`（rapidocr 直连；3.8 返回 RapidOCROutput，用 `.txts/.boxes`，勿用旧 tuple 解包） |
| 模板匹配 | `runtime/input/template_backend`（cv2 多尺度 21 级 + 1280 宽降采样） |
| 输入 | `runtime/input/win32_backend`（SendInput；`dwExtraInfo` 必须 `c_size_t`） |
| 录制 / 回放 | `runtime/input/recorder.py` / `replayer.py` |
| 视角闭环 | `runtime/angle/`（`orientation` 测角、`reset_view` 回正、`synthetic` 合成箭头） |
| 游戏启动 | `runtime/platform/windows/game_launcher.py`（读 `.env`，模板 `assets/templates/click_enter.png`） |

**步骤类型**（`_orchestrator_core.STEP_TYPES`）：
`move` / `interact` / `verify` / `portal` / `trajectory`。
三处白名单必须同步：`_orchestrator_core.STEP_TYPES`、
`knowledge_validation.ALLOWED_STEP_TYPES`、`ingest/compiler/map_converter.STEP_TYPES`。

**界面**：三页导航——**出击**（目标树 / 录制 / 实时观测 / 时间线）、
**藏宝图**（地图集 + 刷新 / 地图导入 / 校验）、
**作坊**（回放灵敏度 / 路径与默认地图 / 健康检测 / 提示音）。

## 数据现状

| 数据 | 现状 |
|---|---|
| 地图集（7 张） | 1504 个点位，**1473 个是骨架空位**（`status=empty`、无坐标）——真实数量底账，是待填清单，**不是垃圾数据，勿删** |
| `08_custom` | 使用者自定义地图：点位用 `trajectory` 字段指向录制的轨迹 JSON |
| `knowledge/source/black_tower_test` | 执行用知识包，保留 `chest_A` 演示目标 |
| 轨迹 | `DATA_ROOT/knowledge/trajectories/*.json`（可写副本，不在仓库里） |

**结论**：官方地图只有数量、没有坐标 → 不可执行。**真正能跑的目标 = 使用者自己录的轨迹**。

## 环境

- 唯一环境 = `m7_venv`（Python 3.14.2）。名字是历史遗留，它是**本项目自己的环境**，勿因名字误删。
  位于 `../PomExpress_local/m7_venv/`，可直接调 `Scripts\python.exe`。
- 启动：双击 `start_pom_express.bat`；或 `..\PomExpress_local\m7_venv\Scripts\pythonw.exe -m app`。
- **坑：Python 3.14 生态**——旧版 numpy/opencv/pillow 在 3.14 无预编译 wheel，
  pip 走源码编译必失败。必须用 `requirements.txt` 当前的 3.14 兼容版。升级前先确认有 cp314 wheel。
- **坑：验证依赖要用真实 `import`，不能用 `importlib.metadata`**（dist-info 残留会误判"已装"）。

## 打包

PyInstaller onedir → `PomExpress.exe` + `_internal/`（约 461 MB）。
产物落**项目外部** `F:\dsh workspace\STAR\_build\PomExpress\`，不要往项目本体倒。
构建与分发细节见 `docs/打包与分发.md`。

- `PomExpress.spec` 的 `hiddenimports` 必须显式列出**运行时动态导入**的模块
  （`app.validate_all`、`ingest.compiler.*`），静态分析收不到，漏了就 ImportError
- 打包版 `sys.executable` 就是 exe 本身，**不能用 `[exe, "-m", "模块"]` 调子进程**——
  参数会被启动器当未知参数忽略并开出一个新 GUI 窗口（功能静默失效）。
  统一走 `app/launcher.py` 的 `--run <模块>` 桥，GUI 侧由
  `gui/pages/world_graph.py::_module_argv` 按 `sys.frozen` 选通道
- 打包版跳过「运行环境」预检（自带解释器与依赖，否则误报 critical 弹框拦启动）
- 提权仅传 `argv[1:]`（源码模式 `argv[0]` 是脚本路径必须原样传；打包后它是 exe 路径）

## 命令行

```bat
..\PomExpress_local\m7_venv\Scripts\python.exe -m app --selftest      :: 自检
..\PomExpress_local\m7_venv\Scripts\python.exe -m app.validate_all    :: 知识包 + 攻略库全量校验
```

## 术语

| 术语 | 含义 |
|------|------|
| 骨架空位 | 地图集里 `status=empty`、无坐标的占位点位（真实数量底账） |
| G3 门槛 | 真机任务前的能力检查：**硬拦** window/capture/ocr；warning 提示 foreground/admin/input L0-L2 |
| L0/L1/L2 | 光标注入 / SendInput 注入 / 游戏响应探测（L2 会按 ESC——仅 gate 开启） |
| pkg_key | 完成状态持久化域（知识目录 sha256——切地图自动隔离） |
| HUD | 游戏窗口左下角日志层（F10 紧急停止 + 点击穿透） |
| F10 | 全局紧急停止热键（keyboard 库——非 RegisterHotKey） |
| watchdog | SessionWatchdog 120s 事件静默 → deadlock 中断 |
| emergency | EmergencyMonitor 光标/前台/Esc 检测 → 人工介入暂停 |
| B1 修复 | 模板未命中 → 拒绝坐标兜底 → **fail-closed**；坐标仅作复盘留档 |

## 关键设计决定

- 提权用 PowerShell RunAs（`ShellExecuteW runas` 本机静默失败）
- 窗口几何恢复必须**同时钳制尺寸与位置**到屏幕可用区：只钳位置不够，
  历史保存的尺寸可能大于当前屏幕，会把窗口底部（含页脚按钮）切到屏幕外。
  最终收口放在 `showEvent`（那时 `frameGeometry` 才真实），全程只用 Qt 的逻辑像素
  API——与 Win32 物理像素混算会算错（本机 devicePixelRatio = 1.25）
- 单实例锁（`QSharedMemory`）在**唤醒旧实例失败时必须接管锁**：旧实例若已卡死，
  它占着锁却无法交互，若直接退出用户就永远打不开（只能去任务管理器杀进程）
- 模板阈值默认 0.60（实测命中在 0.72–0.81）
- 完成状态持久化：`mission_state:{pkg_key}` 单 key 原子（QSettings）
- 连通性检查：有向 portal 图，仅图中节点
- 轨迹回放（`runtime/input/replayer.py`）：归一化坐标 + 相对视角位移；
  3px 累积 + 0.1s 时间阈值；chord 倒序释放；末帧 force_flush + half-away-from-zero 舍入；
  损坏 trajectory 600s 硬截断
- VK 表（`runtime/input/win32_backend.py:_VK_TABLE`）：方向键 / F1-F12 / 小键盘 /
  修饰键左右分开 / 常用符号 / 扩展键白名单（`_EXTENDED_VKS`）；
  `numpad_enter` 走 scan 0xE01C + EXTENDEDKEY 专路（与主回车共享 VK 0x0D）

## 踩过的坑（勿重蹈）

- `bat` 必须 ASCII + CRLF（UTF-8 中文注释破坏 cmd 解析）
- argv 拼接必须加引号（含空格路径会被截断）
- `all(空dict)` 恒 True → 空目标误报 all_done
- `make_event` 事件类型必须注册（漏注册 → 任务必 crashed）
- qfluentwidgets ComboBox 无 `setEditable`，可编辑下拉一律用原生 QComboBox
- 函数内 import 会让名字局部化（`UnboundLocalError`）
- `hash()` 跨进程不稳定（seed 用 sha256）
- **QSettings 必须双后端**：受限环境（沙箱/CI/无权限）注册表写入抛 `AccessError`，
  必须回退 ini 文件；读侧同样要处理。写入静默失败 = 用户以为存了下次却没了，是灾难
  （参考 `gui/pages/settings.py:_SettingsStore`、`gui/sound.py:is_enabled`）
- `tempfile.mkdtemp()` 在受限环境被拦（系统 tmp 不可写）——临时目录一律落副产物区
  `PomExpress_local/logs/`，统一入口 `config.settings.data_path()`
- **「先建资源、后用资源」的脚本不要在 import 期快照路径**：路径与存在性判断
  一律放函数体内动态解析（曾因此让依赖安装整段被跳过、环境成空壳）
- **临时目录会再生**，删一次不等于根治；**临时文件不得落 `docs/`**
  （`docs/` 只放文档，运行产物与草稿一律落副产物区）
- **删代码前先全库 grep 引用，删知识数据后必须跑 `-m app.validate_all`**：
  曾按「workflow 步骤的 template 字段」判定模板是否仍被引用，
  漏了 `portals.json` / `landmarks.json` / verify 信号三类来源，
  删掉仍在用的模板导致校验失败。validate_all 是唯一会报告「引用悬空」的检查。
