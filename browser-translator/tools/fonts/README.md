# 字体补字工具（开发者专用，阶段二十九补完）

`update_translations.bat` 在发现缺字时会自动调用本目录的构建器。**没有开发者入口，
也不需要额外命令**：新增汉字由流程自动补齐。

## 1. 为什么需要它

阶段二十九实现后，若汉化仓库新增了一个 cn25 字体没有的汉字，
`update_translations.bat` 会直接阻止打包，要求人工补字并重签字体基线。
那是设计缺陷：语料更新本来就不该需要人工介入字形。

本目录把「缺字 → 光栅化 → 追加进图集 → 回读校验」做成自动步骤，
仍然使用阶段二十五已验证的同一套参数与同一份字体源。

## 2. 文件

| 文件 | 作用 |
|---|---|
| `ttf_sdf.py` | TrueType → TMP 风格 SDF 瓦片（纯 Python + fontTools）。数值定义：128 = 轮廓线，向内 9px 到 255，向外 9px 到 0。 |
| `tmpfont.py` | TMP_FontAsset / Texture2D / UnityWebData 的读写原语；只追加的 shelf 打包器；越界与重叠校验。 |
| `add_glyphs.py` | 构建器：检测缺字 → 光栅化 → 追加 → 重建 `ntd.data.gz` 与 `font_ui.bytes` → 回读校验 → `report.json`。 |
| `test_font_tools.py` | 单元与负例测试（`python -B tools/fonts/test_font_tools.py`）。 |
| `assets/alimama.ttf` | 与 cn25 完全一致的字体源（SHA-256 `6433578c…`，等于 cn25 `report.json` 的 `fontSha256`）。 |

## 3. 关键约束（都由代码强制，不靠约定）

1. **只追加，不重排。** 既有 5833 条字形记录原样复制：`m_Index`、度量、`GlyphRect`
   全部不变，因此已正常显示的几千个字不可能因为补字而位移或改变 UV。
   验证项 `existing glyph records byte-identical`、`existing atlas rows byte-identical`。
2. **只写从未写过的图集行。** 追加起点 = `max(rect.y + rect.h + 9)`，
   构建前断言该行以下的像素全为 0；构建后断言该行以上逐字节不变。
3. **编码与 cn25 一致。** 48pt、`m_AtlasPadding` 9、自然墨迹矩形 + 外侧 9px 场、
   矩形间距 18px（`GAP`），与 `build_font_compat.py` 的 cell 规则相同。
4. **每份字体都补。** `ntd.data` 主字体、`font_ui.bytes` 里的原始副本与
   `monmusu_cn UI Font SDF` 变体（含 95 个追加 ASCII 字形）同步追加，
   各自分配高于自身 `m_Index` 最大值的新索引。
5. **字体源缺字就失败。** 报出具体码位，绝不臆造字形。
6. **容量不足不丢字。** 先尝试 8192×4096；不够则把同一张图集纵向扩到 8192×8192
   （同一 Texture2D 对象、同一 PPtr，并同步材质的 `_TextureWidth/_TextureHeight`）；
   仍不够则硬失败并说明需要第二张图集（本静态字体资产不支持，需人工设计）。

## 4. 独立用法（调试用）

```bat
python -B tools/fonts/add_glyphs.py --needed needed.json --out-dir work/fonttest/out
```

`needed.json` 形如 `{"codepoints": [39839, 40007]}`。输出目录里会得到
`ntd.data`、`ntd.data.gz`、`font_ui.bytes`、`report.json`；**不会**写进 `resources/`。

## 5. 已知边界

- 按钮字体 `resources/font_buttons.bytes`（`FOT-KurokaneStd-EBAll SDF`，304 个固定
  标签字形、44pt、1024×1024）**不由译文语料驱动**，本工具不重建它，只报告其覆盖。
- 静态 prefab 译文（`resources/ui_detail.bytes`）仍无生成器；语料变化会影响它时
  `update_translations.bat` 会阻止打包。
- 浏览器实机验证仍为 `NOT RUN`：本工具证明的是字形存在、度量自洽、旧字形未变，
  不是像素级的 TMP 着色结果。
