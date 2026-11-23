#!/usr/bin/env python3
"""Add the missing glyphs of a translation corpus to the shipped cn25 font.

Why this exists: `update_translations.bat` must never need a human to add a
character. When the author's repository introduces a character the shipped font
does not have, this tool rasterises exactly that character from the same source
font the cn25 build used (Alimama, 48 pt, m_AtlasPadding 9) and appends it.

Guarantees, all re-verified from the written files:

  * APPEND-ONLY. No existing glyph record is rewritten; every existing glyph
    keeps its rectangle, metrics and pixels byte for byte. New cells are packed
    only into atlas rows that were never written (proved to be all zero).
  * Same encoding as cn25: a natural ink rect plus an OUTSIDE 9 px SDF ring, so
    TMP's m_AtlasPadding window sees the same 9 px field every kept glyph has.
  * Capacity: stays at 8192x4096. If the shelf packer runs out of rows the atlas
    is grown vertically (once, to 8192x8192) with the same texture object,
    pointer and material dimensions synchronised. Anything that still does not
    fit is a hard error; no glyph is ever dropped silently.
  * If the source font itself has no outline for a required character, the build
    fails and names it.

Outputs (never written into resources/):
    <out>/ntd.data, <out>/ntd.data.gz, <out>/font_ui.bytes, <out>/report.json

    python tools/fonts/add_glyphs.py --needed work/translations/font/needed.json \
        --out-dir work/translations/font
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import tmpfont as tf                                            # noqa: E402
from ttf_sdf import GlyphSdf                                    # noqa: E402

FONT_KEY = ('sharedassets0.assets', 497)
TEX_KEY = ('sharedassets0.assets', 6)
CONTROL_MAX = 0x1f


class BuildError(RuntimeError):
    pass


def need(cond, msg):
    if not cond:
        raise BuildError(msg)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def log(msg):
    print(f'[fonts] {msg}')


# --------------------------------------------------------------------------
def load_needed(path: Path) -> list[int]:
    doc = json.loads(path.read_text(encoding='utf-8'))
    cps = doc['codepoints'] if isinstance(doc, dict) else doc
    out = sorted({int(c) for c in cps if int(c) > CONTROL_MAX})
    need(out, f'{path} lists no codepoints above U+001F')
    return out


def open_data_container(path: Path):
    import UnityPy
    blob, entries = tf.load_container(str(path))
    need('data.unity3d' in entries,
         f'{path}: no data.unity3d entry in the UnityWebData container')
    env = UnityPy.load(io.BytesIO(entries['data.unity3d']))
    font_obj = tex_obj = None
    for obj in env.objects:
        key = (obj.assets_file.name, obj.path_id)
        if obj.type.name == 'MonoBehaviour' and key == FONT_KEY:
            font_obj = obj
        elif obj.type.name == 'Texture2D' and key == TEX_KEY:
            tex_obj = obj
    need(font_obj is not None, f'{path}: TMP font object {FONT_KEY} not found')
    need(tex_obj is not None, f'{path}: atlas Texture2D {TEX_KEY} not found')
    return blob, env, font_obj, tex_obj


_SDF = None
_CELL = (48.0, 9)


def _init_worker(font_path, point_size, ring):
    global _SDF, _CELL
    _SDF = GlyphSdf(font_path)
    _CELL = (point_size, ring)


def _make(cp):
    cell, cw, ch, metrics, rw, rh = _SDF.natural_cell(chr(cp), *_CELL)
    return cp, dict(cell=cell.hex(), cw=cw, ch=ch, metrics=metrics,
                    rw=rw, rh=rh)


def rasterise(cps, font_path: Path, point_size: float, ring: int, workers: int):
    """SDF cells for every codepoint, parallel over processes."""
    from concurrent.futures import ProcessPoolExecutor
    from fontTools.ttLib import TTFont

    probe = TTFont(str(font_path), lazy=True)
    try:
        cmap = probe.getBestCmap()
    finally:
        probe.close()
    absent = [cp for cp in cps if cp not in cmap]
    need(not absent,
         'the source font has no outline for these required characters: '
         + ', '.join(f'{chr(c)} U+{c:04X}' for c in absent[:20])
         + f' ({len(absent)} total). No glyph was invented.')

    out = {}
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker,
                             initargs=(str(font_path), point_size, ring)) as pool:
        for cp, rec in pool.map(_make, cps, chunksize=1):
            out[cp] = rec
            log(f'rasterised {chr(cp)} U+{cp:04X} rect {rec["rw"]}x{rec["rh"]}')
    return out


def existing_extent(glyphs, ring: int) -> int:
    """Lowest atlas row written so far (rect bottom + its SDF ring)."""
    return max([g['rect'][1] + g['rect'][3] + (ring if g['rect'][2] > 0 else 0)
                for g in glyphs if g['rect'][2] > 0 and g['rect'][3] > 0],
               default=0)


def plan_placement(new, aw, ah, y0, ring, max_h):
    """Pack the new cells below everything already written."""
    order = sorted(new.items(), key=lambda kv: (-kv[1]['ch'], -kv[1]['cw']))
    for height in [h for h in (ah, max_h) if h >= ah]:
        packer = tf.Packer(aw, height, tf.GAP, y0=y0)
        try:
            placed = {}
            for cp, rec in order:
                if rec['cw'] <= 0 or rec['ch'] <= 0:
                    placed[cp] = dict(rect=[0, 0, 0, 0], cell=[0, 0, 0, 0])
                    continue
                cell, _shelf = packer.add(rec['cw'], rec['ch'], ring)
                placed[cp] = dict(cell=list(cell),
                                  rect=[cell[0] + ring, cell[1] + ring,
                                        rec['rw'], rec['rh']])
            return placed, height, packer.used
        except tf.AtlasExhausted as exc:
            if height >= max_h:
                raise BuildError(
                    f'{exc}. The atlas is full even at {aw}x{max_h}; adding '
                    f'more glyphs now needs a second atlas page, which this '
                    f'static TMP font asset cannot express safely. Nothing was '
                    f'written.') from exc
            log(f'{exc} -> growing the atlas to {aw}x{max_h} '
                f'(same texture object, material sizes resynchronised)')
    raise BuildError('unreachable')


def build_atlas(old_image, aw, ah, old_h, new_cells, placed, ring):
    image = bytearray(old_image)
    if ah > old_h:
        image.extend(b'\0' * (aw * ah - len(image)))
    for cp, rec in new_cells.items():
        cx, cy, cw, ch = placed[cp]['cell']
        if cw <= 0 or ch <= 0:
            continue
        tile = bytes.fromhex(rec['cell'])
        need(len(tile) == cw * ch,
             f'glyph cell size mismatch for U+{cp:04X}')
        for row in range(ch):
            image[(cy + row) * aw + cx:(cy + row) * aw + cx + cw] = \
                tile[row * cw:(row + 1) * cw]
    return bytes(image)


def cell_boxes(glyphs, ring, tag):
    boxes = []
    for g in glyphs:
        x, y, w, h = g['rect']
        if w <= 0 or h <= 0:
            continue
        r = ring
        boxes.append((x - r, y - r, x + w + r, y + h + r, f'{tag}:{g["index"]}'))
    return boxes


def sync_material_dimensions(env, texture_path_id, texture_file, w, h):
    """Keep _TextureWidth/_TextureHeight in step when the atlas is resized."""
    changed = []
    for obj in env.objects:
        if obj.type.name != 'Material':
            continue
        try:
            mat = obj.read()
            main = dict(mat.m_SavedProperties.m_TexEnvs).get('_MainTex')
            if main is None or main.m_Texture.m_PathID != texture_path_id:
                continue
            file_id = main.m_Texture.m_FileID
            externals = obj.assets_file.externals
            target = (obj.assets_file.name if file_id == 0 else
                      externals[file_id - 1].path
                      if 0 < file_id <= len(externals) else '')
            if not target.replace('\\', '/').lower().endswith(texture_file.lower()):
                continue
            floats = mat.m_SavedProperties.m_Floats
            for i, (key, _v) in enumerate(floats):
                if key == '_TextureWidth':
                    floats[i] = (key, float(w))
                elif key == '_TextureHeight':
                    floats[i] = (key, float(h))
            obj.save_typetree(mat)
            changed.append([obj.assets_file.name, obj.path_id])
        except Exception:                                       # noqa: BLE001
            continue
    return changed


def bundle_fonts(env, expected_cps, label):
    """Every TMP font in the bundle that carries the primary character set."""
    out = []
    for obj in env.objects:
        if obj.type.name != 'MonoBehaviour':
            continue
        raw = obj.get_raw_data()
        try:
            glyphs, chars, layout = tf.read_font(raw, label)
        except tf.FontError:
            continue
        if {c['cp'] for c in chars} != expected_cps:
            continue
        out.append((obj, raw, glyphs, chars, layout))
    need(out, f'{label}: no TMP font asset carries the primary character set')
    return out


# --------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default=str(ROOT / 'resources' / 'ntd.data.gz'))
    ap.add_argument('--ui', default=str(ROOT / 'resources' / 'font_ui.bytes'))
    ap.add_argument('--needed', required=True,
                    help='JSON {"codepoints": [...]} of required characters')
    ap.add_argument('--font', default=str(HERE / 'assets' / 'alimama.ttf'))
    ap.add_argument('--out-dir', default=str(ROOT / 'work' / 'translations' / 'font'))
    ap.add_argument('--point-size', type=float, default=tf.POINT_SIZE)
    ap.add_argument('--ring', type=int, default=tf.RING)
    ap.add_argument('--max-atlas-h', type=int, default=8192)
    ap.add_argument('--workers', type=int, default=4)
    args = ap.parse_args(argv)

    data_path, ui_path = Path(args.data), Path(args.ui)
    out = Path(args.out_dir)
    font_path = Path(args.font)
    for p in (data_path, ui_path):
        need(p.is_file(), f'missing input: {p}')
    need(font_path.is_file(), f'missing source font: {font_path}')
    out.mkdir(parents=True, exist_ok=True)

    needed = load_needed(Path(args.needed))
    log(f'{len(needed)} required codepoints in {args.needed}')

    import UnityPy
    blob, env, font_obj, tex_obj = open_data_container(data_path)
    raw = font_obj.get_raw_data()
    info = tf.validate_font(raw, 'primary font')
    glyphs, chars, layout = tf.read_font(raw, 'primary font')
    aw, ah, image = tf.read_texture(tex_obj.get_raw_data())
    need([aw, ah] == info['atlas'], f'atlas {aw}x{ah} != font {info["atlas"]}')
    log(f'primary font: {info["glyphs"]} glyphs / {info["chars"]} chars, '
        f'atlas {aw}x{ah}')

    present = {c['cp'] for c in chars}
    missing = [cp for cp in needed if cp not in present]
    if not missing:
        log('no missing glyph: nothing to build')
        (out / 'report.json').write_text(json.dumps(dict(
            status='NO_MISSING_GLYPHS', required=len(needed),
            glyphs=info['glyphs'], atlas=[aw, ah]), ensure_ascii=False,
            indent=2) + '\n', encoding='utf-8')
        return 0
    log(f'missing {len(missing)}: '
        + ', '.join(f'{chr(c)} U+{c:04X}' for c in missing))

    new_cells = rasterise(missing, font_path, args.point_size, args.ring,
                          max(1, min(args.workers, 8)))

    y0 = existing_extent(glyphs, args.ring)
    need(y0 < ah, f'the atlas has no free rows left (used up to y={y0})')
    tail_bytes = image[y0 * aw:]
    need(not any(tail_bytes),
         f'atlas rows below y={y0} are not empty; refusing to overwrite '
         f'pixels this tool did not write')
    log(f'append region starts at y={y0} / {ah} '
        f'({100.0 * (ah - y0) / ah:.1f}% headroom)')

    placed, new_h, used = plan_placement(new_cells, aw, ah, y0, args.ring,
                                        args.max_atlas_h)
    image_new = build_atlas(image, aw, new_h, ah, new_cells, placed, args.ring)
    need(image_new[:y0 * aw] == image[:y0 * aw],
         'the append-only contract was violated: existing atlas rows changed')

    boxes = cell_boxes(glyphs, args.ring, 'old')
    for cp, rec in new_cells.items():
        x, y, w, h = placed[cp]['rect']
        if w > 0 and h > 0:
            boxes.append((x - args.ring, y - args.ring, x + w + args.ring,
                          y + h + args.ring, f'new:{cp:04X}'))
    overlaps = tf.find_overlaps(boxes)
    need(not overlaps, f'{len(overlaps)} atlas cell overlap(s): {overlaps[:5]}')
    for cp, rec in new_cells.items():
        x, y, w, h = placed[cp]['rect']
        need(0 <= x and 0 <= y and x + w <= aw and y + h <= new_h,
             f'new glyph U+{cp:04X} rect {placed[cp]["rect"]} is outside the atlas')

    new_items = [dict(cp=cp, metrics=new_cells[cp]['metrics'],
                      rect=placed[cp]['rect']) for cp in sorted(new_cells)]
    raw_new, placed_glyphs = tf.append_glyphs(raw, new_items, 'primary font')

    tex_raw = tex_obj.get_raw_data()
    tex_out = tf.write_texture(tex_raw, image_new, aw, new_h)
    font_obj.set_raw_data(raw_new)
    tex_obj.set_raw_data(tex_out)
    if new_h != ah:
        mats = sync_material_dimensions(env, TEX_KEY[1], FONT_KEY[0], aw, new_h)
        need(mats, 'atlas grew but no material referenced it; aborting rather '
                   'than shipping a font with a stale _TextureWidth/Height')
    u3d = env.file.save(packer='lz4')
    data_out = out / 'ntd.data'
    tf.container_repack(blob, u3d, data_out)
    tf.gzip_reproducible(data_out, str(data_out) + '.gz')
    log(f'ntd.data written: {data_out.stat().st_size} B '
        f'(gz {(out / "ntd.data.gz").stat().st_size} B)')

    # ---- the UI bundle carries two copies of the same font ----
    benv = UnityPy.load(str(ui_path))
    bundle = next(iter(benv.files.values()))
    fonts = bundle_fonts(benv, present, 'ui bundle')
    btex = None
    for obj in benv.objects:
        if obj.type.name != 'Texture2D':
            continue
        try:
            if obj.read().m_Width == aw:
                btex = obj
                break
        except Exception:                                       # noqa: BLE001
            continue
    need(btex is not None, f'{ui_path}: {aw}-wide atlas Texture2D not found')
    bw, bh, bimage = tf.read_texture(btex.get_raw_data())
    need((bw, bh) == (aw, ah), f'bundle atlas {bw}x{bh} != primary {aw}x{ah}')
    need(bimage == image, 'bundle atlas pixels differ from the data copy')
    bundle_placed = {}
    bundle_originals = {}
    for obj, braw, bglyphs, bchars, blayout in fonts:
        bundle_originals[str(obj.path_id)] = dict(raw=braw, glyphs=len(bglyphs))
        braw_new, bg = tf.append_glyphs(braw, new_items, f'bundle font {obj.path_id}')
        obj.set_raw_data(braw_new)
        bundle_placed[str(obj.path_id)] = bg
        log(f'bundle font {obj.path_id}: {len(bglyphs)} -> '
            f'{len(bglyphs) + len(new_items)} glyphs')
    btex.set_raw_data(tf.write_texture(btex.get_raw_data(), image_new, aw, new_h))
    if new_h != ah:
        sync_material_dimensions(benv, btex.path_id, btex.assets_file.name, aw, new_h)
    (out / 'font_ui.bytes').write_bytes(bundle.save(packer='lz4'))
    log(f'font_ui.bytes written: {(out / "font_ui.bytes").stat().st_size} B')

    # ---- verification: re-open exactly what was written ----
    checks = verify(out, needed, missing, present, raw, glyphs, chars, layout,
                    image, y0, aw, ah, new_h, new_cells, placed,
                    bundle_originals)
    need(all(c['pass'] for c in checks),
         'post-build verification failed: '
         + ', '.join(c['check'] for c in checks if not c['pass']))

    report = dict(
        status='PASS',
        inputs={p.name: dict(bytes=p.stat().st_size, sha256=sha_file(p))
                for p in (data_path, ui_path, font_path)},
        outputs={p.name: dict(bytes=p.stat().st_size, sha256=sha_file(p))
                 for p in (data_out, out / 'ntd.data.gz', out / 'font_ui.bytes')},
        sourceFont=dict(path=font_path.name, sha256=sha_file(font_path)),
        parameters=dict(pointSize=args.point_size, ring=args.ring,
                        gap=tf.GAP, maxAtlasHeight=args.max_atlas_h),
        previous=dict(glyphs_primary=len(glyphs), chars_primary=len(chars),
                      atlas=[aw, ah], appendStartY=y0, headroomRows=ah - y0),
        added=[dict(cp=cp, char=chr(cp), index=g['index'], rect=g['rect'],
                    metrics=[round(v, 4) for v in g['metrics']])
               for cp, g in zip(sorted(new_cells), placed_glyphs)],
        glyphs_primary=len(glyphs) + len(new_cells),
        atlas=[aw, new_h], atlasUsedRows=used,
        uiFonts={k: len(v) for k, v in bundle_placed.items()},
        checks={c['check']: c['pass'] for c in checks},
        browser='NOT RUN',
    )
    (out / 'report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    log(f'PASS: +{len(new_cells)} glyphs, atlas {aw}x{new_h}, '
        f'{len(checks)} checks')
    return 0


def verify(out, needed, missing, present, raw, glyphs, chars, layout,
           image, y0, aw, ah, new_h, new_cells, placed, bundle_originals):
    """Every claim made above is re-checked against the written files."""
    import UnityPy
    checks = []

    def check(name, ok, detail=''):
        checks.append({'check': name, 'pass': bool(ok), 'detail': detail})

    blob, env, font_obj, tex_obj = open_data_container(out / 'ntd.data.gz')
    new_raw = font_obj.get_raw_data()
    ng, nc, nlay = tf.read_font(new_raw, 'output')
    nw, nh, nimage = tf.read_texture(tex_obj.get_raw_data())
    ncps = {c['cp'] for c in nc}
    by_cp = {c['cp']: c['index'] for c in nc}
    by_index = {g['index']: g for g in ng}

    check('atlas size', (nw, nh) == (aw, new_h), f'{nw}x{nh}')
    check('glyph count', len(ng) == len(glyphs) + len(missing),
          f'{len(glyphs)} -> {len(ng)}')
    check('character count', len(nc) == len(chars) + len(missing),
          f'{len(chars)} -> {len(nc)}')
    check('all required characters present',
          all(cp in ncps for cp in needed),
          f'missing {[hex(c) for c in needed if c not in ncps][:5]}')
    check('no previously shipped character lost',
          present <= ncps, f'lost {[hex(c) for c in present - ncps][:5]}')
    check('every character points at a real glyph',
          all(c['index'] in by_index for c in nc))
    check('existing glyph records byte-identical',
          new_raw[tf.OFF_GLYPH_TABLE:tf.OFF_GLYPH_TABLE
                  + tf.GLYPH_STRIDE * len(glyphs)]
          == raw[tf.OFF_GLYPH_TABLE:tf.OFF_GLYPH_TABLE
                 + tf.GLYPH_STRIDE * len(glyphs)])
    check('existing atlas rows byte-identical',
          nimage[:y0 * aw] == image[:y0 * aw])
    check('header (name, hash, material refs) untouched',
          new_raw[:tf.OFF_GLYPH_COUNT] == raw[:tf.OFF_GLYPH_COUNT])
    check('font atlas width/height fields match the texture',
          tf.font_atlas_size(new_raw, nlay) == (nw, nh))

    new_rects_ok = True
    for cp in missing:
        g = by_index.get(by_cp[cp])
        if g is None or (g['rect'][2] > 0 and g['rect'][1] < y0):
            new_rects_ok = False
    check('new glyphs live below the previous extent', new_rects_ok, f'y0={y0}')

    boxes = cell_boxes(ng, tf.RING, 'gl')
    check('no atlas cell overlap after the build', not tf.find_overlaps(boxes))

    ink_ok, empty = True, []
    for cp in missing:
        rec = new_cells[cp]
        if rec['rw'] <= 0:
            continue
        g = by_index[by_cp[cp]]
        x, y, w, h = g['rect']
        tile = bytes(nimage[(y + r) * nw + x + c] for r in range(h)
                     for c in range(w))
        if max(tile, default=0) <= 132 or max(tile) - min(tile) <= 8:
            empty.append(chr(cp))
    check('new glyph tiles carry a real distance field', not empty, str(empty))
    ring_ok = True
    for cp in missing:
        rec = new_cells[cp]
        if rec['rw'] <= 0:
            continue
        x, y, w, h = by_index[by_cp[cp]]['rect']
        vals = []
        for d in (1, 2):
            if y - d >= 0:
                vals += [nimage[(y - d) * nw + x + i] for i in range(w)]
        if vals and max(vals) > 140:
            ring_ok = False
    check('no neighbour bleed around new glyphs', ring_ok)

    uenv = UnityPy.load(str(out / 'font_ui.bytes'))
    ufonts = bundle_fonts(uenv, present | set(missing), 'output bundle')
    check('ui bundle still carries every font copy', len(ufonts) >= 2,
          f'{len(ufonts)} font asset(s)')
    check('ui bundle fonts carry old and new characters',
          all(present | set(missing) <= {c['cp'] for c in _c}
              for _o, _r, _g, _c, _l in ufonts))
    n0 = {int(k): v for k, v in bundle_originals.items()}
    same = True
    for _obj, braw, bglyphs, _c, _l in ufonts:
        base = n0.get(_obj.path_id)
        if base is None:
            same = False
            break
        n = base['glyphs']
        if braw[tf.OFF_GLYPH_TABLE:tf.OFF_GLYPH_TABLE + tf.GLYPH_STRIDE * n] \
                != base['raw'][tf.OFF_GLYPH_TABLE:tf.OFF_GLYPH_TABLE
                               + tf.GLYPH_STRIDE * n]:
            same = False
    check('ui bundle base glyph records byte-identical', same)

    utex_ok = False
    for obj in uenv.objects:
        if obj.type.name != 'Texture2D':
            continue
        try:
            t = obj.read()
        except Exception:                                       # noqa: BLE001
            continue
        if t.m_Width == nw and bytes(t.image_data) == nimage:
            utex_ok = True
    check('ui bundle atlas equals the primary atlas', utex_ok)

    return checks


if __name__ == '__main__':
    try:
        sys.exit(main())
    except BuildError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(2)
