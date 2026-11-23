# 魔物娘TD 完整汉化

《モンスター娘TD（魔物娘TDX）》PC 版的**中文汉化**，主界面图片ui未汉化（施工中）

---

## 怎么装

**方式一：下发行包

到 [Releases](https://github.com/yu6096516-arch/momusu-zh/releases/tag/v1.0) 下载 `MonmusuTD_zh-Hans_v1.0.zip`（59 MB），
把里面的 `BepInEx` 文件夹覆盖到游戏根目录即可（已含插件、译文、字体、配置）。

**方式二：只用仓库译文**

1. 先装好 **BepInEx**（IL2CPP 版）到游戏根目录
2. 把本仓库 `translations` 整个文件夹放进：

```
<游戏根目录>\BepInEx\plugins\MonsterMusumeTDMod\translations\
```

3. 需要 **MonsterMusumeTDMod.dll**（汉化插件本体，不在本仓库）
4. 启动游戏即生效

> 插件源码在仓库 `source/`，二进制请到原作者处获取：`Berumic/MomusuMod-Android-workflow`

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
- 寝室剧情全量润色收尾
- 角色译名统一：津狩、栞莉音、奈梨香、风香

**2026-11-17**
- 活动剧情逐条润色（258 批）
- 全部界面词、技能、特性、武器翻译完成

---

## 其它

浏览器注入式汉化器（另一套方案，不改游戏文件）见 `browser-translator/`，
分发包与源码在 [Releases](https://github.com/yu6096516-arch/momusu-zh/releases/tag/v0.1.0)。

---

## 支持

如果这个汉化帮到你，可以在 **爱发电** 请我喝杯咖啡：

<https://ifdian.net/a/mmstd>

---

## 鸣谢

- **极大理想环**：浏览器汉化提供者
- **Alks丶**：UI 汉化
- **浅海蜗牛**：主线汉化
- **肉包**：全角色、全皮肤、全插件魔物娘TD账号提供
- 插件与初始译文框架：[Berumic](https://github.com/Berumic)（`MomusuMod-Android-workflow`、`MonsterMusumeTDChineseTranslation`）——魔物娘安卓汉化提供
- 字体：Alimama 方圆体（重做图集以支持生僻字）
