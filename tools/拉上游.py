# -*- coding: utf-8 -*-
r"""从 Berumic 上游「进货」：只补本地缺的键，绝不覆盖本地已有译文

用法：
  python 拉上游.py                 # 预演：只统计，不下载
  python 拉上游.py --apply         # 真拉：下载差异文件并补键
  python 拉上游.py --apply --files # 连本地没有的整份文件也下载
  python 拉上游.py --base <url>    # 换源

原则：
  · 键已存在且值非空 → 一律保留本地（你的润色优先）
  · 键不存在或值为空 → 填入上游译文
  · 本地没有的文件 → 整个复制过来（需 --files）
  · 下过的文件缓存在 _staging/upstream，重复跑不重复下
"""
import io
import os
import sys
import json
import time
import shutil
import hashlib
import datetime
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

G = r"C:\mnms_td_r18\BepInEx\plugins\MonsterMusumeTDMod\translations"
BACKUP = r"C:\Users\yuu\Desktop\寝室润色\_游戏翻译备份"
CACHE = r"C:\Users\yuu\Desktop\寝室润色\_staging\upstream"
DEFAULT_BASE = ("https://raw.githubusercontent.com/"
                "Berumic/MonsterMusumeTDChineseTranslation/main/translations")
UA = {"User-Agent": "momusu-zh-tool/1.0"}
WORKERS = 16


def fetch(url, timeout=25, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            last = e
            time.sleep(1.0 * (i + 1))
    raise last


def sha(b):
    return hashlib.sha256(b).hexdigest()


def cached(rel, meta, base):
    """有缓存直接用，否则下载并按哈希校验"""
    p = os.path.join(CACHE, rel.replace("/", os.sep))
    if os.path.exists(p):
        d = open(p, "rb").read()
        if sha(d) == meta["sha256"]:
            return d
    d = fetch(base + "/" + rel)
    if sha(d) != meta["sha256"]:
        raise RuntimeError("哈希不符 " + rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "wb").write(d)
    return d


def main():
    apply = "--apply" in sys.argv
    do_files = "--files" in sys.argv
    base = DEFAULT_BASE
    if "--base" in sys.argv:
        base = sys.argv[sys.argv.index("--base") + 1].rstrip("/")

    print("源  : %s" % base)
    print("模式: %s%s" % ("写入" if apply else "预演（不下载、不写文件）",
                          " + 下载新文件" if do_files else ""))

    remote_all = json.loads(fetch(base + "/manifest.json").decode("utf-8"))["files"]
    # 只关心译文和样式表；上游仓库里的 source.json / *.bak 一律忽略
    remote = {r: m for r, m in remote_all.items()
              if r.endswith("zh_Hans.json") or r == "styles.json"}
    print("上游: %d 个文件（其中需要关注的译文/样式 %d 个）"
          % (len(remote_all), len(remote)))

    local = {}
    for root, d, fs in os.walk(G):
        for f in fs:
            p = os.path.join(root, f)
            rel = os.path.relpath(p, G).replace(os.sep, "/")
            if rel != "manifest.json":
                local[rel] = p
    print("本地: %d 个文件" % len(local))

    new_files = sorted(r for r in remote if r not in local)
    diff = sorted(r for r, m in remote.items()
                  if r in local and sha(open(local[r], "rb").read()) != m["sha256"])
    print("上游有、本地没有 : %d" % len(new_files))
    print("两边都有但内容不同: %d（逐个比键，只补本地缺的）" % len(diff))

    if not apply:
        print("=" * 52)
        print("预演结束。要真进货：python 拉上游.py --apply --files")
        print("（--files 表示连上游新增的整份文件也一起下）")
        return

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bdir = os.path.join(BACKUP, "upstream_pull_" + stamp)
    new_files_done = new_keys = touched = failed = 0
    changed = {}

    # 1) 整份新文件
    if do_files and new_files:
        print("下载新文件 ...")
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = {ex.submit(cached, r, remote[r], base): r for r in new_files}
            for i, fu in enumerate(as_completed(futs), 1):
                r = futs[fu]
                try:
                    data = fu.result()
                except Exception:
                    failed += 1
                    continue
                dst = os.path.join(G, r.replace("/", os.sep))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                open(dst, "wb").write(data)
                new_files_done += 1
                if i % 20 == 0:
                    print("   %d/%d" % (i, len(new_files)))

    # 2) 逐键合并
    if diff:
        print("比对 %d 个文件，补缺键 ..." % len(diff))
        done = 0

        def one(rel):
            data = cached(rel, remote[rel], base)
            try:
                up = json.loads(data.decode("utf-8-sig"))
            except Exception:
                return rel, 0, None
            try:
                lo = json.load(io.open(local[rel], encoding="utf-8-sig"))
            except Exception:
                return rel, 0, None
            added = 0
            for k, v in up.items():
                if k not in lo or not str(lo.get(k, "")).strip():
                    lo[k] = v
                    added += 1
            return rel, added, (lo if added else None)

        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = [ex.submit(one, r) for r in diff]
            for fu in as_completed(futs):
                done += 1
                try:
                    rel, added, lo = fu.result()
                except Exception:
                    failed += 1
                    continue
                if added:
                    new_keys += added
                    touched += 1
                    changed[rel] = lo
                if done % 200 == 0:
                    print("   进度 %d/%d，已发现新键 %d" % (done, len(diff), new_keys))

    # 3) 落盘
    if changed:
        os.makedirs(bdir, exist_ok=True)
        for rel, lo in changed.items():
            dst = os.path.join(G, rel.replace("/", os.sep))
            bdst = os.path.join(bdir, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(bdst), exist_ok=True)
            shutil.copy2(dst, bdst)
            io.open(dst, "w", encoding="utf-8", newline="\n").write(
                json.dumps(lo, ensure_ascii=False, indent=2))

    print("=" * 52)
    print("新文件 %d / 补键 %d（涉及 %d 文件）/ 失败 %d"
          % (new_files_done, new_keys, touched, failed))
    if changed or new_files_done:
        print("改前备份: %s" % bdir)
        print("下一步: 体检.py → 双击 _发布\\publish_update.bat")


if __name__ == "__main__":
    main()
