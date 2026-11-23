# 交接报告（HANDOFF）

日期：2026-11-14 · 版本：**v0.1.0** · 适配官方构建：**`202611021000`**，同时支持
DMM（`/0/`）与 FANZA（`/1/`）两个资源分支

> **阶段三十三（2026-11-14）**：新增 quiet 控制台、官方版本自动识别、
> 玩家本地后台构建、原子激活以及**可选**便携构建运行时。两种发行包并存，
> 详见 §12。核心发行资源（Wasm / Data / 字体 / 译文）**未改动**。

---

## 1. 当前稳定版本

| 项 | 值 |
|---|---|
| 发行包（标准版） | `dist/MonmusuTDChinese-v0.1.0-win-x64.zip`，158,418,037 B，45 个文件 |
| 发行包（自动更新版） | `dist/MonmusuTDChinese-v0.1.0-win-x64-autobuild.zip`，217,246,998 B，1,660 个文件 |
| ZIP SHA-256 | 标准版 `e33af54df9b211…`／自动更新版 `8a04e4ffb06b5d…`（完整值见 `dist/release_manifest*.json`；阶段三十三重建后以 manifest 为准） |
| 译文语料 | `Berumic/MonsterMusumeTDChineseTranslation` @ `1e2a16a36c9b` |
| 剧情索引 | 4,140 个场景 / 323,928 条字符串 |
| UI 索引 | 9,602 条字符串 / 2,964 组全局配对 |
| 字体 | 主字体 5,840 字形；UI 变体 5,935 字形；图集 8192×4096（两分支共用同一套字形载荷） |
| 平台 | DMM `/0/` 与 FANZA `/1/`，共用一份发行包、一套译文 |
| 静态 prefab | 197 条文本 + 15 处字体引用，**已同步**（`staticPrefabSynced: true`） |

关键资源哈希（最后一次完整构建）：

```
共用（两平台同一份）
ntd.loader.js      799229f18da3be005e6602d389f3d68caf71c8ec42d7b8d61d112ec6b12d20d9
                   （阶段三十二起内嵌按分支生成的运行时绑定配置；Wasm/字体/数据未变）
font_ui.bytes      09a41862522ec2b2fe8166da6405780e1f1bf8ec1fc9c387eb8a03309d1b4347
ui_detail.bytes    e3825ddf3752d6b9e2767c1578151ae1802870ced31d0b6133508fc08a959440

DMM  /0/
ntd.patched.wasm   dd76b87d75fc3498b45e2234ee4830413ca54aec16e9c3e9854110899e3fbd0d
ntd.data.gz        2c312a0d9bc1065460e6b36cdc5a7d038f1e32c4d3181c8cf870fd1e94f6e1f3

FANZA /1/
ntd.patched.1.wasm.gz  af71029fead68b1a8d593c1648453fb83ef6550b6e67862f3df71f94fbdc5c30
ntd.data.1.gz          2158118d585bfde6db345d8ac532e8db35187f88c717d59af935e7533831d0c8
```

### 1.1 两个平台的实际差异（实测）

| 项 | DMM `/0/` | FANZA `/1/` | 是否共用 |
|---|---|---|---|
| 官方 `ntd.wasm`（解压） | 53,481,333 B | 53,481,733 B（+5 函数） | **否** — 各自打补丁 |
| `global-metadata.dat` | 12,991,256 B | 12,991,692 B | **否** — 各自跑 Il2CppDumper |
| 官方 `ntd.data` 的 `data.unity3d` | 7,878 对象 | 7,878 对象，仅 `PlayerSettings` 不同 | 字体载荷共用，容器各自保留 |
| 官方 `ntd.loader.js` | 13,114 B | 同一 SHA-256 | 是 |
| 译文索引 / 字体 / prefab / 按钮字体 | — | — | 是（一套译文） |

9 个 Hook 在两分支上都定位成功，但函数索引不同（`/1/` 普遍 +1，因为多了 5 个函数）：
H1 4603→4604、H4 107830→107831、H5 102370→102371、H6 106421→106422、
H7 106420→106421、H8 106414→106415；H2 694、H3 65961、H9 8644 不变。

