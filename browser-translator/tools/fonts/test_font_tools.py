#!/usr/bin/env python3
"""Unit + negative tests for the font tooling (no game assets needed).

Run: python -B tools/fonts/test_font_tools.py
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
for p in (HERE, ROOT / 'tools' / 'translations'):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import tmpfont as tf                                            # noqa: E402
import target_chars as tc                                       # noqa: E402

CASES = []


def case(name):
    def wrap(fn):
        CASES.append((name, fn))
        return fn
    return wrap


def expect(exc, fn, msg=''):
    try:
        fn()
    except exc:
        return True
    except Exception as e:                                      # noqa: BLE001
        raise AssertionError(f'expected {exc.__name__}, got '
                             f'{type(e).__name__}: {e} {msg}')
    raise AssertionError(f'expected {exc.__name__}, nothing raised {msg}')


def make_font(glyphs, chars, atlas=(16, 16)):
    """Minimal TMP_FontAsset payload with the layout the tools expect."""
    head = bytearray(tf.OFF_GLYPH_COUNT)
    head[28:32] = struct.pack('<I', 3)
    head[32:35] = b'abc'
    body = bytearray(head)
    body += struct.pack('<i', len(glyphs))
    for index, cp, rect in glyphs:
        body += tf.pack_glyph_record(index, [1.0, 2.0, 3.0, 4.0, float(cp)],
                                     list(rect))
    body += struct.pack('<i', len(chars))
    for head_v, cp, index in chars:
        body += struct.pack('<IIIf', head_v, cp, index, 1.0)
    tail = bytearray(tf.REL_ATLAS_H + 8)
    struct.pack_into('<i', tail, tf.REL_ATLAS_W, atlas[0])
    struct.pack_into('<i', tail, tf.REL_ATLAS_H, atlas[1])
    return bytes(body) + bytes(tail)


def make_texture(w, h, fill=0):
    head = bytearray(tf.TEX_PIXELS)
    struct.pack_into('<i', head, tf.TEX_W, w)
    struct.pack_into('<i', head, tf.TEX_H, h)
    struct.pack_into('<i', head, tf.TEX_IMAGE_SIZE, w * h)
    struct.pack_into('<i', head, tf.TEX_DATA_SIZE, w * h)
    return bytes(head) + bytes([fill]) * (w * h) + b'TAIL'


# --------------------------------------------------------------------------
@case('read_font derives the table offsets from the counts')
def _():
    raw = make_font([(1, 0x41, (0, 0, 4, 4)), (2, 0x42, (4, 0, 4, 4))],
                    [(1, 0x41, 1), (1, 0x42, 2)])
    g, c, lay = tf.read_font(raw)
    assert [x['index'] for x in g] == [1, 2]
    assert [x['cp'] for x in c] == [0x41, 0x42]
    assert lay['char_table'] == tf.OFF_GLYPH_TABLE + 2 * tf.GLYPH_STRIDE + 4
    assert tf.font_atlas_size(raw, lay) == (16, 16)


@case('validate_font rejects a truncated payload')
def _():
    raw = make_font([(1, 0x41, (0, 0, 4, 4))], [(1, 0x41, 1)])
    expect(tf.FontError, lambda: tf.validate_font(raw[:300], 'trunc'))


@case('validate_font rejects a character pointing at a missing glyph')
def _():
    raw = make_font([(1, 0x41, (0, 0, 4, 4))], [(1, 0x42, 99)])
    expect(tf.FontError, lambda: tf.validate_font(raw, 'dangling'))


@case('append_glyphs grows both tables and leaves the prefix untouched')
def _():
    raw = make_font([(1, 0x41, (0, 0, 4, 4))], [(1, 0x41, 1)])
    out, placed = tf.append_glyphs(raw, [
        dict(cp=0x9B9F, metrics=[5.0, 6.0, 7.0, 8.0, 9.0], rect=[2, 8, 5, 6])])
    g0, c0, l0 = tf.read_font(raw)
    g1, c1, l1 = tf.read_font(out)
    assert len(g1) == len(g0) + 1 and len(c1) == len(c0) + 1
    assert out[tf.OFF_GLYPH_TABLE:tf.OFF_GLYPH_TABLE + tf.GLYPH_STRIDE] == \
        raw[tf.OFF_GLYPH_TABLE:tf.OFF_GLYPH_TABLE + tf.GLYPH_STRIDE]
    assert out[:tf.OFF_GLYPH_COUNT] == raw[:tf.OFF_GLYPH_COUNT]
    assert c1[-1]['cp'] == 0x9B9F and c1[-1]['index'] == placed[0]['index']
    assert placed[0]['index'] == max(x['index'] for x in g0) + 1
    assert out[l1['tail']:] == raw[l0['tail']:]


@case('append_glyphs refuses a codepoint that already exists')
def _():
    raw = make_font([(1, 0x41, (0, 0, 4, 4))], [(1, 0x41, 1)])
    expect(tf.FontError, lambda: tf.append_glyphs(
        raw, [dict(cp=0x41, metrics=[0] * 5, rect=[0, 0, 1, 1])]))


@case('packer raises instead of dropping a glyph when the atlas is full')
def _():
    p = tf.Packer(32, 32, gap=0)
    p.add(32, 32, 0)
    expect(tf.AtlasExhausted, lambda: p.add(32, 32, 0))


@case('packer seeded at y0 never writes above it')
def _():
    p = tf.Packer(64, 64, gap=18, y0=40)
    rect, shelf = p.add(10, 10, 9)
    assert shelf >= 40 and rect[1] >= 40
    assert all(b == 0 for b in [rect[1] - 40])


@case('overlap detector finds an intersection and passes a clean layout')
def _():
    assert not tf.find_overlaps([(0, 0, 10, 10, 'a'), (10, 0, 20, 10, 'b')])
    assert tf.find_overlaps([(0, 0, 10, 10, 'a'), (9, 9, 20, 20, 'b')])


@case('write_texture rewrites the size fields when the atlas grows')
def _():
    tex = make_texture(4, 4, 7)
    out = tf.write_texture(tex, bytes([9]) * (4 * 8), 4, 8)
    w, h, img = tf.read_texture(out)
    assert (w, h) == (4, 8) and img == bytes([9]) * 32
    assert out.endswith(b'TAIL')


@case('write_texture rejects an image that does not match w*h')
def _():
    tex = make_texture(4, 4)
    expect(tf.FontError, lambda: tf.write_texture(tex, b'\0' * 15, 4, 4))


@case('read_texture rejects an uncompressed-size mismatch')
def _():
    tex = bytearray(make_texture(4, 4))
    struct.pack_into('<i', tex, tf.TEX_IMAGE_SIZE, 999)
    expect(tf.FontError, lambda: tf.read_texture(bytes(tex)))


@case('target_chars refuses a malformed pair table')
def _():
    doc = {'strings': ['', 'a', 'b'], 'parse': {'global': [[1]]},
           'display': {'global': [[1, 2]]}}
    expect(tc.CharError, lambda: tc.story_chars(doc))


@case('target_chars refuses to under-collect when a table disappears')
def _():
    doc = {'strings': ['', 'src', 'dst', 'src2', 'dst2'],
           'parse': {'global': [[1, 2]]},
           'display': {'global': [[1, 2]]},
           'names': [], 'selfTest': []}
    _o, _s, dst = tc.story_chars(doc)
    expect(tc.CharError, lambda: tc.assert_complete(doc, dst, 'story'))


@case('target_chars keeps only the translation side')
def _():
    doc = {'strings': ['', '鮟鱇', '鮟鱇鱼', '丼', '盖饭'],
           'parse': {'global': [[1, 2]]},
           'display': {'global': [[3, 4]]},
           'names': [], 'selfTest': [['鮟鱇', 'x', '鮟鱇料理']]}
    out, _s, _d = tc.story_chars(doc)
    assert ord('丼') not in out, 'source-only kanji must not be required'
    assert {ord(c) for c in '鮟鱇鱼盖饭料理'} <= out


def main():
    bad = 0
    for name, fn in CASES:
        try:
            fn()
            print(f'  ok   {name}')
        except AssertionError as e:
            bad += 1
            print(f'  FAIL {name}: {e}')
    print(f'\n{len(CASES) - bad}/{len(CASES)} font tool tests passed')
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
