#!/usr/bin/env python3
"""TMP_FontAsset / UnityWebData / AssetBundle primitives for incremental work.

Layout facts (verified against the shipped cn25 assets, not assumed):

  MonoBehaviour payload
      0    .. 252   header (name at 28/32, TMP name hash at 56, ...)
      252           int32  m_GlyphTable.size
      256           glyph records, 52 B each:
                      u32 index, 5f metrics(w,h,bx,by,adv), 4i rect(x,y,w,h),
                      f32 scale, u32 atlasIndex, u32 classDefinitionType
      ...           int32  m_CharacterTable.size
      ...           char records, 16 B each: u32 elementType, u32 unicode,
                      u32 glyphIndex, f32 scale
      tail          m_AtlasTextures[] ... m_AtlasWidth at +68004,
                    m_AtlasHeight at +68008  (relative to the tail start)

The glyph and character tables are variable length, so every offset after them
is derived from the counts. Only the two header offsets above are fixed.

Appending is always *append-only*: existing glyph records are copied verbatim
and keep their rectangle, so no already-validated glyph can move.
"""
from __future__ import annotations

import gzip
import struct

OFF_GLYPH_COUNT = 252
OFF_GLYPH_TABLE = 256
GLYPH_STRIDE = 52
CHAR_STRIDE = 16

# Texture2D raw payload header (Unity 2022.3, Alpha8, inline pixels)
TEX_W, TEX_H, TEX_IMAGE_SIZE, TEX_DATA_SIZE = 44, 48, 52, 128
TEX_PIXELS = 132

# m_AtlasWidth / m_AtlasHeight inside the font tail
REL_ATLAS_W = 318980 - 250976
REL_ATLAS_H = 318984 - 250976

RING = 9                 # m_AtlasPadding of the shipped asset
POINT_SIZE = 48.0        # m_FaceInfo.m_PointSize of the shipped asset
GAP = 18                 # rect-to-rect distance used by the cn25 packer

CJK_RANGES = ((0x3400, 0x4DBF), (0x4E00, 0x9FFF))


class FontError(RuntimeError):
    """Raised when an asset does not match the layout this tool understands."""


def is_hanzi(cp: int) -> bool:
    return any(lo <= cp <= hi for lo, hi in CJK_RANGES)


# --------------------------------------------------------------------------
# TMP_FontAsset payload
# --------------------------------------------------------------------------
def read_font(raw: bytes, label: str = 'font'):
    """Parse the glyph and character tables. Raises FontError on any doubt."""
    if len(raw) < OFF_GLYPH_TABLE:
        raise FontError(f'{label}: payload too short ({len(raw)} B)')
    u32 = lambda o: struct.unpack_from('<I', raw, o)[0]          # noqa: E731
    i32 = lambda o: struct.unpack_from('<i', raw, o)[0]          # noqa: E731
    f32 = lambda o: struct.unpack_from('<f', raw, o)[0]          # noqa: E731

    ng = i32(OFF_GLYPH_COUNT)
    if not (1 <= ng <= 100000):
        raise FontError(f'{label}: implausible glyph count {ng}')
    if OFF_GLYPH_TABLE + GLYPH_STRIDE * ng + 4 > len(raw):
        raise FontError(f'{label}: glyph table exceeds the payload')
    glyphs = []
    for k in range(ng):
        o = OFF_GLYPH_TABLE + GLYPH_STRIDE * k
        glyphs.append(dict(
            pos=k, index=u32(o),
            metrics=[f32(o + 4 + 4 * j) for j in range(5)],
            rect=[i32(o + 24 + 4 * j) for j in range(4)],
            scale=f32(o + 40), atlas=u32(o + 44), tail=u32(o + 48)))

    char_count_off = OFF_GLYPH_TABLE + GLYPH_STRIDE * ng
    nc = i32(char_count_off)
    if not (0 <= nc <= 100000):
        raise FontError(f'{label}: implausible character count {nc}')
    char_table = char_count_off + 4
    tail = char_table + CHAR_STRIDE * nc
    if tail > len(raw):
        raise FontError(f'{label}: character table exceeds the payload')
    chars = []
    for k in range(nc):
        o = char_table + CHAR_STRIDE * k
        chars.append(dict(head=u32(o), cp=u32(o + 4), index=u32(o + 8),
                          scale=f32(o + 12)))

    layout = dict(glyph_count=ng, glyph_table=OFF_GLYPH_TABLE,
                  char_count_off=char_count_off, char_count=nc,
                  char_table=char_table, tail=tail)
    return glyphs, chars, layout


