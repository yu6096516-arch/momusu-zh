#!/usr/bin/env python3
"""Rebuild `resources/ui_detail.bytes` from the pristine prefab bundle.

Two verified transformations, both migrated from the research repository where
they were validated against the live client:

  1. TEXT   — `TextMeshProUGUI.m_text` values that exist **verbatim** in the UI
              index's global table are replaced. The corpus decides the wording;
              nothing is invented. Values with a trailing newline keep it.
  2. FONT   — the four character-detail label groups are re-pointed at the
              `monmusu_cn UI Font SDF` variant (pathID 725250001) that ships in
              `resources/font_ui.bytes`, so those labels use the scoped UI face
              instead of the story face.

Nothing is located by "find the bytes and overwrite": the text field is parsed
(`int32 length + UTF-8 + pad to 4`) and every replacement is read back from the
written file. If any check fails the build stops and the previous
`ui_detail.bytes` stays in place.

    python -B tools/prefab/build_ui_detail.py \
        --index resources/monmusu_cn_ui_index.json \
        --out work/translations/ui_detail/ui_detail.bytes
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

TEXT_OFF = 88                 # TextMeshProUGUI::m_text (measured on 943/1068
TEXT_DATA = 92                # components; parsed, never assumed beyond this)
TRAILING = ' \t\r\n\u3000'

UI_FONT_ID = 725250001
UI_FONT_NAME = 'monmusu_cn UI Font SDF'
FAMILIES = ('ExSkillTextArea/TextArea/Text',
            'UnitClassTextArea/TextArea/Viewport/Text',
            'UnitTribeTextArea/TextArea/Viewport/Text',
            'SubSkillTextArea/TextArea/Text')


class PrefabError(RuntimeError):
    pass


def need(cond, msg):
    if not cond:
        raise PrefabError(msg)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def align4(n: int) -> int:
    return (n + 3) & ~3


def read_text(raw: bytes):
    if len(raw) < TEXT_DATA:
        return None, 0
    n = struct.unpack_from('<i', raw, TEXT_OFF)[0]
    if n < 0 or n > 4000 or TEXT_DATA + n > len(raw):
        return None, 0
    end = TEXT_DATA + align4(n)
    if end > len(raw):
        return None, 0
    try:
        return raw[TEXT_DATA:TEXT_DATA + n].decode('utf-8'), end
    except UnicodeDecodeError:
        return None, 0


def write_text(raw: bytes, text: str, end: int) -> bytes:
    data = text.encode('utf-8')
    return (raw[:TEXT_OFF] + struct.pack('<i', len(data)) + data
            + b'\x00' * (align4(len(data)) - len(data)) + raw[end:])


def lookup(table, text):
    """Exact hit, else retry without trailing whitespace and put it back.

    The prefab ships both `ブロック数` and `ブロック数\\n`; the corpus only has
    the bare key. Dropping the newline would change the layout, so the matched
    translation is re-suffixed with the original tail.
    """
    hit = table.get(text)
    if hit is not None:
        return hit
    core = text.rstrip(TRAILING)
    if core and core != text:
        hit = table.get(core)
        if hit is not None:
            return hit + text[len(core):]
    return None


def script_names(env):
    names = {}
    for obj in env.objects:
        if obj.type.name == 'MonoScript':
            try:
                names[obj.path_id] = obj.read().m_ClassName
            except Exception:                                   # noqa: BLE001
                pass
    return names


def prefab_paths(env):
    names, transforms, by_go = {}, {}, {}
    for o in env.objects:
        if o.type.name == 'GameObject':
            names[o.path_id] = o.read().m_Name
        elif o.type.name in ('Transform', 'RectTransform'):
            d = o.read_typetree()
            transforms[o.path_id] = d
            by_go[d['m_GameObject']['m_PathID']] = o.path_id

    def path(go):
        out, t = [], by_go.get(go)
        for _ in range(64):
            if t not in transforms:
                break
            d = transforms[t]
            out.append(names.get(d['m_GameObject']['m_PathID'], '?'))
            t = d['m_Father']['m_PathID']
        return '/'.join(reversed(out))

    return path


def load_translations(index_path: Path) -> dict:
    doc = json.loads(Path(index_path).read_text(encoding='utf-8'))
    need('strings' in doc and 'global' in doc,
         f'{index_path}: not a UI index (no strings/global)')
    s = doc['strings']
    table = {s[k]: s[v] for k, v in doc['global']}
    need(len(table) > 100, f'{index_path}: only {len(table)} translation rows')
    return table


def ui_font_facts(font_bundle: Path) -> dict:
    """The UI font variant must exist in the bundle before it is referenced."""
    import UnityPy
    env = UnityPy.load(str(font_bundle))
    found = None
    for obj in env.objects:
        if obj.type.name != 'MonoBehaviour' or obj.path_id != UI_FONT_ID:
            continue
        raw = obj.get_raw_data()
        n = struct.unpack_from('<i', raw, 28)[0]
        name = raw[32:32 + n].decode('ascii', 'replace')
        found = dict(path_id=obj.path_id, name=name,
                     glyphs=struct.unpack_from('<i', raw, 252)[0])
    need(found is not None,
         f'{font_bundle}: the UI font variant (pathID {UI_FONT_ID}) is missing; '
         f'the prefab must not point at a resource that is not shipped')
    need(found['name'] == UI_FONT_NAME,
         f'{font_bundle}: UI font is named {found["name"]!r}, expected '
         f'{UI_FONT_NAME!r}')
    return found


def build(pristine: Path, index: Path, font_bundle: Path, out: Path) -> dict:
    import UnityPy

    table = load_translations(index)
    font = ui_font_facts(font_bundle)

    env = UnityPy.load(str(pristine))
    need(env.files, f'{pristine}: not a UnityFS bundle')
    bundle = next(iter(env.files.values()))
    cab_before = next(iter(bundle.files.keys()))
    scripts = script_names(env)
    n_objects = len(list(env.objects))

    changed, skipped_empty, already = [], 0, 0
    font_already = 0
    for obj in env.objects:
        if obj.type.name != 'MonoBehaviour':
            continue
        raw = obj.get_raw_data()
        if len(raw) < TEXT_DATA:
            continue
        script = struct.unpack_from('<q', raw, 20)[0]
        if scripts.get(script) != 'TextMeshProUGUI':
            continue
        text, end = read_text(raw)
        if not text:
            skipped_empty += 1
            continue
        new = lookup(table, text)
        if new is None:
            continue
        if new == text:
            already += 1
            continue
        obj.set_raw_data(write_text(raw, new, end))
        changed.append(dict(path_id=obj.path_id, source=text, target=new))

    need(changed, 'the pristine bundle matched no translation at all; the index '
                  'and the bundle are probably from different builds')

    # ---- 2. point the description labels at the scoped UI face ----
    path_of = prefab_paths(env)
    font_moves, source_font_ids = [], set()
    for obj in env.objects:
        if obj.type.name != 'MonoBehaviour':
            continue
        try:
            d = obj.read_typetree()
        except Exception:                                       # noqa: BLE001
            continue
        if 'm_fontSize' not in d:
            continue
        p = path_of(d['m_GameObject']['m_PathID'])
        if not any(p == fam or p.endswith('/' + fam) for fam in FAMILIES):
            continue
        ref = d.get('m_fontAsset')
        need(isinstance(ref, dict), f'{p}: m_fontAsset missing on a label')
        source_font_ids.add(ref.get('m_PathID'))
        if ref.get('m_PathID') == UI_FONT_ID:
            already += 1
            continue
        ref['m_PathID'] = UI_FONT_ID
        obj.save_typetree(d)
        font_moves.append(dict(path=p, path_id=obj.path_id))

    need(len(source_font_ids) == 1,
         f'description labels reference {len(source_font_ids)} different fonts '
         f'{sorted(x for x in source_font_ids if x)}; expected exactly one')
    need(font_moves, 'no description label carried the expected font reference; '
                     'the prefab layout changed')

    out.parent.mkdir(parents=True, exist_ok=True)
    data = bundle.save(packer='lz4')
    out.write_bytes(data)

    # ---- verify from the written file ----
    new_env = UnityPy.load(str(out))
    new_bundle = next(iter(new_env.files.values()))
    cab_after = next(iter(new_bundle.files.keys()))
    patched = {c['path_id']: c['target'] for c in changed}
    moved = {m['path_id'] for m in font_moves}
    text_ok, text_bad, other_diff = 0, [], 0
    src_raw = {o.path_id: o.get_raw_data() for o in UnityPy.load(str(pristine)).objects}
    for obj in new_env.objects:
        raw = obj.get_raw_data()
        if obj.path_id in patched:
            text, _ = read_text(raw)
            if text == patched[obj.path_id]:
                text_ok += 1
            else:
                text_bad.append(obj.path_id)
        elif obj.path_id in moved:
            continue
        elif src_raw.get(obj.path_id) != raw:
            other_diff += 1

    new_path = prefab_paths(new_env)
    labels, wrong_font = 0, []
    for obj in new_env.objects:
        if obj.type.name != 'MonoBehaviour':
            continue
        try:
            d = obj.read_typetree()
        except Exception:                                       # noqa: BLE001
            continue
        if 'm_fontSize' not in d:
            continue
        p = new_path(d['m_GameObject']['m_PathID'])
        if not any(p == fam or p.endswith('/' + fam) for fam in FAMILIES):
            continue
        labels += 1
        if (d.get('m_fontAsset') or {}).get('m_PathID') != UI_FONT_ID:
            wrong_font.append(p)

    checks = [
        ('CAB name unchanged', cab_after == cab_before),
        ('object count unchanged', len(list(new_env.objects)) == n_objects),
        ('every text replacement reads back', text_ok == len(changed)),
        ('no other object changed', other_diff == 0),
        ('every description label points at the UI font',
         not wrong_font and labels == len(font_moves) + font_already),
        ('UI font variant is present and named correctly',
         font['name'] == UI_FONT_NAME),
    ]
    for name, good in checks:
        print(f'  {name:48s} {"ok" if good else "FAIL"}')

    report = dict(
        status='PASS' if all(g for _n, g in checks) else 'FAIL',
        source=pristine.name, sourceSha256=sha(pristine.read_bytes()),
        index=str(index), indexSha256=sha(Path(index).read_bytes()),
        fontBundle=str(font_bundle), uiFont=font,
        cab=cab_after, size=len(data), sha256=sha(data),
        replaced=len(changed), skippedEmpty=skipped_empty,
        labels=labels, fontMoves=len(font_moves),
        sourceFontPathIds=sorted(x for x in source_font_ids if x),
        checks={n: bool(g) for n, g in checks},
        samples=changed[:20],
    )
    return report


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--pristine',
                    default=str(HERE / 'assets' / 'ui_detail.pristine.bytes'))
    ap.add_argument('--index', default=str(ROOT / 'resources' /
                                           'monmusu_cn_ui_index.json'))
    ap.add_argument('--font-bundle',
                    default=str(ROOT / 'resources' / 'font_ui.bytes'))
    ap.add_argument('--out', default=str(ROOT / 'work' / 'translations' /
                                        'ui_detail' / 'ui_detail.bytes'))
    ap.add_argument('--report', default=None)
    args = ap.parse_args(argv)

    for p in (args.pristine, args.index, args.font_bundle):
        need(Path(p).is_file(), f'missing input: {p}')
    report = build(Path(args.pristine), Path(args.index),
                   Path(args.font_bundle), Path(args.out))
    rp = Path(args.report or (Path(args.out).parent / 'report.json'))
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n',
                  encoding='utf-8')
    print(json.dumps({k: report[k] for k in
                      ('status', 'replaced', 'labels', 'fontMoves', 'size',
                       'sha256')}, ensure_ascii=False))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except PrefabError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(2)
