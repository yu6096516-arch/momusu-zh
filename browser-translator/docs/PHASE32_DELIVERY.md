# 阶段三十二：DMM / FANZA 运行时抗更新改造

**结论**：JS 侧原本有 **62 处**手写的构建绑定，现在

- **58 处由构建期解析生成**（24 个函数表槽位 + 6 个 icall 地址/缓存/resolve 槽位
  + 21 个托管字段偏移 + 7 个 bootstrap 常量）；
- **4 处降级为 `VERIFIED_STATIC`**（`m_CachedPtr`、`Il2CppClass::name`、
  `Il2CppString.length/chars`——IL2CPP 运行时布局，不在托管元数据里，不随游戏代码变）；
- 另有 **2 个从未被引用的常量被删除**。

过程中发现并修掉一个真实缺陷：**旧手写表在 FANZA `/1/` 上有 19 个函数表槽位是错的**。

---

## 一、依赖审计：逐项处理结果

### 1.1 Bootstrap（`monmusu_cn_bootstrap.js`）—— 7 项全部移出源码

| 原值 | 用途 | 跨 `/0`·`/1` | 处理 |
|---|---|---|---|
| `CFG.version = '202611021000'` | 探测失败时拼索引 URL、日志 | 否 | **删除**；改从配置的 `game_build` 取 |
| `CFG.importModule = 'a'` | 取 `imports.a` | 否 | **删除**；`RC.imports.module`（从 import 段读） |
| `CFG.importName = 'nf'` | 译文通道 | 否（混淆名恰好相同） | **删除**；`RC.imports.translation.name` |
| `CFG.contextName = 'md'` | 上下文通道 | 否 | **删除**；`RC.imports.context.name` |
| `CFG.importIndex = 336` | 仅日志 | 否 | **删除**；`RC.imports.translation.index` |
| `CFG.contextIndex = 228` | 仅日志 | 否 | **删除**；`RC.imports.context.index` |
| `CFG.importCount = 540` | **运行时整构建指纹**（不符则不打补丁） | 否 | **删除**；`RC.imports.count`（从 import 段数出来） |

保留在源码里的 `CFG` 只剩构建无关的策略：分支集合 `['0','1']`、三个索引文件名、
超时、`uiFont`/`uiFontUnify`/`uiFontRefreshAll`、`debug`。

> 注意：**import 名在两分支恰好相同**，所以"名字对"并不能证明 Wasm 对。
> 真正的绑定由 `wasm.sha256` 在打包时校验（见 §三）。

### 1.2 JS 函数表槽位 —— 24 项全部动态解析

| id | 方法（`script.json` 里的真名） | TS | `/0/` | `/1/` | Δ |
|---|---|---|---:|---:|---:|
| `get_transform` | `UnityEngine.Component::get_transform` | `iii` | 3928 | 3928 | +0 |
| `get_parent` | `UnityEngine.Transform::get_parent` | `iii` | 4158 | 4158 | +0 |
| `get_name` | `UnityEngine.Object::get_name` | `iii` | 4459 | 4459 | +0 |
| `type_gettype` | `System.Type::GetType` | `iii` | 105327 | 105335 | **+8** |
| `find_all` | `UnityEngine.ResourcesAPIInternal::FindObjectsOfTypeAll` | `iii` | 86935 | 86943 | **+8** |
| `font_has_char` | `UnityEngine.Font::HasCharacter` | `iiii` | 91549 | 91557 | **+8** |
| `create_font` | `TMPro.TMP_FontAsset::CreateFontAsset` | `iiiiiiiiii` | 101047 | 101055 | **+8** |
| `create_font_short` | `TMPro.TMP_FontAsset::CreateFontAsset` | `iii` | 101046 | 101054 | **+8** |
| `object_destroy` | `UnityEngine.Object::Destroy` | `vifi` | 87129 | 87137 | **+8** |
| `tmp_instance` | `TMPro.TMP_Settings::get_instance` | `ii` | 101444 | 101452 | **+8** |
| `mat_shader` | `UnityEngine.Material::get_shader` | `iii` | 86182 | 86190 | **+8** |
| `mat_has_prop` | `UnityEngine.Material::HasProperty` | `iiii` | 86191 | 86199 | **+8** |
| `mat_get_float` | `UnityEngine.Material::GetFloat` | `fiii` | 86226 | 86234 | **+8** |
| `mat_set_float` | `UnityEngine.Material::SetFloat` | `viifi` | 86216 | 86224 | **+8** |
| `mat_enable_kw` | `UnityEngine.Material::EnableKeyword` | `viii` | 86193 | 86201 | **+8** |
| `mat_main_tex` | `UnityEngine.Material::get_mainTexture` | `iii` | 86185 | 86193 | **+8** |
| `tmp_has_char` | `TMPro.TMP_FontAsset::HasCharacter` | `iiiiii` | 101062 | 101070 | **+8** |
| `tmp_set_hpc` | `TMPro.TMP_Text::set_havePropertiesChanged` | `viii` | 101736 | 101744 | **+8** |
| `tmp_get_font` | `TMPro.TMP_Text::get_font` | `iii` | 101611 | 101619 | **+8** |
| `tmp_set_font` | `TMPro.TMP_Text::set_font` | `viii` | 101612 | 101620 | **+8** |
| `tmp_get_shared_mat` | `TMPro.TMP_Text::get_fontSharedMaterial` | `iii` | 101613 | 101621 | **+8** |
| `tmp_set_shared_mat` | `TMPro.TMP_Text::set_fontSharedMaterial` | `viii` | 101614 | 101622 | **+8** |
| `object_gettype` | `System.Object::GetType` | `iii` | 2324 | 2324 | +0 |
| `array_create` | `System.Array::CreateInstance` | `iiii` | 3028 | 3028 | +0 |

