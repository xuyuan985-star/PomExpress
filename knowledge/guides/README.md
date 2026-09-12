# 攻略存储体系（参照米游社互动地图结构）

> 原则：目录/文件/字段命名**对齐互动地图官方概念**，不另造体系。
> 互动地图结构：`map（地图）→ area（区域/楼层）→ point（点位，带类型标签）`

---

## 一、层级对应

| 互动地图概念 | 本项目存储 | 说明 |
|---|---|---|
| map（map_id，如 146） | `maps/<map_id>/` | 大地图；`map.json` 保留 `interactive_map_id` 引用 |
| 区域/楼层（area/floor） | `areas/<area_id>.json` | 游戏内可切换区域 |
| 点位类型（label tree） | `points/<type>.json` | 每类型一个文件 |
| 点位（point） | 数组项 | `id/name/type/region/x/y/rarity/tier/note` |

## 二、目录结构

```
knowledge/guides/
├── README.md                     ← 本文件（体系说明）
├── _schema/
│   └── guide.schema.json         ← 存储 schema（校验用）
├── maps/
│   ├── 01_starlight_express/     ← 星穹列车（3 区域）
│   ├── 02_herta_space_station/   ← 空间站「黑塔」（5 舱段）
│   │   ├── map.json
│   │   ├── areas/                ← base_zone / master_zone / ...
│   │   └── points/
│   │       ├── chests.json       ← 宝箱（normal/rare/precious/luxurious）
│   │       ├── warptrotters.json ← 扑满
│   │       ├── puzzles.json      ← 解密/机关
│   │       ├── books.json        ← 书籍/阅读物
│   │       ├── enemies.json      ← 强敌/首领
│   │       ├── achievements.json ← 成就
│   │       ├── quests.json       ← 任务（支线/隐藏）
│   │       ├── shops.json        ← 商店/商人
│   │       └── anchors.json      ← 传送锚点
│   ├── 03_jarilo_vi/             ← 雅利洛-Ⅵ（上/下层区 10 区域）
│   ├── 04_xianzhou_luofu/        ← 仙舟「罗浮」（12 区域）
│   ├── 05_penacony/              ← 匹诺康尼（13 区域）
│   ├── 06_amphoreus/             ← 翁法罗斯（18 区域）
│   └── 07_dream_paradise/        ← 二相乐园（8 区域）
```

## 三、地图层级（官方结构，2026-08 版本）

| 地图 | 区域 | 备注 |
|---|---|---|
| 01 星穹列车 | 观景/客房/派对车厢 | |
| 02 空间站「黑塔」 | 主控/基座/收容/支援/禁闭舱段 | M1-A 验证区 |
| 03 雅利洛-Ⅵ | 上层区 6 + 下层区 4 | areas 带 group=upper/lower |
| 04 仙舟「罗浮」 | 12 区域 | 长乐天/鳞渊境/金人巷等 |
| 05 匹诺康尼 | 13 区域 | 黄金的时刻/流梦礁等 |
| 06 翁法罗斯 | 18 区域 | 3.0 起，含双形态 |
| 07 二相乐园 | 8 区域 | 新开放 |

> 各 `map.json` 的 `interactive_map_id` 待互动地图 API 打通后回填（含用户分享的 146）。

| 对象 | 规范 | 示例 |
|---|---|---|
| 地图目录 | `NN_<map_slug>`（NN=整理顺序 01-99） | `01_herta_space_station` |
| 区域文件 | `<area_slug>.json`（snake_case） | `base_zone.json` |
| 点位类型文件 | 固定 9 类 | `chests.json` |
| 点位 id | `<map_slug>_<area_slug>_<seq>`（3 位序号） | `herta_base_zone_001` |
| 交互地图引用 | `map.json["interactive_map_id"]` | `146`（待打通 API 后核对） |

## 四、点位字段 schema

```json
{
  "id": "herta_base_zone_001",
  "name": "基座舱段·进门左侧宝箱",
  "type": "chest",
  "region": "base_zone",
  "x": 0.42, "y": 0.35,
  "rarity": "normal",
  "tier": "T0",
  "note": "主线必经，顺手开"
}
```

- `x/y`：地图归一化坐标（0~1，互动地图经纬度换算后）
- `rarity`：normal/rare/precious/luxurious/warptrotter（仅宝箱/扑满类）
- `tier`：T0 必做 / T1 推荐 / T2 可选 / T3 图鉴
- `link`（可选）：互动地图点位分享链接

## 五、填充方式（数据来源）

| 来源 | 状态 |
|---|---|
| 互动地图 API（需 DS 签名+登录） | 受阻（-502）；打通后按 map/point/list 导入 |
| VLM 截图采集（capture_frames 链路） | 可用 |
| B 站攻略（ingest/bilibili.py） | 可用 |
| 人工补录 | 按规范逐条 |
