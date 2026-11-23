# -*- coding: utf-8 -*-
"""Bake missing CJK glyphs into the Alimama TMP atlas and normalize weight."""
import json
import shutil
import struct
from pathlib import Path

import numpy as np
import UnityPy
from PIL import Image, ImageDraw, ImageFont

SRC_BUNDLE = Path(r"C:\mnms_td_r18\BepInEx\plugins\alimama")
TTF = Path(r"C:\Users\yuu\Desktop\寝室润色\AlimamaFangYuanTi-ja-test.ttf")
MISSING_LIST = Path(r"C:\Users\yuu\Desktop\寝室润色\_missing_chars.txt")
OUT_BUNDLE = Path(r"C:\Users\yuu\Desktop\寝室润色\alimama.patched")
PREVIEW = Path(r"C:\Users\yuu\Desktop\寝室润色\font_patch_preview.png")
STYLES = Path(r"C:\mnms_td_r18\BepInEx\plugins\MonsterMusumeTDMod\translations\styles.json")
STYLES_OUT = Path(r"C:\Users\yuu\Desktop\寝室润色\styles.patched.json")

POINT_SIZE = 35.0
UNITS_PER_EM = 1000.0
SCALE = POINT_SIZE / UNITS_PER_EM  # 0.035
SPREAD = 5.0
SUPERSAMPLE = 4
PAD = 2


def parse_ttf_tables(data: bytes):
    assert data[:4] == b"\x00\x01\x00\x00", data[:4]
    num = struct.unpack(">H", data[4:6])[0]
    tables = {}
    off = 12
    for _ in range(num):
        tag, _, toff, tlen = struct.unpack(">4sIII", data[off : off + 16])
        tables[tag] = data[toff : toff + tlen]
        off += 16
    return tables


def cmap_format12(blob: bytes):
    version, num = struct.unpack(">HH", blob[:4])
    sub = None
    for i in range(num):
        plat, enc, offset = struct.unpack(">HHI", blob[4 + i * 8 : 12 + i * 8])
        fmt = struct.unpack(">H", blob[offset : offset + 2])[0]
        if plat == 3 and enc == 10 and fmt == 12:
            sub = offset
            break
    if sub is None:
        raise RuntimeError("no cmap format 12")
    n_groups = struct.unpack(">I", blob[sub + 12 : sub + 16])[0]
    mapping = {}
    p = sub + 16
    for _ in range(n_groups):
        start, end, glyph = struct.unpack(">III", blob[p : p + 12])
        p += 12
        for cp in range(start, end + 1):
            mapping[cp] = glyph + (cp - start)
    return mapping


def glyf_metrics(tables, glyph_id):
    loca = tables[b"loca"]
    glyf = tables[b"glyf"]
    head = tables[b"head"]
    maxp = tables[b"maxp"]
    hmtx = tables[b"hmtx"]
    hhea = tables[b"hhea"]
    index_to_loc = struct.unpack(">H", head[50:52])[0]
    num_glyphs = struct.unpack(">H", maxp[4:6])[0]
    num_hmetrics = struct.unpack(">H", hhea[34:36])[0]
    if index_to_loc == 0:
        start = struct.unpack(">H", loca[glyph_id * 2 : glyph_id * 2 + 2])[0] * 2
        end = struct.unpack(">H", loca[(glyph_id + 1) * 2 : (glyph_id + 1) * 2 + 4])[0] * 2
    else:
        start = struct.unpack(">I", loca[glyph_id * 4 : glyph_id * 4 + 4])[0]
        end = struct.unpack(">I", loca[(glyph_id + 1) * 4 : (glyph_id + 1) * 4 + 4])[0]
    if glyph_id < num_hmetrics:
        advance, lsb = struct.unpack(">Hh", hmtx[glyph_id * 4 : glyph_id * 4 + 4])
    else:
        advance = struct.unpack(">H", hmtx[(num_hmetrics - 1) * 4 : (num_hmetrics - 1) * 4 + 2])[0]
        lsb = struct.unpack(">h", hmtx[num_hmetrics * 4 + (glyph_id - num_hmetrics) * 2 : num_hmetrics * 4 + (glyph_id - num_hmetrics) * 2 + 2])[0]
    if end <= start:
        return {
            "advance": advance * SCALE,
            "lsb": lsb * SCALE,
            "width": 0.0,
            "height": 0.0,
            "bearing_x": lsb * SCALE,
            "bearing_y": 0.0,
            "empty": True,
        }
    x_min, y_min, x_max, y_max = struct.unpack(">hhhh", glyf[start + 2 : start + 10])
    return {
        "advance": advance * SCALE,
        "lsb": lsb * SCALE,
        "width": (x_max - x_min) * SCALE,
        "height": (y_max - y_min) * SCALE,
        "bearing_x": x_min * SCALE,
        "bearing_y": y_max * SCALE,
        "empty": False,
    }


