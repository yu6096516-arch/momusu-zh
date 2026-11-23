"""Build the Berumic UI translation index for the WebGL bridge.

Faithful to the author's Services/TranslationManager.cs (ReadUiFile / AddUiPathTable /
RegisterUiEntry / TryTranslateUiTextCore) and Services/UiStylePolicy.cs
(NormalizePath / PathContains):

  * every file under translations/UICanvas/*/zh_Hans.json is read in directory order
  * "$global" / "$globe" hold plain rows and (rarely) nested path tables
  * "$paths" holds named path tables; any other object-valued key is a table
  * globals from all files merge into one table with FIRST-WINS semantics, which is
    exactly what RegisterUiEntry does (it keeps the already-present value and
    logs "重复的 UI 翻译 key，保留先读取的条目")
  * the author's LoadTranslations ALSO registers every path-table row into the
    global map (RegisterUiEntry, first-wins), so even rows whose path rule never
    matches stay reachable globally.  TRUE hierarchy path tables are kept
    separate here: the WebGL bridge deliberately forbids path-scoped strings from
    leaking into unrelated widgets (test_ui.js pins this).  DATA-FIELD tables
    (table[].name, text, documents[].message ...) are not hierarchy rules at all,
    so their rows are merged into the global table with the author's FIRST-WINS
    rule: the already-present global wording wins, the new corpus only fills keys
    that are still missing.  This also keeps a bare "text" rule from matching
    every .../Text node and short-circuiting shared-tail rule borrowing.
  * a data row is NOT merged when another scope already owns its key.  The WebGL
    bridge keeps path tables, sub-skills, names and <num> templates as separate
    scopes around the global map; letting a data row into global for one of
    those keys would silently replace a verified translation.  This extends
    RegisterUiEntry's "keep the already-present entry" rule to those scopes.
    --check fails on an unrecognised single-segment table key so a future
    corpus layout change is reported instead of silently misclassified.
  * path tables are kept as separate tables - never merged into the global map, so a
    path-qualified translation can never leak into an unrelated widget
  * empty values and "untranslated self copies" are dropped, matching
    RegisterUiEntry's IsUntranslatedSelfCopy guard
  * per-table "stripped" maps remove TMP layout tags (<cspace>, <indent>, <size>...)
    exactly like StripUiLayoutTags
  * translations/names/zh_Hans.json is kept in its own table (the author's _names)
  * translations/subskills/zh_Hans.json is read with the same ReadUiFile parser and
    kept in its own table (the author's _subSkills).  Its lookup priority is the
    author's: scene templates > UI path tables > _uiTexts > _subSkills > _names,
    so a key UICanvas already translates keeps the UICanvas value instead of being
    silently overwritten, and a sub-skill key wins over a same-named character.

  * every row whose key still contains <num> after StripUiLayoutTags becomes a
    NUMBER TEMPLATE (see collect_templates).  Templates are emitted per scope -
    global, sub-skills and each path table - and matched at runtime with an
    anchored regex; the captured digits are copied into the translation verbatim.
    Adding a template later needs only a rebuild, no code change.

Deliberately NOT implemented in this stage (documented, not silently dropped):
  * <ex> arbitrary-string templates (the author's ExPlaceholder engine)
  * scene-tagged UI templates

Output is data-only; the wasm binary never contains any of it.
"""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REPO = os.path.join(HERE, 'ref', 'MonsterMusumeTDChineseTranslation')
DEFAULT_OUT = os.path.join(HERE, 'resources', 'monmusu_cn_ui_index.json')

UI_DIR = 'translations/UICanvas'
NAMES_FILE = 'translations/names/zh_Hans.json'
SUB_SKILLS_FILE = 'translations/subskills/zh_Hans.json'
MANIFEST = 'translations/manifest.json'
NUM = '<num>'
EX = '<ex>'

# UiStylePolicy.UiLayoutTagRegex
LAYOUT_TAG = re.compile(
    r'</?(?:cspace|indent|line-indent|line-height|margin|scale|pos|voffset|'
    r'mspace|space|size)(?:=(?:<num>|<ex>|[^>])*)?>',
    re.IGNORECASE)

