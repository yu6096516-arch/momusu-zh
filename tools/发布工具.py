# -*- coding: utf-8 -*-
r"""生成可推送到 GitHub 的发布目录

用法：
  python 发布工具.py              # 只做 translations（推荐，字体不发布）
  python 发布工具.py --with-font  # 连字体包一起发布（assets/alimama，41.5 MB）

产物：
  C:\Users\yuu\Desktop\寝室润色\_发布\
      translations\  ← 整个译文树 + 重新生成的 manifest.json
      assets\        ← 仅 --with-font 时存在
"""
import io
import os
import sys
import json
import shutil
import hashlib
import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SRC = r"C:\mnms_td_r18\BepInEx\plugins\MonsterMusumeTDMod\translations"
FONT = r"C:\mnms_td_r18\BepInEx\plugins\alimama"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_发布")


SKIP = {"manifest.json", "ui_snapshot.tsv", "missing.json"}


def keep(name):
    low = name.lower()
    if name in SKIP:
        return False
    if ".before-" in name or low.endswith(".pending") or low.endswith(".pending.json"):
        return False
    if low.endswith(".bak") or low.endswith(".tmp"):
        return False
    if low == "source.json" or "source" in low:
        return False          # 日文原稿，插件不读
    return True


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_translations():
    dst = os.path.join(OUT, "translations")
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    files = {}
    n = 0
    for root, dirs, fs in os.walk(SRC):
        for f in fs:
            if not keep(f):
                continue
            p = os.path.join(root, f)
            rel = os.path.relpath(p, SRC).replace(os.sep, "/")
            q = os.path.join(dst, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(q), exist_ok=True)
            shutil.copy2(p, q)
            files[rel] = {"sha256": sha(q), "size": os.path.getsize(q)}
            n += 1
    man = {
        "schemaVersion": 1,
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "commit": "local",
        "files": files,
    }
    io.open(os.path.join(dst, "manifest.json"), "w", encoding="utf-8", newline="\n").write(
        json.dumps(man, ensure_ascii=False, indent=2) + "\n")
    print("translations: %d 个文件，manifest 已生成" % n)


def build_assets():
    dst = os.path.join(OUT, "assets")
    os.makedirs(dst, exist_ok=True)
    q = os.path.join(dst, "alimama")
    shutil.copy2(FONT, q)
    man = {
        "schemaVersion": 1,
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "commit": "local",
        "files": {"alimama": {"sha256": sha(q), "size": os.path.getsize(q)}},
    }
    io.open(os.path.join(dst, "manifest.json"), "w", encoding="utf-8", newline="\n").write(
        json.dumps(man, ensure_ascii=False, indent=2) + "\n")
    print("assets: alimama %.1f MB，manifest 已生成" % (os.path.getsize(q) / 1048576))


if __name__ == "__main__":
    build_translations()
    if "--with-font" in sys.argv:
        build_assets()
    total = 0
    cnt = 0
    for root, d, fs in os.walk(OUT):
        for f in fs:
            total += os.path.getsize(os.path.join(root, f))
            cnt += 1
    print("发布目录 %s ：%d 文件，%.1f MB" % (OUT, cnt, total / 1048576))
