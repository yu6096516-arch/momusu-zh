# 使用说明（玩家 / 开发者 / 汉化维护）

- **只想玩** → 看 §1（含两种发行包的区别）。
- **要改代码或构建** → 从 §2 开始。
- **只想更新译文** → §4。
- **官方更新了游戏** → §5（含玩家本地自动重建）。
- **控制台太吵 / 想看完整网络日志** → §1 的「控制台输出」。

技术原理见 [`ARCHITECTURE.md`](ARCHITECTURE.md)。

---

## 1. 玩家怎么用

1. 下载 `MonmusuTDChinese-v0.1.0-win-x64.zip`。
2. **完整解压**到可写目录（支持中文和空格路径，但不要在 ZIP 内直接双击运行）。
3. 完全退出 Chrome / Edge（已经开着的浏览器会忽略新的代理参数）。
4. 双击 `启动汉化.bat`。首次运行会：
   - 解压内置的 mitmdump 运行时（稍慢，只发生一次）；
   - 在 `data/` 目录生成 mitmproxy 的 CA 证书；
   - 若当前用户尚未信任该 CA，调用 Windows 原生 `certutil -user -addstore Root` 导入公钥证书，
     然后**再验一次指纹**，通过才打开浏览器。Windows 是否弹确认框由系统策略决定。
5. 游戏在浏览器里打开后，保持那个黑色命令行窗口不要关。关掉它 = 关掉代理 = 汉化失效。

**证书与隐私**：`data/mitmproxy-ca.pem` **含私钥**，不要分享整个 `data/` 目录。
ZIP 里不含任何开发者证书，每台机器自己生成。不需要管理员权限；浏览器侧的 HTTPS 校验保持开启。
只有配置了 `upstream_port` 时，才会对**本机上游代理**这一段放宽校验（`upstream_insecure`，默认 `true`），
因为 beptard / ACGP 等加速器会用自己的证书替换 TLS，严格校验会直接 502。

**退出顺序**：先关浏览器，再关命令行窗口。若顺序反了，浏览器可能还挂在已停止的代理上，
表现为"打不开网页"——重启浏览器即可恢复（代理设置只对那次启动生效，不改系统代理）。

**配置文件** `config.ini`（唯一玩家配置）：

| 字段 | 说明 |
|---|---|
| `listen_port` | 本机监听端口，默认 8080，范围 1–65535。被占用时改成别的。 |
| `upstream_port` | 留空 = 直连官方。填数字 = 走 `http://127.0.0.1:<该端口>` 的现有代理。常见：clash 7890 / 岛风go 8099 / beptard 8444 / ACGP 8123。 |
| `upstream_insecure` | 默认 `true`，只在上游代理启用时生效：放宽**本机上游代理**这一段的证书校验。beptard / ACGP 会替换证书，保持 `true` 才能用；clash / v2ray 这类普通 HTTP 代理可改 `false` 恢复严格校验。 |
| `type` | `chrome` / `edge` / `custom`。找不到 Chrome 就改 `edge`。 |
| `path` | 仅 `custom` 用，浏览器 EXE 绝对路径，可带双引号。 |
| `args` | 留空用默认 `--proxy-server="http://127.0.0.1:<listen_port>" --disable-quic`；填写后**完整替代**，且必须自己写对 `--proxy-server`。 |
| `debug` | 默认 `false`。改成 `true` 进入开发者调试模式，恢复完整网络日志（HTTP 请求、连接信息）；普通玩家保持 `false`。 |

### 两种发行包

| ZIP | 文件数 | 大小 | 官方更新时 |
|---|---|---|---|
| `MonmusuTDChinese-v0.1.0-win-x64.zip`（标准版） | 45 | 约 151 MiB | 官方流量**直通**，控制台明确提示「本发行包未附带构建运行时」，等待维护者更新。 |
| `MonmusuTDChinese-v0.1.0-win-x64-autobuild.zip`（自动更新版） | 1660 | 约 207 MiB | 后台自动尝试重建补丁；失败仍直通，成功则提示刷新页面。 |

两者**只差一个可选的便携构建运行时**（自带 Python 与 .NET 运行时，无需玩家安装任何东西）。
自动更新版多约 56 MiB 压缩后数据；标准版适合只想玩、不介意等维护者的人。