## 2. 功能状态

| 功能 | 状态 |
|---|---|
| 剧情 / 选项 / 折行 / 数字模板 | 可用 |
| 菜单 / 任务 / 角色名 / 技能（UI 索引 + 路径匹配） | 可用 |
| 角色详情静态标签（prefab 内嵌文本） | **可用且自动重建**（本轮新增） |
| 中文 SDF 字体（5840 字形，8192×4096） | 可用 |
| 译文更新自动补字 | **可用**（无人工步骤） |
| Wasm Hook 动态定位（9 Hook + 2 import + 分配器） | 可用，构建结果与发行版字节一致 |
| 官方版本更新后重新定位 | 可用（`rebuild_wasm.bat --update <版本>`），但 JS 侧仍需人工复核 |
| 浏览器实机验证 | **NOT RUN** |

## 3. 静态 prefab 自动化（本轮重点）

**结论：已实现（情况 A），不是 WARNING 降级。**

历史研究仓库里已有两段经过实机验证的实现，本轮把整条链路迁到 `tools/prefab/`：

1. `build_font_compat.patch_prefab()` 的字体引用改写（4 组详情标签 → UI 字体变体 pathID 725250001）；
2. `patch_prefab_text.py` 的 `TextMeshProUGUI.m_text` 改写（`int32 长度 + UTF-8 + 4 字节对齐`，
   逐条解析、逐条回读）。

合并为一个脚本 `tools/prefab/build_ui_detail.py`，输入是**官方原始 bundle**
（`tools/prefab/assets/ui_detail.pristine.bytes`，SHA-256 `d3cab9f2…`），输出 `ui_detail.bytes`。

**正确性证据**：用当前语料重建后与已实机验证的发行文件**逐字节相同**
（`e3825ddf…`，197 条替换 + 15 处字体引用）。6 项校验全部通过：
CAB 名不变、对象数不变、每条替换可读回、其它对象零改动、15 个标签都指向 UI 字体、UI 字体存在且名字正确。

**失败时**（例如官方换了这个 bundle）：不发布损坏资源，保留上一版 `ui_detail.bytes`，
在 `UPDATE_REPORT.md` 顶部输出 WARNING（含受影响的键表）、在 `dist/release_manifest.json`
写入 `staticPrefabSynced: false` 与 `warnings[]`，打包继续。
**基线不会被改成"已同步"**——`write_baseline()` 在 WARNING 时沿用旧的 `static_prefab_pairs`，
所以下一次运行仍会再次尝试重建，不会把问题藏起来。

已实测的两条路径：
- 正常：改一条基线键 → 检测到变化 → 重建 → 生成文件与发行文件字节相同 → PASS；
- 故障：移除原始 bundle + 改一条基线键 → 重建失败 → WARNING + 继续打包 + 基线保持不变 → PASS（带 WARNING）。

## 4. 验证结果

全部在清理**之后**重跑。

