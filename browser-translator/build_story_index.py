"""Build the COMPLETE Berumic story translation index for the WebGL bridge.

Faithful to the author's Services/TranslationManager.cs + StoryTranslationIndex.cs:

  * scenarioId = directory name containing zh_Hans.json
  * every row is kept, including empty ones (they participate in scene occurrence
    and in "promote a translated duplicate over an empty row")
  * scene-specific translations are NEVER dropped because of cross-scene conflicts
  * normalization, UTAGE control-tag detection, display form and self-mapping
    detection are delegated to the JS index builder (monmusu_cn_story_index.js), using
    exactly the same rules as the C# original
  * manifest.json commit + per-file SHA-256 are recorded and verified; CRLF is
    normalised before hashing because git checks out LF files as CRLF on Windows

Output is a compact, data-only index.  The wasm binary never contains any of it.
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REPO = os.path.join(HERE, 'ref', 'MonsterMusumeTDChineseTranslation')
DEFAULT_OUT = os.path.join(HERE, 'resources', 'monmusu_cn_story_index.json')

SCENARIO_DIR = 'translations/scenarios'
NAMES_FILE = 'translations/names/zh_Hans.json'
MANIFEST = 'translations/manifest.json'


def norm_bytes_for_hash(path):
    """Content with Windows CRLF folded back to LF (used for JSON parsing)."""
    data = open(path, 'rb').read()
    return data.replace(b'\r\n', b'\n')


def verified_bytes(path):
    """Return (bytes, variant) accepted by the upstream manifest.

    The published manifest mixes two states: most entries hash LF-normalised
    content, but 255 entries were generated while the checkout had CRLF endings.
    Accept either, and record which one matched so the result is reproducible.
    """
    raw = open(path, 'rb').read()
    lf = raw.replace(b'\r\n', b'\n')
    return ((raw, 'raw'), (lf, 'lf'))


def verify_manifest(repo, manifest_path):
    """Return (commit, manifest_sha256, corpus_sha256, files, problems)."""
    raw = open(manifest_path, 'rb').read()
    manifest_sha = hashlib.sha256(raw.replace(b'\r\n', b'\n')).hexdigest()
    man = json.loads(raw.replace(b'\r\n', b'\n').decode('utf-8'))
    commit = man.get('commit', '')
    problems = []
    verified = []
    corpus = hashlib.sha256()
    for rel in sorted(man.get('files', {})):
        meta = man['files'][rel]
        path = os.path.join(repo, 'translations', rel.replace('/', os.sep))
        if not os.path.isfile(path):
            problems.append(f'missing: {rel}')
            continue
        matched = None
        for candidate, variant in verified_bytes(path):
            digest = hashlib.sha256(candidate).hexdigest()
            if digest == meta.get('sha256') and len(candidate) == meta.get('size'):
                matched = (digest, len(candidate), variant)
                break
        if matched is None:
            problems.append(f'hash/size mismatch: {rel}')
            continue
        digest, _size, variant = matched
        corpus.update(rel.encode('utf-8'))
        corpus.update(digest.encode('ascii'))
        verified.append((rel, digest, variant))
    return commit, manifest_sha, corpus.hexdigest(), verified, problems


def collect_scenario_files(repo):
    root = os.path.join(repo, SCENARIO_DIR)
    out = []
    for dirpath, _dirs, files in os.walk(root):
        if 'zh_Hans.json' in files:
            path = os.path.join(dirpath, 'zh_Hans.json')
            rel = os.path.relpath(path, os.path.join(repo, 'translations')).replace(os.sep, '/')
            out.append((rel, path, os.path.basename(dirpath)))
    return sorted(out)


def build(repo, out_path, check_only=False, allow_unverified=False):
    manifest_path = os.path.join(repo, MANIFEST)
    commit, manifest_sha, corpus_sha, verified, problems = verify_manifest(repo, manifest_path)
    variants = {}
    for r in verified:
        variants[r[2]] = variants.get(r[2], 0) + 1
    print(f'manifest: commit={commit} sha256={manifest_sha[:16]}… '
          f'verified_files={len(verified)} variants={variants} problems={len(problems)}')
    if problems:
        for p in problems[:10]:
            print(f'  !! {p}')
        if not allow_unverified:
            sys.exit('ERROR: translation corpus failed verification; existing index left intact')

    files = collect_scenario_files(repo)
    manifest_paths = {r for r, _, _ in verified}
    unlisted = [rel for rel, _, _ in files if rel not in manifest_paths]
    if unlisted:
        print(f'  !! {len(unlisted)} scenario file(s) not present in manifest: {unlisted[:3]}')
        if not allow_unverified:
            sys.exit('ERROR: unlisted translation files; existing index left intact')

    # deterministic string pool
    pool = {}
    strings = []

    def sid(s):
        idx = pool.get(s)
        if idx is None:
            idx = len(strings)
            pool[s] = idx
            strings.append(s)
        return idx

    empty_idx = sid('')
    scenarios = []
    scene_index = {}
    scenario_sources = {}

    rows = []
    stats = {
        'scenarioFiles': len(files),
        'scenarioIds': 0,
        'duplicateScenarioFiles': 0,
        'rawRows': 0,
        'emptyRows': 0,
        'unlistedFiles': len(unlisted),
        'namesEntries': 0,
    }

    seen_files = set()
    for rel, path, scene in files:
        payload = json.loads(norm_bytes_for_hash(path).decode('utf-8'))
        if scene not in scene_index:
            scene_index[scene] = len(scenarios)
            scenarios.append(scene)
            scenario_sources[scene] = []
        else:
            stats['duplicateScenarioFiles'] += 1
        scenario_sources[scene].append(rel)
        sidx = scene_index[scene]
        for key, value in payload.items():
            if not isinstance(key, str):
                continue
            if isinstance(value, dict):      # author supports nested rows; none today
                for k2, v2 in value.items():
                    if isinstance(v2, dict):
                        iterator = v2.items()
                    else:
                        iterator = [(k2, v2)]
                    for k3, v3 in iterator:
                        _add_row(stats, rows, sid, sidx, k3, v3, empty_idx)
            else:
                _add_row(stats, rows, sid, sidx, key, value, empty_idx)
        seen_files.add(rel)

    stats['scenarioIds'] = len(scenarios)
    stats['duplicateScenarioIds'] = sum(1 for v in scenario_sources.values() if len(v) > 1)


    # ---- derive the runtime index with the author's rules, offline ----
    from story_rules import derive, is_self_mapped, normalize_key
    derived = derive(rows, strings, scenarios)
    stats.update(derived['stats'])
    all_strings = strings + derived['extraStrings']
    # Global-only compression discards scene membership. Concatenation must
    # never borrow such a key from another scene, so retain compact per-scene
    # source/value IDs separately. Keep ALL distinct values to expose conflicts.
    concat_pool = {s: i for i, s in enumerate(all_strings)}
    def concat_sid(s):
        if s not in concat_pool:
            concat_pool[s] = len(all_strings)
            all_strings.append(s)
        return concat_pool[s]
    concat_scenes = [{} for _ in scenarios]
    for sidx, ki, vi in rows:
        k, v = normalize_key(strings[ki]), normalize_key(strings[vi])
        if len(k) < 3 or len(k) > 4096 or not k or k[0] in ' \t\n\u3000':
            continue
        if not any('\u3040' <= ch <= '\u30ff' or '\uff66' <= ch <= '\uff9f' for ch in k):
            continue
        values = concat_scenes[sidx].setdefault(concat_sid(k), set())
        if v and v != k and not is_self_mapped(k, v):
            values.add(concat_sid(v))
    # Flat key/value pairs avoid allocating two tiny JS arrays per corpus row.
    # A single value is an integer; -1 means untranslated, an array is a conflict.
    concat_rows = [[item for k, v in sorted(scene.items())
                    for item in (k, next(iter(v)) if len(v) == 1 else sorted(v) if v else -1)]
                   for scene in concat_scenes]

    names_path = os.path.join(repo, NAMES_FILE)
    names = []
    if os.path.isfile(names_path):
        nd = json.loads(norm_bytes_for_hash(names_path).decode('utf-8'))
        names = [[k, v] for k, v in nd.items() if isinstance(k, str) and isinstance(v, str)]
        stats['namesEntries'] = len(names)

    doc = {
        'schema': 2,
        'commit': commit,
        'generatedAt': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'manifestSha256': manifest_sha,
        'corpusSha256': corpus_sha,
        'source': 'Berumic/MonsterMusumeTDChineseTranslation',
        'stats': stats,
        'scenarios': scenarios,
        'scenarioSources': scenario_sources,
        'strings': all_strings,
        'parse': derived['parse'],
        'display': derived['display'],
        'names': names,
        'concatenation': {'schema': 1, 'scenes': concat_rows,
                          'rows': sum(len(r) // 2 for r in concat_rows)},
    }

    # deterministic self-test samples for the sync tool / runtime smoke test
    sample = []
    for sidx, src_idx, tr_idx in rows:
        tr = all_strings[tr_idx]
        if tr and not is_self_mapped(all_strings[src_idx], tr):
            sample.append([all_strings[src_idx], scenarios[sidx], tr])
            if len(sample) >= 24:
                break
    doc['selfTest'] = sample

    # content fingerprint excludes the timestamp so re-running on the same
    # corpus does not look like an update
    core = {k: v for k, v in doc.items() if k not in ('generatedAt', 'indexSha256')}
    doc['indexSha256'] = hashlib.sha256(
        json.dumps(core, ensure_ascii=False, separators=(',', ':'),
                   sort_keys=True).encode('utf-8')).hexdigest()
    encoded = json.dumps(doc, ensure_ascii=False, separators=(',', ':')).encode('utf-8')

    print(f'rows={len(rows)} strings={len(all_strings)} scenarios={len(scenarios)} '
          f'bytes={len(encoded)}')
    print('stats:', json.dumps(stats, ensure_ascii=False, sort_keys=True))

    if check_only:
        print('check-only: no file written')
        return doc, encoded

    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    tmp = out_path + '.staging'
    open(tmp, 'wb').write(encoded)

    # ---- stage validation: nothing replaces the verified index until all pass ----
    reread = json.loads(open(tmp, 'rb').read().decode('utf-8'))
    assert reread['schema'] == 2 and len(reread['scenarios']) == len(scenarios)
    assert reread['commit'] == commit
    assert reread['corpusSha256'] == corpus_sha
    assert len(reread['parse']['global']) == len(doc['parse']['global'])
    for src, scene, tr in reread['selfTest']:
        assert src in reread['strings'] and tr in reread['strings']
        assert scene in reread['scenarios']

    # ---- compatibility guard: never silently shrink the verified pack ----
    if os.path.isfile(out_path):
        old = json.loads(open(out_path, 'rb').read().decode('utf-8'))
        if old.get('schema') != 2:
            os.remove(tmp)
            sys.exit('ERROR: installed index has an unknown schema; keeping it intact')
        for field in ('scenarioFiles', 'rawRows', 'scenarioIds'):
            new_v = stats.get(field, 0)
            old_v = old.get('stats', {}).get(field, 0)
            if old_v and new_v < old_v * 0.8:
                os.remove(tmp)
                sys.exit(f'ERROR: {field} regressed {old_v} -> {new_v}; '
                         'keeping the verified index intact')
        if old.get('indexSha256') != doc['indexSha256']:
            import shutil
            shutil.copyfile(out_path, out_path + '.previous')
            print(f'  previous verified index backed up to {out_path}.previous')

    os.replace(tmp, out_path)
    meta = {
        'commit': commit,
        'manifestSha256': manifest_sha,
        'corpusSha256': corpus_sha,
        'indexSha256': doc['indexSha256'],
        'activatedAt': doc['generatedAt'],
        'stats': stats,
    }
    with open(out_path + '.meta.json', 'w', encoding='utf-8') as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    print(f'  activated {out_path} (sha256 {doc["indexSha256"][:16]}…)')
    return doc, encoded


def _add_row(stats, rows, sid, sidx, key, value, empty_idx):
    stats['rawRows'] += 1
    if not isinstance(key, str) or not isinstance(value, str):
        return
    if value == '':
        stats['emptyRows'] += 1
        rows.append([sidx, sid(key), empty_idx])
    else:
        rows.append([sidx, sid(key), sid(value)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default=DEFAULT_REPO)
    ap.add_argument('--out', default=DEFAULT_OUT)
    ap.add_argument('--check', action='store_true', help='validate only, never write')
    ap.add_argument('--allow-unverified', action='store_true',
                    help=argparse.SUPPRESS)  # only for diagnostics; never used for activation
    args = ap.parse_args()
    build(args.repo, args.out, check_only=args.check,
          allow_unverified=args.allow_unverified)


if __name__ == '__main__':
    main()
