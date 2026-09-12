# 帕姆巡宝 PomExpress

崩坏：星穹铁道（Windows）宝箱收集自动化助手。

半自动工作方式：**你自己录一次跑图轨迹，之后反复回放复现**。执行以模板匹配 +
差分验证为主，不调用任何视觉模型、不依赖任何外部项目。

---

## 一、运行

### 方式 A：独立程序（推荐）

发行包是**一个文件夹 + 一个 exe**，拷过去双击即用，不需要装 Python。

```
PomExpress\
    PomExpress.exe      双击运行
    _internal\          依赖与只读资源（必须一起拷，不能只拷 exe）
```

启动会**请求管理员权限**（向游戏窗口注入输入需要）；拒绝提权则以浏览模式打开。

### 方式 B：从源码运行

| 项 | 要求 |
|---|---|
| 系统 | Windows 10 / 11（用 Windows API：窗口枚举、SendInput、PrintWindow） |
| Python | 3.12 以上（开发环境 3.14.2） |
| 权限 | 真机执行（注入输入）需要**管理员权限** |

```bat
git clone https://github.com/xuyuan985-star/PomExpress.git
cd PomExpress
start_pom_express.bat
```

`start_pom_express.bat` 自动完成：建副产物区 → 找 Python → 建环境 → 装依赖 → 提权启动。
依赖清单见 `requirements.txt`。

也可手工：

```bat
mkdir ..\PomExpress_local
py -3.14 -m venv ..\PomExpress_local\m7_venv
..\PomExpress_local\m7_venv\Scripts\python.exe -m pip install -r requirements.txt
..\PomExpress_local\m7_venv\Scripts\pythonw.exe -m app
```

---

## 二、上手：录一条轨迹然后回放

1. 启动游戏，进入你想跑的地图
2. 打开**出击**页 → 点「● 录制轨迹」→ 3 秒倒计时后开始手动跑图
   （WASD 移动、转视角、点开箱子都照常操作）
3. 按 **F10** 或再点按钮结束录制 → 轨迹存进你的自定义地图
4. 回到目标树勾选刚录的目标 → 开始执行，程序按轨迹复现

**F10 是全局紧急停止**：任何时刻按下都会立刻释放所有按键、停止任务与录制。

> 为什么必须自己录：官方地图数据只有**箱子数量**，没有坐标（1473 个占位点位）。
> 坐标无法从公开接口取得，所以「自动跑图」这条路线不可行——
> 能跑的目标就是你自己录的轨迹。

---

## 三、界面

三页导航：

| 页面 | 用途 |
|---|---|
| **出击** | 目标树（勾选要跑的目标）、轨迹录制、实时观测卡片、事件统计与时间线、运行健康状态 |
| **藏宝图** | 地图集展示；底操作行：刷新统计 / 地图导入 / 校验 |
| **作坊** | 回放灵敏度、路径与默认地图、健康检测、提示音开关 |

关键操作：

- **录制轨迹**：见上一节。轨迹保存在可写数据区，不入库。
- **紧急停止**：**F10**（系统级全局热键，游戏内也可触发）。
- **提示音**：作坊页开关。任务失败、等待人工处理、异常结束时会发声提醒。
- **健康检测**：作坊页点按钮运行。**零副作用**——不抢前台、不动鼠标、不向游戏按键。

---

## 四、目录布局

项目本体与工作副产物**物理分离**，清理残留只需处理副产物区一个目录：

```
<父目录>\
├── PomExpress\          项目本体（= 仓库）
└── PomExpress_local\    工作副产物
    ├── m7_venv\         Python 环境
    ├── knowledge\       可写知识副本（含你录的轨迹、自定义地图）
    ├── logs\ state\ settings\ reports\
    ├── runtime.db
    └── .env             本机配置
```

代码侧由 `config/settings.py` 的两个根实现：只读资源读**项目本体**，
可写数据写**副产物区**。副产物区不存在时自动回退为写入项目根。

### 项目本体结构

```
app/          入口、启动流程、前置检查、知识包校验
config/       配置读取（路径分层、日志脱敏）
gui/          PySide6 界面（三页导航 + HUD + 全局热键 + 提示音）
runtime/      运行时核心
  win_capture/vision_quality/ocr_engine    截图与识别
  input/       输入注入、模板匹配、轨迹录制与回放
  angle/       视角闭环（测角 / 回正 / 合成箭头）
  observers/ guards/ events/ infra/        观察、守卫、事件总线、熔断
  platform/windows/                        窗口、坐标、提权、游戏启动
ingest/       离线管线（地图 转换、点位图校验）
knowledge/    地图集与执行用知识包
assets/       map 模板素材、应用图标、运行期模板
docs/         文档
```

---

## 五、数据现状与限制

| 数据 | 数量 | 说明 |
|---|---|---|
| 地图集 | 1504 点位 | **其中 1473 个是骨架空位**（只有数量、没有坐标）——真实数量底账，待填清单 |
| `08_custom` | 你自己录的目标 | 用 `trajectory` 字段指向轨迹 JSON |
| `knowledge/source/black_tower_test` | 1 个演示目标 | `chest_A`，用于链路自检 |

**已知限制**

- **官方地图不可自动执行**：只有数量没有坐标，需要你录轨迹。
- **后台截图对部分 DirectX 渲染路径无效**：`PrintWindow` 可能返回空帧或陈旧帧，需前台截图兜底。
- **输入注入受权限约束**：低完整性进程无法向高完整性窗口注入（UIPI 拦截），需以管理员运行。
- **模板未命中时不会用坐标兜底点击**（安全设计，fail-closed）；坐标只用于失败诊断。
- 游戏自动拉起需在 `.env` 配置 `GAME_PATH`（不配则只提示手动先开游戏）。

---

## 六、开发约定

- **分层**：`app → gui → runtime → ingest → knowledge`。
- **事件**：事件总线同步广播，订阅者异常互相隔离。
- **界面线程**：本环境 Qt 跨线程信号不可靠，一律用主线程轮询消费。
- **注释风格**：见 `docs/comment-style.md`——注释写设计原因与边界条件，
  不写修改轮次与任务编号，不用表情符号。
- **目录纪律**：`docs/` 只放文档；运行产物与临时文件一律落副产物区。
- **改动知识包后必须跑校验**：

```bat
..\PomExpress_local\m7_venv\Scripts\python.exe -m app.validate_all
```

---

## 七、第三方与许可

- 实现思路参考 [外部参考实现](https://github.com/linruowuyin/外部参考实现)（GPL-3.0），
  相关机制在本项目内重新实现。
- `assets/map_templates/` 内的地图与界面模板素材来自 外部参考实现。
- 许可状况与待决事项见 `docs/THIRD_PARTY_LICENSING.md`。
