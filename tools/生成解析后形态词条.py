# -*- coding: utf-8 -*-
r"""生成“解析后形态”词条（分支标记 + 数值占位符 + 特性标题）。

为什么需要
----------
游戏在把文本交给 UI 之前会先做两件事：

1. 解析分支标记
       觉醒单位：  [awaked,X] -> X        [notawaked,Y] -> （整段去掉）
       未觉醒单位：[awaked,X] -> （去掉）  [notawaked,Y] -> Y
2. 把方括号表达式算成实际数值
       [MAG,0,0] / [0,2,0] / [duration] / [attack,[MAG,0,0]] ...
   例如「再出撃時間[0,2,0]%短縮」运行时是「再出撃時間40%短縮」。

而词库里只存了“未解析”的原始形态，插件又是拿运行时字符串做匹配的，所以：
- 带分支标记的文本一定匹配不上（种族特性、能力说明）；
- 带方括号数值的文本也匹配不上（除非插件已有 <num> 模板）。

本工具为这类词条生成解析后的副本：
- 分支标记按觉醒 / 未觉醒各生成一份；
- 方括号数值统一换成插件的 <num> 占位符（插件会用它做数字模板匹配）；
- 顺便按 ability_data 的特性名表批量生成「種族特性：XXX」标题键。
产物写到 translations/UICanvas/_awaked/zh_Hans.json，插件会递归加载 UICanvas 下所有 *.json。

用法
----
    python 生成解析后形态词条.py            # 重建词条 + 更新仓库 manifest
    python 生成解析后形态词条.py --check    # 只体检，不写文件

游戏更新 / 译文更新后跑一次，再走发布流程即可；重复执行幂等。
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEFAULT_GAME_TRANSLATIONS = r"C:\mnms_td_r18\BepInEx\plugins\MonsterMusumeTDMod\translations"
CATEGORY = "_awaked"
MANAGED_REL = "UICanvas/%s/zh_Hans.json" % CATEGORY

BRANCH = ("awaked", "notawaked")
NUM = "<num>"


def flatten(node, out):
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str):
                out.append((k, v))
            else:
                flatten(v, out)


def tokenize(s):
    """切成 ("lit", text) / ("awaked", inner) / ("notawaked", inner)，支持内容嵌套方括号。"""
    parts, buf, i, n = [], [], 0, len(s)
    while i < n:
        hit = None
        for name in BRANCH:
            prefix = "[" + name + ","
            if s.startswith(prefix, i):
                hit = (name, prefix)
                break
        if not hit:
            buf.append(s[i])
            i += 1
            continue
        name, prefix = hit
        j = i + len(prefix)
        depth = 1
        while j < n:
            c = s[j]
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if depth != 0:
            buf.append(s[i:])
            break
        if buf:
            parts.append(("lit", "".join(buf)))
            buf = []
        parts.append((name, s[i + len(prefix):j]))
        i = j + 1
    if buf:
        parts.append(("lit", "".join(buf)))
    return parts


def resolve_branch(s, awakened):
    out = []
    for kind, text in tokenize(s):
        if kind == "lit":
            out.append(text)
        elif kind == "awaked":
            if awakened:
                out.append(text)
        elif not awakened:
            out.append(text)
    return "".join(out)


def _is_expr(content):
    c = content.strip()
    if not c:
        return False
    if re.fullmatch(r"\d+(?:,\d+)*", c):
        return True
    head = c.split(",")[0].strip()
    return bool(re.fullmatch(r"[A-Za-z_]+", head)) and head not in BRANCH


def normalize_numbers(s):
    """把 [MAG,0,0] / [0,2,0] / [duration] 这类表达式换成 <num>（支持嵌套）。"""
    out, i, n = [], 0, len(s)
    while i < n:
        if s[i] != "[":
            out.append(s[i])
            i += 1
            continue
        j = i + 1
        depth = 1
        while j < n:
            if s[j] == "[":
                depth += 1
            elif s[j] == "]":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if depth != 0:
            out.append(s[i:])
            break
        content = s[i + 1:j]
        out.append(NUM if _is_expr(content) else s[i:j + 1])
        i = j + 1
    return "".join(out)


def has_branch(s):
    return any("[" + m + "," in s for m in BRANCH)


def collect(root):
    pairs, bad = [], 0
    for dp, dn, fn in os.walk(root):
        if os.path.basename(dp) == CATEGORY:
            continue
        for f in fn:
            if not f.endswith(".json"):
                continue
            try:
                flatten(json.load(open(os.path.join(dp, f), encoding="utf-8")), pairs)
            except Exception:
                bad += 1
    return pairs, bad


def build(pairs):
    existing = {}
    for k, v in pairs:
        existing.setdefault(k, v)

    source = {}
    for k, v in pairs:
        if has_branch(k) or normalize_numbers(k) != k:
            source.setdefault(k, v)

    new = {"branch": 0, "number": 0, "title": 0, "itemname": 0}
    skipped = 0
    for k, v in source.items():
        flags = (True, False) if has_branch(k) else (None,)
        for awakened in flags:
            k2 = resolve_branch(k, awakened) if awakened is not None else k
            v2 = resolve_branch(v, awakened) if awakened is not None else v
            if k2 != k and k2.strip() and k2 not in existing and k2 not in new.get("_map", {}):
                new.setdefault("_map", {})[k2] = v2
                new["branch"] += 1
            k3, v3 = normalize_numbers(k2), normalize_numbers(v2)
            if k3 != k2 and k3.strip() and k3 not in existing and k3 not in new.get("_map", {}):
                new.setdefault("_map", {})[k3] = v3
                new["number"] += 1
            elif k3 in existing:
                skipped += 1

    # 特性标题：種族特性：<名字>
    names = {}
    for dp, dn, fn in os.walk(os.path.join(DEFAULT_UI_ROOT, "ability_data")):
        for f in fn:
            if f.endswith(".json"):
                try:
                    d = json.load(open(os.path.join(dp, f), encoding="utf-8"))
                except Exception:
                    continue
                bucket = d.get("table[].name")
                if isinstance(bucket, dict):
                    for jp, cn in bucket.items():
                        if isinstance(cn, str) and cn and cn != jp:
                            names.setdefault(jp, cn)
    for jp, cn in names.items():
        k = "種族特性：" + jp
        if k not in existing and k not in new.get("_map", {}):
            new["_map"][k] = "种族特性：" + cn
            new["title"] += 1

    # 专武 / 装备 / 道具名 + 强化等级：运行时会在名字后面直接接「＋10」
    for jp, cn in collect_item_names().items():
        if NUM in jp:
            cand = [(jp, cn)]
        else:
            cand = [(jp, cn), (jp + NUM, cn + NUM)]
        for k, v in cand:
            if not k.strip() or k in existing or k in new.get("_map", {}):
                continue
            new["_map"][k] = v
            new["itemname"] += 1

    return existing, new.pop("_map", {}), new, skipped


def collect_item_names():
    """从各表的名字桶里收集 名字 -> 中文名。

    优先级：unit_weapon 的路径表（专武名字控件）最高 —— 插件会优先用路径表，
    全局词表跟着用同一个译名才不会出现「列表一个名、弹窗另一个名」。
    """
    out = {}

    def take(d):
        if isinstance(d, dict):
            for k, v in d.items():
                if isinstance(v, str) and v and v != k:
                    out.setdefault(k, v)
                elif isinstance(v, dict):
                    take(v)

    # 1) unit_weapon 是路径表：只取专武名字控件
    p = os.path.join(DEFAULT_UI_ROOT, "unit_weapon", "zh_Hans.json")
    if os.path.exists(p):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            d = {}
        for path, table in (d.get("$global") or {}).items():
            if path.endswith("UniqueWeapon_NameView/NameText"):
                take(table)

    # 2) 数据表
    plan = {
        "unique_weapon_data": "table[].name",
        "equipment": "table[].name",
        "item_template": "table[].display_name",
    }
    for cat, bucket in plan.items():
        p = os.path.join(DEFAULT_UI_ROOT, cat, "zh_Hans.json")
        if not os.path.exists(p):
            continue
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        take(d.get(bucket))
    return out


DEFAULT_UI_ROOT = os.path.join(DEFAULT_GAME_TRANSLATIONS, "UICanvas")


def update_manifest(repo, path_rel, data):
    mp = os.path.join(repo, "translations", "manifest.json")
    if not os.path.exists(mp):
        print("! manifest.json 不存在，跳过:", mp)
        return
    man = json.load(open(mp, encoding="utf-8"))
    man.setdefault("files", {})[path_rel] = {
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
    }
    man["generatedAt"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with open(mp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(man, ensure_ascii=False, indent=2) + "\n")


def main():
    global DEFAULT_UI_ROOT
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-translations", default=DEFAULT_GAME_TRANSLATIONS)
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    ui_root = os.path.join(args.game_translations, "UICanvas")
    if not os.path.isdir(ui_root):
        sys.exit("找不到 UICanvas 目录: %s" % ui_root)
    DEFAULT_UI_ROOT = ui_root

    pairs, bad = collect(ui_root)
    existing, generated, stats, skipped = build(pairs)
    print("译文条目: %d（去重 %d） 读取失败: %d" % (len(pairs), len(existing), bad))
    print("  + 分支标记形态: %d" % stats["branch"])
    print("  + 数值占位符形态: %d" % stats["number"])
    print("  + 特性标题: %d" % stats["title"])
    print("  + 装备/专武强化名: %d" % stats["itemname"])
    print("  新增合计: %d（已存在跳过 %d）" % (len(generated), skipped))

    if args.check:
        return

    payload = json.dumps(dict(sorted(generated.items())), ensure_ascii=False, indent=2) + "\n"
    raw = payload.encode("utf-8")

    gdir = os.path.join(args.game_translations, "UICanvas", CATEGORY)
    os.makedirs(gdir, exist_ok=True)
    with open(os.path.join(gdir, "zh_Hans.json"), "wb") as f:
        f.write(raw)
    print("写入游戏目录:", os.path.join(gdir, "zh_Hans.json"))

    rdir = os.path.join(args.repo, "translations", "UICanvas", CATEGORY)
    os.makedirs(rdir, exist_ok=True)
    with open(os.path.join(rdir, "zh_Hans.json"), "wb") as f:
        f.write(raw)
    print("写入仓库目录:", os.path.join(rdir, "zh_Hans.json"))

    update_manifest(args.repo, MANAGED_REL, raw)
    print("manifest 已更新:", MANAGED_REL)
    print("提示：游戏内按 F6 重载，或重启游戏。")


if __name__ == "__main__":
    main()