def edt(binary: np.ndarray) -> np.ndarray:
    """Euclidean distance to nearest True pixel. binary True = feature."""
    h, w = binary.shape
    inf = float((h + w) ** 2)
    f = np.where(binary, 0.0, inf)

    def edt_1d(col):
        n = col.shape[0]
        v = np.zeros(n, dtype=np.int32)
        z = np.empty(n + 1, dtype=np.float64)
        k = 0
        v[0] = 0
        z[0] = -1e18
        z[1] = 1e18
        for q in range(1, n):
            s = ((col[q] + q * q) - (col[v[k]] + v[k] * v[k])) / (2 * q - 2 * v[k])
            while s <= z[k]:
                k -= 1
                s = ((col[q] + q * q) - (col[v[k]] + v[k] * v[k])) / (2 * q - 2 * v[k])
            k += 1
            v[k] = q
            z[k] = s
            z[k + 1] = 1e18
        k = 0
        out = np.empty(n, dtype=np.float64)
        for q in range(n):
            while z[k + 1] < q:
                k += 1
            dx = q - v[k]
            out[q] = dx * dx + col[v[k]]
        return out

    tmp = np.empty_like(f)
    for x in range(w):
        tmp[:, x] = edt_1d(f[:, x])
    out = np.empty_like(f)
    for y in range(h):
        out[y, :] = edt_1d(tmp[y, :])
    return np.sqrt(out)


def render_sdf(font: ImageFont.FreeTypeFont, ch: str, metrics: dict):
    if metrics["empty"] or metrics["width"] < 0.5 or metrics["height"] < 0.5:
        w = max(1, int(round(metrics["advance"])))
        return np.zeros((1, w), dtype=np.uint8), metrics
    px_w = max(1, int(np.ceil(metrics["width"])))
    px_h = max(1, int(np.ceil(metrics["height"])))
    canvas = max(px_w, px_h) * SUPERSAMPLE + 32
    img = Image.new("L", (canvas, canvas), 0)
    draw = ImageDraw.Draw(img)
    # Place baseline so the glyph bbox sits with padding.
    origin_x = 8
    origin_y = int(round(8 + metrics["bearing_y"] * SUPERSAMPLE))
    draw.text((origin_x, origin_y), ch, font=font, fill=255, anchor="ls")
    arr = np.array(img)
    ys, xs = np.where(arr > 16)
    if len(xs) == 0:
        return np.zeros((px_h, px_w), dtype=np.uint8), metrics
    x0, x1 = xs.min(), xs.max() + 1
    y0, y1 = ys.min(), ys.max() + 1
    # pad in high-res pixels so the edge SDF is not clipped
    pad = int(SPREAD * SUPERSAMPLE) + 2
    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(arr.shape[1], x1 + pad)
    y1 = min(arr.shape[0], y1 + pad)
    crop = arr[y0:y1, x0:x1] > 96
    dist_out = edt(~crop)
    dist_in = edt(crop)
    signed = (dist_out - dist_in) / SUPERSAMPLE
    alpha = np.clip(128.0 + signed * (127.0 / SPREAD), 0, 255).astype(np.uint8)
    # Crop back to ink bounds plus 1px, matching the existing tight rects.
    ink = np.where(crop)
    if len(ink[0]) == 0:
        return np.zeros((px_h, px_w), dtype=np.uint8), metrics
    iy0, iy1 = ink[0].min(), ink[0].max() + 1
    ix0, ix1 = ink[1].min(), ink[1].max() + 1
    extra = SUPERSAMPLE  # 1 output pixel
    iy0 = max(0, iy0 - extra)
    ix0 = max(0, ix0 - extra)
    iy1 = min(alpha.shape[0], iy1 + extra)
    ix1 = min(alpha.shape[1], ix1 + extra)
    tight = alpha[iy0:iy1, ix0:ix1]
    # Downsample by box filter so the rect matches 1x metrics.
    oh = max(1, int(round(tight.shape[0] / SUPERSAMPLE)))
    ow = max(1, int(round(tight.shape[1] / SUPERSAMPLE)))
    resized = np.array(Image.fromarray(tight).resize((ow, oh), Image.Resampling.BOX))
    return resized, metrics


def load_missing():
    chars = []
    for line in MISSING_LIST.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        ch = line.split("\t", 1)[0]
        chars.append(ch)
    return chars


