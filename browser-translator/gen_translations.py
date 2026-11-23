"""Build the runtime translation table from Berumic's translation repository.

Reuses the author's matching semantics instead of inventing new ones:

  * NormalizeSubSkillKey   - CRLF/CR and literal "\\n" folded to LF      (TranslationManager.cs:3004)
  * IsUntranslatedSelfMapping - drops rows whose "translation" is still
    Japanese, or identical to the source once control tags are stripped
    (StoryTranslationIndex.cs:213)
  * control tags           - <interval>/<speed>/<sound> ... only used for the
    self-mapping test, never stripped from the payload  (StoryTranslationIndex.cs:10)

Conflict policy (StoryTranslationIndex): a source that maps to *different*
translations in different scenes must not be merged unconditionally. In
`--scope all` such sources are dropped (the packager keeps them for the
scene-aware index); in a single-scene scope there is nothing to conflict with.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, 'ref', 'MonsterMusumeTDChineseTranslation')
SCEN = os.path.join(REPO, 'translations', 'scenarios')

CONTROL_TAG = re.compile(r'</?(?:interval|speed|sound)(?:=[^>]*)?>', re.IGNORECASE)
JAPANESE_RANGES = ((0x3040, 0x30ff), (0x3400, 0x9fff), (0xf900, 0xfaff), (0xff66, 0xff9f))


def normalize_key(value: str) -> str:
    """TranslationManager.NormalizeSubSkillKey"""
    if not value or ('\r' not in value and '\\' not in value):
        return value
    return (value.replace('\r\n', '\n')
                 .replace('\r', '\n')
                 .replace('\\r\\n', '\n')
                 .replace('\\n', '\n'))


def contains_japanese(value: str) -> bool:
    for ch in value:
        o = ord(ch)
        if any(lo <= o <= hi for lo, hi in JAPANESE_RANGES):
            return True
    return False


def has_control_tag(value: str) -> bool:
    if not value or '<' not in value:
        return False
    low = value.lower()
    return 'interval' in low or 'speed' in low or 'sound' in low


def strip_control_tags(value: str) -> str:
    return value if not has_control_tag(value) else CONTROL_TAG.sub('', value)


def is_untranslated_self_mapping(source: str, translation: str) -> bool:
    """StoryTranslationIndex.IsUntranslatedSelfMapping"""
    if not contains_japanese(source):
        return False
    if source == translation:
        return True
    if not has_control_tag(source) and not has_control_tag(translation):
        return False
    return strip_control_tags(source) == strip_control_tags(translation)


def load_file(path):
    with open(path, encoding='utf-8') as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        return []
    return [(k, v) for k, v in data.items() if isinstance(k, str) and isinstance(v, str)]


def collect_prologue():
    """Single-scene scope: 主线/序章/1100011nml (first prologue chapter)."""
    path = os.path.join(SCEN, '主线', '序章', '1100011nml', 'zh_Hans.json')
    if not os.path.isfile(path):
        sys.exit(f'ERROR: missing source file {path}')
    return [(path, load_file(path))]


def collect_all():
    out = []
    for root, _dirs, files in os.walk(SCEN):
        for name in files:
            if name == 'zh_Hans.json':
                p = os.path.join(root, name)
                out.append((p, load_file(p)))
    return sorted(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--scope', choices=['prologue', 'all'], default='prologue')
    ap.add_argument('--out', default='resources/monmusu_cn_translations.json')
    ap.add_argument('--report', default=None)
    ap.add_argument('--repo', default=REPO,
                    help='translation repository root (used by update_translations.bat)')
    args = ap.parse_args()

    # Allow the developer translation updater to point at a fresh remote clone
    # or a local checkout without touching the checked-in ref/ copy.
    global SCEN
    SCEN = os.path.join(args.repo, 'translations', 'scenarios')

    files = collect_prologue() if args.scope == 'prologue' else collect_all()
    print(f'scope={args.scope}  scenario files={len(files)}')

    # source -> {translation: count}
    seen = {}
    dropped_self = dropped_empty = 0
    for _path, rows in files:
        for key, dst in rows:
            if not key or not dst:
                dropped_empty += 1
                continue
            src = normalize_key(key)
            tgt = normalize_key(dst)
            if is_untranslated_self_mapping(src, tgt):
                dropped_self += 1
                continue
            seen.setdefault(src, {})
            seen[src][tgt] = seen[src].get(tgt, 0) + 1

    unique, conflicts = {}, {}
    for src, variants in seen.items():
        if len(variants) == 1:
            unique[src] = next(iter(variants))
        else:
            # different scenes disagree -> keep only the dominant one when it is
            # strictly more frequent, otherwise exclude entirely
            ordered = sorted(variants.items(), key=lambda kv: -kv[1])
            if len(ordered) > 1 and ordered[0][1] > ordered[1][1] * 2:
                unique[src] = ordered[0][0]
            else:
                conflicts[src] = variants

    # drop exact duplicates that would be no-ops at runtime
    unique = {k: v for k, v in unique.items() if k != v}
    pairs = sorted(unique.items(), key=lambda kv: kv[0])

    doc = {
        'schema': 1,
        'scope': args.scope,
        'count': len(pairs),
        'sourceFiles': len(files),
        'pairs': [[k, v] for k, v in pairs],
    }
    os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, ensure_ascii=False, separators=(',', ':'))

    longest = max((len(k) for k, _ in pairs), default=0)
    print(f'  kept            {len(pairs)}')
    print(f'  dropped empty   {dropped_empty}')
    print(f'  dropped selfmap {dropped_self}')
    print(f'  conflicts       {len(conflicts)}')
    print(f'  longest source  {longest} UTF-16 units')
    print(f'  wrote {args.out} ({os.path.getsize(args.out)} bytes)')

    if args.report:
        with open(args.report, 'w', encoding='utf-8') as fh:
            json.dump({'conflicts': {k: v for k, v in list(conflicts.items())[:200]}},
                      fh, ensure_ascii=False, indent=1)

    # sanity: the acceptance string must be present in the prologue scope
    if args.scope == 'prologue':
        hit = unique.get('創世記。')
        print(f'  acceptance: 創世記。 -> {hit!r}')
        if hit is None:
            sys.exit('ERROR: acceptance string 創世記。 missing from prologue table')


if __name__ == '__main__':
    main()