| # | 项目 | 命令 | 结果 |
|---|---|---|---|
| 1 | Wasm 本地重建 | `rebuild_wasm.bat` | **PASS**（10.3 s，产物与发行版逐字节一致） |
| 2 | 9 Hook 动态定位 | 同上（`resolve_targets.py`） | **PASS**（9 hooks + 2 imports + 1 allocator） |
| 3 | 补丁验证 / 定位负例 | 同上 | **PASS**（verify / 58 项定位测试 / 21 项重排测试） |
| 4 | 翻译索引构建 + 全量审计 | `update_translations.bat` | **PASS**（415,050 用例 → 412,132 PASS / 2,918 UNTESTABLE / **0 FAIL**，负例 1394/1394） |
| 5 | 自动补字与字体覆盖 | `tools/test_assets.py` | **PASS**（5,840 字形，需求 4,298 字符，`glyphGaps: 0`） |
| 6 | `update_translations.bat` remote | 实机 | **PASS**（含 git fetch、索引、字体复用、打包、基线刷新） |
| 7 | 静态 prefab 未同步 → WARNING 继续打包 | 实机（注入故障） | **PASS** |
| 8 | `build_release.bat` | 实机 | **PASS**（43 文件，CRC + 白名单 + 逐文件 SHA-256） |
| 9 | 发行 manifest 校验 | `python -c …`（见 USAGE §7.2） | **PASS**（mismatches: `[]`） |
| 10 | 字体工具单元测试 | `tools/fonts/test_font_tools.py` | **PASS**（14/14） |
| 11 | 字体布局诊断 | `node test_font_layout.js` | **PASS**（15 项） |
| 12 | Bootstrap 集成 | `node test_bootstrap.js` | **PASS** |
| 13 | 剧情索引测试 | `node test_story_index.js` | **PASS** |
| 14 | 剧情拼接 / 运行时 | `node test_story_concat.js` / `test_story_runtime.js` | **PASS**（128 项 + 运行时探针） |
| 15 | 翻译运行时 | `node test_translate.js` | **PASS** |
| 16 | UI 索引测试 | `python -B test_ui_index.py` | **PASS** |
| 17 | 审计工具自测 | `python -B -m unittest test_audit` | **PASS**（25 项） |
| 18 | 资源路由（双分支 / 白名单） | `python -B tools/test_addon_routing.py` | **PASS**（39 项） |
| 19 | FANZA `/1/` 构建 | `rebuild_wasm.bat --update 202611021000 1` | **PASS**（9 Hook 全部定位，59 项定位测试） |
| 20 | FANZA 数据容器移植 | `tools/fonts/graft_branch_data.py` | **PASS**（6 项校验；在 `/0/` 上复现发行文件**逐字节相同**） |
| 21 | 双分支发行包 | `build_release.bat` | **PASS**（45 文件，ZIP 158,410,842 B，manifest 0 mismatch，`runtimeConfigBranches: ["0","1"]`） |
| 22 | 运行时绑定解析（两分支） | `tools/wasm/test_runtime_config.py` | **PASS**（30/30；`/0/` 结果与旧手写表逐项相同） |
| 23 | 运行时绑定负例 | 同上 + 真实 `build_release.bat` | **PASS**（歧义/签名/arity/字面量/跨分支/字段改名/哈希不符 全部拒绝） |
| 24 | 配置与打包 Wasm 绑定 | `build_release.py::check_runtime_config` | **PASS**（伪造 `/1/` 哈希 → 退出 1 并指出两个 SHA） |
| 25 | 浏览器实机（DMM / FANZA） | — | **NOT RUN** |

`test_font_layout.js` / `test_bootstrap.js` / `test_audit.py` 之前没有被任何入口调用，
本轮已接进 `update_translations.bat` 的测试步骤（现在每次更新都会跑）。

## 5. 已知限制

1. **版本绑定 `202611021000`**：`resources/assets.json` 的 `version_paths`（两个分支前缀）
   仍是手写的。官方换版本后代理一个资源都不会替换，游戏能启动但中文全消失，
   **且没有报错**——这是最容易漏的一步，也是整个系统唯一"静默失效"点。
   **两个分支必须分别重建**：`/1/` 的 Wasm 比 `/0/` 多 5 个函数，IL2CPP 元数据也不同，
   把 `/0/` 的补丁当 `/1/` 用会直接错调函数。
2. **JS 运行时绑定已改为构建期生成（阶段三十二）**：24 个函数表槽位、6 个 icall 字面量地址、
   2 个缓存指针、20 个托管字段偏移，现在都由 `tools/wasm/resolve_runtime.py` 从当次构建的
   `script.json` / `dump.cs` / Wasm 解析，写在 `monmusu_cn_runtime_config.js` 里并按分支绑定
   Wasm SHA-256。**实测旧手写表在 `/1/` 上有 19 个槽位是错的**（`call_indirect` 会用错槽位，
   不是"字体失效"而是可能踩内存），这是本轮修掉的真问题。
   仅剩 4 个 IL2CPP/ABI 常量仍为 `VERIFIED_STATIC`（`m_CachedPtr`、`Il2CppClass::name`、
   `Il2CppString` 的 length/chars）；换 Unity 大版本才需要重看。
