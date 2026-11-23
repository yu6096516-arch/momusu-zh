# 魔物娘TD 汉化

《モンスター娘TD（魔物娘TDX）》PC 版中文汉化资料库：译文、发布工具、插件源码。

---

## 目录结构

| 目录 | 内容 |
|---|---|
| `translations/` | 全部译文（插件读取的就是这里）+ `manifest.json`（哈希清单） |
| `tools/` | 发布、体检、上游合并等脚本，以及术语表、风格指南 |
| `source/` | 汉化插件 `MonsterMusumeTDMod` 的 C# 源码（上游：[Berumic/MomusuMod-Android-workflow](https://github.com/Berumic/MomusuMod-Android-workflow)） |

### translations 内部

```
translations/
  manifest.json           哈希清单：路径 → {sha256, size}，插件靠它判断要不要更新
  styles.json             171 条 UI 字体/样式规则
  scenarios/              剧情（活动 / 寝室 / 主线 / 特殊 / 各期活动备份）
  UICanvas/               约 100 张界面数据表（ui_text、unit_skill、skill_data…）
  names/                  角色名
  subskills/              子技能
```

---

## 插件怎么用这个库

插件（BepInEx）读取 `BepInEx\plugins\MonsterMusumeTDMod\translations\`，
启动时若开启自动更新，会拉取本仓库的 `manifest.json`，逐文件比对 sha256：

- 不一致 → 下载覆盖
- 本地有、远端没有 → 删除本地

所以 **改了译文必须先重新生成 `manifest.json` 再推送**，否则下次启动会被回滚。

---

## 更新流程

```powershell
# 1 改译文（编辑 translations 下对应 zh_Hans.json，键禁止改动）

# 2 体检
python tools\体检.py

# 3 生成发布目录 + 重算 manifest + 推送（双击）
tools\publish_update.bat
```

游戏内按 **F6** 立即生效，或重启游戏自动同步。

### 从上游进货（原作者更新了新活动）

```powershell
python tools\拉上游.py            # 预演，只统计
python tools\拉上游.py --apply    # 真拉：只补本地缺的键，不覆盖已有译文
```

---

## 铁律

1. **键（日文原文）一个字符都不许改**——它是查表的索引
2. 值的**换行数**必须等于键的换行数（游戏按行渲染）
3. 每行的**标签**（`<interval>` `<size>` `<ruby>` `<color>`）位次必须与键逐行一致
4. 改完**先发布、后进游戏**，否则同步会覆盖本地改动
5. 大改之前先备份 `translations/`

---

## 术语约定（节选）

| 日文 | 中文 |
|---|---|
| ご主人 / ご主人くん | 主人 / 主人君（仅铃） |
| モンスター | 魔物（不用「怪物」） |
| 密猟者 | 偷猎者 |
| シンギュラリティ | 奇点 |
| ギュラ | 奇拉 |
| なの（露露口癖） | 呐喏 |
| モン（巨兽口癖） | 哒蒙 |
| っす / であります | 的说 |
| だち | 哒嘁 |

完整表见 `tools/翻译术语表 (1).md`、`tools/翻译风格指南.md`。

---

## 已知问题

- 字体包 `alimama` 已修补字形（含生僻字），**不要再让插件同步字体**（配置里 `AssetBaseUrl` 留空）
- 图鉴 ID 473/474/475 头像与人物对不上的问题仍在排查
