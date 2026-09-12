# 第三方素材与许可（待决策）

> 本文件只记录**已核实的事实**与**可选方向**，供仓库所有者决策。
> **未做任何授权变更** —— 仓库当前仍无 LICENSE 文件（GitHub API `license: null`）。

## 1. 事实：仓库内有一份 GPL-3.0 来源的素材

| 素材 | 来源 | 许可 | 现状 |
|---|---|---|---|
| `assets/map_templates/` 368 张地图/UI 模板图 | [linruowuyin/Fhoe-Rail](https://github.com/linruowuyin/Fhoe-Rail) | **GPL-3.0** | **已入库**，无来源声明、无许可声明 |

核实方式：GitHub API `/license` 返回 `license.key = "gpl-3.0"`。

仓库另有 `assets/templates/click_enter.png` 与 `assets/logo.*`，均为本项目自有内容。

## 2. 事实：本仓库当前状态

- **无 LICENSE 文件**（`git ls-files` 匹配 `^LICENSE|NOTICE|COPYING|THIRD` = 0 条）。
- **`assets/map_templates/` 的 368 张图已入库**（`git ls-files assets/fhoe` = 368 条），无任何来源或许可声明。
- **仓库为 public**（GitHub API `private: false`），即构成「向公众分发」。
- `runtime/` 为独立实现，未搬运上游源码；`runtime/drivers/local/` 只是 5 个自研薄接口。

## 3. 风险要点（非法律意见）

GPL-3.0 是**强 copyleft**：向公众分发「基于该作品的作品（work based on the Program）」时，
通常要求按 §5 以 GPL-3.0 授权**整个作品**并提供 Corresponding Source。

需要所有者判断的两点：

1. **`assets/map_templates/` 的 368 张图**是否构成 Fhoe-Rail 作品的衍生/复制部分
   （模板图是该工具的功能资产，一般认为属于其作品内容）。
2. 本项目「**功能自研、仅参考思路**」的定位能否支撑「非衍生作品」的立场
   （`runtime/` 为独立实现是有利事实；但**素材图片直接入库**这一点不利于该立场）。

## 4. 可选方向（供决策，均未执行）

| 方向 | 做法 | 代价 |
|---|---|---|
| **A. 整体改用 GPL-3.0** | 加 `LICENSE`（GPL-3.0 全文）+ `THIRD_PARTY.md` 声明素材来源 | 项目整体变为 GPL-3.0，第三方复用受限；但最省事且合规性最强 |
| **B. 移除内化素材** | 从仓库删除 `assets/map_templates/`，改为运行时首次使用前从上游获取（或让用户自行提供） | 失去「开箱即用」；需改模板加载路径与启动流程 |
| **C. 先咨询再定** | 暂停处理，找懂 GPL 的人评估「仅参考思路 + 素材内化」的定性 | 期间风险敞口仍在（仓库公开） |
| **D. 暂不处理** | 保持现状，仅在本文件留档 | 风险敞口不变 |
