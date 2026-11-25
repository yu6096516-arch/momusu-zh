# -*- coding: utf-8 -*-
r"""从官方 MasterData 里提取「还没翻译的新文本」。

背景
----
仓库里 `translations/UICanvas/<分类>/zh_Hans.json` 的键，就是官方主数据表
（`Data/*.json` 里的 `table[].<列>`）的原始字符串。主数据可以用
`tools\拉官方主数据.py` 从官方 CDN 拉到（版本接口 + ablist + XOR 解密）。

所以「新文本」根本不用逐屏按 F8：把新版本主数据拉下来，逐表对比现有词库，
差集就是待翻译清单。本脚本做这件事。

用法
----
    python 提取主数据新文本.py                       # 用默认路径
    python 提取主数据新文本.py --masterdata D:\md --translations "...\translations"
    python 提取主数据新文本.py --table ability_data   # 只看某张表

输出：<out>\主数据待翻译.tsv（表 \t 列 \t 原文），以及按表统计。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_MASTER = os.path.join(os.path.expanduser("~"), "Desktop", "mnms_masterdata")
DEFAULT_TR = r"C:\mnms_td_r18\BepInEx\plugins\MonsterMusumeTDMod\translations"
DEFAULT_OUT = os.path.join(os.path.expanduser("~"), "Desktop")

KANA = re.compile(r"[\u3040-\u309f\u30a0-\u30fa\u30fc-\u30ff]")
SKIP_COLS = {"kana", "ruby", "reading", "yomi"}          # 读音列不是 UI 文本
EXPR = re.compile(r"\[[^\[\]]*\]")


def normalize(s: str) -> str:
    """把 [0,2,0] / [MAG,0,0] 这类数值表达式统一成 <num>，便于和词库比对。"""
    def repl(m):
        inner = m.group(0)[1:-1]
        head = inner.split(",")[0].strip()
        if re.fullmatch(r"\d+", head) or re.fullmatch(r"[A-Za-z_]+", head):
            if head not in ("awaked", "notawaked"):
                return "<num>"
        return m.group(0)

    return EXPR.sub(repl, s)


def load_known(tr_root: str) -> set[str]:
    keys: set[str] = set()
    for sub in ("UICanvas", "names", "subskills"):
        base = os.path.join(tr_root, sub)
        for dp, dn, fn in os.walk(base):
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


def iter_strings(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from iter_strings(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from iter_strings(v, path)
    elif isinstance(node, str):
        yield path, node


def scan(master_root: str, table_filter=None):
    found = {}
    for cat in sorted(os.listdir(master_root)):
        cdir = os.path.join(master_root, cat)
        if not os.path.isdir(cdir):
            continue
        for f in sorted(os.listdir(cdir)):
            if not f.endswith(".json"):
                continue
            table = os.path.splitext(f)[0]
            if table_filter and table != table_filter:
                continue
            p = os.path.join(cdir, f)
            try:
                data = json.load(open(p, encoding="utf-8"))
            except Exception:
                continue
            t = data.get("table") if isinstance(data, dict) else None
            if not isinstance(t, list):
                continue
            for row in t:
                if not isinstance(row, dict):
                    continue
                for col, val in row.items():
                    if not isinstance(val, str) or len(val) < 2:
                        continue
                    if col in SKIP_COLS:
                        continue
                    if not KANA.search(val):
                        continue
                    found.setdefault(val, (table, col))
    return found


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--masterdata", default=DEFAULT_MASTER)
    ap.add_argument("--translations", default=DEFAULT_TR)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--table", default=None)
    args = ap.parse_args()

    if not os.path.isdir(args.masterdata):
        sys.exit("找不到主数据目录，先跑 tools\\拉官方主数据.py --out %s" % args.masterdata)

    known = load_known(args.translations)
    known_norm = {normalize(k) for k in known}
    found = scan(args.masterdata, args.table)
    new = {}
    for v, meta in found.items():
        if v in known or normalize(v) in known_norm:
            continue
        new[v] = meta

    print("主数据里的日文字符串: %d（去重）" % len(found))
    print("词库键: %d" % len(known))
    print("未收录（= 待翻译）: %d" % len(new))

    by_table = Counter(meta[0] for meta in new.values())
    print("\n按表分布（前 20）:")
    for t, n in by_table.most_common(20):
        print(f"   {t:36s} {n}")

    os.makedirs(args.out, exist_ok=True)
    p = os.path.join(args.out, "主数据待翻译.tsv")
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["表", "列", "原文"])
        for v, (t, c) in sorted(new.items(), key=lambda kv: (kv[1][0], kv[1][1])):
            w.writerow([t, c, v.replace("\n", "\\n").replace("\r", "")])
    print("\n写出:", p)


if __name__ == "__main__":
    main()