# The newer corpus ships master-data dumps under UICanvas/.  Their object keys
# are column names ("table[].name", "text", "documents[].message"), not Unity
# hierarchy paths.  TranslationManager.ReadUiFile turns them into path tables
# too, but the author's runtime selects candidates by leaf segment AND registers
# every path-table row globally, so they can never scope a real widget.  Our
# bridge scans every path table, so a bare "text" rule matches any .../Text
# node and suppresses the shared-tail borrow that the detail page needs.
# Only multi-segment names are hierarchy rules; the one flat category from the
# original six-file corpus is grandfathered so published behaviour is unchanged.
LEGACY_FLAT_CATEGORIES = frozenset({'momusu_names'})
BARE_DATA_FIELD_NAMES = frozenset({'text', 'name', 'charaName', 'nickname'})


# ---------------------------------------------------------------- author rules
def normalize_path(path):
    """UiStylePolicy.NormalizePath."""
    if not path or not path.strip():
        return ''
    if ('(Clone)' not in path and '\\' not in path and '//' not in path
            and path[0] != '/' and path[-1] != '/'):
        return path
    parts = [p for p in path.replace('\\', '/').split('/') if p]
    return '/'.join(p.replace('(Clone)', '') for p in parts)


def is_hierarchy_path(name):
    """UiStylePolicy.NormalizePath + a shape check on the rule path.

    A hierarchy rule needs at least two '/' separated segments.  Single-segment
    names are master-data column names (or the one legacy flat category).
    """
    normalized = normalize_path(name)
    if normalized in LEGACY_FLAT_CATEGORIES:
        return True
    return len([p for p in normalized.split('/') if p]) >= 2


def is_data_field_name(name):
    """Recognise the master-data column names the author's scanner emits."""
    if name in BARE_DATA_FIELD_NAMES:
        return True
    return '[' in name or ']' in name or '.' in name


def number_template_shape(key):
    """Canonical shape of a runtime string for <num> template ownership.

    A data row such as "自身が95％の確率で…" must not enter the global table
    when a <num> template already owns that shape: the bridge checks global
    exact rows before templates, so it would shadow a verified translation.
    The corpus also ships format placeholders ({0}) and CRLF variants of the
    same <num> templates, so fold all of them into one shape.
    """
    key = (key.replace('\r\n', '\n').replace('\r', '\n'))
    key = re.sub(r'\{\d+(?::[^}]*)?\}', NUM, key)
    key = re.sub(r'[0-9０-９]+(?:[.,．][0-9０-９]+)?', NUM, key)
    return key.replace('％', '%')


def strip_layout_tags(value):
    """TranslationManager.StripUiLayoutTags."""
    if not value or '<' not in value:
        return value
    return LAYOUT_TAG.sub('', value)


def normalize_sub_skill_key(value):
    """TranslationManager.NormalizeSubSkillKey."""
    if not value or ('\r' not in value and '\\' not in value):
        return value
    return (value.replace('\r\n', '\n').replace('\r', '\n')
            .replace('\\r\\n', '\n').replace('\\n', '\n'))


def contains_japanese(value):
    """TranslationManager.ContainsJapanese."""
    for ch in value:
        cp = ord(ch)
        if (0x3040 <= cp <= 0x30FF or 0x3400 <= cp <= 0x9FFF
                or 0xF900 <= cp <= 0xFAFF or 0xFF66 <= cp <= 0xFF9F):
            return True
    return False


def is_untranslated_self_copy(key, value):
    """TranslationManager.IsUntranslatedSelfCopy."""
    if not key or not value or not contains_japanese(key):
        return False
    nk, nv = normalize_sub_skill_key(key), normalize_sub_skill_key(value)
    return nk == nv or strip_layout_tags(nk) == strip_layout_tags(nv)


def collect_templates(rows):
    """Split a translation map into number templates.

    A row is a number template when its *layout-stripped* key still contains
    <num> - a <num> that only lives inside a TMP tag such as <cspace=-<num>> is
    tag syntax, not a placeholder, and such a row belongs to the stripped table
    instead.  Both the raw key and its stripped variant are kept when they
    differ, exactly like TranslationManager.AddNumberTemplate.

    Rows are dropped (and counted) when the translation has a different number of
    placeholders: filling what we can would leave a literal "<num>" on screen,
    so the whole row is skipped and the source stays Japanese.
    """
    out = {}
    skipped = 0
    ex = 0
    for k, v in rows.items():
        if not v or NUM not in k:
            continue
        if EX in k:
            ex += 1
            continue
        variants = [(k, v)]
        sk = strip_layout_tags(k)
        if sk != k:
            variants.append((sk, strip_layout_tags(v) or v))
        for src, dst in variants:
            if NUM not in src:
                continue
            if NUM not in dst or src.count(NUM) != dst.count(NUM):
                skipped += 1
                continue
            if src not in out:
                out[src] = dst
    return out, skipped, ex


