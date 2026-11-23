# -*- coding: utf-8 -*-
r"""汉化体检（基线对比版）

用法：
  python 体检.py                # 和基线比，只看有没有变差
  python 体检.py --set-baseline # 把当前数字记为基线

硬性检查（必须为 0）：
  · JSON 能否解析
  · manifest 缺失 / 哈希不符
软性指标（记录并对比，不单独判失败）：
  · 空值 / 键值行数不符 / 标签位次差异 / 假名残留
"""
import io
import os
import re
import sys
import json
import glob
import hashlib

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

G = r"C:\mnms_td_r18\BepInEx\plugins\MonsterMusumeTDMod\translations"
BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "体检基线.json")
TAG = re.compile(r"<[^>]*>")
KANA = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")


def scan():
    st = {"parse_error": 0, "empty": 0, "line_mismatch": 0, "tag_mismatch": 0,
          "kana": 0, "files": 0}
    by_dir = {}
    for p in glob.glob(os.path.join(G, "**", "zh_Hans.json"), recursive=True):
        rel = os.path.relpath(p, G)
        top = rel.split(os.sep)[0] if os.sep in rel else rel
        d = by_dir.setdefault(top, {"empty": 0, "line": 0, "tag": 0, "kana": 0})
        st["files"] += 1
        try:
            o = json.load(io.open(p, encoding="utf-8-sig"))
        except Exception:
            st["parse_error"] += 1
            continue
        for k, v in o.items():
            s = str(v)
            if not s.strip():
                st["empty"] += 1
                d["empty"] += 1
                continue
            if k.count("\n") != s.count("\n"):
                st["line_mismatch"] += 1
                d["line"] += 1
            if [tuple(TAG.findall(l)) for l in s.split("\n")] != \
               [tuple(TAG.findall(l)) for l in k.split("\n")]:
                st["tag_mismatch"] += 1
                d["tag"] += 1
            if KANA.search(TAG.sub("", s)):
                st["kana"] += 1
                d["kana"] += 1
    # manifest
    man = os.path.join(G, "manifest.json")
    st["manifest_missing"] = st["manifest_mismatch"] = 0
    if os.path.exists(man):
        m = json.load(io.open(man, encoding="utf-8-sig"))["files"]
        st["manifest_count"] = len(m)
        for rel, meta in m.items():
            fp = os.path.join(G, rel.replace("/", os.sep))
            if not os.path.exists(fp):
                st["manifest_missing"] += 1
            elif hashlib.sha256(open(fp, "rb").read()).hexdigest() != meta.get("sha256"):
                st["manifest_mismatch"] += 1
    else:
        st["manifest_count"] = 0
        st["manifest_missing"] = 1
    return st, by_dir


def main():
    st, by_dir = scan()
    print("文件 %d 个 / manifest %s 条" % (st["files"], st.get("manifest_count")))
    print("  硬性：解析失败 %d / manifest 缺失 %d / 哈希不符 %d"
          % (st["parse_error"], st["manifest_missing"], st["manifest_mismatch"]))
    print("  软性：空值 %d / 行数不符 %d / 标签差异 %d / 假名 %d"
          % (st["empty"], st["line_mismatch"], st["tag_mismatch"], st["kana"]))

    hard = st["parse_error"] + st["manifest_missing"] + st["manifest_mismatch"]
    if hard:
        print("!! 硬性问题 %d 个，先修这个再发布" % hard)

    if "--set-baseline" in sys.argv:
        io.open(BASE, "w", encoding="utf-8").write(
            json.dumps({"stats": st, "by_dir": by_dir}, ensure_ascii=False, indent=1))
        print("已记录基线 -> %s" % BASE)
        return
    if os.path.exists(BASE):
        old = json.load(io.open(BASE, encoding="utf-8"))["stats"]
        diff = {k: st.get(k, 0) - old.get(k, 0) for k in
                ("empty", "line_mismatch", "tag_mismatch", "kana")}
        worse = {k: v for k, v in diff.items() if v > 0}
        better = {k: v for k, v in diff.items() if v < 0}
        print("对比基线：变差 %s / 变好 %s" % (worse or "无", better or "无"))
        if worse:
            print("!! 有新引入的问题，检查一下再发布")
    else:
        print("（还没有基线，可用 --set-baseline 记录）")


if __name__ == "__main__":
    main()
