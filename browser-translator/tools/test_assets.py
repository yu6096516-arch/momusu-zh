"""cn25 pairing contracts, using only the migrated resources (UnityPy for developers)."""
import gzip
import io
import json
from pathlib import Path
import struct
import sys
import UnityPy

ROOT = Path(__file__).resolve().parent.parent


def font_table(raw):
    count = struct.unpack_from('<I', raw, 252)[0]
    glyphs = {}
    for i in range(count):
        offset = 256 + 52 * i
        index = struct.unpack_from('<I', raw, offset)[0]
        metrics = struct.unpack_from('<5f', raw, offset + 4)
        rect = struct.unpack_from('<4i', raw, offset + 24)
        glyphs[index] = (metrics, rect)
    offset = 256 + count * 52
    nc = struct.unpack_from('<I', raw, offset)[0]
    chars = {}
    for i in range(nc):
        _, cp, index, _ = struct.unpack_from('<IIIf', raw, offset + 4 + 16 * i)
        chars[cp] = glyphs[index]
    return chars, count


def run():
    blob = gzip.decompress((ROOT / 'resources/ntd.data.gz').read_bytes())
    assert blob[:15] == b'UnityWebData1.0'
    end = struct.unpack_from('<I', blob, 16)[0]
    p = 20
    while p < end:
        start, size, n = struct.unpack_from('<III', blob, p)
        name = blob[p+12:p+12+n].decode()
        p += 12+n
        if name == 'data.unity3d':
            data = UnityPy.load(io.BytesIO(blob[start:start+size]))
            break
    else:
        raise AssertionError('Unity data missing')
    bundle = UnityPy.load(str(ROOT / 'resources/font_ui.bytes'))
    detail = UnityPy.load(str(ROOT / 'resources/ui_detail.bytes'))
    primary = next(o for o in data.objects if o.assets_file.name == 'sharedassets0.assets' and o.path_id == 497)
    tex = next(o for o in data.objects if o.assets_file.name == 'sharedassets0.assets' and o.path_id == 6).read()
    variant = next(o for o in bundle.objects if o.path_id == 725250001)
    raw = variant.get_raw_data()
    n = struct.unpack_from('<I', raw, 28)[0]
    name = raw[32:32+n].decode()
    assert name == 'monmusu_cn UI Font SDF'
    h = 0
    for c in name:
        h = ((h * 33) ^ ord(c)) & 0xffffffff
    assert struct.unpack_from('<I', raw, 56)[0] == h
    font_hash = h
    main, main_count = font_table(primary.get_raw_data())
    ui, ui_count = font_table(raw)
    assert main.keys() == ui.keys()
    # The UI variant appends the 95 ASCII glyphs the scoped UI face needs. The
    # absolute counts grow whenever the font is extended with new translation
    # characters, so only the invariant is asserted here.
    assert ui_count - main_count == 95, (main_count, ui_count)
    assert main_count >= 5833, main_count
    assert (tex.m_Width, tex.m_Height) == (8192, 4096)
    # The variant inherits its primary atlas pointer. Locate that exact texture.
    shared = next(o for o in bundle.objects if o.type.name == 'MonoBehaviour' and o.path_id != 725250001 and o.get_raw_data()[252:256] == struct.pack('<I',main_count))
    assert font_table(shared.get_raw_data()) == (main, main_count)
    textures = [o.read() for o in bundle.objects if o.type.name == 'Texture2D']
    assert any(bytes(t.image_data) == bytes(tex.image_data) for t in textures)
    seen = 0
    for o in detail.objects:
        if o.type.name != 'MonoBehaviour':
            continue
        try:
            t = o.read_typetree()
        except Exception:
            continue
        ref = t.get('m_fontAsset', {})
        if ref.get('m_PathID') != 725250001:
            continue
        assert shared.assets_file.name in o.assets_file.externals[ref['m_FileID']-1].path
        seen += 1
    assert seen == 15
    text = '40秒内，攻击力变为2.1倍，射程变为1.2倍'
    width = sum(ui[ord(c)][0][4] for c in text) * 14 / 48
    assert abs(width - 264.04) < .001 and width < 290.6663818359375 - 18
    for ch in '能量转换攻击的很往披':
        _, (x,y,w,h) = main[ord(ch)]
        assert 9 <= x and 9 <= y and x+w+9 <= 8192 and y+h+9 <= 4096
        pixels = bytes(tex.image_data)
        border = [pixels[(y-1)*8192+x+dx] for dx in range(w)]
        border += [pixels[(y+h)*8192+x+dx] for dx in range(w)]
        assert max(border) > 70
    result = dict(status='PASS', primaryGlyphs=main_count, uiGlyphs=ui_count,
                  atlas=[8192,4096], detailReferences=seen, sampleWidthAt14=width,
                  fontName=name, fontHash=font_hash, browser='NOT RUN')

    # ---- the invariant the whole font pipeline exists to keep true ----
    # Every character the shipped indexes actually display must have a glyph.
    # This is corpus-driven, not a list of expected characters, so it keeps
    # working when the translation repository moves on.
    sys.path.insert(0, str(ROOT / 'tools' / 'translations'))
    import target_chars as tc
    docs = [json.loads((ROOT / 'resources' / n).read_text(encoding='utf-8'))
            for n in ('monmusu_cn_story_index.json', 'monmusu_cn_ui_index.json',
                      'monmusu_cn_translations.json')]
    required = tc.collect(*docs)
    gaps = sorted(set(required['codepoints']) - set(main.keys()))
    assert not gaps, (
        f'{len(gaps)} character(s) displayed by the shipped indexes have no '
        f'glyph in the shipped font: '
        + ', '.join(f'{chr(c)} U+{c:04X}' for c in gaps[:10]))
    result['requiredChars'] = required['detail']['total']
    result['glyphGaps'] = 0
    (ROOT / 'work/assets_test.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    run()
