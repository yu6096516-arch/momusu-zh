# -*- coding: utf-8 -*-
r"""检查剧情覆盖：主数据里的场景 ID vs 已翻译的 scenarios 目录。

主数据的 `story_data_main / story_data_other / story_data_unit / story_data_event`
每行都有 `scenario_name`（如 `ev1118051Nml`、`10006A0_Nml`），正好对应
`translations/scenarios/<分类>/<scenario_id>/zh_Hans.json` 的目录名（小写）。

本脚本列出「主数据有、词库没有」的场景 —— 也就是新主线 / 新活动 / 新角色剧情。

用法
----
    python 检查剧情覆盖.py
    python 检查剧情覆盖.py --masterdata D:\md --translations "...\translations"
输出：<out>\剧情未覆盖.tsv（表 \t chapter_name \t scenario_id \t 标题）
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_MASTER = os.path.join(os.path.expanduser("~"), "Desktop", "mnms_masterdata")
DEFAULT_TR = r"C:\mnms_td_r18\BepInEx\plugins\MonsterMusumeTDMod\translations"
TABLES = ["story_data_main", "story_data_event", "story_data_other",
          "story_data_unit", "story_data_group"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--masterdata", default=DEFAULT_MASTER)
    ap.add_argument("--translations", default=DEFAULT_TR)
    ap.add_argument("--out", default=os.path.join(os.path.expanduser("~"), "Desktop"))
    args = ap.parse_args()

    scen_root = os.path.join(args.translations, "scenarios")
    if not os.path.isdir(scen_root):
        sys.exit("找不到 scenarios 目录: %s" % scen_root)
    have = set()
    for dp, dn, fn in os.walk(scen_root):
        if "zh_Hans.json" in fn:
            have.add(os.path.basename(dp).lower())

    rows, total = [], 0
    for name in TABLES:
        p = os.path.join(args.masterdata, "Data", name + ".json")
        if not os.path.exists(p):
            continue
        d = json.load(open(p, encoding="utf-8"))
        t = d.get("table") or []
        miss = 0
        for r in t:
            sid = (r.get("scenario_name") or "").strip()
            if not sid:
                continue
            total += 1
            if sid.lower() in have:
                continue
            miss += 1
            rows.append([name, r.get("chapter_name", ""), sid, r.get("title", "")])
        print(f"{name:20s} 行 {len(t):5d}  未覆盖 {miss:4d}")

    print(f"\n场景总数 {total}，已覆盖 {total - len(rows)}，未覆盖 {len(rows)}")
    if rows:
        os.makedirs(args.out, exist_ok=True)
        p = os.path.join(args.out, "剧情未覆盖.tsv")
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter="\t")
            w.writerow(["表", "chapter_name", "scenario_id", "标题"])
            w.writerows(rows)
        print("写出:", p)


if __name__ == "__main__":
    main()