def template_literal_len(src):
    return len(src.replace(NUM, ''))


def pack_templates(map_):
    """Longest literal first: the most specific template wins."""
    rows = sorted(map_.items(),
                  key=lambda kv: (-template_literal_len(kv[0]), -len(kv[0]), kv[0]))
    return [(k, v) for k, v in rows]


# ---------------------------------------------------------------- file reading
def read_ui_file(path):
    """TranslationManager.ReadUiFile -> (global rows, [(path, rows), ...])."""
    global_rows = {}
    tables = []
    with open(path, encoding='utf-8') as fh:
        doc = json.load(fh)
    if not isinstance(doc, dict):
        return global_rows, tables

    def add_row(name, val):
        if not isinstance(val, str):
            return
        # author: store when non-empty, or when the key is new (empty placeholder)
        if val or name not in global_rows:
            global_rows[name] = val

    def add_table(name, obj):
        if not isinstance(obj, dict):
            return
        rows = {}
        for k, v in obj.items():
            if isinstance(v, str) and (v or k not in rows):
                rows[k] = v
        np = normalize_path(name)
        if rows and np:
            tables.append((np, rows))

    for key, val in doc.items():
        if isinstance(val, str):
            add_row(key, val)
        elif key in ('$global', '$globe') and isinstance(val, dict):
            for k2, v2 in val.items():
                if isinstance(v2, str):
                    add_row(k2, v2)
                elif isinstance(v2, dict):
                    add_table(k2, v2)
        elif key == '$paths' and isinstance(val, dict):
            for k2, v2 in val.items():
                add_table(k2, v2)
        elif not key.startswith('$') and isinstance(val, dict):
            add_table(key, val)
    return global_rows, tables


def build_path_table(path, rows):
    """TranslationManager.UiPathTable ctor (exact + stripped)."""
    exact = {}
    for k, v in rows.items():
        if not v or is_untranslated_self_copy(k, v):
            continue
        exact[k] = v
    stripped = {}
    for k, v in exact.items():
        sk = strip_layout_tags(k)
        if not sk:
            continue
        sv = strip_layout_tags(v) or v
        if sk not in stripped:
            stripped[sk] = sv
    return {'path': path, 'exact': exact, 'stripped': stripped}


