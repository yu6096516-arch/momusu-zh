# 魔物娘TD 完整汉化

《モンスター娘TD（魔物娘TDX）》PC 版的**全剧情 + 全界面中文汉化**。



---

## 汉化了多少

**总计 4,393 个译文文件，22 万条文本。**

| 部分 | 数量 | 内容 |
|---|---:|---|
| 寝室剧情 | 
| 活动剧情 | 
| 主线剧情 | 
| 特殊剧情 | 
| 当期活动 | 
| 技能与特性| 
| 技能数据| 
| 特性数据 |
| 界面文本 |
| 角色名 | 
| 子技能 | 
| 其它界面表 |  | 道具、关卡、图鉴、商店、帮助… |

**连字体都修了**：

---


## 怎么装

1. 先装好 **BepInEx**（IL2CPP 版）到游戏根目录
2. 把本仓库 `translations` 整个文件夹放进：

```
<游戏根目录>\BepInEx\plugins\MonsterMusumeTDMod\translations\
```

3. 需要 **MonsterMusumeTDMod.dll**（汉化插件本体，不在本仓库，见下）
4. 启动游戏即生效

> 插件是本项目使用的加载器，源码在仓库 `source/`，二进制请到原作者处获取：`Berumic/MomusuMod-Android-workflow`

---

## 怎么用

| 操作 | 效果 |
|---|---|
| 启动游戏 | 自动加载中文 |
| 按 **F6** | 手动重载/同步译文（改完文件不用重启） |
| 按 **F8** | 导出当前界面的文本快照（排查漏翻用） |

配置文件 `BepInEx\config\MonsterMusumeTDMod.cfg`：

```ini
[Translation]
Enabled = true

[Translation.Font]
FontAssetPath = alimama      # 中文用这套字体

[Translation.Update]
Enabled = false              # 自动更新：建议关，别让它覆盖本地
ManualSyncWithF6 = false     # F6 只重载本地
```

> ⚠️ `AssetBaseUrl` 一定留空——否则插件会去下载原版字体，把补好字形的字体覆盖回去。

---

## 更新记录

**2026-11-23**
- 主线第 10–14 章补全（2,694 条，此前是空壳）
- 活动剧情 118 个场景全量润色（11/14 新角色、11/16 新活动）
- 新增 11/16 更新内容 1,367 条（新活动「行乐游览-孤岛净化谭-」、礼包、新角色剧情）
- 字体包修补字形（「栞」等生僻字不再显示方框）

**2026-11-19**
- 寝室剧情全量润色收尾（2,031 个场景 / 93,416 条）
- 角色译名统一：津狩、栞莉音、奈梨香、风香

**2026-11-17**
- 活动剧情 1,835 个文件逐条润色（258 批）
- 全部界面词、技能、特性、武器翻译完成

---


---

## 鸣谢

- 插件与初始译文框架：[Berumic](https://github.com/Berumic)（`MomusuMod-Android-workflow`、`MonsterMusumeTDChineseTranslation`）
- 字体：Alimama 方圆体（重做图集以支持生僻字）