### 控制台输出

默认启动参数为 `--set flow_detail=0 --set termlog_verbosity=error`：
不再刷 HTTP 请求、WebSocket 心跳和正常连接关闭。汉化器自己的状态直接写 stdout，
不受 mitmproxy 日志级别影响，因此 `[MonmusuCN]` 的 INFO / WARNING / ERROR 始终可见：

```text
[MonmusuCN] 汉化代理启动成功
[MonmusuCN] 汉化资源就绪：0:6, 1:6，3 个按后缀匹配的资源包
[MonmusuCN] DMM 分支支持版本：202611021000
[MonmusuCN] FANZA 分支支持版本：202611021000
[MonmusuCN] 检测到 DMM / 202611021000（已支持）
```

详细记录（含异常堆栈、每请求 DEBUG）写入 `logs/monmusu_cn.log`，单文件超过 2 MiB
轮转一次，不会无限增长。日志**不记录**请求头、Cookie、Token、认证参数或查询串；
意外出现的凭据样式文本也会先做脱敏。错误从不用 `>nul 2>&1` 隐藏。

需要完整网络日志排查时，把 `config.ini` 的 `debug` 改成 `true` 再启动。

**常见问题**

| 现象 | 处理 |
|---|---|
| 双击没反应 / 闪退 | 用命令行在该目录执行 `启动汉化.bat` 看报错；检查是否被杀软拦截。 |
| 提示端口被占用 | 改 `config.ini` 的 `listen_port`（如 8081）。 |
| 提示未找到浏览器 | 装 Chrome，或把 `type` 改成 `edge`，或用 `custom` 填路径。 |
| 浏览器打开了但全是日文 | ①之前没退出浏览器；②`args` 覆盖了默认参数却没写 `--proxy-server`；③端口与 `args` 里写的不一致。 |
| 502 Bad Gateway / `Certificate verify failed`（beptard / ACGP） | 加速器用自己的证书替换了 TLS，而 `upstream_insecure=false` | 保持 `upstream_insecure = true`（默认值）；或改用 clash / v2ray 这类不替换证书的普通 HTTP 代理 |
| 页面加载失败 + 大量 `10054` / `mitmproxy has crashed` `WantReadError` | 上游证书校验失败后的连锁反应，不是独立故障 | 同上；先确认不启动汉化时，加速器本身能打开游戏 |
| 提示证书安装失败/被阻止 | 系统策略或用户取消了授权。重新运行，按 Windows 提示完成授权。 |
| 关掉窗口后浏览器不能上网 | 重启浏览器。 |
| 游戏能开但白屏 | 官方构建已更新，见 §5。 |

---

## 2. 开发环境准备

在 Windows 10/11 x64 上，以下版本是**实际验证过**的组合：

| 依赖 | 版本 | 用途 | 是否必需 |
|---|---|---|---|
| Windows PowerShell 5.1 | 系统自带 | 启动器 | 运行玩家包需要 |
| Python | 3.10+（验证于 3.13.14） | 所有构建/测试脚本 | 必需 |
| Node.js | 18+（验证于 v22.22.2） | 剧情/UI/翻译运行时测试 | 跑测试必需 |
| .NET 运行时 | 6.0+（IL2CPP 元数据生成） | `Il2CppDumper.exe` | 只在 `rebuild_wasm.bat --update` 需要 |
| Git | 任意近期版本 | `target = remote` 拉取语料 | 更新译文必需 |
| UnityPy | 1.25.4 | 读 Unity 资源 | 必需 |
| fontTools | 4.68.0 | 字形光栅化 | 补字必需 |

首次准备：

```bat
:: 1. 装 Python 依赖（仓库根目录）
pip install -r requirements.txt

:: 2. 确认 Node
node -v

:: 3. 确认 .NET（若不在 PATH，用环境变量指定）
dotnet --list-runtimes
set IL2CPP_DOTNET_ROOT=C:\path\to\dotnet
```

`Il2CppDumper` 已随仓库提供（`tools/il2cppdumper/`），**不需要单独安装**。
`find_dotnet_root()` 依次尝试：`IL2CPP_DOTNET_ROOT` → `DOTNET_ROOT` → `%USERPROFILE%\.dotnet8` →
PATH 上的 `dotnet`。

