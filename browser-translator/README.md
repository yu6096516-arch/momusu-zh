# MonmusuTDChinese 浏览器汉化器

另一条汉化路线：**不改游戏文件**，用本地代理把游戏里浏览器内核（WebView）的请求拦下来，实时替换文本。

和本仓库 `translations/` 那套（BepInEx 插件 + JSON 表）是**两套独立方案**，互不冲突。

---

## 它做什么

```
游戏内网页界面 ──→ 本地代理(mitmproxy) ──→ 注入 JS ──→ 显示中文
```

- 拦 HTTPS 流量，用自签证书解密
- 注入 `monmusu_cn_bootstrap.js` 等脚本
- 用 `story_index` / `ui_index` 做文本匹配替换
- 不写游戏文件，关掉就恢复原样

**当前进度（作者原话）**：剧情和技能已汉化，其余部分没做。

---

## 两个文件去哪拿

仓库不放二进制（体积太大，单个 200 MB+），都在 **Releases** 里：

| 文件 | 大小 | 说明 |
|---|---:|---|
| `MonmusuTDChinese-v0.1.0-win-x64-autobuild.zip` | 212 MB | **分发包**，解压双击 `启动汉化.bat` 就能用（PyInstaller 打包，含 Python 运行时） |
| `MonmusuTDChinese-dev-src-20261116.rar` | 235 MB | **仓库源码**，含 wasm、翻译索引、构建脚本 |

下载页：<https://github.com/yu6096516-arch/momusu-zh/releases>

---

## 目录里是什么（源码精简版）

本目录只收**代码和文档**，不含 wasm / 翻译索引 / 运行时（那些在 Release 的 rar 里）：

| 路径 | 内容 |
|---|---|
| `monmusu_cn_bootstrap.js` | 注入入口 |
| `monmusu_cn_shim.js` | 运行时垫片（替换 DOM/文本） |
| `monmusu_cn_story_index.js` | 剧情索引 |
| `monmusu_cn_ui_index.js` | 界面索引 |
| `loader_base.js` | 加载器基类 |
| `*.py` | 索引构建、审计、翻译生成脚本 |
| `test_*.js` / `test_*.py` | 自测 |
| `启动汉化.bat` | 分发包里的启动脚本 |
| `ARCHITECTURE.md` / `USAGE.md` / `HANDOFF_REPORT.md` | 作者留下的设计文档 |

---

## 怎么用

1. 从 Releases 下载 `MonmusuTDChinese-v0.1.0-win-x64-autobuild.zip`
2. 解压到任意目录
3. 双击 `启动汉化.bat`
4. 按提示装证书、启动代理，然后开游戏

> 需要管理员权限（装证书 + 改系统代理）。用完记得在脚本里还原代理设置。

---

## 和主方案怎么选

| | 本仓库主方案（`translations/`） | 浏览器汉化器 |
|---|---|---|
| 原理 | BepInEx 插件替换 TMP 文本 | 本地代理注入 JS |
| 覆盖 | **全剧情 + 全界面 + 技能 + 名字（22 万条）** | 剧情 + 技能 |
| 是否改文件 | 是（放 translations 目录） | 否（运行时替换） |
| 需要装证书 | 否 | 是 |
| 稳定性 | 高 | 看游戏 WebView 版本 |

**建议用主方案**，这个当参考或备用。
