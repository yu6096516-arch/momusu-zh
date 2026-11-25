# -*- coding: utf-8 -*-
r"""收集「还没翻译的新文本」（PC 端取法）。

原理
----
PC 版的界面/数据表文本不在本地 assets 里（扫过 resources / sharedassets / level*，
只有 LineBreaking、BillingMode、ErrorMsg 这类系统小文件），它们是**运行时**由游戏
从服务端主数据拼出来的。所以只能用插件侧已经做好的两个出口：

1. `ui_snapshot.tsv`  —— 按 **F8** 导出，含当前界面每条文本的**控件路径 + 真实字符串**
2. `missing.json`     —— 按 **F8** 导出，插件自己记录的“没匹配到译文”的条目（按路径归档）

本脚本把这两个文件和现有词库对账，产出待翻译清单：
- 只含假名、词库里没有 → 纯新文本
- 同时含假名与汉字 → “半中半日”，说明形态不匹配（去跑 生成解析后形态词条.py）

用法
----
    python 收集缺失文本.py                 # 用游戏目录里的 snapshot/missing
    python 收集缺失文本.py --snapshot ... --missing ... --out ...
    python 收集缺失文本.py --report        # 只打印统计

输出：<out>\待翻译清单.tsv（path \\t 原文 \\t 类型）
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

GAME_TR = r"C:\mnms_td_r18\BepInEx\plugins\MonsterMusumeTDMod\translations"
KANA = re.compile(r"[\u3040-\u309f\u30a0-\u30fa\u30fc-\u30ff]")
HAN = re.compile(r"[\u4e00-\u9fff]")


def load_dictionary(root: str) -> set[str]:
    keys: set[str] = set()
    for dp, dn, fn in os.walk(os.path.join(root, "UICanvas")):
        for f in fn:
            if not f.endswith(".json"):
                continue
            try:
                data = json.load(open(os.path.join(dp, f), encoding="utf-8"))
            except Exception:
                continue

            def walk(node):
                if isinstance(node, dict):
                    for k, v in node.items():
                        if isinstance(v, str):
                            keys.add(k)
                        else:
                            walk(v)

            walk(data)
    return keys


def rows_from_snapshot(path: str, known: set[str]):
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            text = (r.get("text") or "").replace("\\n", "\n").strip()
            ui = r.get("ui_path") or ""
            if not text or not KANA.search(text):
                continue
            if text in known:
                continue
            kind = "半中半日" if HAN.search(text) else "纯日文"
            out.append((ui, text.replace("\n", "\\n"), kind))
    return out


def rows_from_missing(path: str, known: set[str]):
    out = []
    if not os.path.exists(path):
        return out
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        return out
    for ui, table in (data.get("$paths") or {}).items():
        if not isinstance(table, dict):
            continue
        for text in table:
            t = (text or "").strip()
            if not t or not KANA.search(t) or t in known:
                continue
            kind = "半中半日" if HAN.search(t) else "纯日文"
            out.append((ui, t.replace("\n", "\\n"), kind))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="收集待翻译的新文本")
    ap.add_argument("--translations", default=GAME_TR)
    ap.add_argument("--snapshot", default=os.path.join(GAME_TR, "ui_snapshot.tsv"))
    ap.add_argument("--missing", default=os.path.join(GAME_TR, "missing.json"))
    ap.add_argument("--out", default=os.path.join(os.path.expanduser("~"), "Desktop"))
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    known = load_dictionary(args.translations)
    rows = rows_from_snapshot(args.snapshot, known) + rows_from_missing(args.missing, known)

    uniq, seen = [], set()
    for p, t, k in rows:
        if t in seen:
            continue
        seen.add(t)
        uniq.append((p, t, k))

    pure = [r for r in uniq if r[2] == "纯日文"]
    half = [r for r in uniq if r[2] == "半中半日"]
    print(f"词库键: {len(known)}")
    print(f"快照/缺失里未命中的假名文本: {len(uniq)}  其中纯日文 {len(pure)} / 半中半日 {len(half)}")
    for p, t, k in uniq[:10]:
        print(f"   [{k}] {p[-60:]} :: {t[:60]}")

    if args.report:
        return
    os.makedirs(args.out, exist_ok=True)
    out = os.path.join(args.out, "待翻译清单.tsv")
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["ui_path", "原文", "类型"])
        w.writerows(uniq)
    print("写出:", out)


if __name__ == "__main__":
    main()