自检（应当全绿，约 1 分钟）：

```bat
python -B tools/fonts/test_font_tools.py
python -B tools/test_assets.py
node test_ui_index.py 2>nul & python -B test_ui_index.py
node test_story_index.js
```

> 不需要 Unity Editor。仓库里没有 `.unitypackage`，也不需要打开 Unity。

---

## 3. 四个入口分别做什么

| 入口 | 什么时候用 | 产物 |
|---|---|---|
| `启动汉化.bat` | **玩家**启动游戏（DMM 或 FANZA 都可，无需切换） | 无（运行时） |
| `rebuild_wasm.bat` | 用 `tools/wasm/inputs/` 里已保存的官方资源离线重建 **DMM（/0/）** 补丁 | `build/wasm/ntd.patched.wasm(.gz)`、`build/wasm/BUILD_REPORT.md` |
| `rebuild_wasm.bat --update 202611021000` | 下载指定版本的 **DMM /0/** 资源并重建（与旧命令等价） | 同上，外加刷新 `tools/wasm/inputs/` |
| `rebuild_wasm.bat --update 202611021000 1` | 下载 **FANZA /1/** 资源并重建 | `build/wasm/branch-1/ntd.patched.1.wasm.gz`、`build/wasm/branch-1/BUILD_REPORT.md`，输入在 `tools/wasm/inputs-1/` |
| `update_translations.bat` | 译文仓库有更新，或想改译文来源（**只更新一套译文，两个平台共用**） | `resources/` 下的索引/字体/prefab、`dist/*.zip`、`build/translations/UPDATE_REPORT.md` |
| `build_release.bat` | 资源都已就位，只想重新打包（会把两个分支一起打进同一个 ZIP） | `dist/MonmusuTDChinese-v0.1.0-win-x64.zip`、`dist/release_manifest.json` |

第三个参数只允许 `0` 或 `1`，不写默认 `0`（保持旧命令兼容）：
`0` = DMM，`1` = FANZA。非法值、完整 URL、多余参数一律打印用法并退出。

`启动汉化.bat` **只存在于发行 ZIP 里**（源仓库根目录那一份会被 `build_release.py` 打进 ZIP）。
它不是构建脚本，只是 `runtime/launch.ps1` 的包装。

三者关系：

```
官方更新 → rebuild_wasm.bat --update  ← 只改 Wasm 补丁，不动译文
译文更新 → update_translations.bat     ← 改索引/字体/prefab，并自动重新打包
只想打包 → build_release.bat           ← 不重建任何资源，只按白名单压 ZIP
```

> **注意**：`tools/wasm/inputs/`（官方原始 Wasm / data / 反编译产物，约 286 MB）
> 不在仓库里（见 `.gitignore`）。新克隆的仓库第一次跑 `rebuild_wasm.bat` 会用一条明确的
> 报错提示你先执行 `rebuild_wasm.bat --update <版本号>`。
> `build_release.bat` 不依赖它，离线即可打包。

---

## 4. 汉化作者更新译文时

### 4.1 配置来源 `translation_source.ini`

只有两个键，多一个都会报错：

```ini
target = remote
path = https://github.com/Berumic/MonsterMusumeTDChineseTranslation.git
```

或指向本地目录（只读，不联网、不 `git pull`）：

```ini
target = local
path = D:\Projects\MonsterMusumeTDChineseTranslation
```

`remote` 每次都执行 `git fetch --depth 1 origin HEAD` + `reset --hard`，**不判断有没有更新**，
直接取最新。

### 4.2 运行

```bat
update_translations.bat
```

不接受任何参数（传参直接打印用法并退出 2）。它依次做：

1. 拉取/读取语料，用现有生成器校验 manifest 与语料哈希；
2. 生成剧情 / UI / 回退三份索引到 `work/translations/build/`（不直接写生产目录）；
3. 收集**译文实际会显示**的字符；
4. **按需补字**：从 `tools/fonts/assets/alimama.ttf` 光栅化缺的字形，只追加到 cn25 图集
   已用区域之下，旧字形逐字节不动；主字体与 `font_ui.bytes` 两份副本同步；
5. **按需重建静态 prefab**：如果角色详情页那些烘焙在 prefab 里的标签译文变了，
   从 `tools/prefab/assets/ui_detail.pristine.bytes` 重建 `ui_detail.bytes` 并回读校验；
6. 备份 → 激活 → 跑测试 + 全量审计；
7. 重新打包 `dist/MonmusuTDChinese-v0.1.0-win-x64.zip`；
8. 用**实际产物**重写 `tools/translations/cn25_baseline.json`。

任何一步失败都会回滚到运行前的生产资源与 ZIP。

### 4.3 结果在哪

- 发行包：`dist/MonmusuTDChinese-v0.1.0-win-x64.zip`
- 校验清单：`dist/release_manifest.json`（每个文件的字节数与 SHA-256）
- 报告：`build/translations/UPDATE_REPORT.md`（按来源另存 `_remote.md` / `_local.md`）
- 分步日志：`build/translations/logs/`

### 4.4 关于补字

**不需要手动加字**。缺字会被自动补上；只有两种情况会失败并阻止打包：

- 字体源 `alimama.ttf` 本身没有该字的轮廓；
- 图集在 8192×8192 仍放不下（需要第二张图集，本静态字体资产不支持）。

---

## 5. 官方（DMM / FANZA）更新时

Wasm 补丁与一堆 JS 常量都绑定官方构建，换版本必须重建并复核。

### 5.1 取版本号

打开游戏页面，在浏览器开发者工具的 Network 里看任意资源 URL：

```
https://assets.game-monmusu-td.net/web/arl/<版本号>/0/Build/ntd.wasm.gz
```

`<版本号>` 形如 `202611021000`（纯数字）。只填这一段，`/0/Build/` 与域名由脚本补齐。

### 5.2 重建

先确认要更新哪个平台（两者对应同一构建号的不同资源分支）：

| 平台 | 分支 | 命令 |
|---|---|---|
| DMM | `/0/` | `rebuild_wasm.bat --update 202611021000` |
| FANZA | `/1/` | `rebuild_wasm.bat --update 202611021000 1` |

> **两个平台的 Wasm 不是同一个二进制。** 实测：`/1/` 比 `/0/` 多 5 个函数
> （Code +725 B、elem 槽位 +5），IL2CPP 元数据也不同。所以**不能**把 `/0/` 的补丁
> 直接当 `/1/` 用，必须各自构建；两者的输入目录也分开
> （`tools/wasm/inputs/` 与 `tools/wasm/inputs-1/`），派生文件不会混用。
> 如果这次官方更新两个平台都要用，就各跑一次。

它会：下载 `ntd.wasm.gz` / `ntd.data.gz` / `ntd.loader.js` → 校验 → 从 `ntd.data` 抽
`global-metadata.dat` → 跑 Il2CppDumper 生成 `script.json`/`dump.cs` → 生成 `slot2name.json`
→ 动态定位 9 个 Hook + 2 个 import + 字符串分配器 → 打补丁 → 验证。
所有输入以目录级原子切换，下载失败不会留下半新半旧。

构建完成后还要把产物放进 `resources/`（发行包只认这里）：

```bat
:: /0/ 分支
copy build\wasm\ntd.patched.wasm      resources\ntd.patched.wasm
copy build\wasm\ntd.patched.wasm.gz   resources\ntd.patched.wasm.gz
:: /1/ 分支
copy build\wasm\branch-1\ntd.patched.1.wasm      resources\ntd.patched.1.wasm
copy build\wasm\branch-1\ntd.patched.1.wasm.gz   resources\ntd.patched.1.wasm.gz
```

`resources/ntd.data.1.gz`（FANZA 的数据容器）由
`tools/fonts/graft_branch_data.py` 生成：把中文字体载荷移植进 FANZA 自己的
`ntd.data`，保留它的 `PlayerSettings`。**不要**把 `/0/` 的 `ntd.data.gz` 改名当成 `/1/` 用。

### 5.3 **构建成功 ≠ 可以发布**

Wasm 成功只说明 9 个 Hook 还找得到。以下仍需人工核对（见 `ARCHITECTURE.md` §7）：

1. **`resources/assets.json` 的 `version_paths`** —— 两个分支前缀都要改。阶段三十三起
   **允许两个分支指向不同的版本号**（DMM 与 FANZA 不一定同时更新）：每个分支有独立的
   资源表，`version_paths` / `branch_versions` 是权威来源，旧标量 `version` 在两分支版本
   不同时为 `null`。但仍会拒绝「分支号与前缀不一致」或「前缀格式不合法」的配置。
   不改这里，代理一个资源都不会替换，游戏会正常启动但中文全消失、**且没有任何报错**。
   这是最容易漏的一步。
2. **两个分支各重建一次** —— `/0/` 与 `/1/` 的 Wasm、`ntd.data`、IL2CPP 元数据都不同；
   只更新一个平台会得到"一半好一半坏"。`ntd.data.1.gz` 用 `graft_branch_data.py` 重新生成，
   不要复用 `/0/` 的文件。
3. **生成的运行时配置** —— `build/wasm/RUNTIME_CONFIG_REPORT.md` 与
   `build/wasm/branch-1/RUNTIME_CONFIG_REPORT.md` 里每一项都应是 `RESOLVED`。
   有 `UNRESOLVED` 时构建**已经**失败并删掉了候选 Wasm，不会给你一个能发的包。
   `VERIFIED_STATIC` 的 4 项是 IL2CPP 运行时布局，只有换 Unity 大版本才需要重看。
   这份配置会在 `build_release.bat` 时被再次校验：解压将要打包的 `ntd.wasm.gz`
   比对 `wasm.sha256`，不一致就拒绝出包。
4. **槽位语义** —— 槽位是自动定位的，但"定位到同名方法"不等于"行为没变"。
   如果官方重构了 TMP 或资源 API，报告会显示解析成功而实际行为可能不同；
   这种只能靠第 7 条把关。
5. **静态 prefab** —— 若官方换了 `c8554d98…bytes`，`tools/prefab/assets/ui_detail.pristine.bytes`
   必须替换成新原件；否则重建会失败（流程会保留旧文件并给 WARNING，不会发布损坏资源）。
6. **字体** —— 新版本若改了字体资产结构，`add_glyphs.py` 的布局假设会校验失败并停止。
7. **真机验证** —— 必须**两个平台各打开一次**走一遍：开场剧情、菜单、角色详情、战斗、选项按钮。
   自动化只覆盖资源层。

任何一项没做完，就不要把包发出去。

### 5.4 玩家本地自动重建（阶段三十三）

只适用于 **`…-autobuild.zip`** 自动更新版。玩家仍然只运行 `启动汉化.bat`，不需要输入版本号、
不需要安装 Python / Node.js / .NET SDK、不需要管理员权限。

发生什么：

1. addon 只接受 `assets.game-monmusu-td.net` 上匹配
   `/web/arl/<12 位版本号>/<0 或 1>/…` 的请求；从 URL 路径取版本与分支，
   **不读取任何请求头、Cookie、Token 或查询串**。
2. 版本与本地已支持版本一致 → 正常汉化，不打印多余内容。
3. 检测到未知版本 → 明确提示「官方资源直通，不注入旧补丁」，
   该版本的请求全部原样放行（**绝不把旧版 Wasm 塞给新客户端**），
   同时在**独立子进程**里启动一次构建，不阻塞代理事件循环。
4. 同一「版本 + 分支」只允许一个构建任务；浏览器并发请求不会起多个构建器。
   失败后 300 秒冷却、最多 2 次尝试，之后停止直到下次启动。
5. 完整链：下载三件官方资源 → 抽 `global-metadata.dat` → Il2CppDumper →
   动态定位 9 个 Hook → 打补丁 → 生成并与新 Wasm SHA-256 绑定的运行时配置 →
   把中文字体载荷移植进该分支自己的 `ntd.data` → 结构测试 → 全部门禁。
6. 任一门禁失败：候选留在 `work/autobuild/` 临时目录，旧版资源**逐字节不动**，
   官方流量继续直通。不会自动执行 `--emit-baseline`，不会绕过 `UNRESOLVED`，
   不会启用与 Wasm 哈希不一致的配置。
7. 全部通过后原子替换 `ntd.*.gz`、`ntd.loader.js` 内嵌的运行时配置、`assets.json`；
   任一失败整体回滚。控制台提示**刷新页面**后生效（不改动正在运行的 Wasm）。

```text
[MonmusuCN] 检测到 FANZA / 209912310000
[MonmusuCN] 发现未支持的新版本：官方资源直通，不注入旧补丁
[MonmusuCN] 正在后台准备新版本构建（第 1 次）…
[MonmusuCN] FANZA / 209912310000 新补丁已构建完成
[MonmusuCN] 校验级别：static（静态结构校验；尚未经浏览器实机验证）
[MonmusuCN] 请刷新游戏页面以应用汉化
```

**`static` 不是「已验证可玩」。** 静态门禁只证明结构一致。任何自动构建的新补丁都
没有经过浏览器实机验收，请自行确认剧情、菜单、战斗、按钮显示正常；异常时删掉
`work/autobuild/<版本>-<分支>/backup-*` 中的对应备份可恢复旧版文件，或重新解压标准版 ZIP。

**仍需维护者人工介入的更新类型**（自动构建会明确失败并保持直通，不会假装成功）：

| 变化 | 门禁 |
|---|---|
| 官方改了字体/Data 容器里的对象 | `graft_branch_data.py` 的 SHA-256 基线校验失败 |
| 官方换了静态 prefab / `*.bytes` 哈希 | 资源哈希不再匹配，需替换 pristine 原件 |
| 编译器结构指纹变化、Hook 无法唯一命中 | `resolve_targets` 拒绝，`UNRESOLVED` 禁止启用 |
| IL2CPP 字段布局变化 | `VERIFIED_STATIC` 项需人工复核 |
| 两个分支版本不同 | 允许，但各自独立构建、独立资源，不混用 |
| 新版 Wasm 与新配置哈希不一致 | 发行门禁拒绝激活并回滚 |

**维护者如何生成自动更新版**（在开发者机器上执行一次）：

```bat
python -B tools\autobuild\build_toolchain.py
build_release.bat --with-toolchain
```

`build_toolchain.py` 从本机裁剪出自带 Python（含 UnityPy 依赖闭包）、最小 .NET 运行时与
Il2CppDumper，写入 `runtime/toolchain.json`，并在**清空 PATH 到只剩 System32** 的环境里
自检三者都能运行。`build_release.py --with-toolchain` 才把 `runtime/toolchain/`
和 21 个构建脚本纳入 ZIP 白名单；标准版永远不含这些文件。

**测试**（均在开发者机器执行）：

```bat
python -B tools\autobuild\test_phase33.py           :: 快速门禁（38 项）
python -B tools\autobuild\test_phase33.py --full    :: + 本地 mock 官方服务器跑完整构建链
python -B tools\autobuild\test_quiet_console.py     :: 真实 mitmdump quiet / debug 控制台
python -B tools\autobuild\test_portable_autobuild.py :: 解压后自带运行时（剥离开发环境）
```

`--full` 用本机已验证的官方输入在 `127.0.0.1` 上模拟一个假版本号，真实执行下载、
IL2CPP dump、9 Hook 定位、打补丁、运行时配置、字体移植和结构测试，
并以 `--no-activate` 保证**不触碰** `resources/`。

---

## 6. 常见故障排查

| 症状 | 原因 | 处理 |
|---|---|---|
| `--update` 下载失败 / 403 | 版本号错、该版本已下线、网络被拦 | 确认 iframe URL 里的版本号；重试；检查代理 |
| `git fetch` 失败 | 网络、证书、仓库改名 | `git ls-remote <url>` 手动确认可达 |
| `Il2CppDumper requires a .NET 6+ runtime` | 找不到 dotnet | 设 `IL2CPP_DOTNET_ROOT`（见 §2） |
| `ImportError: UnityPy` / `fontTools` | 依赖没装 | `pip install -r requirements.txt` |
| `resolve_targets` 报零命中/多命中 | 官方改了结构，或元数据与 Wasm 不是同一版本 | 重新 `--update`；若仍失败，人工核对 `hook_targets.json` 的 `(方法名, TypeSignature)` |
| 结构指纹不匹配 | 编译器优化改变了函数体 | 人工复核后 `python -B tools/wasm/resolve_targets.py --emit-baseline` 重新签核；**不要**放宽匹配 |
| 审计 `WRONG_TRANSLATION` | 语料本身有问题，或审计用了别的语料版本 | 检查 `build/translations/logs/full_translation_audit.log`；审计必须和构建同源（脚本已自动传 `--repo`） |
| 字体构建失败（源字体缺字） | `alimama.ttf` 没有该字轮廓 | 换字体源或人工设计该字形；**不要**把它加进允许列表绕过 |
| 静态 prefab WARNING | 官方换了 prefab bundle，或译文里出现了新的烘焙标签 | 报告里会列出受影响的键；替换 `ui_detail.pristine.bytes` 后重跑 |
| 浏览器打不开 / 白屏 | 版本不匹配、资源路径没改、代理未生效 | 先确认 `version_paths`；再确认浏览器确实走了代理（看 mitmdump 窗口日志） |
| DMM 汉化正常、FANZA 还是日文 | `/1/` 的补丁或数据没更新 | 跑 `rebuild_wasm.bat --update <版本> 1`，并确认 `resources/ntd.patched.1.wasm.gz`、`resources/ntd.data.1.gz` 都在；看浏览器控制台是否出现 `armed for build …/1` |
| 控制台提示 assumed /0/ (DMM) | 分支识别三条路径都没命中 | 说明 loader 不是从 `/web/arl/<版本>/<分支>/Build/` 加载的；检查 `assets.json` 的前缀和代理是否真的生效 |
| 游戏里中文变方块 | 字体没被替换或索引未加载 | 检查 `resources/ntd.data.gz` 是否为最新、`assets.json` 是否指向它 |

---

## 7. 如何验证发行包

### 7.1 全自动（推荐）

```bat
python -B tools/test_assets.py
```

预期输出（一行 JSON）：

```json
{"status": "PASS", "primaryGlyphs": 5840, "uiGlyphs": 5935, "atlas": [8192, 4096],
 "detailReferences": 15, "sampleWidthAt14": 264.04, "fontName": "monmusu_cn UI Font SDF",
 "requiredChars": 4298, "glyphGaps": 0, "browser": "NOT RUN"}
```

`glyphGaps: 0` 表示"发行索引里出现的每个字符，字体里都有字形"。

### 7.2 打包完整性与哈希

```bat
build_release.bat
```

预期结尾：

```
ZIP CRC, allowlist and all 43 file hashes: PASS
```

然后逐文件核对（无需额外工具）：

```bat
python -B -c "import json,hashlib;m=json.load(open('dist/release_manifest.json',encoding='utf-8'));bad=[k for k,v in m['files'].items() if hashlib.sha256(open(k,'rb').read()).hexdigest()!=v['sha256']];print('mismatches:',bad)"
```

预期 `mismatches: []`。

### 7.3 解压后独立运行

把 ZIP 解压到**另一个目录**（不要用仓库目录），双击 `启动汉化.bat`。
确认：证书提示 → 浏览器打开 → 开场剧情为中文 → 菜单/角色详情为中文 → 选项按钮可读。

### 7.4 各子系统单独验证

```bat
:: Wasm 重建（离线，用已存官方输入）
rebuild_wasm.bat

:: 译文更新（会重新打包）
update_translations.bat

:: 字体工具单元测试
python -B tools/fonts/test_font_tools.py

:: 翻译索引与运行时
python -B test_ui_index.py
node test_story_index.js
node test_story_concat.js
node test_story_runtime.js
node test_translate.js

:: 全量审计（约 1 分钟，41 万用例）
python -B audit_all_translations.py
```

预期结果见 `HANDOFF_REPORT.md` 的"验证结果"表。

> `node test_story_index.js` 会按索引里记录的 `manifestSha256` 去找**同一版本的语料**：
> 先找 `ref/`，再找 `work/translations/src/repo-*`。如果两个都不匹配（例如索引是用远端新版本
> 构建的，而 `work/` 被清空了），它会明确报"找不到对应语料清单"。先跑一次
> `update_translations.bat` 把语料拉下来即可，这不是断言失败。
>
> **浏览器实机验证仍为 `NOT RUN`**。上面所有命令都不打开真实浏览器，
> 只能证明资源与数据层正确，不能替代 §7.3。
