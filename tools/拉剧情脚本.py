# -*- coding: utf-8 -*-
r"""直接从官方 CDN 拉剧情脚本（不用玩、不用 F8、不用 crawl 19 GB）。

原理
----
官方资源包里剧情书的**包名是固定的 md5 规则**（来自 alksdesu/monster-musume-ps）：

    bundle = md5(("ScenarioChapter/" + scenario_name + ".book").lower().replace("/", "-")) + ".bytes"

scenario_name 从主数据拿：
  - story_data_main / _unit / _other / _event / _group 的 `scenario_name`
  - quest_data 的 `before_story_chapter_id` / `bfr_btl_story_chapter_id` /
    `afr_btl_story_chapter_id` / `after_story_chapter_id`（关卡前后剧情）
另外 `ScenarioChapter/config.chapter` 是章节索引。

拿到包名后在 ablist.json 里查到 hash/size，下载（剧情包不加密，几 KB~几十 KB），
UnityPy 读 UTAGE grid，第 10 列就是台词。

用法
----
    python 拉剧情脚本.py                       # 只补还没有的（对比 translations\scenarios）
    python 拉剧情脚本.py --all                 # 全量
    python 拉剧情脚本.py --id Ev1118051Nml     # 只拉指定场景
    python 拉剧情脚本.py --missing --dry-run   # 只列出缺哪些

依赖：pip install requests UnityPy
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CDN = "https://assets.game-monmusu-td.net/assetbundles"
PLATFORM = "windows_r18"
DEFAULT_MASTER = os.path.join(os.path.expanduser("~"), "Desktop", "mnms_masterdata")
DEFAULT_TR = r"C:\mnms_td_r18\BepInEx\plugins\MonsterMusumeTDMod\translations"
STORY_TABLES = ("story_data_main", "story_data_event", "story_data_other",
                "story_data_unit", "story_data_group")
QUEST_TABLE = "quest_data"
QUEST_FIELDS = ("before_story_chapter_id", "bfr_btl_story_chapter_id",
                "afr_btl_story_chapter_id", "after_story_chapter_id")
CHAPTER_INDEX = "ScenarioChapter/config.chapter"


def bundle_name(logical: str) -> str:
    return hashlib.md5(logical.lower().replace("/", "-").encode()).hexdigest() + ".bytes"


def scenario_names(master: str) -> list[str]:
    names = set()
    for table in STORY_TABLES:
        p = os.path.join(master, "Data", table + ".json")
        if not os.path.exists(p):
            continue
        for row in json.load(open(p, encoding="utf-8")).get("table") or []:
            n = (row.get("scenario_name") or "").strip()
            if n:
                names.add(n)
    p = os.path.join(master, "Data", QUEST_TABLE + ".json")
    if os.path.exists(p):
        for row in json.load(open(p, encoding="utf-8")).get("table") or []:
            for f in QUEST_FIELDS:
                n = (row.get(f) or "").strip()
                if n:
                    names.add(n)
    return sorted(names)


def translated(tr_root: str) -> set[str]:
    got = set()
    for dp, dn, fn in os.walk(os.path.join(tr_root, "scenarios")):
        if "zh_Hans.json" in fn:
            got.add(os.path.basename(dp).lower())
    return got


def parse_book(raw: bytes) -> list[dict]:
    import UnityPy
    env = UnityPy.load(raw)
    rows = []
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        try:
            tree = obj.read_typetree()
        except Exception:
            continue
        for grid in tree.get("importGridList") or []:
            for row in grid.get("rows") or []:
                cells = row.get("strings") or []
                text = cells[9] if len(cells) > 9 else ""
                if not text or text == "Text":
                    continue
                rows.append({"speaker": cells[2] if len(cells) > 2 else "",
                             "text": text})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--masterdata", default=DEFAULT_MASTER)
    ap.add_argument("--translations", default=DEFAULT_TR)
    ap.add_argument("--out", default=None, help="输出目录（默认写回 translations\\scenarios）")
    ap.add_argument("--all", action="store_true", help="全量拉（默认只补缺失）")
    ap.add_argument("--id", action="append", default=None, help="只拉指定 scenario_name，可重复")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()

    try:
        import requests
    except ImportError:
        sys.exit("需要 requests：pip install requests")

    ab_p = os.path.join(args.masterdata, "ablist.json")
    if not os.path.exists(ab_p):
        sys.exit("缺 ablist.json，先跑 tools\\拉官方主数据.py")
    ab = json.load(open(ab_p, encoding="utf-8"))
    assets = {a["path"]: a for a in ab["data"]}
    base_url = f"{CDN}/ver_{ab['baseVersion']}/{PLATFORM}"

    if args.id:
        names = list(args.id)
    else:
        names = scenario_names(args.masterdata)
        if not args.all:
            have = translated(args.translations)
            names = [n for n in names if n.lower() not in have]

    wanted = {n: bundle_name(f"ScenarioChapter/{n}.book") for n in names}
    wanted["config.chapter"] = bundle_name(CHAPTER_INDEX)
    listed = [(n, assets[p]) for n, p in wanted.items() if p in assets]
    missing = [n for n, p in wanted.items() if p not in assets]
    total = sum(int(a["size"]) for _, a in listed)
    print(f"场景 {len(names)} 个 → 命中资源包 {len(listed)} 个（{total/1048576:.1f} MB），未列出 {len(missing)}")
    if missing:
        print("   未在 ablist 里（旧版/已下架）:", ", ".join(missing[:8]))
    if args.dry_run:
        for n, a in listed[:20]:
            print(f"   {n:26s} {a['size']:>8}B")
        return

    out_root = args.out or os.path.join(args.translations, "scenarios")
    os.makedirs(out_root, exist_ok=True)
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0"

    def grab(item):
        name, a = item
        url = f"{base_url}/{a['hash']}{a['path']}"
        blob = s.get(url, timeout=90).content
        if len(blob) != int(a["size"]):
            return name, None, "size mismatch"
        rows = parse_book(blob)
        return name, rows, None

    ok = lines = 0
    fails = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for name, rows, err in pool.map(grab, listed):
            if err or rows is None:
                fails.append((name, err))
                continue
            d = os.path.join(out_root, name.lower())
            os.makedirs(d, exist_ok=True)
            table = {}
            for r in rows:
                table.setdefault(r["text"], "")
            with open(os.path.join(d, "zh_Hans.json"), "w", encoding="utf-8") as f:
                json.dump(table, f, ensure_ascii=False, indent=4)
            with open(os.path.join(d, "source.json"), "w", encoding="utf-8") as f:
                json.dump({"scenario_name": name, "rows": rows}, f, ensure_ascii=False, indent=2)
            ok += 1
            lines += len(rows)

    print(f"导出场景 {ok} 个，台词 {lines} 行，失败 {len(fails)}")
    for n, e in fails[:5]:
        print("   FAIL", n, e)


if __name__ == "__main__":
    main()