3. **静态 prefab 依赖官方原始 bundle**：官方若替换 `c8554d98…bytes`，
   `tools/prefab/assets/ui_detail.pristine.bytes` 必须同步更新，否则重建失败（会 WARNING）。
4. **按钮字体** `resources/font_buttons.bytes`：304 个固定标签字形，不由语料驱动，无自动补字路径。
5. **字体容量**：8192×4096 已用 3,245 / 4,096 行。不足时自动纵向扩到 8192×8192；
   再不够需要第二张图集（本静态字体资产不支持，必须人工设计）。
6. **浏览器实机 `NOT RUN`**：所有自动化只覆盖资源层与数据层。
7. **自动兼容有明确边界**：函数重排、索引位移、数据地址变化能自动适配；
   目标方法消失/被内联、import 协议改变、字段语义变化、Unity 大版本升级会**明确失败**
   （`UNRESOLVED`，构建直接停并删掉候选 Wasm），不会静默用旧值。自动构建通过仍**不等于**
   可发布：翻译覆盖、字体字形、静态 prefab、资源路径要各自验证，最后必须人工做浏览器验收。

## 6. 删除的文件及原因

| 删除 | 原因 |
|---|---|
| `dist/MonmusuTDChinese/`（105 MB） | 一次手工解压留下的陈旧副本；**内含 `data/mitmproxy-ca.pem` 私钥**，属于必须清除的秘密 |
| `work/translations_src/`（6,311 文件） | 早期阶段遗留的第二份语料克隆，已被 `work/translations/src/repo-<hash>` 取代 |
| `work/` 下的审计缓存与旧日志（约 700 MB） | 可再生；`audit_all_translations.py` 每次重新扫描 |
| `__pycache__/`（5 个目录） | Python 字节码缓存 |
| `phase26_delivery.md`、`certificate_flow_update.md` | 阶段性说明，内容已被 `USAGE.md` §1/§2 与 `ARCHITECTURE.md` §2.1 完整覆盖 |
| `docs/PHASE29_DELIVERY.md`、`docs/PHASE29B_DELIVERY.md` | 阶段交付记录，已由本报告取代 |
| `docs/HOOK_AUDIT.md` | 阶段二十七审计快照，其中大量固定槽位/偏移已被动态定位取代，保留会误导维护者 |

保留：`docs/font_before_after.png`（补字前后渲染对照，19 KB，作为效果证据）。

**没有删除**任何仍被引用的文件。删除前逐一核对过引用：
`resources/ntd.test.wasm`（52 MB）虽大，但被 `test_bootstrap.js`、`test_story_index.js`、
`test_translate.js` 读取，**保留**；`story_concat_audit.py` 被 `audit_corpus.py` 导入，保留。

## 7. 保留的大型资源

| 路径 | 体积 | 说明 | 是否入库 |
|---|---|---|---|
| `tools/wasm/inputs/` | 286 MB | DMM 分支：官方原始 `ntd.wasm`/`ntd.data.gz` + `global-metadata.dat` + Il2CppDumper 输出 | **否**（`.gitignore`，用 `--update` 重新获取） |
| `tools/wasm/inputs-1/` | 286 MB | FANZA 分支：同上，独立一套，派生文件不混用 | **否**（`.gitignore`，用 `--update … 1` 重新获取） |
| `resources/ntd.patched.wasm` | 52 MB | 发行用补丁 Wasm | 是 |
| `resources/ntd.test.wasm` | 52 MB | 3 个测试读取的 stub-allocator 变体 | 是 |
| `resources/ntd.data.gz` | 38 MB | DMM 分支：含中文 SDF 字体的数据容器 | 是 |
| `resources/ntd.data.1.gz` | 38 MB | FANZA 分支：同一字体载荷移植进 FANZA 自己的容器 | 是 |
| `resources/ntd.patched.1.wasm.gz` | 14 MB | FANZA 分支补丁（`/1/` 多 5 个函数，索引不同） | 是 |
| `resources/monmusu_cn_story_index.json(.gz)` | 41 + 14 MB | 剧情索引（明文供测试/审计，gz 供发行） | 是 |
| `ref/` | 76 MB / 6,269 文件 | 上游译文语料快照（离线测试与 `target=local` 使用） | 是 |
| `tools/fonts/assets/alimama.ttf` | 21 MB | 补字字体源（SHA-256 与 cn25 构建一致） | 是 |
| `tools/prefab/assets/ui_detail.pristine.bytes` | 3.1 MB | 官方原始 prefab bundle，静态标签重建的输入 | 是 |
| `runtime/mitmdump.exe` | 28 MB | 打包进去的 mitmproxy 运行时 | 是 |