def patch_styles():
    data = json.loads(STYLES.read_text(encoding="utf-8"))
    styles = data["$styles"]
    changed = 0
    for path, rule in styles.items():
        before = json.dumps(rule, ensure_ascii=False, sort_keys=True)
        if rule.get("font") == "FZKane":
            rule["font"] = "alimama"
        dilate = rule.get("faceDilate")
        if dilate == 0 or dilate == 0.0:
            rule["faceDilate"] = 0.15
        elif isinstance(dilate, (int, float)) and dilate >= 0.3:
            rule["faceDilate"] = 0.2
        if json.dumps(rule, ensure_ascii=False, sort_keys=True) != before:
            changed += 1
    STYLES_OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("styles changed", changed, "->", STYLES_OUT)


def main():
    raw = TTF.read_bytes()
    tables = parse_ttf_tables(raw)
    cmap = cmap_format12(tables[b"cmap"])
    print("cmap", len(cmap), "has 栞", 0x681E in cmap, "gid", cmap.get(0x681E))
    ref = glyf_metrics(tables, cmap[ord("的")])
    print("的 metrics", ref)

    missing = load_missing()
    print("missing", len(missing), "".join(missing[:40]))

    env = UnityPy.load(str(SRC_BUNDLE))
    ali_obj = tex_obj = mat_obj = None
    for obj in env.objects:
        if obj.type.name == "MonoBehaviour" and obj.read().m_Name == "Alimama":
            ali_obj = obj
        elif obj.type.name == "Texture2D" and obj.read().m_Name == "Alimama Atlas":
            tex_obj = obj
        elif obj.type.name == "Material" and obj.read().m_Name == "Alimama-ja Material":
            mat_obj = obj
    tree = ali_obj.read_typetree()
    have = {c["m_Unicode"] for c in tree["m_CharacterTable"]}
    used_ids = {g["m_Index"] for g in tree["m_GlyphTable"]}
    tex = tex_obj.read()
    atlas = np.array(tex.image)
    assert atlas.shape == (4096, 4096, 4)
    alpha = atlas[:, :, 3]
    H = alpha.shape[0]

    # Confirm the chosen free rect is empty.
    fx, fy, fw, fh = 3376, 3441, 555, 654
    pil_y = H - (fy + fh)
    region = alpha[pil_y : pil_y + fh, fx : fx + fw]
    print("free region max alpha", int(region.max()), "at", fx, fy, fw, fh)

    font = ImageFont.truetype(str(TTF), int(POINT_SIZE * SUPERSAMPLE))
    # Row packer inside the free rect, Unity bottom-left coordinates.
    cursor_x = fx + 4
    cursor_y = fy + fh - 4  # top of free rect in Unity y (higher y = up)
    row_h = 0
    placed = []
    next_id = max(used_ids) + 1

    for ch in missing:
        cp = ord(ch)
        if cp in have:
            continue
        gid = cmap.get(cp)
        if gid is None:
            print("skip no cmap", ch)
            continue
        metrics = glyf_metrics(tables, gid)
        sdf, metrics = render_sdf(font, ch, metrics)
        bh, bw = sdf.shape
        if cursor_x + bw + 2 > fx + fw:
            cursor_x = fx + 4
            cursor_y -= row_h + 2
            row_h = 0
        if cursor_y - bh < fy:
            raise RuntimeError(f"atlas free rect full at {ch}")
        unity_x = cursor_x
        unity_y = cursor_y - bh
        pil_top = H - (unity_y + bh)
        # Write alpha only. RGB stays 0, matching Alpha8.
        atlas[pil_top : pil_top + bh, unity_x : unity_x + bw, 3] = sdf
        atlas[pil_top : pil_top + bh, unity_x : unity_x + bw, 0:3] = 0
        if gid in used_ids:
            gid = next_id
            next_id += 1
        used_ids.add(gid)
        glyph = {
            "m_Index": int(gid),
            "m_Metrics": {
                "m_Width": float(metrics["width"]),
                "m_Height": float(metrics["height"]),
                "m_HorizontalBearingX": float(metrics["bearing_x"]),
                "m_HorizontalBearingY": float(metrics["bearing_y"]),
                "m_HorizontalAdvance": float(metrics["advance"]),
            },
            "m_GlyphRect": {
                "m_X": int(unity_x),
                "m_Y": int(unity_y),
                "m_Width": int(bw),
                "m_Height": int(bh),
            },
            "m_Scale": 1.0,
            "m_AtlasIndex": 0,
            "m_ClassDefinitionType": 0,
        }
        char = {
            "m_ElementType": 1,
            "m_Unicode": int(cp),
            "m_GlyphIndex": int(gid),
            "m_Scale": 1.0,
        }
        tree["m_GlyphTable"].append(glyph)
        tree["m_CharacterTable"].append(char)
        tree["m_UsedGlyphRects"].append(
            {"m_X": int(unity_x), "m_Y": int(unity_y), "m_Width": int(bw), "m_Height": int(bh)}
        )
        placed.append((ch, glyph))
        cursor_x += bw + 2
        row_h = max(row_h, bh)

    tree["m_CharacterTable"].sort(key=lambda c: c["m_Unicode"])
    # Shrink the consumed free rect so dynamic population cannot overwrite us.
    new_free = []
    for rect in tree["m_FreeGlyphRects"]:
        if rect["m_X"] == 3376 and rect["m_Y"] == 3441:
            continue
        new_free.append(rect)
    # Remainder below the packed rows.
    used_bottom = min(g["m_GlyphRect"]["m_Y"] for _, g in placed)
    if used_bottom - fy > 8:
        new_free.append(
            {"m_X": fx, "m_Y": fy, "m_Width": fw, "m_Height": int(used_bottom - fy - 2)}
        )
    tree["m_FreeGlyphRects"] = new_free
    print("placed", len(placed), "chars now", len(tree["m_CharacterTable"]))

    # Thicken the shared Thin face so unstyled story text is readable.
    mat = mat_obj.read_typetree()
    floats = mat["m_SavedProperties"]["m_Floats"]
    for i, item in enumerate(floats):
        name, value = item
        if name == "_FaceDilate":
            print("material dilate", value, "-> 0.15")
            floats[i] = [name, 0.15]
    mat_obj.save_typetree(mat)

    img = Image.fromarray(atlas, "RGBA")
    tex_data = tex_obj.read()
    tex_data.set_image(img, target_format=1)
    tex_data.save()
    ali_obj.save_typetree(tree)

    blob = env.file.save()
    OUT_BUNDLE.write_bytes(blob)
    print("wrote", OUT_BUNDLE, "bytes", len(blob), "header", blob[:8])

    # Verify reload and build a thresholded contact sheet.
    env2 = UnityPy.load(str(OUT_BUNDLE))
    atlas2 = None
    chars = glyphs = None
    for obj in env2.objects:
        if obj.type.name == "Texture2D" and obj.read().m_Name == "Alimama Atlas":
            atlas2 = np.array(obj.read().image)[:, :, 3]
        if obj.type.name == "MonoBehaviour" and obj.read().m_Name == "Alimama":
            t2 = obj.read_typetree()
            chars = {c["m_Unicode"]: c for c in t2["m_CharacterTable"]}
            glyphs = {g["m_Index"]: g for g in t2["m_GlyphTable"]}
    print("reload has 栞", 0x681E in chars, "fmt", "ok" if atlas2 is not None else "missing atlas")

    samples = list("的莉音栞") + missing[:12]
    tiles = []
    labels = []
    for ch in samples:
        c = chars.get(ord(ch))
        if not c:
            continue
        g = glyphs[c["m_GlyphIndex"]]
        r = g["m_GlyphRect"]
        top = 4096 - (r["m_Y"] + r["m_Height"])
        crop = atlas2[top : top + r["m_Height"], r["m_X"] : r["m_X"] + r["m_Width"]]
        ink = (crop >= 128).astype(np.uint8) * 255
        tile = np.full((48, 40), 30, dtype=np.uint8)
        y = max(0, 40 - r["m_Height"])
        tile[y : y + r["m_Height"], 2 : 2 + r["m_Width"]] = np.maximum(
            tile[y : y + r["m_Height"], 2 : 2 + r["m_Width"]], ink
        )
        tiles.append(tile)
        labels.append(ch)
    sheet = np.concatenate(tiles, axis=1)
    # second row: the name 栞莉音 enlarged
    name = "栞莉音"
    name_tiles = []
    for ch in name:
        c = chars[ord(ch)]
        g = glyphs[c["m_GlyphIndex"]]
        r = g["m_GlyphRect"]
        top = 4096 - (r["m_Y"] + r["m_Height"])
        crop = atlas2[top : top + r["m_Height"], r["m_X"] : r["m_X"] + r["m_Width"]]
        ink = (crop >= 120).astype(np.uint8) * 255
        tile = np.full((64, 48), 20, dtype=np.uint8)
        y = 56 - r["m_Height"]
        tile[y : y + r["m_Height"], 4 : 4 + r["m_Width"]] = ink
        name_tiles.append(tile)
    name_row = np.concatenate(name_tiles, axis=1)
    # stack
    width = max(sheet.shape[1], name_row.shape[1])
    canvas = np.full((48 + 8 + 64, width), 20, dtype=np.uint8)
    canvas[:48, : sheet.shape[1]] = sheet
    canvas[56 : 56 + 64, : name_row.shape[1]] = name_row
    Image.fromarray(canvas, "L").save(PREVIEW)
    print("preview", PREVIEW)
    print("sample advances", [(ch, glyphs[chars[ord(ch)]["m_GlyphIndex"]]["m_Metrics"]["m_HorizontalAdvance"]) for ch in "的栞莉音"])
    patch_styles()


if __name__ == "__main__":
    main()