def font_atlas_size(raw: bytes, layout: dict) -> tuple[int, int]:
    off = layout['tail']
    if off + REL_ATLAS_H + 4 > len(raw):
        raise FontError('font tail is too short to hold m_AtlasWidth/Height')
    return (struct.unpack_from('<i', raw, off + REL_ATLAS_W)[0],
            struct.unpack_from('<i', raw, off + REL_ATLAS_H)[0])


def validate_font(raw: bytes, label: str = 'font') -> dict:
    """Structural sanity: counts, table bounds, glyph references, atlas size."""
    glyphs, chars, layout = read_font(raw, label)
    by_index = {g['index'] for g in glyphs}
    if len(by_index) != len(glyphs):
        raise FontError(f'{label}: duplicate m_Index in the glyph table')
    bad = [c for c in chars if c['index'] not in by_index]
    if bad:
        raise FontError(f'{label}: {len(bad)} character records point at '
                        f'missing glyph indices')
    aw, ah = font_atlas_size(raw, layout)
    if not (512 <= aw <= 16384 and 512 <= ah <= 16384):
        raise FontError(f'{label}: implausible atlas {aw}x{ah}')
    return dict(glyphs=len(glyphs), chars=len(chars), atlas=[aw, ah],
                max_index=max(by_index), tail=layout['tail'])


def pack_glyph_record(index: int, metrics, rect) -> bytes:
    out = bytearray(GLYPH_STRIDE)
    struct.pack_into('<I', out, 0, index)
    struct.pack_into('<5f', out, 4, *metrics)
    struct.pack_into('<4i', out, 24, *rect)
    struct.pack_into('<f', out, 40, 1.0)
    struct.pack_into('<I', out, 44, 0)          # m_AtlasIndex
    struct.pack_into('<I', out, 48, 0)          # m_ClassDefinitionType
    return bytes(out)


def append_glyphs(raw: bytes, new_items, label: str = 'font') -> tuple[bytes, list]:
    """Append glyph + character records without touching any existing record.

    `new_items` is a list of dicts with `cp`, `metrics` and `rect` already
    decided. Fresh m_Index values are allocated above the current maximum.
    """
    glyphs, chars, layout = read_font(raw, label)
    if not new_items:
        return raw, []
    existing_cps = {c['cp'] for c in chars}
    dup = [it['cp'] for it in new_items if it['cp'] in existing_cps]
    if dup:
        raise FontError(f'{label}: codepoints already present: '
                        f'{[hex(c) for c in dup[:8]]}')
    max_index = max(g['index'] for g in glyphs)
    head = chars[-1]['head'] if chars else 1
    cscale = chars[-1]['scale'] if chars else 1.0
    added_g, added_c, placed = [], [], []
    for n, item in enumerate(new_items):
        idx = max_index + 1 + n
        added_g.append(pack_glyph_record(idx, item['metrics'], item['rect']))
        added_c.append(struct.pack('<IIIf', head, item['cp'], idx, cscale))
        placed.append(dict(cp=item['cp'], index=idx, rect=list(item['rect']),
                           metrics=list(item['metrics'])))
    out = (raw[:OFF_GLYPH_COUNT]
           + struct.pack('<i', len(glyphs) + len(new_items))
           + raw[OFF_GLYPH_TABLE:layout['char_count_off']]
           + b''.join(added_g)
           + struct.pack('<i', len(chars) + len(new_items))
           + raw[layout['char_table']:layout['tail']]
           + b''.join(added_c)
           + raw[layout['tail']:])
    return out, placed