**19/24 在 FANZA 上整体 +8。** 旧代码把 `/0/` 的表同时用于两个分支；因为
`call_indirect` 只校验参数个数，19 个错槽位里有相当一部分"参数个数恰好相同"，
会直接跳进另一个方法的函数体。这不是"字体不生效"，是可能踩内存。

定位规则（`tools/wasm/resolve_runtime.py`）：

1. `(Name, TypeSignature)` 在 `script.json` 里必须**唯一命中**；
2. 命中多个重载时（`Type.GetType`、`Font.HasCharacter`、`Material.HasProperty/GetFloat/SetFloat`、
   `Array.CreateInstance` 各有两个同名同 TS 的重载），必须靠声明的
   **native 参数类型列表**（`runtime_targets.json` 的 `native_params`，例如
   `["UnityEngine_Font_o*","uint16_t","const MethodInfo*"]`）再筛到**恰好 1 个**；
3. `Address` 必须能映射到元素表，函数 Wasm 类型必须与 `TypeSignature` 逐参数相符；
4. shim 实际传入的参数个数必须等于 `len(TypeSignature)-1`（`call_indirect` 的 arity 必须对）。

任一条不成立 → `UNRESOLVED` → 构建失败。

### 1.3 icall —— 7 项动态解析，2 项删除

| 原值 | 类型 | 处理 |
|---|---|---|
| `ICALL_INIT = 0x064b8c` | 线性地址（字符串字面量） | 按字面量内容解析 |
| `ICALL_LOADFACE = 0x05ef61` | 同上 | 同上（必须用**完整**字符串，含参数表） |
| `ICALL_FACEINFO = 0x06953b` | 同上 | 同上 |
| `ICALL_ADDGLYPH = 0x06808b` | 同上（可选） | 同上；解析失败不阻断（该探测可跳过） |
| `FIND_ALL_SPTR = 0x786738` | 静态缓存指针地址 | 从包装函数体的 `i32.const` 提取 |
| `HAS_CHAR_SPTR = 0x77ad14` | 同上 | 同上 |
| `SLOT_RESOLVE_ICALL = 0xd9b` | `il2cpp_codegen_resolve_icall` 的槽位 | **无托管元数据**，从包装函数调用点反推 |
| `ICALL_FIND_ALL = 0x59f91` | 声明但**从未引用** | 删除 |
| `ICALL_HAS_CHAR = 0x6108a` | 声明但**从未引用** | 删除 |

字面量为什么必须按内容而不是按地址附近搜索：同一个字符串在 Data 段里有**多份副本**
（`FontEngine::LoadFontFace` 有 8 份）。只用**完整**字符串（带参数表）搜索时，
每一条在 Data 段里恰好只有 1 份，而且这一份被代码用 `i32.const` 装载。
两个条件同时成立才接受。

缓存指针与 `resolve_icall` 来自包装函数的固定形状：