## 8. 版权与安全检查

**密钥 / 凭据**：源目录中未发现 token、Cookie、代理凭据或 `.env`。
唯一一处私钥是 `dist/MonmusuTDChinese/data/mitmproxy-ca.pem`（陈旧解压残留），**已删除**。
玩家端证书由每台机器首次运行时自行生成，ZIP 内不含证书。`.gitignore` 已屏蔽 `*.pem/*.p12/*.pfx/*.key`。

**本机绝对路径**：源码、脚本、配置、文档中未发现 `C:\Users\ASUS` 之类硬编码路径
（已全量 grep 确认）。`build/wasm/BUILD_REPORT.md` 等生成报告里会出现本机路径，但它们在
`build/`（已忽略）。

**分发风险（接手后必须自行确认，本次未改动任何许可证）**：

| 内容 | 风险 | 建议 |
|---|---|---|
| `resources/ntd.patched.wasm`、`resources/ntd.data.gz`、`tools/prefab/assets/ui_detail.pristine.bytes` | DMM 官方资源的衍生文件 | 仅个人本地使用，**不得公开再分发**；若要开源仓库，应把这些文件排除并只发布构建脚本 |
| `tools/wasm/inputs/` | 官方原始二进制 + 完整反编译结果 | 已 `.gitignore`，不要提交 |
| `ref/` | 第三方汉化仓库的完整副本 | 再分发前确认原作者许可；`licenses/license_sources.json` 有来源记录 |
| `tools/fonts/assets/alimama.ttf` | 阿里妈妈方圆体 | 商用/再分发条款需确认；当前仅作为构建输入 |
| `runtime/mitmdump.exe` | 打包的 mitmproxy（MIT）与 Python 运行时 | `licenses/runtime/` 已附各组件许可 |
| `tools/il2cppdumper/Il2CppDumper.exe` | 第三方工具 | 若公开仓库，附其许可证 |

## 9. 接手后首先应处理的事项

1. **做一次真实浏览器验证**（目前 `NOT RUN`）：开场剧情、菜单、角色详情、战斗、选项按钮。
   这是唯一还没被自动化覆盖的环节。
2. **确认分发边界**：这个目录**不能**直接公开；先决定哪些二进制要移出仓库（见 §8）。
3. **做一次 FANZA 侧的真实浏览器验证**（`/1/` 分支目前只有离线验证）。
4. **官方下次更新时**按 `USAGE.md` §5 走一遍：两个分支各跑一次 `--update`
   （`rebuild_wasm.bat --update <版本> 0` 和 `… 1`），核对 `assets.json` 的
   `version_paths`，重新生成 `ntd.data.1.gz`。只更新一个平台会得到"一半好一半坏"。
   运行时绑定（槽位/icall/字段偏移）现在会自动重新解析，看
   `build/wasm/RUNTIME_CONFIG_REPORT.md` 与 `build/wasm/branch-1/…` 是否全绿；
   有 `UNRESOLVED` 时构建会自己停下并把候选 Wasm 删掉，**不要**手工改配置放行。