def set_font_atlas_size(raw: bytes, layout: dict, w: int, h: int) -> bytearray:
    out = bytearray(raw)
    struct.pack_into('<i', out, layout['tail'] + REL_ATLAS_W, w)
    struct.pack_into('<i', out, layout['tail'] + REL_ATLAS_H, h)
    return out


# --------------------------------------------------------------------------
# Texture2D payload
# --------------------------------------------------------------------------
def read_texture(tex_raw: bytes) -> tuple[int, int, bytes]:
    w = struct.unpack_from('<i', tex_raw, TEX_W)[0]
    h = struct.unpack_from('<i', tex_raw, TEX_H)[0]
    size = struct.unpack_from('<i', tex_raw, TEX_IMAGE_SIZE)[0]
    if not (0 < w <= 16384 and 0 < h <= 16384):
        raise FontError(f'implausible Texture2D size {w}x{h}')
    if size != w * h:
        raise FontError(f'Texture2D {w}x{h} but m_ImageData is {size} B '
                        f'(not an uncompressed 8-bit atlas)')
    if TEX_PIXELS + size > len(tex_raw):
        raise FontError('Texture2D pixel block exceeds the payload')
    return w, h, tex_raw[TEX_PIXELS:TEX_PIXELS + size]


def write_texture(tex_raw: bytes, image: bytes, w: int, h: int) -> bytes:
    """Replace the pixel block; rewrite the size fields only when they change."""
    old_size = struct.unpack_from('<i', tex_raw, TEX_IMAGE_SIZE)[0]
    if w * h != len(image):
        raise FontError(f'atlas image {len(image)} B != {w}x{h}')
    head = bytearray(tex_raw[:TEX_PIXELS])
    struct.pack_into('<i', head, TEX_W, w)
    struct.pack_into('<i', head, TEX_H, h)
    struct.pack_into('<i', head, TEX_IMAGE_SIZE, len(image))
    struct.pack_into('<i', head, TEX_DATA_SIZE, len(image))
    return bytes(head) + image + tex_raw[TEX_PIXELS + old_size:]


# --------------------------------------------------------------------------
# atlas packing (append-only)
# --------------------------------------------------------------------------
def read_cell(atlas: bytes, aw: int, ah: int, x: int, y: int,
              w: int, h: int, ring: int) -> bytes:
    """Copy `rect` plus a `ring` of pixels around it; outside the atlas = 0."""
    cw, ch = w + 2 * ring, h + 2 * ring
    buf = bytearray(cw * ch)
    sy0, sx0 = y - ring, x - ring
    for row in range(ch):
        sy = sy0 + row
        if not (0 <= sy < ah):
            continue
        d0 = max(0, -sx0)
        d1 = min(cw, aw - sx0)
        if d1 <= d0:
            continue
        buf[row * cw + d0:row * cw + d1] = atlas[sy * aw + sx0 + d0:
                                                 sy * aw + sx0 + d1]
    return bytes(buf)


class AtlasExhausted(FontError):
    pass


