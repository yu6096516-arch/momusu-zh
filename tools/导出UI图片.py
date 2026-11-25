# -*- coding: utf-8 -*-
r"""导出游戏 UI 图片（Sprite / Texture2D）为 PNG，并生成清单。

用途
----
《魔物娘TDX》的主界面/按钮/标题图里有一部分是**把日文烘进图里的**（例如
`Shop_FontImageItemsale` = 「アイテム売却」、`Home_BtnSettingOff` = 「設定」），
这些只能改图，不能靠词表。本脚本把 UI 精灵和贴图全部导出来，改完再用
UnityPy / UABEA 回写 `sharedassets0.assets`（+ 对应 `.resS` 图集）。

用法
----
    python 导出UI图片.py                       # 全量导出到 Desktop\mnms_ui
    python 导出UI图片.py --filter FontImage Btn # 只导名字含这些片段的
    python 导出UI图片.py --list-only           # 只列名字，不写图

依赖：pip install UnityPy pillow
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_DATA = r"C:\mnms_td_r18\monster_musume_tdx_Data"
DEFAULT_OUT = os.path.join(os.path.expanduser("~"), "Desktop", "mnms_ui")
ASSET_FILES = ["sharedassets0.assets", "sharedassets1.assets", "sharedassets2.assets",
               "sharedassets4.assets", "sharedassets6.assets", "resources.assets"]

_BAD = '<>:"/\\|?*'


def safe_name(name: str) -> str:
    """Windows 文件名清洗：贴图名里常带 | : / 之类非法字符。"""
    out = "".join("_" if (c in _BAD or ord(c) < 32) else c for c in name).strip(" .")
    return out or "unnamed"


def main() -> None:
    ap = argparse.ArgumentParser(description="导出 UI 图片")
    ap.add_argument("--data", default=DEFAULT_DATA, help="monster_musume_tdx_Data 目录")
    ap.add_argument("--out", default=DEFAULT_OUT, help="输出目录")
    ap.add_argument("--filter", nargs="*", default=None, help="只导出名字含这些片段的（不区分大小写）")
    ap.add_argument("--list-only", action="store_true")
    args = ap.parse_args()

    try:
        import UnityPy
    except ImportError:
        sys.exit("需要 UnityPy：pip install UnityPy")

    filters = [f.lower() for f in args.filter] if args.filter else None
    os.makedirs(args.out, exist_ok=True)
    sprites_dir = os.path.join(args.out, "sprite")
    tex_dir = os.path.join(args.out, "texture")
    os.makedirs(sprites_dir, exist_ok=True)
    os.makedirs(tex_dir, exist_ok=True)

    seen = set()
    rows = []
    n_sprite = n_tex = 0
    for fn in ASSET_FILES:
        path = os.path.join(args.data, fn)
        if not os.path.exists(path):
            continue
        print("· 读取", fn)
        env = UnityPy.load(path)
        for obj in env.objects:
            kind = obj.type.name
            if kind not in ("Sprite", "Texture2D"):
                continue
            try:
                data = obj.read()
            except Exception:
                continue
            name = (getattr(data, "m_Name", "") or "").strip()
            if not name or name in seen:
                continue
            if filters and not any(f in name.lower() for f in filters):
                continue
            seen.add(name)
            try:
                img = data.image
            except Exception:
                continue
            w, h = img.size
            rows.append({"name": name, "type": kind, "asset": fn, "width": w, "height": h})
            if args.list_only:
                print(f"   {kind:9s} {name:52s} {w}x{h}  ({fn})")
                continue
            sub = sprites_dir if kind == "Sprite" else tex_dir
            p = os.path.join(sub, f"{safe_name(name)}.png")
            try:
                img.save(p)
            except Exception as e:
                print("   保存失败", name, e)
                continue
            rows[-1]["file"] = os.path.relpath(p, args.out)
            if kind == "Sprite":
                n_sprite += 1
            else:
                n_tex += 1

    if not args.list_only:
        manifest = os.path.join(args.out, "manifest.csv")
        with open(manifest, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["name", "type", "asset", "width", "height", "file"])
            w.writeheader()
            for r in sorted(rows, key=lambda r: (r["type"], r["name"])):
                w.writerow({k: r.get(k, "") for k in ["name", "type", "asset", "width", "height", "file"]})
        print(f"\n导出 Sprite {n_sprite} 张 / Texture2D {n_tex} 张 → {args.out}")
        print("清单:", manifest)
    else:
        print(f"\n共 {len(rows)} 个对象")


if __name__ == "__main__":
    main()
