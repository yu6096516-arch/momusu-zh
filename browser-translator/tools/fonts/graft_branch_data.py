#!/usr/bin/env python3
"""Inject the built Chinese font payload into a branch's own ntd.data container.

Why not just serve the `/0/` container to `/1/`: the two branches ship different
`PlayerSettings` (880 vs 888 bytes) — a store identifier — and different IL2CPP
metadata.  Everything else in `data.unity3d` is byte-identical, including the
pristine TMP font, its atlas and the two runtime `Font` objects.  So instead of
copying a whole container across branches we copy only the objects the Chinese
build actually rewrites, leaving the target branch's own settings intact.

Objects copied (measured, not guessed — these are exactly the objects that
differ between the official container and the shipped Chinese one):

  sharedassets0.assets / 497   TMP_FontAsset   glyph + character tables
  sharedassets0.assets / 6     Texture2D       the 8192x4096 SDF atlas
  sharedassets4.assets / 30    Font            main runtime font blob
  resources.assets / 173       Font            fallback font blob
  every Material whose _MainTex is the atlas   _TextureWidth / _TextureHeight

Everything else in the target container is left untouched.

    python -B tools/fonts/graft_branch_data.py \
        --official work/branch/ntd.data.1 \
        --out work/branch/ntd.data.1.cn
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

FONT_KEY = ('sharedassets0.assets', 497)
TEX_KEY = ('sharedassets0.assets', 6)
RUNTIME_FONTS = (('sharedassets4.assets', 30), ('resources.assets', 173))
BASELINE = HERE / 'branch_baseline.json'


class GraftError(RuntimeError):
    pass


def need(cond, msg):
    if not cond:
        raise GraftError(msg)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def open_container(path: Path):
    import UnityPy
    blob, entries = tf.load_container(str(path))
    need('data.unity3d' in entries, f'{path}: no data.unity3d entry')
    env = UnityPy.load(io.BytesIO(entries['data.unity3d']))
    return blob, entries, env


def index(env):
    return {(o.assets_file.name, o.path_id): o for o in env.objects}


def raw_of(objs, key, path):
    need(key in objs, f'{path}: object {key} is missing')
    return objs[key].get_raw_data()


def sync_materials(env, texture_id, texture_file, w, h):
    changed = []
    for obj in env.objects:
        if obj.type.name != 'Material':
            continue
        try:
            mat = obj.read()
            main = dict(mat.m_SavedProperties.m_TexEnvs).get('_MainTex')
            if main is None or main.m_Texture.m_PathID != texture_id:
                continue
            fid = main.m_Texture.m_FileID
            ext = obj.assets_file.externals
            target = (obj.assets_file.name if fid == 0 else
                      ext[fid - 1].path if 0 < fid <= len(ext) else '')
            if not target.replace('\\', '/').lower().endswith(texture_file.lower()):
                continue
            floats = mat.m_SavedProperties.m_Floats
            for i, (key, _v) in enumerate(floats):
                if key == '_TextureWidth':
                    floats[i] = (key, float(w))
                elif key == '_TextureHeight':
                    floats[i] = (key, float(h))
            obj.save_typetree(mat)
            changed.append(obj.path_id)
        except Exception:                                       # noqa: BLE001
            continue
    return changed


def build(official: Path, source: Path, out: Path, baseline_path: Path):
    base = json.loads(baseline_path.read_text(encoding='utf-8')) if baseline_path.is_file() else None
    need(base is not None, f'missing baseline {baseline_path}; regenerate it from '
                           f'both official branches first')

    sblob, _sentries, senv = open_container(source)
    so = index(senv)
    want_raw = raw_of(so, FONT_KEY, source)
    want_w, want_h, want_img = tf.read_texture(raw_of(so, TEX_KEY, source))
    want_fonts = {k: raw_of(so, k, source) for k in RUNTIME_FONTS}

    oblob, oentries, oenv = open_container(official)
    oo = index(oenv)

    # ---- the target must still be the build the baseline describes ----
    for key, rec in base['objects'].items():
        parts = key.split('/')
        need(len(parts) == 2, f'bad baseline key {key!r}')
        k = (parts[0], int(parts[1]))
        got = sha(raw_of(oo, k, official))
        need(got == rec['sha256'],
             f'{official}: {key} is {got[:16]}, expected {rec["sha256"][:16]} — '
             f'that branch shipped a different build; refusing to graft')

    # ---- copy the five payloads ----
    oo[FONT_KEY].set_raw_data(want_raw)
    tex_raw = oo[TEX_KEY].get_raw_data()
    oo[TEX_KEY].set_raw_data(tf.write_texture(tex_raw, want_img, want_w, want_h))
    for key in RUNTIME_FONTS:
        oo[key].set_raw_data(want_fonts[key])
    mats = sync_materials(oenv, TEX_KEY[1], FONT_KEY[0], want_w, want_h)
    need(mats, 'no material referenced the atlas; the container layout changed')

    out.parent.mkdir(parents=True, exist_ok=True)
    tf.container_repack(oblob, oenv.file.save(packer='lz4'), str(out))
    tf.gzip_reproducible(str(out), str(out) + '.gz')

    # ---- verify from the written file ----
    _vb, _ve, venv = open_container(out)
    vo = index(venv)
    checks = [
        ('object count unchanged', len(vo) == len(oo)),
        ('font asset payload matches',
         raw_of(vo, FONT_KEY, out) == want_raw),
        ('atlas matches', tf.read_texture(raw_of(vo, TEX_KEY, out))[2] == want_img),
        ('atlas size matches', tf.read_texture(raw_of(vo, TEX_KEY, out))[:2]
         == (want_w, want_h)),
        ('runtime font blobs match',
         all(raw_of(vo, k, out) == want_fonts[k] for k in RUNTIME_FONTS)),
        ('target branch settings preserved',
         raw_of(vo, ('globalgamemanagers', 1), out)
         == raw_of(oo, ('globalgamemanagers', 1), official)),
    ]
    # every object we did not intend to touch must be byte-identical
    intended = {FONT_KEY, TEX_KEY, *RUNTIME_FONTS}
    changed = [k for k in vo
               if k not in intended and vo[k].get_raw_data() != oo[k].get_raw_data()]
    unexpected = [k for k in changed if vo[k].type.name != 'Material']
    checks.append(('only material dimensions changed elsewhere', not unexpected))
    for name, good in checks:
        print(f'  {name:44s} {"ok" if good else "FAIL"}')
    need(all(g for _n, g in checks), 'graft verification failed')

    report = dict(
        status='PASS', official=official.name, source=source.name,
        officialSha256=sha(official.read_bytes() if official.suffix != '.gz'
                           else __import__('gzip').decompress(official.read_bytes())),
        out=str(out), size=out.stat().st_size, sha256=sha(out.read_bytes()),
        gzSize=(out.parent / (out.name + '.gz')).stat().st_size,
        atlas=[want_w, want_h], glyphPayload=len(want_raw),
        runtimeFonts={f'{a}/{b}': len(v) for (a, b), v in want_fonts.items()},
        materials=len(changed), checks={n: bool(g) for n, g in checks},
    )
    (out.parent / 'graft_report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('status', 'size', 'sha256', 'materials')},
                     ensure_ascii=False))
    return report


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--official', required=True,
                    help='pristine ntd.data for the target branch')
    ap.add_argument('--source', default=str(ROOT / 'resources' / 'ntd.data.gz'),
                    help='the shipped Chinese container to copy the payload from')
    ap.add_argument('--out', required=True)
    ap.add_argument('--baseline', default=str(BASELINE))
    args = ap.parse_args(argv)
    for p in (args.official, args.source):
        need(Path(p).is_file(), f'missing input: {p}')
    build(Path(args.official), Path(args.source), Path(args.out), Path(args.baseline))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except GraftError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(2)
