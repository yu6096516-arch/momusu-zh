# -*- coding: utf-8 -*-
r"""生成 [awaked,...] / [notawaked,...] 分支词条的“解析后形态”。

为什么需要它
------------
游戏在把文本交给 UI 之前会先解析分支标记：
    觉醒单位：  [awaked,X] -> X        [notawaked,Y] -> （整段去掉）
    未觉醒单位：[awaked,X] -> （去掉）  [notawaked,Y] -> Y
而词库里只存了“未解析”的原始形态，插件用运行时字符串做精确匹配，
所以凡是带分支标记的文本（种族特性、能力说明等）都会匹配失败、回退日文。

本工具为每个含分支标记的键生成两种解析后形态的副本，写到
    translations/UICanvas/_awaked/zh_Hans.json
插件会递归加载 UICanvas 下所有 *.json，因此新分类直接生效。

用法
----
    python 生成觉醒分支词条.py            # 重建词条 + 更新仓库 manifest
    python 生成觉醒分支词条.py --check    # 只体检，不写文件

游戏更新、译文更新之后，跑一次再走发布流程即可；重复执行是幂等的。
"""
import argparse
import datetime
import hashlib
import json
import os
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

MARKERS = ("awaked", "notawaked")
# 这些方括号是正文内容（角色名/词条名），不是运行时标记
IGNORE_HEADS = ("触手",)


def flatten(node, out):
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str):
                out.append((k, v))
            else:
                flatten(v, out)


def tokenize(s):
    """切成 [("lit", text) | ("awaked", inner) | ("notawaked", inner)]，支持内容里的嵌套方括号。"""
    parts, buf, i = [], [], 0
    n = len(s)
    while i < n:
        hit = None
        for name in MARKERS:
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
        if depth != 0:            # 括号不闭合，按普通文本处理
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


def resolve(s, awakened):
    out = []
    for kind, text in tokenize(s):
        if kind == "lit":
            out.append(text)
        elif kind == "awaked":
            if awakened:
                out.append(text)
        else:  # notawaked
            if not awakened:
                out.append(text)
    return "".join(out)


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

    marked = [(k, v) for k, v in pairs if any("[" + m + "," in k for m in MARKERS)]
    new, collisions = {}, 0
    for k, v in marked:
        for awakened in (True, False):
            variant = resolve(k, awakened)
            if variant == k or not variant.strip():
                continue
            if variant in existing:
                collisions += 1
                continue
            new.setdefault(variant, resolve(v, awakened))
    return marked, existing, new, collisions


def update_manifest(repo, path_rel, data):
    mp = os.path.join(repo, "translations", "manifest.json")
    if not os.path.exists(mp):
        print("! manifest.json 不存在，跳过 manifest 更新:", mp)
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-translations", default=DEFAULT_GAME_TRANSLATIONS)
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--check", action="store_true", help="只体检，不写文件")
    args = ap.parse_args()

    game = args.game_translations
    if not os.path.isdir(game):
        sys.exit("找不到游戏译文目录: %s" % game)

    ui_root = os.path.join(game, "UICanvas")
    if not os.path.isdir(ui_root):
        sys.exit("找不到 UICanvas 目录: %s" % ui_root)
    pairs, bad = collect(ui_root)   # 只处理 UICanvas（插件会把它们合并成同一张全局词表）
    marked, existing, new, collisions = build(pairs)
    print("译文条目: %d（去重 %d）  读取失败文件: %d" % (len(pairs), len(existing), bad))
    print("含分支标记的条目: %d" % len(marked))
    print("需要补充的解析后形态: %d（与现有键撞车跳过 %d）" % (len(new), collisions))

    missing = [k for k, v in marked for w in (True, False)
               if resolve(k, w) != k and resolve(k, w).strip()
               and resolve(k, w) not in existing and resolve(k, w) not in new]
    print("未覆盖缺口: %d" % len(missing))
    if missing:
        for k in missing[:5]:
            print("   缺:", repr(k)[:110])

    if args.check:
        return

    payload = json.dumps(dict(sorted(new.items())), ensure_ascii=False, indent=2) + "\n"
    raw = payload.encode("utf-8")

    gdir = os.path.join(game, "UICanvas", CATEGORY)
    os.makedirs(gdir, exist_ok=True)
    with open(os.path.join(gdir, "zh_Hans.json"), "wb") as f:
        f.write(raw)
    print("写入游戏目录: %s" % os.path.join(gdir, "zh_Hans.json"))

    rdir = os.path.join(args.repo, "translations", "UICanvas", CATEGORY)
    os.makedirs(rdir, exist_ok=True)
    with open(os.path.join(rdir, "zh_Hans.json"), "wb") as f:
        f.write(raw)
    print("写入仓库目录: %s" % os.path.join(rdir, "zh_Hans.json"))

    update_manifest(args.repo, MANAGED_REL, raw)
    print("manifest 已更新: %s" % MANAGED_REL)
    print("提示：游戏内按 F6 重载，或重启游戏。")


if __name__ == "__main__":
    main()