```
i32.const <cache>      ; 先取缓存地址
i32.load
local.tee / i32.eqz
if
  i32.const <cache>
  i32.const <literal>  ; 字面量地址
  call    <resolve>    ; ← resolve_icall，参数是字面量地址
  local.tee
  i32.store            ; *(cache) = 结果
end
... call_indirect
```

`resolve_icall` 用**两个**包装函数（`FindObjectsOfTypeAll`、`Font::HasCharacter`）
分别推导，必须得到**同一个**函数，且类型为 `(i32)->(i32)`。

### 1.4 托管字段偏移 —— 20 项来自 `dump.cs`，4 项 `VERIFIED_STATIC`

`dump.cs` 里每个实例字段都带 `// 0xNN` 注释，按 `class <名>` + 字段名精确取值；
`static`/`const` 字段被跳过（它们的偏移注释是 TypeDef 内的序号，不是对象偏移）。

| 来源 | 数量 | 例子 |
|---|---:|---|
| `dump.cs`（`RESOLVED`） | 20 | `TMP_Text.m_fontAsset=0x90`、`TMP_FontAsset.m_AtlasWidth=0xb0`、`TMP_Settings.m_fallbackFontAssets=0x44` |
| IL2CPP/ABI（`VERIFIED_STATIC`） | 4 | `m_CachedPtr=8`、`Il2CppClass::name=8`、`Il2CppString.length=8`、`.chars=12` |

另外消掉一处重复定义：`OFF_FA_ATLAS_TEX` 与 `OFF_FA_ATLAS_TEX0`、
`OFF_FA_ATLAS_W/H` 与 `OFF_FA_ATLAS_TEXW/H` 原本各写一遍同值，
现在后者是前者的别名，只剩一个真值来源。

### 1.5 其他被检查过的项

| 项 | 结论 |
|---|---|
| Wasm 表导出 | 已按**形状**找（有 `get/set/length`、无 `buffer`），不按名字 `zj` → 无需改 |
| 追加函数的导出名 `monmusu_cn_test/ui_test/alloc` | 由 `build_patch.py` 追加并写入 Export 段，位置由运行时按名取 → 无需改 |
| 追加函数编号 | 阶段二十八起就是 `imported + defined + offset` 算出来的，未回退 |
| Loader 内的调用位置 | `loader_base.js` 是官方原件，未修改；唯一绑定是被追加引擎的常量 → 已改 |
| `resources/assets.json` 的 `version_paths` | **仍是手写**（见 §六） |
| `suffix_assets` 的内容哈希文件名 | **仍是手写**（官方改 bundle 才需变） |
| 元素表 → 函数索引 | 构建期解析；运行时按槽位取表项 |

---

## 二、索引空间在补丁前还是补丁后

`resolve_runtime.py` 明确校验：**打补丁前后 Import / Table / Element 三个 section
逐字节相同**，否则拒绝生成配置（报"the patch changed the ... section"）。
因此"用原始 Wasm 解析出的槽位"对"实际要发布的补丁 Wasm"同样成立，
不存在"读到旧索引"的窗口。这条也在报告里落成 `wasm.index_space: {import,table,element}`。

Hook 追加的 8 个函数位于索引空间末尾，不改变任何已有索引；这一点由
`verify_patch.py` 独立复核。

---

## 三、分支专属配置与门禁

```
tools/wasm/runtime_targets.json        声明"要哪个方法"（不含任何编号）
        ↓ rebuild_wasm.py 第 4 步（两分支各跑一次）
build/wasm/runtime_config.json         /0/
build/wasm/branch-1/runtime_config.json /1/
        ↓ 全部测试通过后合并（第 8 步）
monmusu_cn_runtime_config.js           { branches: { "0": …, "1": … } }
        ↓ build_release.py 拼接
resources/ntd.loader.js                loader_base + 配置 + 4 引擎
```

- 配置与 Wasm 的绑定：`wasm.sha256`（补丁后明文 Wasm）。
- **打包门禁**（`tools/build_release.py::check_runtime_config`）：对 `assets.json`
  里声明的每个 `ntd.wasm.gz` 分支，解压实际要打包的文件、算 SHA-256、与配置比对；
  不符 / 缺条目 / `publishable=false` → `ValueError`，不出 ZIP。
