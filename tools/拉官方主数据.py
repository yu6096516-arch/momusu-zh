# -*- coding: utf-8 -*-
r"""从官方 CDN 拉取《モンスター娘TDX》的 MasterData，解密并导出成 JSON。

原理（来自 alksdesu/monster-musume-ps 的 fetch_master.py，实测可用）
------------------------------------------------------------------
1. DMM 商店接口取当前客户端版本 cvr
       GET  https://api.store.games.dmm.com/freeapp/688044
2. 用它换资源包版本
       POST https://gapi.game-monmusu-td.net/api/asset_bundle/version   {"cvr":..,"provider":"dmm"}
3. 拉包清单
       GET  https://assets.game-monmusu-td.net/assetbundles/ver_<ver>/windows_r18/ablist.json
4. 按清单下载 MasterData 的 .bytes（整包是 XOR 加密的 UnityFS）
5. 解密：key = base_key("KYSSTMDL") + 反向拼接；base_key 见下
6. UnityPy 读 MonoBehaviour typetree → 每张表一个 JSON

用法
----
    python 拉官方主数据.py                    # 默认导出到 Desktop\mnms_masterdata
    python 拉官方主数据.py --out D:\md        # 指定目录
    python 拉官方主数据.py --list             # 只打印版本/清单，不下载
    python 拉官方主数据.py --file Data        # 只拉某一类

依赖：pip install requests UnityPy
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

APP_INFO_URL = "https://api.store.games.dmm.com/freeapp/688044"
VERSION_API = "https://gapi.game-monmusu-td.net/api/asset_bundle/version"
CDN = "https://assets.game-monmusu-td.net/assetbundles"
PLATFORM = "windows_r18"

# ablist 里的 path -> 人类可读名
MASTER_FILES = {
    "a4a197b8ff3816f0ddf353ead4768c80.bytes": "Data",
    "75e09e20429de1372f375685aa4386b3.bytes": "Bomcard",
    "2c4638ec28d824dfc4992d2f2b492c1f.bytes": "UnitClass",
    "94840ed598854fe89a08f12ceb25fd1e.bytes": "UnitCard",
    "6ccdbcdbe6ce4a57abcd31247745c242.bytes": "EnemyData",
}


def base_key(src: str) -> bytes:
    s2 = bytes(b ^ 0x55 for b in src.encode("ascii"))
    sha = hashlib.sha256(s2).digest()
    s4 = bytes(b ^ 0xAA for b in sha)
    return s4[::2] + s4[1::2]


def decrypt_table(data: bytes) -> bytes:
    k32 = base_key("KYSSTMDL")
    key = k32 + k32[::-1]
    klen = len(key)
    out = bytearray(len(data))
    for i in range(0, len(data), klen):
        chunk = data[i:i + klen]
        out[i:i + len(chunk)] = bytes(b ^ key[j] for j, b in enumerate(chunk))
    return bytes(out)


def export_bundle(raw: bytes, out_dir: str, label: str) -> list[str]:
    import UnityPy
    env = UnityPy.load(raw)
    names = []
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        try:
            tree = obj.read_typetree()
        except Exception:
            continue
        name = tree.get("m_Name") or ("pid_%s" % obj.path_id)
        d = os.path.join(out_dir, label)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, f"{name}.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(tree, f, ensure_ascii=False, indent=1)
        names.append(name)
    return names


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.path.expanduser("~"), "Desktop", "mnms_masterdata"))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--file", default=None, help="只处理某一类：Data / UnitClass / UnitCard / Bomcard / EnemyData")
    args = ap.parse_args()

    try:
        import requests
    except ImportError:
        sys.exit("需要 requests：pip install requests")

    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0"

    cvr = s.get(APP_INFO_URL, timeout=20).json()["free_appinfo"]["app_version_name"]
    print("cvr:", cvr)
    r = s.post(VERSION_API, json={"cvr": cvr, "provider": "dmm"}, timeout=20)
    ver = r.json()["data"]["version"]
    print("bundle version:", ver)
    ab = s.get(f"{CDN}/ver_{ver}/{PLATFORM}/ablist.json", timeout=60).json()
    base = ab["baseVersion"]
    entries = ab["data"]
    print("baseVersion:", base, "清单条目:", len(entries))
    if args.list:
        hits = [(MASTER_FILES[e["path"]], e["size"]) for e in entries if e["path"] in MASTER_FILES]
        print("MasterData:", hits)
        return

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "ablist.json"), "w", encoding="utf-8") as f:
        json.dump(ab, f, ensure_ascii=False)

    base_url = f"{CDN}/ver_{base}/{PLATFORM}"
    for e in entries:
        label = MASTER_FILES.get(e["path"])
        if not label or (args.file and label != args.file):
            continue
        url = f"{base_url}/{e['hash']}{e['path']}"
        blob = s.get(url, timeout=180).content
        if len(blob) != int(e["size"]):
            print(f"! {label} 大小不符 {len(blob)} != {e['size']}")
        dec = decrypt_table(blob)
        if dec[:7] != b"UnityFS":
            print(f"! {label} 解密后不是 UnityFS: {dec[:8]}")
            continue
        names = export_bundle(dec, args.out, label)
        print(f"{label}: {len(blob)}B 表 {len(names)} 张 -> {args.out}\\{label}")
    print("完成")


if __name__ == "__main__":
    main()
