# Wasm 补丁工具（开发者专用）

阶段二十八：把 Wasm Hook 的**动态定位**与**可重复构建**能力迁入本项目。
阶段二十九：增加**本地离线构建**与**指定官方版本强制下载 + 元数据自动重建**。

> 这些文件**不进入玩家 ZIP**（`tools/build_release.py` 使用显式白名单）。
> Wasm 构建不修改 `resources/ntd.patched.wasm(.gz)`、正式发行 ZIP、
> `启动汉化.bat`、生产 Loader / Bootstrap / Shim、字体或翻译数据。
> 所有产出只写在 `build/wasm/`。

## 1. 目录

```
tools/wasm/
  hook_targets.json        声明式 Hook 目标（主键 = IL2CPP 方法身份）
  resolve_targets.py       动态定位器：元数据 -> slot -> func -> 结构 -> 补丁位置
  build_patch.py           补丁生成器（从 resolved_targets.json 取全部编号）
  verify_patch.py          字节级 / 结构级等价验证
  rebuild_wasm.py          编排器：inputs -> resolve -> patch -> verify -> test -> report
  official_inputs.py       本地元数据复用/重生成 + 指定版本下载 + 原子切换
  release_baseline.json    已发布构建对应的官方 Wasm 哈希（决定是否要求字节一致）
  test_reorder.py          合成 Wasm 函数/槽位重排测试
  test_wasm_build.py       定位正例 + 负例 + 哈希绑定测试
  wastool.py               Wasm 解析器与指令解码器
  extract_metadata.py      从 UnityWebData 容器抽取 global-metadata.dat
  symmap.py                dump.cs -> slot2name.json
  xref.py / ildis.py       调用点扫描 / 带符号反汇编
  patterns.json            overwrite 回滚模式用的样例
  test_hook.js             overwrite 模式 PoC 的 Node 执行测试
  inputs/                  受控开发输入（当前构建，非玩家资源）
    ntd.orig.wasm.gz       官方原始 Wasm 压缩包
    ntd.data.gz            官方原始 Unity Data 压缩包
    ntd.loader.js          官方 WebGL Loader
    ntd.orig.wasm          解压后的官方未打补丁 Wasm
    global-metadata.dat    从同版本 ntd.data 抽取
    script.json            Il2CppDumper 生成的 ScriptMethod 等
    dump.cs / il2cpp.h     同版本反编译产物
    slot2name.json         同版本 dump.cs 生成的槽位名映射
    build_info.json        版本 + 各输入 SHA-256（元数据有效性依据）
tools/il2cppdumper/          Il2CppDumper.exe + 运行配置（独立副本，不依赖研究目录）
build/wasm/                  （生成物，已在 .gitignore 中忽略）
```

历史研究仓库 `C:\Users\ASUS\mnms_td\webgl_research` 只作**只读证据源**：
必要工具与原始资源已复制进本项目；**后续构建不依赖该目录**。
Il2CppDumper 需要本机 .NET 6+ 运行时，可用 `IL2CPP_DOTNET_ROOT`（或
`DOTNET_ROOT`）指定；找不到时**明确失败**，绝不会拿旧版元数据冒充。

## 2. 快速开始：`rebuild_wasm.bat` 只有两种用法

```bat
:: 1) 本地离线构建：不联网、不检查官方版本、不下载
rebuild_wasm.bat

:: 2) 下载指定官方版本并重建（/0/ 与域名/路径自动补齐）
rebuild_wasm.bat --update 202611021000
```

- 无参数模式：`inputs/` 中元数据有效且绑定当前 Wasm 哈希 → **直接复用**；
  元数据缺失/失效 → 从本地 `ntd.orig.wasm.gz` + `ntd.data.gz` **重新生成**
  （抽 `global-metadata.dat` → Il2CppDumper → `symmap.py`）；
  本地必需资源缺失 → 直接报错并提示 `--update <版本号>`。
- `--update` 只接受纯数字官方版本号。`--update` 不带参数、`--update abc`、
  完整 URL、多余参数一律显示用法并退出，**不做自动版本发现**。
- 下载资源仅 3 个：`ntd.wasm.gz`、`ntd.data.gz`、`ntd.loader.js`。
  不下载 `ablist.json`、framework JS、AssetBundle、音频或立绘。
- 下载/解压/校验**全部成功**后才以目录级原子切换替换 `inputs/`；
  任何失败保留原有可用输入，不会留下一半新、一半旧的文件。
- 新版本的 `script.json` / `dump.cs` / `il2cpp.h` / `slot2name.json`
  全部由该版本重新生成，绝不复用旧版元数据。

开发者单步命令（排障用，不是对外入口）：

```bat
python -B tools\wasm\resolve_targets.py
python -B tools\wasm\build_patch.py --out build\wasm\ntd.patched.wasm
python -B tools\wasm\verify_patch.py
python -B tools\wasm\test_reorder.py
python -B tools\wasm\test_wasm_build.py
```

人工签核结构基线（**仅在新版结构经人工复核后**）：

```bat
python -B tools\wasm\resolve_targets.py --emit-baseline
```

## 3. 定位链与门禁

```
(ScriptMethod.Name, TypeSignature)      <- 唯一主键，禁止相似度匹配
   -> ScriptMethod.Address              (当前版本 == Element Table Slot)
   -> wastool.table_slot_to_func(slot)  -> Wasm Function Index
   -> Wasm 函数类型 / C# 调用约定 / 参数位置
   -> 归一化结构指纹（元数据定位后的安全确认）
   -> 补丁插入位置（local decl 边界、参数编号、唯一尾部 end）
```

严格门禁（任一不满足立即失败，退出非零，不生成 Wasm）：