- **运行时门禁**：配置的 `branch` 与页面识别的分支不一致 → 拒绝打补丁；
  import 总数不符 → 拒绝打补丁并说明"served wasm is not the one this loader was built for"。
- 失败安全：`applyRuntimeConfig()` 是**全有或全无**——任一必要项缺失就把
  `FONT.enabled=false`、`pathState='broken'`，译文照常工作，字体与路径规则关闭。
  这是"宁可少一个功能，也不猜槽位"。

---

## 四、实测

### 4.1 两分支构建

| 分支 | 结论 | Hook 定位 | 运行时绑定 | 产物 SHA-256 |
|---|---|---|---|---|
| `/0/` DMM | PASS | 9/9 | 50 RESOLVED + 4 VERIFIED_STATIC + 0 UNRESOLVED | `dd76b87d75fc…fbd0d`（与发行版逐字节一致） |
| `/1/` FANZA | PASS | 9/9 | 50 RESOLVED + 4 VERIFIED_STATIC + 0 UNRESOLVED | `a641537cb1b4…fdb4` |

### 4.2 打包与门禁

- `build_release.bat`：PASS，45 文件，ZIP **158,410,842 B**，manifest 逐文件 0 mismatch，
  新增字段 `runtimeConfigBranches: ["0","1"]`。
- 门禁负例（真实执行，不是单测）：把配置里 `/1/` 的 `wasm.sha256` 改成全 0 →
  `build_release.bat` 退出 1，报
  `branch 1: runtime config is bound to wasm 0000000000000000 but the packaged wasm is a641537cb1b4ebf1`，
  随后恢复原配置并校验 SHA 一致。
- 这次负例还**顺带抓到一个真 bug**：`build_release` 原本先写 `resources/ntd.loader.js`
  再校验配置，于是"被拒绝的那次"把伪造配置写进了 loader；恢复源文件后 loader 仍是坏的，
  随后被 `audit_corpus.loader_consistency` 抓住（`monmusu_cn_runtime_config.js_embedded = False`）。
  已把门禁移到写 loader **之前**，并重跑全流程。这条正好说明"loader 是产物、必须可校验"。

### 4.3 生产资源未回退

| 资源 | SHA-256（前 16） | 结论 |
|---|---|---|
| `resources/ntd.patched.wasm` | `dd76b87d75fc3498` | 未变（与阶段三十一发行版逐字节一致） |
| `resources/ntd.patched.1.wasm` | `a641537cb1b4ebf1` | 未变 |
| `resources/ntd.data.gz` | `2c312a0d9bc10654` | 未变 |
| `resources/ntd.data.1.gz` | `2158118d585bfde6` | 未变 |
| `resources/font_ui.bytes` | `09a41862522ec2b2` | 未变 |
| `resources/ui_detail.bytes` | `e3825ddf3752d6b9` | 未变 |
| `resources/ntd.loader.js` | `799229f18da3be00` | **变了**（内嵌生成的运行时配置，设计如此） |

---

## 五、回归测试

| 项 | 结果 |
|---|---|
| `tools/wasm/test_runtime_config.py` | **PASS 30/30** |
| `tools/wasm/test_wasm_build.py`（定位正例 + 负例） | PASS（两分支各跑） |
| `tools/wasm/test_reorder.py`（合成重排） | PASS |
| `test_bootstrap.js` | PASS（新增 `/1/` 配置、缺配置、错分支三个场景） |
| `test_font_layout.js`（15 项） | PASS（改为喂真实生成配置） |
| `test_ui.js`（模拟 Wasm 全流程） | PASS |
| `test_story_index/concat/runtime`、`test_translate` | PASS |
| `tools/test_assets.py`、`tools/test_addon_routing.py` | PASS |
| `test_audit`（25 项） | PASS |
| 全量翻译审计 | **PASS**：415,050 用例 → 412,132 PASS / 2,918 UNTESTABLE / 0 FAIL，负例 1394/1394 |
| 浏览器实机（DMM / FANZA） | **NOT RUN** |

`test_runtime_config.py` 覆盖的负例（全部真实执行）：

- `(Name, TS)` 有两个重载但没有 `native_params` → 拒绝；
- 声明的 `TypeSignature` 方法根本没有 → 拒绝；
- shim 实参数与签名不符 → 拒绝（并说明 `call_indirect` 会 trap）；
- icall 字面量在新构建里不存在 → 拒绝；
- 从错误的包装函数读缓存指针 → 拒绝；
- 用另一分支的补丁 Wasm 做输入 → 拒绝（Import/Table/Element 不符）；
- `dump.cs` 里字段改名 → 拒绝；
- 配置绑定的 SHA 与打包 Wasm 不符 → 打包拒绝；
- 某分支没有配置条目 → 打包拒绝。