5. 决定是否把按钮字体（304 字形）也接入自动补字；目前是已知缺口。
6. 若要用 git 管理，先 `git init` 并按现有 `.gitignore` 检查 `git status`，
   **确认没有把 `tools/wasm/inputs/`、`data/`、`dist/` 提交进去**。

## 10. 清理前后对比

| 指标 | 清理前 | 清理后 |
|---|---|---|
| 全目录体积 | 约 2.10 GB | 约 1.11 GB |
| 全目录文件数 | 约 19,300 | 12,807 |
| `work/`（可再生缓存） | 1.1 GB / 12,789 文件 | 211 MB / 6,314 文件（单一语料副本） |
| 交付范围（不含 `work`/`build`/`dist`） | — | **638 MB / 6,406 文件** |

## 11. 能否从当前仓库独立构建发行 ZIP

**可以，且不需要联网。**

```bat
pip install -r requirements.txt   :: 仅首次
build_release.bat
```

产物 `dist/MonmusuTDChinese-v0.1.0-win-x64.zip` + `dist/release_manifest.json`，
自带 CRC、白名单与逐文件 SHA-256 校验（43 个文件全 PASS）。

只有两条路径需要额外输入：
- `rebuild_wasm.bat`（本地模式）需要 `tools/wasm/inputs/`——该目录**未入库**，
  新克隆的仓库第一次请用 `rebuild_wasm.bat --update 202611021000` 获取；
- `update_translations.bat` 的 `remote` 模式需要网络（GitHub）与 Git。

## 12. 阶段三十三交付：控制台清理与玩家本地自动重建

### 12.1 两种发行包（实测）

| ZIP | 文件 | 大小 | SHA-256 | 自动重建 |
|---|---|---|---|---|
| `MonmusuTDChinese-v0.1.0-win-x64.zip` | 45 | 158,418,037 B（151.1 MiB） | `e33af54df9b211…` | 禁用，明确提示 + 官方直通 |
| `MonmusuTDChinese-v0.1.0-win-x64-autobuild.zip` | 1,660 | 217,246,998 B（207.2 MiB） | `8a04e4ffb06b5d…` | 启用（自带便携运行时） |

ZIP 增量 **58,828,961 B（56.1 MiB）**；构建运行时解压后 **144.96 MiB / 1,593 文件**
（Python 74.17 MiB、.NET 8 运行时 70.79 MiB、构建脚本 0.26 MiB、Il2CppDumper 0.85 MiB）。
完整哈希见 `dist/release_manifest.json` 与 `dist/release_manifest_autobuild.json`。
两个 ZIP 的 CRC、白名单、逐文件 SHA-256 全部 PASS；`runtime/provenance.json` 的
mitmdump 校验值一致；运行时配置对 `/0/`、`/1/` 两个分支都与实际打包 Wasm 绑定。

### 12.2 实际做了什么

- **quiet**：`launch.ps1` 加 `--set termlog_verbosity=error`（原已有 `flow_detail=0`），
  `config.ini` 加 `debug = false`。`Reporter` 直接写 stdout + 2 MiB 轮转日志
  `logs/monmusu_cn.log`，脱敏 Token/Cookie/查询串，保留异常堆栈，不用 `>nul 2>&1`。
- **版本识别**：addon 严格匹配官方域名 + `/web/arl/<10–16位>/<0|1>/`，
  两分支允许不同版本（`version_paths` / `branch_versions` 权威），未知版本直通。
- **后台构建**：单飞（按版本+分支）、独立子进程、非阻塞、300 秒冷却 / 最多 2 次、
  临时目录、失败回滚、成功提示刷新。资源重载在后台线程完成，不阻塞事件循环。
- **构建链**：复用阶段 28–32，不重写 Hook 引擎。新增 `tools/autobuild/build_worker.py`，
  自动改写 `ntd.loader.js` 内嵌的 `RUNTIME_JSON` 块（**不依赖**开发者专属的
  `loader_base.js`／五个引擎源码），并自行重做 SHA-256 绑定门禁。
