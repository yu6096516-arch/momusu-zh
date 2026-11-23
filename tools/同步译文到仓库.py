# -*- coding: utf-8 -*-
r"""把游戏目录里的译文树同步到本仓库，并重建 manifest.json。

与旧 发布工具.py 的区别：输出直接落在仓库的 translations\ 下（而不是 tools\_发布），
所以可以直接配合 publish_update.bat 提交推送。

用法：
    python 同步译文到仓库.py            # 同步 translations + 重建 manifest
    python 同步译文到仓库.py --dry-run  # 只看会改哪些文件
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEFAULT_SRC = r"C:\mnms_td_r18\BepInEx\plugins\MonsterMusumeTDMod\translations"

SKIP = {"manifest.json", "ui_snapshot.tsv", "missing.json", "missing_index.json"}


def keep(name):
    low = name.lower()
    if name in SKIP:
        return False
    if ".before-" in name or low.endswith(".pending") or low.endswith(".pending.json"):
        return False
    if low.endswith(".bak") or low.endswith(".tmp") or ".bak-" in low:
        return False
    if low == "source.json" or "source" in low:
        return False
    return True


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=DEFAULT_SRC)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = args.src
    dst = os.path.join(REPO, "translations")
    if not os.path.isdir(src):
        sys.exit("找不到游戏译文目录: %s" % src)

    added, changed, same = [], [], 0
    files = {}
    for root, dirs, fs in os.walk(src):
        for f in fs:
            if not keep(f):
                continue
            p = os.path.join(root, f)
            rel = os.path.relpath(p, src).replace(os.sep, "/")
            if rel == "manifest.json":
                continue
            q = os.path.join(dst, rel.replace("/", os.sep))
            h = sha(p)
            files[rel] = {"sha256": h, "size": os.path.getsize(p)}
            if not os.path.exists(q):
                added.append(rel)
            elif sha(q) != h:
                changed.append(rel)
            else:
                same += 1
            if not args.dry_run:
                os.makedirs(os.path.dirname(q), exist_ok=True)
                shutil.copy2(p, q)

    print("新增 %d / 变更 %d / 未变 %d，共 %d 个文件" % (len(added), len(changed), same, len(files)))
    for rel in (added + changed)[:15]:
        print("   *", rel)
    if args.dry_run:
        return

    # 删掉仓库里已经不存在于游戏目录的受管文件（译文被上游删除的情况）
    removed = []
    for root, dirs, fs in os.walk(dst):
        for f in fs:
            p = os.path.join(root, f)
            rel = os.path.relpath(p, dst).replace(os.sep, "/")
            if rel == "manifest.json" or not keep(f):
                continue
            if rel not in files:
                os.remove(p)
                removed.append(rel)
    if removed:
        print("删除 %d 个已失效文件，例如 %s" % (len(removed), removed[:3]))

    man = {
        "schemaVersion": 1,
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "commit": "local",
        "files": files,
    }
    with open(os.path.join(dst, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(man, ensure_ascii=False, indent=2) + "\n")
    print("manifest.json 已重建：%d 个文件" % len(files))


if __name__ == "__main__":
    main()