正向还包含一条**回归证明**：`/0/` 上解析出的 24 个槽位、6 个字面量、2 个缓存指针、
`resolve_icall` 槽位、3 个 import 值，与旧手写表**完全相同**——说明新方法不是"换了一套数"，
而是从构建里重新推出了同一组数。

---

## 六、仍然依赖固定结构、无法自动解析的部分

| 项 | 状态 | 为什么不能自动 | 失败表现 |
|---|---|---|---|
| `resources/assets.json` 的 `version_paths` | 手写 | 版本号只能从官方页面/更新得知 | **静默**：一个资源都不替换，游戏全日文、无报错 |
| `suffix_assets` 的 4 个内容哈希文件名 | 手写 | 文件名是官方内容的哈希 | 代理不命中 → 缺字体/prefab |
| `m_CachedPtr` 等 4 个 ABI 常量 | `VERIFIED_STATIC` | 属于 IL2CPP 运行时布局，不在 `dump.cs` 里 | 换 Unity 大版本才需人工重看 |
| 9 个 Hook 的结构指纹基线 | 签核 | 官方改写函数体时无法证明语义等价 | **构建停止**，要求人工复核后 `--emit-baseline` |
| 按钮字体 304 字形 / 静态 prefab 原件 | 手写 | 与语料无关 | 重建失败 → WARNING（prefab）或阻止打包（字体） |
| `monmusu_cn_shim.js` 的协议常量（tag 位、`Il2CppString` 布局、TMP 采样参数） | 常量 | 是我们自己的协议，不属于官方构建 | 不变 |

---

## 七、为什么自动构建通过后仍需浏览器验收

自动化的边界是**"结构能对上"**，不是**"行为一致"**：

1. 槽位能重新定位到同名方法，但**方法内部实现可以变**（比如 `TMP_Text.set_font`
   改成会重置 material）。定位成功、行为不同。
2. `import a.nf` 的名字是按内容匹配的，但**官方可以改变它的语义**
   （原来 1 处调用点、第二参数是长度；新版可能变）。协议假设无法从二进制证明。
3. 字体回退链、TMP 材质关键字、atlas 尺寸这些是**运行时状态**，
   只有真的渲染一次才知道对不对。
4. 译文覆盖率、字形覆盖、静态 prefab 是**内容**问题，与 Wasm 无关，需各自门禁。
5. 代理是否真的命中新前缀，只有真实浏览器 + 真实页面能证明。

所以本轮的定位是：**让可识别的编译偏移变化自动适配，让真正不兼容的变化明确报错**，
而不是承诺永久兼容。`NOT RUN` 就是 `NOT RUN`。

---

## 八、文件清单

新增：

- `tools/wasm/runtime_targets.json` —— 声明式目标（方法身份，无编号）
- `tools/wasm/resolve_runtime.py` —— 解析器 + 报告 + 配置合并
- `tools/wasm/test_runtime_config.py` —— 30 项正负例
- `monmusu_cn_runtime_config.js` —— 生成的按分支配置（被拼进 loader）
- 本文件

修改：

- `monmusu_cn_shim.js` —— 58 个常量改为 `null` + `applyRuntimeConfig()`；删除 2 个死常量；
  `slotFn/tableFn/slotArity/alive` 增加 null 保护；`FONT.enabled` 默认关闭
- `monmusu_cn_bootstrap.js` —— 7 个常量删除，改读生成配置；新增分支/配置一致性检查
- `tools/wasm/rebuild_wasm.py` —— 新增 `--dump`、第 4 步解析、第 7 步测试、第 8 步合并
- `tools/build_release.py` —— 配置拼进 loader；新增打包门禁
- `tools/wasm/test_wasm_build.py`、`test_bootstrap.js`、`test_font_layout.js`、`test_ui.js`
- `audit_corpus.py` —— loader 一致性检查纳入配置文件
- `tools/translations/update_translations.py` —— 流水线纳入新测试
- `ARCHITECTURE.md`、`USAGE.md`、`HANDOFF_REPORT.md`