- **便携化**：`tools/autobuild/build_toolchain.py` 从开发者机器裁剪自带 Python +
  .NET + Il2CppDumper；`build_release.py --with-toolchain` 用严格白名单打包。
  `curl.exe` 用 Windows 自带；Node.js 不需要（`WebAssembly.validate` 记为 SKIPPED）。
  `Il2CppDumper`（MIT，已核对上游许可）、UnityPy、CPython、.NET 均附许可说明。
- **修掉的真实缺陷**：环境变量在 `import official_inputs` **之后**设置（会覆盖开发者
  已验证输入）；字体嫁接 `--out` 用了 `.gz` 名（会把原始容器当 gzip 发布）；
  `build_worker` 依赖玩家 ZIP 里不存在的源码；`verify_bindings` 读了硬编码 ROOT；
  非法版本号不写 `result.json`；`auto_build_guard`、批量删除导致构建中断；
  现有 `test_addon_routing.py` 的「混合版本必须拒绝」断言与阶段三十三语义冲突。

### 12.3 测试结果

| 测试 | 结果 |
|---|---|
| `test_phase33.py`（快速） | **38/38 PASS** |
| `test_phase33.py --full`（本地 mock 官方服务器，真实 IL2CPP dump） | **58/58 PASS**；9/9 Hook、RESOLVED 50 / VERIFIED_STATIC 4 / UNRESOLVED 0、字体移植 PASS、结构测试 PASS、生产资源逐字节未变 |
| `test_quiet_console.py`（真实 mitmdump） | **16/16 PASS**（quiet + debug 双模式） |
| `test_portable_autobuild.py`（解压后剥离开发环境） | **13/13 PASS** |
| `test_addon_routing.py` | 43/43 PASS（含分支独立版本隔离） |
| `test_portable.py` / 启动器 | 19/19 PASS |
| `rebuild_wasm.bat`（离线 /0/） | PASS，两分支配置 publishable |
| `test_reorder.py` / `test_wasm_build.py` / `test_runtime_config.py` | PASS / 59/59 / 30/30 |
| 6 个 JS 套件 + 14 项字体工具测试 | 全 PASS |
| 全量翻译审计（用与索引同源的语料 `--repo`） | **415,050 用例 → 412,132 PASS / 2,918 UNTESTABLE / 0 FAIL**，负例 1394/1394 |
| ZIP 白名单与逐文件哈希 | 两版全 PASS，manifest 0 mismatch |

> 审计必须用 `--repo work/translations/src/repo-a46b394349a9`（索引构建时的语料版本）。
> 默认的 `ref/` 后来被更新过，直接跑会报 45 条 `WRONG_TRANSLATION`——那是语料版本
> 不一致，不是代码回归；换回同源语料即恢复 0 FAIL。

### 12.4 明确 NOT RUN / 未解决

- **干净 Windows 虚拟机（完全无开发工具链）+ 浏览器实机自动更新**：`NOT RUN`。
  已验证的是「解压后把 PATH 剥到只剩 System32、清空所有 PYTHON*/DOTNET* 变量，
  自带 Python/.NET/Il2CppDumper 仍能运行」，不是一台全新 Windows。
- **浏览器实机游玩验证（两分支）**：`NOT RUN`，与历次阶段一致。
- 官方若改了字体/Data 对象、静态 prefab 哈希、IL2CPP 字段布局或函数指纹，
  自动构建会**明确失败并保持直通**，需要维护者按 `USAGE.md` §5.4 人工介入。
- 自动构建结果一律标 `static`；控制台明确写出「静态结构校验；尚未经浏览器实机验证」。
- 标准版 ZIP **不含**构建运行时，未知版本只能等维护者更新——这是有意的体积/安全取舍。
- 便携工具链由开发者机器上的 CPython 3.13 / .NET 8 裁剪而来，不能凭空生成；
  换 Python 或 .NET 版本后需重跑 `build_toolchain.py` 与自检。

未提交、未推送、未部署；旧研究目录未改动。