- 元数据命中数必须恰好 1；0 或 ≥2 直接失败并打印候选。
- Wasm 类型必须与 `TypeSignature` 完全一致；C# `Signature` 必须逐字一致。
- Element slot 必须有效且能反查回同一函数。
- 结构指纹（控制流、指令数、memarg 偏移、调用目标语义、静态引用存在性、
  参数编号、local 声明、体长）必须与基线完全一致。
- 补丁位置必须安全；`AdvScenarioData::Init` 的尾部插入要求唯一尾部 `end`。
- 两个 import 必须：名字唯一、类型 `(i32,i32)->i32`、官方原模块恰好 1 个
  直接调用点、调用点函数结构/身份一致、原第二参数不含协议 tag 位。
- 分配函数必须：经 `System.String$$FastAllocateString` 三指令薄包装反推、
  类型 `(i32)->i32`、含 `length@+8` / `NUL@+12` 写入与 GC 分配调用、
  结构指纹一致、存在直接调用者。
- `resolved_targets.json` 绑定所有输入文件的 SHA-256；`build_patch.py`
  打补丁前重新验证。哈希不符 / 文件缺失 / 状态非 PASS → 直接终止。

**不存在**「动态定位失败 → 回退旧函数编号」的路径。`build_patch.py` 里的
`HISTORICAL_REFERENCE` 只供回归测试读取，不参与任何构建决策。

## 4. 指纹归一化规则

归一化掉（非语义）：LEB128 编码长度、绝对文件偏移、函数索引（改用
`import:<field>:<sig>` 或 `def:<sig>:<方法名>:bodylen=N` 语义符号）、
数据段绝对地址（替换为 `<DATA_ADDR>`，但保留「存在静态引用」这一事实）。
`raw_body_sha256` 仅作诊断，不作为门禁。

保留（影响语义）：`local.get/set/tee` 的编号（参数顺序、局部变量重用）、
memarg 的 offset/align、全部控制流 opcode 与分支标签、调用目标的语义、
小常量、f32/f64/未知前缀指令的立即数字节、指令总数与顺序。

`AdvEngine.StartScenario` 与 `AdvScenarioThread.StartScenario` 的归一化
opcode 前缀相同但指纹不同；不能仅凭参数/前缀判定等价。结构变化时**停止并
要求人工复核**，工具不会自动放宽阈值，也不会自动 `--emit-baseline`。

## 5. 官方更新时会自动完成什么 / 仍需人工提供什么

`--update <版本号> [0|1]` 自动完成（`0` = DMM，默认；`1` = FANZA）：

1. 拼接 `https://assets.game-monmusu-td.net/web/arl/<版本>/<分支>/Build/` 并下载
   3 个必需文件，逐个校验 gzip / Wasm 魔数 / UnityWebData 头 / Loader。
2. 从**同版本** `ntd.data` 抽取 `global-metadata.dat`。
3. 用**同版本** Wasm + metadata 运行 Il2CppDumper 生成 `script.json`、
   `dump.cs`、`il2cpp.h`、`stringliteral.json`。
4. 用**同版本** `dump.cs` 生成 `slot2name.json`。
5. 原子切换 `inputs/`（分支 0）或 `inputs-1/`（分支 1），然后执行动态定位、补丁与验证。

**两个分支的 Wasm 不是同一个二进制**（`/1/` 多 5 个函数、IL2CPP 元数据也不同），
必须各跑一次；输入目录分开，派生文件不会跨分支混用。
产物分别是 `build/wasm/ntd.patched.wasm` 与 `build/wasm/branch-1/ntd.patched.1.wasm`。

仍需人工介入：

- 缺少 .NET 运行时或 `Il2CppDumper.exe` → 明确失败（不会用旧元数据）。
- 新版编译优化改变了 9 个方法的结构指纹 → 停止；人工复核后
  `--emit-baseline` 重新签核。
- `a.nf` / `a.md` 混淆名或 import 集合变化 → 更新 `hook_targets.json` 的
  import 声明，**并同步修改生产 `monmusu_cn_bootstrap.js`**。
- 新版 Wasm 构建通过 **不等于** 汉化兼容新版（见 §6）。

## 6. 本轮明确未解决（需人工介入）

- JS Shim 中约 25 个固定函数表槽位、6 个 icall 字面量地址、2 个 icall
  静态缓存指针、TMP / FontAsset 托管字段偏移。
- `resources/assets.json` 的 `version_paths`（两个分支前缀）与 3 个资源哈希后缀。
- `monmusu_cn_bootstrap.js` 的 `CFG.version / importCount / importName /
  contextName`。
- 新版 Unity 资源、中文字体与 AssetBundle 兼容性。
- 浏览器实机验证（`NOT RUN`）。

**不能**因为 9 个 Hook 能重新定位，就宣称整个汉化系统已兼容新版游戏；
**不能**把旧 Wasm 补丁直接重定向给结构不同的新版游戏。

## 7. 验证说明

- 官方 Wasm 与 `release_baseline.json` 中已发布构建一致时，主判据是生成
  Wasm 与 `resources/ntd.patched.wasm` 的 **SHA-256 / 逐字节一致**。
- 官方 Wasm 不同（真正的新版本）时，**不要求**与旧补丁哈希相同；等价性由
  动态定位 + section 白名单 `[3,7,10]` + 9 处 prologue + 8 个追加函数 +
  3 个导出 + `WebAssembly.validate()` 共同证明。
- 生成的 `.gz` 与 `resources/ntd.patched.wasm.gz` 可能不同：gzip 头
  （FNAME）与压缩参数不同，属**非语义编码差异**；解压后明文一致。
  发行版 `.gz` 始终保持原样。
- 合成重排测试只证明指定重排条件下定位器正确，不代表兼容任意未来真实版本。