class Packer:
    """Shelf packer over cells (tile + ring), tallest first.

    Seeded at `y0` so an incremental build can only ever grow downwards into
    atlas rows that were never written before.
    """

    def __init__(self, w: int, h: int, gap: int = GAP, y0: int = 0):
        self.w, self.h, self.gap = w, h, gap
        self.x, self.y, self.row_h = 0, y0, 0
        self.row_ring = self.prev_ring = 0
        self.row_open = False
        self.used = y0

    def add(self, cw: int, ch: int, ring: int = 0):
        if cw <= 0 or ch <= 0:
            return (0, 0, 0, 0), self.y
        adv = max(self.gap - self.prev_ring - ring, 0) if self.row_open else 0
        if self.x + adv + cw > self.w:
            self.y += self.row_h + (max(self.gap - self.row_ring, 0)
                                    if self.row_open else 0)
            self.x = 0
            self.row_h = 0
            self.row_ring = 0
            self.prev_ring = 0
            self.row_open = False
            adv = 0
        if self.y + ch > self.h:
            raise AtlasExhausted(
                f'atlas {self.w}x{self.h} exhausted: need {ch} more rows at '
                f'y={self.y}')
        self.x += adv
        rect = (self.x, self.y, cw, ch)
        shelf = self.y
        self.x += cw
        self.row_open = True
        self.prev_ring = ring
        self.row_h = max(self.row_h, ch)
        self.row_ring = max(self.row_ring, ring)
        self.used = max(self.used, self.y + self.row_h)
        return rect, shelf


def find_overlaps(boxes, band: int = 128):
    """Rectangle intersection test for (x0, y0, x1, y1, label) boxes."""
    grid = {}
    for b in boxes:
        x0, y0, x1, y1, _ = b
        if x1 <= x0 or y1 <= y0:
            continue
        for gy in range(y0 // band, (y1 - 1) // band + 1):
            for gx in range(x0 // band, (x1 - 1) // band + 1):
                grid.setdefault((gx, gy), []).append(b)
    bad = set()
    for items in grid.values():
        for i in range(len(items)):
            a = items[i]
            for j in range(i + 1, len(items)):
                b = items[j]
                if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                    bad.add(tuple(sorted((a[4], b[4]))))
    return sorted(bad)


# --------------------------------------------------------------------------
# UnityWebData container
# --------------------------------------------------------------------------
def container_entries(blob: bytes):
    if blob[:15] != b'UnityWebData1.0':
        raise FontError('not a UnityWebData container')
    header_size = struct.unpack_from('<I', blob, 16)[0]
    p, out = 20, []
    while p < header_size:
        off, size, nlen = struct.unpack_from('<III', blob, p)
        name = blob[p + 12:p + 12 + nlen].decode()
        out.append((name, off, size))
        p += 12 + nlen
    return out


def load_container(path) -> tuple[bytes, dict]:
    blob = open(path, 'rb').read()
    if blob[:2] == b'\x1f\x8b':
        blob = gzip.decompress(blob)
    return blob, {n: blob[o:o + s] for n, o, s in container_entries(blob)}


def container_repack(src: bytes, new_unity3d: bytes, out_path) -> int:
    entries = container_entries(src)
    if sum(1 for n, _o, _s in entries if n.endswith('data.unity3d')) != 1:
        raise FontError('container must hold exactly one data.unity3d')
    blobs = [(n, new_unity3d if n.endswith('data.unity3d') else src[o:o + s])
             for n, o, s in entries]
    table = sum(12 + len(n) for n, _ in blobs)
    header = 20 + table
    hdr, body, cursor = bytearray(), bytearray(), header
    for name, blob in blobs:
        raw = name.encode()
        hdr += struct.pack('<III', cursor, len(blob), len(raw)) + raw
        body += blob
        cursor += len(blob)
    out = bytearray(src[:16]) + struct.pack('<I', header) + bytes(hdr) + bytes(body)
    with open(out_path, 'wb') as fh:
        fh.write(out)
    return len(out)


def gzip_reproducible(src_path, out_path, level: int = 9):
    """Deterministic gzip (mtime=0) so an unchanged input gives an unchanged file."""
    data = open(src_path, 'rb').read()
    with open(out_path, 'wb') as fh:
        with gzip.GzipFile(filename='', fileobj=fh, mode='wb',
                           compresslevel=level, mtime=0) as gz:
            gz.write(data)
    return len(data)