def build(repo, out_path, check_only=False):
    ui_root = os.path.join(repo, UI_DIR)
    if not os.path.isdir(ui_root):
        sys.exit(f'ERROR: {ui_root} not found')

    # ---- manifest provenance (same acceptance rules as the story builder) ----
    manifest_path = os.path.join(repo, MANIFEST)
    raw = open(manifest_path, 'rb').read()
    manifest_sha = hashlib.sha256(raw.replace(b'\r\n', b'\n')).hexdigest()
    man = json.loads(raw.replace(b'\r\n', b'\n').decode('utf-8'))
    commit = man.get('commit', '')

    dirs = sorted(d for d in os.listdir(ui_root)
                  if os.path.isfile(os.path.join(ui_root, d, 'zh_Hans.json')))
    if not dirs:
        sys.exit('ERROR: no UICanvas translation files found')

    problems, verified = [], []
    corpus = hashlib.sha256()

    def verify(rel):
        path = os.path.join(repo, 'translations', rel.replace('/', os.sep))
        meta = man.get('files', {}).get(rel)
        if not os.path.isfile(path):
            problems.append(f'missing: {rel}')
            return None
        if meta is None:
            problems.append(f'not in manifest: {rel}')
            return None
        for candidate in (open(path, 'rb').read(),
                          open(path, 'rb').read().replace(b'\r\n', b'\n')):
            digest = hashlib.sha256(candidate).hexdigest()
            if digest == meta.get('sha256') and len(candidate) == meta.get('size'):
                corpus.update(rel.encode('utf-8'))
                corpus.update(digest.encode('ascii'))
                verified.append(rel)
                return digest
        problems.append(f'hash/size mismatch: {rel}')
        return None

    for d in dirs:
        verify(f'UICanvas/{d}/zh_Hans.json')
    verify('names/zh_Hans.json')
    verify('subskills/zh_Hans.json')   # verify() paths are under translations/

    # ---- read & merge -------------------------------------------------------
    stats = {
        'files': len(dirs), 'rawRows': 0, 'emptyRows': 0, 'selfCopyRows': 0,
        'globalEntries': 0, 'pathTables': 0, 'pathEntries': 0,
        'pathStrippedEntries': 0, 'nameEntries': 0, 'crossFileConflicts': 0,
        'numTemplateEntries': 0, 'taggedEntries': 0, 'rejectedPathTables': 0,
        'dataTables': 0, 'dataRows': 0, 'dataRowsMerged': 0,
        'dataConflicts': 0, 'dataReserved': 0, 'dataPathOwned': 0,
        'dataTemplateOwned': 0, 'dataKnownValue': 0,
        'unclassifiedTableKeys': 0,
        'subSkillRawRows': 0, 'subSkillSelfCopy': 0, 'subSkillNumTemplates': 0,
        'subSkillEntries': 0, 'subSkillShadowedByUi': 0,
        'subSkillOverridesName': 0, 'subSkillConflicts': 0,
        'numTemplatesGlobal': 0, 'numTemplatesSub': 0, 'numTemplatesPath': 0,
        'numTemplatesSkipped': 0, 'numTemplatesEx': 0,
    }
    merged = {}
    conflicts = []
    data_rows = {}
    data_conflicts = []
    tables = []

    # Keys owned by the sub-skill / name tables stay in those tables.  The WebGL
    # bridge checks global first, so a data row for one of these keys would
    # silently replace a verified sub-skill or character-name wording.
    reserved_keys = set()
    reserved_values = set()
    template_shapes = set()
    sub_path = os.path.join(repo, SUB_SKILLS_FILE)
    if os.path.isfile(sub_path):
        srows, _ = read_ui_file(sub_path)
        reserved_keys |= {normalize_sub_skill_key(k) for k in srows}
        reserved_values |= {v for v in srows.values() if v}
        template_shapes |= {number_template_shape(k) for k in srows if NUM in k}
    names_path = os.path.join(repo, NAMES_FILE)
    if os.path.isfile(names_path):
        nd = json.loads(open(names_path, 'rb').read()
                        .replace(b'\r\n', b'\n').decode('utf-8'))
        reserved_keys |= {normalize_sub_skill_key(k) for k, v in nd.items()
                          if isinstance(k, str) and isinstance(v, str)}
        reserved_values |= {v for v in nd.values()
                            if isinstance(v, str) and v}

    for d in dirs:
        rows, tbls = read_ui_file(os.path.join(ui_root, d, 'zh_Hans.json'))
        stats['rawRows'] += len(rows) + sum(len(t[1]) for t in tbls)
        for k, v in rows.items():
            if not v:
                stats['emptyRows'] += 1
                continue
            if is_untranslated_self_copy(k, v):
                stats['selfCopyRows'] += 1
                continue
            if k in merged:
                if merged[k] != v:
                    stats['crossFileConflicts'] += 1
                    conflicts.append({'key': k, 'kept': merged[k], 'ignored': v,
                                      'file': d})
                continue
            merged[k] = v
        for path, trows in tbls:
            if is_hierarchy_path(path):
                pt = build_path_table(path, trows)
                if pt['exact']:
                    tables.append(pt)
                else:
                    stats['rejectedPathTables'] += 1
                continue

            # Master-data column, not a Unity hierarchy rule.  The author's
            # LoadTranslations feeds every path-table row through
            # RegisterUiEntry as well, with FIRST-WINS semantics (the already
            # present _uiTexts value is kept).  Collect them first: all UICanvas
            # globals are registered before any data-table row, so the global
            # wording always wins and the new corpus only fills missing keys.
            stats['dataTables'] += 1
            stats['dataRows'] += len(trows)
            if not is_data_field_name(path):
                stats['unclassifiedTableKeys'] += 1
                problems.append(
                    f'unclassified single-segment table key {path!r} in '
                    f'UICanvas/{d}: decide hierarchy category or data field')
            for k, v in trows.items():
                if not v or is_untranslated_self_copy(k, v):
                    continue
                if k not in data_rows:
                    data_rows[k] = v

    # Scopes that already own a key must keep it.  The WebGL bridge checks
    # path tables, sub-skills, names and <num> templates around the global map;
    # a data row entering global for one of these keys would change a verified
    # translation.  This is the bridge's extension of RegisterUiEntry's
    # first-wins rule to the scopes the author keeps as separate tables.
    path_owned = set()
    known_values = set(merged.values()) | reserved_values | set(data_rows.values())
    for t in tables:
        path_owned |= set(t['exact'])
        path_owned |= set(t['stripped'])
        known_values |= set(t['exact'].values())
        known_values |= set(t['stripped'].values())
    template_shapes |= {number_template_shape(k) for k in merged if NUM in k}

    for k, v in data_rows.items():
        nk = normalize_sub_skill_key(k)
        if nk in reserved_keys or k in reserved_keys:
            stats['dataReserved'] += 1
            continue
        if k in merged:
            stats['dataConflicts'] += 1
            if len(data_conflicts) < 40:
                data_conflicts.append({'key': k, 'kept': merged[k],
                                       'ignored': v, 'table': 'data-field',
                                       'rule': 'first-wins: global wins'})
            continue
        if k in path_owned or nk in path_owned:
            stats['dataPathOwned'] += 1
            continue
        if number_template_shape(k) in template_shapes:
            stats['dataTemplateOwned'] += 1
            continue
        # The bridge short-circuits any source that is already one of our
        # translated values (knownValues).  Such a row could never be served,
        # so it is not imported instead of producing a permanent miss.
        if k in known_values:
            stats['dataKnownValue'] += 1
            continue
        merged[k] = v
        stats['dataRowsMerged'] += 1

    # ---- sub-skills: same ReadUiFile parser, its $paths tables join the UI ones
    sub_rows, sub_tables = {}, []
    sub_path = os.path.join(repo, SUB_SKILLS_FILE)
    if os.path.isfile(sub_path):
        sub_rows, sub_tables = read_ui_file(sub_path)
        stats['subSkillRawRows'] = len(sub_rows) + sum(len(t[1]) for t in sub_tables)
        for path, trows in sub_tables:
            pt = build_path_table(path, trows)
            if pt['exact']:
                tables.append(pt)

    # author sorts by path length descending; ties keep file order (deterministic)
    tables.sort(key=lambda t: -len(t['path']))

    stats['globalEntries'] = len(merged)
    stats['pathTables'] = len(tables)
    stats['pathEntries'] = sum(len(t['exact']) for t in tables)
    stats['pathStrippedEntries'] = sum(len(t['stripped']) for t in tables)

    for k in list(merged) + [k for t in tables for k in t['exact']]:
        if '<num>' in k:
            stats['numTemplateEntries'] += 1
        elif '<' in k:
            stats['taggedEntries'] += 1

    # ---- names --------------------------------------------------------------
    names = {}
    names_path = os.path.join(repo, NAMES_FILE)
    if os.path.isfile(names_path):
        nd = json.loads(open(names_path, 'rb').read()
                        .replace(b'\r\n', b'\n').decode('utf-8'))
        for k, v in nd.items():
            if not isinstance(k, str) or not isinstance(v, str):
                continue
            if not v or is_untranslated_self_copy(k, v):
                continue
            names[normalize_sub_skill_key(k)] = v
    stats['nameEntries'] = len(names)

    # ---- sub-skill table (priority: UI global > sub-skills > names) ----------
    sub_skills = {}
    sub_tpl_rows = {}          # rows with <num>: never exact, but valid templates
    sub_conflicts = []
    for k, v in sub_rows.items():
        if not v:
            continue
        if is_untranslated_self_copy(k, v):
            stats['subSkillSelfCopy'] += 1
            continue
        nk = normalize_sub_skill_key(k)
        if '<num>' in nk:
            # Dynamic descriptions.  They cannot be exact rows (the runtime text
            # carries real numbers), but they ARE legal templates - stage 14
            # dropped them entirely, which is why no sub-skill description with
            # a number ever translated.  They go to the template table instead.
            stats['subSkillNumTemplates'] += 1
            sub_tpl_rows[nk] = v
            continue
        if nk in merged:
            # the author checks _uiTexts before _subSkills, so UICanvas wins
            stats['subSkillShadowedByUi'] += 1
            if merged[nk] != v:
                stats['subSkillConflicts'] += 1
                sub_conflicts.append({'key': k, 'kept': merged[nk],
                                      'ignored': v, 'rule': 'UICanvas wins'})
            continue
        if nk in names:
            stats['subSkillOverridesName'] += 1
        sub_skills[nk] = v
    stats['subSkillEntries'] = len(sub_skills)

    # ---- number templates (global / sub-skills / per path table) -------------
    num_global, skipped_g, ex_g = collect_templates(merged)
    num_sub, skipped_s, ex_s = collect_templates(sub_tpl_rows)
    num_path = {}
    for t in tables:
        got, skipped_p, ex_p = collect_templates(t['exact'])
        if got:
            num_path[id(t)] = got
        stats['numTemplatesSkipped'] += skipped_p
        stats['numTemplatesEx'] += ex_p
    stats['numTemplatesSkipped'] += skipped_g + skipped_s
    stats['numTemplatesEx'] += ex_g + ex_s
    stats['numTemplatesGlobal'] = len(num_global)
    stats['numTemplatesSub'] = len(num_sub)
    stats['numTemplatesPath'] = sum(len(v) for v in num_path.values())

    # ---- packed document ----------------------------------------------------
    pool, pool_index = [], {}

    def sid(text):
        i = pool_index.get(text)
        if i is None:
            i = len(pool)
            pool_index[text] = i
            pool.append(text)
        return i

    def pack(map_):
        return [[sid(k), sid(v)] for k, v in map_.items()]

    doc = {
        'schema': 3,
        'kind': 'ui',
        'commit': commit,
        'generatedAt': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'manifestSha256': manifest_sha,
        'corpusSha256': corpus.hexdigest(),
        'source': 'Berumic/MonsterMusumeTDChineseTranslation',
        'rules': {
            'globalMerge': ('first-wins across files in directory order; '
                            'data-field tables merged after their file globals, '
                            'global wording wins (TranslationManager.RegisterUiEntry)'),
            'tableClassification': ('multi-segment names are hierarchy path tables; '
                                    'single-segment master-data column names merge '
                                    'into global; momusu_names is grandfathered'),
            'pathMatch': 'leaf + segment-subsequence, table path length desc',
            'lookup': 'scene templates > UI path tables > global > subskills > names',
            'ignored': ['<num> number templates', 'regex templates',
                        'scene-tagged UI templates',
                        'subskill <num> templates (kept in the source file only)'],
        },
        'stats': stats,
        'strings': [],
        'global': pack(merged),
        'globalStripped': pack({strip_layout_tags(k): strip_layout_tags(v) or v
                                for k, v in merged.items()
                                if strip_layout_tags(k)}),
        'numTemplates': [[sid(k), sid(v)] for k, v in pack_templates(num_global)],
        'numTemplatesSub': [[sid(k), sid(v)] for k, v in pack_templates(num_sub)],
        'pathTables': [{'p': t['path'],
                        'exact': pack(t['exact']),
                        'stripped': pack(t['stripped']),
                        'num': [[sid(k), sid(v)]
                                for k, v in pack_templates(num_path.get(id(t), {}))]}
                       for t in tables],
        'subskills': pack(sub_skills),
        'subskillsStripped': pack({strip_layout_tags(k): strip_layout_tags(v) or v
                                   for k, v in sub_skills.items()
                                   if strip_layout_tags(k)}),
        'names': pack(names),
        'conflicts': conflicts[:40],
        'dataConflicts': data_conflicts,
        'subSkillConflicts': sub_conflicts[:40],
    }
    doc['strings'] = pool

    # deterministic self-test rows: prefer short, path-free ones first
    sample = []
    for k, v in sorted(merged.items(), key=lambda kv: (len(kv[0]), kv[0])):
        if 2 <= len(k) <= 24 and '<' not in k:
            sample.append([k, '', v])
        if len(sample) >= 12:
            break
    for t in tables:
        for k, v in sorted(t['exact'].items(), key=lambda kv: (len(kv[0]), kv[0])):
            if 2 <= len(k) <= 24 and '<' not in k:
                sample.append([k, t['path'], v])
                break
        if len(sample) >= 20:
            break
    # real sub-skill rows (no path: the table is global), so the staged index is
    # validated against the strings this stage actually added
    for k, v in sorted(sub_skills.items(), key=lambda kv: (len(kv[0]), kv[0]))[:3]:
        sample.append([k, '', v])
    doc['selfTest'] = sample

    core = {k: v for k, v in doc.items() if k not in ('generatedAt', 'indexSha256')}
    doc['indexSha256'] = hashlib.sha256(
        json.dumps(core, ensure_ascii=False, separators=(',', ':'),
                   sort_keys=True).encode('utf-8')).hexdigest()
    encoded = json.dumps(doc, ensure_ascii=False,
                         separators=(',', ':')).encode('utf-8')

    print(f'files={stats["files"]} globals={stats["globalEntries"]} '
          f'pathTables={stats["pathTables"]} pathRows={stats["pathEntries"]} '
          f'names={stats["nameEntries"]} subskills={stats["subSkillEntries"]} '
          f'numTemplates={stats["numTemplatesGlobal"]}+{stats["numTemplatesSub"]}'
          f'+{stats["numTemplatesPath"]} bytes={len(encoded)}')
    print('stats:', json.dumps(stats, ensure_ascii=False, sort_keys=True))
    print(f'data-field tables: {stats["dataTables"]} tables / '
          f'{stats["dataRows"]} rows, {stats["dataRowsMerged"]} merged into global, '
          f'{stats["dataConflicts"]} kept the already-present global wording '
          f'(first-wins), {stats["dataReserved"]} left to sub-skills/names, '
          f'{stats["dataPathOwned"]} left to path tables, '
          f'{stats["dataTemplateOwned"]} left to <num> templates, '
          f'{stats["dataKnownValue"]} skipped as known translation values')
    print(f'provenance: commit={commit[:12]} corpus={corpus.hexdigest()[:16]} '
          f'verified={len(verified)} problems={len(problems)}')
    for p in problems[:5]:
        print('  !', p)

    if problems:
        sys.exit('ERROR: translation corpus failed verification; '
                 'existing index left intact')

    if check_only:
        return doc, encoded

    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    tmp = out_path + '.staging'
    open(tmp, 'wb').write(encoded)

    # stage validation: nothing replaces the verified index until all pass
    reread = json.loads(open(tmp, 'rb').read().decode('utf-8'))
    assert reread['schema'] == 3 and reread['kind'] == 'ui'
    assert reread['commit'] == commit and reread['corpusSha256'] == corpus.hexdigest()
    assert len(reread['global']) == len(merged)
    assert len(reread['pathTables']) == len(tables)
    for src, path, tr in reread['selfTest']:
        assert src in reread['strings'] and tr in reread['strings']
        assert not path or any(t['p'] == path for t in reread['pathTables'])
    if reread['indexSha256'] != doc['indexSha256']:
        sys.exit('ERROR: staged index hash mismatch')

    if os.path.isfile(out_path):
        old = json.loads(open(out_path, 'rb').read().decode('utf-8'))
        if old.get('schema') != 3:
            os.remove(tmp)
            sys.exit('ERROR: installed UI index has an unknown schema; keeping it')
        for field in ('globalEntries', 'pathTables', 'pathEntries', 'nameEntries',
                      'subSkillEntries'):
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
        'corpusSha256': corpus.hexdigest(),
        'indexSha256': doc['indexSha256'],
        'activatedAt': doc['generatedAt'],
        'stats': stats,
    }
    with open(out_path + '.meta.json', 'w', encoding='utf-8') as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    print(f'  activated {out_path} (sha256 {doc["indexSha256"][:16]}…)')
    return doc, encoded


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default=DEFAULT_REPO)
    ap.add_argument('--out', default=DEFAULT_OUT)
    ap.add_argument('--check', action='store_true')
    args = ap.parse_args()
    build(args.repo, args.out, check_only=args.check)


if __name__ == '__main__':
    main()
