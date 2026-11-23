# -*- coding: utf-8 -*-
r"""活动文本润色：批次规划 + 质检工具

用法：
  python 活动润色工具.py plan                 # 生成批次清单 _event/batches.json
  python 活动润色工具.py show 1               # 导出第 1 批底稿 _event/b001.txt
  python 活动润色工具.py apply 1 patch.json   # 应用补丁（键不变、行数对齐、标签逐行一致）
  python 活动润色工具.py check 1              # 质检第 1 批
  python 活动润色工具.py stat                 # 全局统计
"""
import io
import os
import re
import sys
import json
import glob

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
SC = r"C:\mnms_td_r18\BepInEx\plugins\MonsterMusumeTDMod\translations\scenarios"
WORK = os.path.join(HERE, "_event")
CATS = ["261024", "261102", "261105", "261114s", "261116s"]
MAX_FILES = 10
MAX_KEYS = 260
TAG = re.compile(r"<[^>]*>")
KANA = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
BAD_WORDS = ["怪物", "小弟弟", "奶子", "老王", "阿尔吉", "德米", "校长", "学院"]
REFUSAL = ["无法翻译涉及", "抱歉，无法提供"]


def files():
    out = []
    for cat in CATS:
        out += sorted(glob.glob(os.path.join(SC, cat, "**", "zh_Hans.json"), recursive=True))
    return out


def tags_per_line(s):
    return [tuple(TAG.findall(ln)) for ln in s.split("\n")]


def plan():
    data, cur, curk = [], [], 0
    for p in files():
        try:
            o = json.load(io.open(p, encoding="utf-8-sig"))
        except Exception:
            continue
        n = len(o)
        if cur and (len(cur) >= MAX_FILES or curk + n > MAX_KEYS):
            data.append(cur)
            cur, curk = [], 0
        cur.append({"path": p, "keys": n})
        curk += n
    if cur:
        data.append(cur)
    os.makedirs(WORK, exist_ok=True)
    out = [{"batch": i + 1, "files": b} for i, b in enumerate(data)]
    io.open(os.path.join(WORK, "batches.json"), "w", encoding="utf-8").write(
        json.dumps(out, ensure_ascii=False, indent=1))
    print("批次 %d，文件 %d，键 %d" % (len(out), sum(len(b["files"]) for b in out),
                                     sum(f["keys"] for b in out for f in b["files"])))
    for b in out:
        print("  B%02d files=%d keys=%d" % (b["batch"], len(b["files"]),
                                            sum(f["keys"] for f in b["files"])))


def load_plan():
    return json.load(io.open(os.path.join(WORK, "batches.json"), encoding="utf-8"))


def show(n):
    b = next(x for x in load_plan() if x["batch"] == n)
    lines = []
    for f in b["files"]:
        o = json.load(io.open(f["path"], encoding="utf-8-sig"))
        lines.append("=== %s  (%d 键)" % (os.path.relpath(f["path"], SC), len(o)))
        for i, (k, v) in enumerate(o.items()):
            lines.append("---- #%d [日] %s" % (i, k.replace("\n", " ⏎ ")))
            lines.append("     [中] %s" % str(v).replace("\n", " ⏎ "))
        lines.append("")
    os.makedirs(WORK, exist_ok=True)
    p = os.path.join(WORK, "b%02d.txt" % n)
    io.open(p, "w", encoding="utf-8").write("\n".join(lines))
    print(p)


def apply(n, patch_path):
    b = next(x for x in load_plan() if x["batch"] == n)
    pmap = {}
    for ent in json.load(io.open(patch_path, encoding="utf-8-sig")):
        pmap[(ent["file"], ent["idx"])] = ent["value"]
    changed = 0
    for f in b["files"]:
        rel = os.path.relpath(f["path"], SC)
        o = json.load(io.open(f["path"], encoding="utf-8-sig"))
        keys = list(o.keys())
        dirty = False
        for (frel, idx), new in pmap.items():
            if frel != rel:
                continue
            k = keys[idx]
            old = o[k]
            if k.count("\n") != new.count("\n"):
                raise SystemExit("行数不符 %s#%d" % (rel, idx))
            if tags_per_line(new) != tags_per_line(k):
                raise SystemExit("标签位次不符 %s#%d" % (rel, idx))
            o[k] = new
            dirty = True
            changed += 1
        if dirty:
            io.open(f["path"], "w", encoding="utf-8", newline="\n").write(
                json.dumps(o, ensure_ascii=False, indent=2))
    print("已应用 %d 处" % changed)


def check(n):
    b = next(x for x in load_plan() if x["batch"] == n)
    bad = 0
    for f in b["files"]:
        rel = os.path.relpath(f["path"], SC)
        o = json.load(io.open(f["path"], encoding="utf-8-sig"))
        for i, (k, v) in enumerate(o.items()):
            s = str(v)
            if not s.strip():
                print("!! 空值 %s#%d" % (rel, i)); bad += 1; continue
            if k.count("\n") != s.count("\n"):
                print("!! 行数 %s#%d 键%d/值%d" % (rel, i, k.count("\n") + 1, s.count("\n") + 1)); bad += 1
            if tags_per_line(s) != tags_per_line(k):
                print("!! 标签 %s#%d" % (rel, i)); bad += 1
            if KANA.search(TAG.sub("", s)):
                print("!! 假名残留 %s#%d" % (rel, i)); bad += 1
            for w in BAD_WORDS:
                if w in s:
                    print("!! 禁用词 %s %s#%d" % (w, rel, i)); bad += 1
            for w in REFUSAL:
                if w in s:
                    print("!! 拒译 %s#%d" % (rel, i)); bad += 1
    print("B%02d check: 问题=%d" % (n, bad))


def stat():
    fs = files()
    n = k = 0
    for p in fs:
        n += 1
        try:
            k += len(json.load(io.open(p, encoding="utf-8-sig")))
        except Exception:
            pass
    print("活动批次文件 %d，键 %d" % (n, k))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stat"
    if cmd == "plan":
        plan()
    elif cmd == "show":
        show(int(sys.argv[2]))
    elif cmd == "apply":
        apply(int(sys.argv[2]), sys.argv[3])
    elif cmd == "check":
        check(int(sys.argv[2]))
    else:
        stat()
