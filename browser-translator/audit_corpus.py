"""Full-corpus scanner + test-case generator for the WebGL translation audit.

This module deliberately imports the *production* parsers instead of rewriting
them:

  build_ui_index.read_ui_file / normalize_path / strip_layout_tags /
      normalize_sub_skill_key / is_untranslated_self_copy / collect_templates
  build_story_index.collect_scenario_files / _add_row conventions
  story_rules.normalize_key / normalize_display_key / is_self_mapped

It never implements a matcher.  It only (a) enumerates every translated row the
shipped engine can consume, (b) asks whether the generated index contains it,
and (c) materialises realistic runtime inputs.  Execution is done by
audit_engine_runner.js against monmusu_cn_ui_index.js / monmusu_cn_story_index.js verbatim.

Output: a gzip JSON-lines case file, one case per line.
"""
import collections
import glob
import gzip
import hashlib
import json
import os
import re

import build_ui_index as BUI
import build_story_index as BSI
import story_rules as SR

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, 'ref', 'MonsterMusumeTDChineseTranslation')
TRANS = os.path.join(REPO, 'translations')


def set_repo(path):
    """Point the audit at the corpus revision that produced the index.

    The audit compares the engine's answer against the corpus's own expected
    translation, so it must read the SAME revision the index was built from.
    Without this, an updated corpus reports every changed line as
    WRONG_TRANSLATION against the stale bundled copy.
    """
    global REPO, TRANS
    REPO = os.path.abspath(str(path))
    TRANS = os.path.join(REPO, 'translations')
    if not os.path.isdir(TRANS):
        raise RuntimeError('audit corpus has no translations/ directory: %s'
                           % TRANS)
    return REPO

BUILD = os.path.join(HERE, 'resources')
UI_INDEX = os.path.join(BUILD, 'monmusu_cn_ui_index.json')
STORY_INDEX = os.path.join(BUILD, 'monmusu_cn_story_index.json')
FALLBACK = os.path.join(BUILD, 'monmusu_cn_translations.json')
PROXY_CFG = os.path.join(HERE, 'resources', 'assets.json')
LOADER = os.path.join(BUILD, 'ntd.loader.js')

NUM = BUI.NUM
EX = BUI.EX

# Runtime path variants that were confirmed on the real client (stages 18/19).
# Only the Unit_Detail_Rework family is known to be remounted under these
# wrappers; applying them to every path would be inventing a mapping.
DETAIL_PREFIX = 'CanvasManagerContent/Page/Unit_Detail_Rework/'
DETAIL_VARIANTS = (
    lambda p: 'WindowPageStockObject/' + p[len('CanvasManagerContent/Page/'):],
    lambda p: 'CanvasManagerContent/Page/UI_Com_Scene_LeftIn_Center/Control/' +
              p[len('CanvasManagerContent/Page/'):],
)
# A widget path that does not occur in any corpus path table and shares no
# 4-segment tail with one, so neither pathContains() nor the tail fallback can
# borrow a rule.  HeaderTitleCommon/Text WAS a real corpus path - using it as
# the "unrelated" control produced false collisions.
UNRELATED_PATH = 'AuditSandbox/NeverMounted/Control/Text'

# <num> materialisation matrix.  Each row is a list of literal strings that are
# substituted in placeholder order.  Values are chosen to be distinct so a
# swapped or dropped placeholder is detectable.
NUM_SETS = [
    ('ascii_int', ['1', '2', '3', '4', '5', '6', '7', '8']),
    ('distinct_int', ['7', '41', '302', '8', '95', '16', '203', '4']),
    ('decimal', ['1.5', '2.25', '10.75', '0.5', '3.0', '6.4', '0.8', '9.1']),
    ('fullwidth', ['１２', '３４', '５６', '７８', '９０', '２１', '４３', '６５']),
    ('signed', ['+5', '-3', '＋１２', '−７', '0', '+9', '-11', '＋８']),
    ('mixed', ['1', '２.５', '−3', '＋４', '0.75', '６', '-2.5', '90']),
    ('format_placeholder', ['{0}', '{1}', '{2:0.0}', '{3}', '{4}', '{5}', '{6}', '{7}']),
]

TAG_SPLIT = re.compile(r'(<[^>]*>)')


def is_identity(jp, cn):
    """An untranslated row: source and value are the same text.

    The author's IsUntranslatedSelfCopy only fires for Japanese sources, but
    corpus rows such as "HP" -> "HP" are equally untranslated.  Counting them
    as misses would be counting the absence of a translation as a bug.
    """
    return jp == cn or SR.normalize_key(jp) == SR.normalize_key(cn)


def flip_percent_outside_tags(text):
    """Flip %/％ only in prose, never inside a TMP tag's attribute.

    Changing "<indent=-6%>" to "<indent=-6％>" is not a legal runtime input:
    it changes tag syntax and makes stripLayoutTags() take a different path.
    """
    parts = TAG_SPLIT.split(text)
    changed = False
    out = []
    for i, seg in enumerate(parts):
        if i % 2 == 1:                       # literal tag
            out.append(seg)
            continue
        if '%' in seg or '％' in seg:
            changed = True
            out.append(''.join('%' if ch == '％' else '％' if ch == '%' else ch
                               for ch in seg))
        else:
            out.append(seg)
    return ''.join(out) if changed else None


CASE_FIELDS = ('id', 'entry', 'module', 'kind', 'engine', 'negative', 'variant',
               'source', 'path', 'scene', 'expected', 'expect_hit',
               'jp', 'cn', 'source_file', 'scope', 'indexed', 'index_scope')


# ------------------------------------------------------------------ index map
def jload(path):
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


def load_index_maps():
    """Key sets straight out of the two shipped index documents."""
    ui = jload(UI_INDEX)
    S = ui['strings']

    def keys(rows):
        return {S[a] for a, _ in (rows or [])}

    path_scopes = {}
    for t in ui['pathTables']:
        path_scopes[t['p']] = {
            'exact': keys(t.get('exact')),
            'stripped': keys(t.get('stripped')),
            'num': keys(t.get('num')),
        }
    umap = {
        'global': keys(ui.get('global')),
        'globalStripped': keys(ui.get('globalStripped')),
        'numGlobal': keys(ui.get('numTemplates')),
        'numSub': keys(ui.get('numTemplatesSub')),
        'names': keys(ui.get('names')),
        'subskills': keys(ui.get('subskills')),
        'subskillsStripped': keys(ui.get('subskillsStripped')),
        'paths': path_scopes,
        'stats': ui.get('stats', {}),
        'commit': ui.get('commit', ''),
        'sha': hashlib.sha256(open(UI_INDEX, 'rb').read()).hexdigest(),
    }

    st = jload(STORY_INDEX)
    T = st['strings']

    def story_keys(half):
        out = set()
        for a, _ in half.get('global', []):
            out.add(T[a])
        for row in half.get('ambiguous', []):
            out.add(T[row['k']])
        return out

    smap = {
        'parse': story_keys(st['parse']),
        'display': story_keys(st['display']),
        'names': {k for k, _ in st.get('names', [])},
        'stats': st.get('stats', {}),
        'commit': st.get('commit', ''),
        'sha': hashlib.sha256(open(STORY_INDEX, 'rb').read()).hexdigest(),
        'scenarioCount': len(st['scenarios']),
    }
    return umap, smap


def loader_consistency():
    """Prove the tested indices/engines are the ones the browser actually gets."""
    cfg = jload(PROXY_CFG)
    served = {}
    for a in cfg.get('assets', []):
        # assets.json declares each wire file by bare name plus a resource
        # branch (0 = DMM, 1 = FANZA); shared files are declared once as "any".
        name = a.get('name') or os.path.basename(a.get('path', ''))
        if name.endswith('.json'):
            served[name] = os.path.join(HERE, a['file'])
    loader_bytes = open(LOADER, 'rb').read()
    checks = {}
    for js in ('monmusu_cn_runtime_config.js', 'monmusu_cn_ui_index.js',
               'monmusu_cn_story_index.js', 'monmusu_cn_shim.js',
               'monmusu_cn_bootstrap.js'):
        raw = open(os.path.join(HERE, js), 'rb').read()
        checks[js + '_embedded'] = loader_bytes.find(raw) >= 0
    # gzip on the wire must decompress to exactly the JSON being tested
    for base, plain in (('monmusu_cn_ui_index.json', UI_INDEX),
                        ('monmusu_cn_story_index.json', STORY_INDEX)):
        gz = served.get(base + '.gz') or served.get(base)
        if gz and os.path.isfile(gz):
            data = gzip.open(gz, 'rb').read() if gz.endswith('.gz') else open(gz, 'rb').read()
            checks[base + '_wire_matches'] = (data == open(plain, 'rb').read())
        else:
            checks[base + '_wire_matches'] = False
    checks['served_files'] = sorted(os.path.basename(v) for v in served.values())
    return checks


# ------------------------------------------------------------------ entries
class Corpus:
    def __init__(self):
        self.entries = []
        self.module_counts = {}
        self.skip_counts = {}
        self.all_values = collections.defaultdict(set)
        self.skipped_rows = []
        self.exact_percent_keys = 0
        self.tagged_entries = 0           # rows carrying TMP rich-text tags
        self.multi_num_templates = 0      # rows with >= 2 <num> placeholders
        self.subskill_num_templates = 0
        self.umap, self.smap = load_index_maps()

    def add(self, module, jp, cn, source_file, scope, kind):
        e = {
            'entry': len(self.entries),
            'module': module, 'jp': jp, 'cn': cn,
            'source_file': source_file, 'scope': scope, 'kind': kind,
            'indexed': False, 'index_scope': '', 'skip': '',
        }
        self.entries.append(e)
        self.module_counts[module] = self.module_counts.get(module, 0) + 1
        # Cross-module duplicates (e.g. a character name translated differently
        # in names/ and UICanvas/unit_names/) are legitimate corpus conflicts:
        # the engine follows the author's priority, so returning another known
        # translation is NOT a wrong-entry match.
        self.all_values[SR.normalize_key(jp)].add(SR.normalize_key(cn))
        return e

    def skip(self, reason, module, jp=None, cn=None):
        key = module + ':' + reason
        self.skip_counts[key] = self.skip_counts.get(key, 0) + 1
        if jp is not None and len(self.skipped_rows) < 400:
            self.skipped_rows.append({'jp': jp, 'cn': cn or '', 'module': module,
                                      'reason': reason, 'kind': reason,
                                      'scope': '', 'indexed': False,
                                      'index_scope': '', 'entry': -1,
                                      'source_file': '(untranslated corpus row)'})

    # ---- UI ---------------------------------------------------------------
    def scan_ui(self):
        ui_root = os.path.join(TRANS, 'UICanvas')
        files = sorted(glob.glob(os.path.join(ui_root, '*', 'zh_Hans.json')))
        for f in files:
            rel = os.path.relpath(f, TRANS).replace(os.sep, '/')
            rows, tables = BUI.read_ui_file(f)     # production parser
            for k, v in rows.items():
                self._ui_row(rel, 'ui_global', k, v, '')
            for path, trows in tables:
                if not BUI.is_hierarchy_path(path):
                    # Master-data column names (table[].name, text, ...).  The
                    # builder merges these rows into the global table with the
                    # author's FIRST-WINS rule; they are not path-scoped at
                    # runtime.  A row the builder deliberately left to a
                    # higher-priority scope (path table / sub-skill / name /
                    # <num> template) has no independent runtime query, so it
                    # is reported as a skip instead of a false INDEX_MISSING.
                    for k, v in trows.items():
                        if (k in self.umap['global'] or
                                k in self.umap['globalStripped'] or
                                BUI.strip_layout_tags(k) in self.umap['globalStripped']):
                            self._ui_row(rel, 'ui_global', k, v, '')
                        else:
                            e = self.add('ui_global', k, v, rel, '', 'exact')
                            e['skip'] = 'data_field_owned_by_other_scope'
                            self.skip('data_field_owned_by_other_scope', 'ui_global')
                    continue
                for k, v in trows.items():
                    self._ui_row(rel, 'ui_path', k, v, path)

        # names: used by BOTH the UI index (names table) and the story index
        f = os.path.join(TRANS, 'names', 'zh_Hans.json')
        if os.path.isfile(f):
            d = jload(f)
            rel = 'names/zh_Hans.json'
            for k, v in d.items():
                if isinstance(k, str) and isinstance(v, str):
                    self._name_row(rel, k, v)

        # sub-skills (global rows + their own $paths tables)
        f = os.path.join(TRANS, 'subskills', 'zh_Hans.json')
        if os.path.isfile(f):
            rel = 'subskills/zh_Hans.json'
            rows, tables = BUI.read_ui_file(f)
            for k, v in rows.items():
                self._ui_row(rel, 'subskill', k, v, '')
            for path, trows in tables:
                for k, v in trows.items():
                    self._ui_row(rel, 'ui_path', k, v, path)

    def _count_features(self, module, jp):
        if '<' in jp and re.search(r'<(?:font|color|link|size|cspace|indent|scale)\b',
                                   jp, re.IGNORECASE):
            self.tagged_entries += 1
        if jp.count(NUM) >= 2:
            self.multi_num_templates += 1
        if module == 'subskill' and NUM in jp:
            self.subskill_num_templates += 1

    def _ui_row(self, rel, module, jp, cn, path):
        if not isinstance(jp, str) or not isinstance(cn, str):
            return
        if not cn:
            self.skip('empty_translation', module, jp, cn)
            return
        if is_identity(jp, cn):
            self.skip('identity_translation', module, jp, cn)
            return
        if BUI.is_untranslated_self_copy(jp, cn):
            self.skip('untranslated_self_copy', module, jp, cn)
            return
        self._count_features(module, jp)
        if EX in jp:
            e = self.add(module, jp, cn, rel, path, 'ex_template')
            e['skip'] = 'ex_not_implemented'
            return
        if NUM in jp:
            if jp.count(NUM) != cn.count(NUM):
                e = self.add(module, jp, cn, rel, path, 'num_template')
                e['skip'] = 'placeholder_count_mismatch'
                self.skip('placeholder_count_mismatch', module)
                return
            kind = 'num_template' if not path else 'path_num_template'
            e = self.add(module, jp, cn, rel, path, kind)
            self._mark_ui_index(e)
            return
        kind = 'exact' if not path else 'path_exact'
        e = self.add(module, jp, cn, rel, path, kind)
        if '%' in jp or '％' in jp:
            # Exact rows are plain Map lookups: the engine deliberately does
            # NOT fold percent width for them (only <num> template regexes do).
            # Flipping the width here would be an illegal runtime input.
            self.exact_percent_keys += 1
        self._mark_ui_index(e)

    def _name_row(self, rel, jp, cn):
        if not cn:
            self.skip('empty_translation', 'names', jp, cn)
            return
        if is_identity(jp, cn):
            self.skip('identity_translation', 'names', jp, cn)
            return
        if BUI.is_untranslated_self_copy(jp, cn):
            self.skip('untranslated_self_copy', 'names', jp, cn)
            return
        e = self.add('names', jp, cn, rel, '', 'name')
        key = BUI.normalize_sub_skill_key(jp)
        e['indexed'] = key in self.umap['names']
        e['index_scope'] = 'ui.names' if e['indexed'] else ''

    def _mark_ui_index(self, e):
        jp, path, kind = e['jp'], e['scope'], e['kind']
        m, u = e['module'], self.umap
        found, scope = False, ''
        if path:
            tbl = u['paths'].get(path)
            if tbl:
                if kind == 'path_num_template':
                    found = jp in tbl['num']
                else:
                    found = jp in tbl['exact'] or jp in tbl['stripped']
                    if not found:
                        sk = BUI.strip_layout_tags(jp)
                        found = sk in tbl['stripped']
                scope = 'ui.pathTables[%s]' % path[-60:]
        elif kind == 'num_template':
            found = jp in u['numGlobal'] or jp in u['numSub']
            scope = 'ui.numTemplates / numTemplatesSub'
            if not found and m == 'subskill':
                found = jp in u['global']       # shadowed by UICanvas first-wins
                scope = 'ui.global (shadowed)'
        elif m == 'subskill':
            found = jp in u['subskills'] or jp in u['subskillsStripped']
            scope = 'ui.subskills'
            if not found and jp in u['global']:
                # author priority: _uiTexts wins over _subSkills, so a row
                # shadowed by UICanvas is still imported, just under global
                found, scope = True, 'ui.global (UICanvas wins over subskill)'
        else:
            found = jp in u['global'] or jp in u['globalStripped']
            if not found:
                sk = BUI.strip_layout_tags(jp)
                found = sk in u['globalStripped']
            scope = 'ui.global'
        e['indexed'] = bool(found)
        e['index_scope'] = scope if found else ''

    # ---- story ------------------------------------------------------------
    def scan_story(self):
        for rel, path, scene in BSI.collect_scenario_files(REPO):
            # build_story_index.build() parses norm_bytes_for_hash(path), which
            # folds Windows CRLF before JSON decoding.  Reading the raw file
            # here would produce keys the production builder never emitted.
            d = json.loads(BSI.norm_bytes_for_hash(path).decode('utf-8'))
            for k, v in d.items():
                if not isinstance(k, str) or not isinstance(v, str):
                    continue
                self._story_row('scenarios/' + rel.split('scenarios/', 1)[-1],
                                k, v, scene)

        # prologue-only rollback table (only used when the full index fails)
        if os.path.isfile(FALLBACK):
            d = jload(FALLBACK)
            for k, v in d.get('pairs', []):
                if isinstance(k, str) and isinstance(v, str):
                    e = self.add('story_fallback', k, v, 'resources/monmusu_cn_translations.json',
                                 '', 'fallback_exact')
                    e['indexed'] = (SR.normalize_key(k) in self.smap['parse'] or
                                    SR.normalize_display_key(k) in self.smap['display'])
                    e['index_scope'] = 'story.parse/display' if e['indexed'] else ''

    def _story_row(self, rel, jp, cn, scene):
        if not cn:
            self.skip('empty_translation', 'story', jp, cn)
            return
        if is_identity(jp, cn):
            self.skip('identity_translation', 'story', jp, cn)
            return
        if SR.is_self_mapped(jp, cn):
            self.skip('untranslated_self_copy', 'story', jp, cn)
            return
        e = self.add('story', jp, cn, rel, scene, 'story_exact')
        pk, dk = SR.normalize_key(jp), SR.normalize_display_key(jp)
        e['indexed'] = pk in self.smap['parse'] or dk in self.smap['display'] or \
                       pk in self.smap['display']
        e['index_scope'] = 'story.parse/display' if e['indexed'] else ''
        # remember normalised forms for the runner
        e['_pk'], e['_dk'] = pk, dk

    # ---- prefab static labels ---------------------------------------------
    def scan_prefab(self):
        """Static TMP labels rewritten inside the prefab bundle.

        These are serialised m_text values, not runtime lookups: there is no
        production query function to call, so they are reported but never
        counted as engine-testable.
        """
        p = os.path.join(BUILD, 'ui_detail.bytes')
        if not os.path.isfile(p):
            return
        for f in [p]:
            e = self.add('prefab_static', '(serialised m_text in %s)' % os.path.basename(f),
                         '', os.path.relpath(f, HERE).replace(os.sep, '/'), '',
                         'static_prefab')
            e['skip'] = 'no_runtime_query'


# ------------------------------------------------------------------ cases
class CaseGen:
    def __init__(self, corpus):
        self.c = corpus
        self.cases = []
        self.case_counts = {}

    def emit(self, e, **kw):
        c = {f: '' for f in CASE_FIELDS}
        c.update({
            'id': len(self.cases), 'entry': e['entry'], 'module': e['module'],
            'kind': e['kind'], 'negative': False, 'variant': 'base',
            'source': e['jp'], 'path': e['scope'] if e['module'] == 'ui_path' or
                      e['kind'].startswith('path_') else '',
            'scene': e['scope'] if e['module'] == 'story' else '',
            'expected': e['cn'], 'expect_hit': True,
            'jp': e['jp'], 'cn': e['cn'], 'source_file': e['source_file'],
            'scope': e['scope'], 'indexed': e['indexed'],
            'index_scope': e['index_scope'],
        })
        c.update(kw)
        self.cases.append(c)
        self.case_counts[c['module']] = self.case_counts.get(c['module'], 0) + 1

    # -- number template materialisation -----------------------------------
    def _number_cases(self, e):
        pieces = e['jp'].split(NUM)
        tpieces = e['cn'].split(NUM)
        n = len(pieces) - 1
        if n != len(tpieces) - 1 or n == 0:
            return
        # buildTemplateRe() captures only INT_RE when the next literal starts
        # with a decimal separator.  Generating a decimal there would be an
        # illegal runtime input, so pin those slots to integers.
        int_only = []
        for i in range(n):
            nxt = pieces[i + 1]
            int_only.append(bool(nxt) and nxt[0] in '.,．')

        for label, values in NUM_SETS:
            vals = []
            for i in range(n):
                v = values[i % len(values)]
                if int_only[i]:
                    # buildTemplateRe() uses INT_RE when the next literal starts
                    # with a decimal separator, so this slot can only ever be
                    # an integer at runtime.  "{0}" and "1.5" are both illegal
                    # here; normalising them is generating a REAL input, not
                    # weakening the assertion.
                    v = re.sub(r'[^0-9０-９+−＋\-]', '', v) or '1'
                vals.append(v)
            src = pieces[0] + ''.join(vals[i] + pieces[i + 1] for i in range(n))
            dst = tpieces[0] + ''.join(vals[i] + tpieces[i + 1] for i in range(n))
            self.emit(e, source=src, expected=dst, variant='num:' + label,
                      engine='ui', kind=e['kind'])
            # CRLF variant for the first two sets only (keeps the case count
            # honest: one template does not silently become 20 "corpus rows")
            if label in ('ascii_int', 'mixed') and '\n' in src:
                self.emit(e, source=src.replace('\n', '\r\n'), expected=dst,
                          variant='num:' + label + ':crlf', engine='ui')
            # full/half-width percent is folded by escPiece() in template
            # regexes only, so this variant belongs here, never on exact rows
            if label == 'ascii_int':
                flipped = flip_percent_outside_tags(src)
                if flipped and flipped != src:
                    self.emit(e, source=flipped, expected=dst,
                              variant='template_percent_width', engine='ui')
                self._path_variants(e, source=src, expected=dst)

    def _exact_cases(self, e):
        self.emit(e, engine='ui' if e['module'] != 'story' else 'story')
        jp = e['jp']
        # CRLF is only a legal variant for pure-LF rows.  The newer master-data
        # files already contain \r\n; folding a second time yields \r\r\n, which
        # never occurs at runtime and would manufacture thousands of false
        # misses.  The story family applies the same guard.
        if '\n' in jp and '\r' not in jp:
            self.emit(e, source=jp.replace('\n', '\r\n'), expected=e['cn'],
                      variant='crlf',
                      engine='ui' if e['module'] != 'story' else 'story')
        # Only when the corpus key has NO trailing whitespace of its own: the
        # trim branch puts the *original* tail back, so expecting cn + suffix
        # would be wrong if the author's key/value tails already differ.
        if e['module'] != 'story' and not jp.endswith(('\n', ' ', '\r', '\t', '\u3000')):
            self.emit(e, source=jp + '  ', expected=e['cn'] + '  ',
                      variant='trailing_ws', engine='ui')
    def _path_variants(self, e, source=None, expected=None):
        p = e['scope']
        # Stages 18/19 confirmed these wrappers ONLY for the StatusDialog /
        # UnitClassTextArea widget family.  Applying them to every
        # Unit_Detail_Rework child (e.g. Common_FastToast) would be inventing a
        # mapping and would manufacture false path failures.
        if not p or not p.startswith(DETAIL_PREFIX) or \
                'StatusDialog/UnitClassTextArea' not in p:
            return
        for i, fn in enumerate(DETAIL_VARIANTS):
            self.emit(e, source=source or e['jp'], path=fn(p),
                      expected=e['cn'] if expected is None else expected,
                      variant='runtime_path_%d' % (i + 1), engine='ui')

    def generate(self):
        for e in self.c.entries:
            if e['skip']:
                continue                      # UNTESTABLE, reported separately
            mod = e['module']
            if mod == 'prefab_static':
                continue
            if mod == 'story_fallback':
                # the prologue rollback table is queried through the production
                # bridge's flat map (setTable -> resolveText), not the story index
                self.emit(e, engine='fallback', variant='fallback_table',
                          expected=SR.normalize_key(e['cn']))
                continue
            if mod == 'story':
                # expected is the value the index stores (normalised like the
                # production builder), not the raw corpus value.
                self.emit(e, engine='story', expected=SR.normalize_key(e['cn']),
                          scene=e['scope'])
                # CRLF is only a legal variant for pure-LF corpus lines.  A few
                # scenario files carry mixed \r\r\n endings already; folding
                # those a second time produces a string that never occurs at
                # runtime and would be a false miss.
                if '\n' in e['jp'] and '\r' not in e['jp']:
                    self.emit(e, engine='story', expected=SR.normalize_key(e['cn']),
                              source=e['jp'].replace('\n', '\r\n'), variant='crlf',
                              scene=e['scope'])
                    # Same normalization contract as production NormalizeKey.
                    # Exercise all story rows, not just the screenshot samples.
                    self.emit(e, engine='story', expected=SR.normalize_key(e['cn']),
                              source=e['jp'].replace('\n', '\\n'), variant='story_literal_newline',
                              scene=e['scope'])
                if e['scope'] == '1101011nml' and e['jp'].startswith((
                        '自由な少年との', 'リン・クロミに加え', 'いやいやながら幼馴染み',
                        '違う違う違う、', '私は！', 'ケンタウロス娘じゃ！', 'なーい！！')):
                    self.emit(e, engine='story', expected=SR.normalize_key(e['cn']),
                              variant='phase23_real_fragment', scene=e['scope'])
                continue
            # UI family
            if e['kind'].endswith('num_template'):
                # _number_cases() already emits the confirmed runtime path
                # wrappers on a MATERIALISED input.  Calling _path_variants()
                # again here would feed the raw "<num>" template to the engine,
                # which is explicitly not a legal runtime string.
                self._number_cases(e)
            else:
                self._exact_cases(e)
                self._path_variants(e)
        from story_concat_audit import discover_groups, group_cases
        self.concat_groups = discover_groups(self.c.entries)
        for group, case in group_cases(self.concat_groups):
            e = group['entry']
            self.emit(e, engine='story', scene=e['scope'], kind='story_concat',
                      jp=case['source'], cn=case['expected'], **case)
        self._negative_cases()
        return self.cases

    # -- negative / collision cases ----------------------------------------
    def _negative_cases(self):
        ui_templates = [e for e in self.c.entries
                        if e['kind'].endswith('num_template') and not e['skip']]
        # Only rows that are *exclusively* path-scoped can be used for the
        # "unrelated widget must not hit" negative: a row that also lives in
        # the global / name / sub-skill tables is legitimately path-independent.
        # The author's newer corpus repeats many short keys there, so all of
        # those scopes must be excluded or the negative becomes a false
        # collision.
        glob = (self.c.umap['global'] | self.c.umap['globalStripped'] |
                self.c.umap['names'] | self.c.umap['subskills'] |
                self.c.umap['subskillsStripped'])
        ui_paths = [e for e in self.c.entries
                    if e['module'] == 'ui_path' and not e['skip'] and
                    not e['kind'].endswith('num_template') and
                    e['jp'] not in glob and
                    BUI.strip_layout_tags(e['jp']) not in glob]
        # Untranslated corpus rows are used as "must not be replaced"
        # controls.  A few of them are raw <num> templates; those are not
        # runtime strings, so they must not be fed to the engine at all.
        # The same Japanese key may be translated in ANOTHER file of the newer
        # corpus - then the engine is right to answer, so drop it as a control.
        answerable = set(glob)
        for tbl in self.c.umap['paths'].values():
            answerable |= tbl['exact'] | tbl['stripped']
        untranslated = [e for e in self.c.skipped_rows
                        if NUM not in e['jp'] and e['jp'] not in answerable][:200]

        def neg(e, source, path, variant, engine='ui', expect_hit=False,
                expected='', kind=None):
            self.emit(e, source=source, path=path, variant=variant,
                      engine=engine, negative=True, expect_hit=expect_hit,
                      expected=expected, kind=kind or e['kind'])

        # 1. illegal numbers and altered literals must not match
        for e in ui_templates[:400]:
            p = e['jp'].split(NUM)
            bad = p[0] + 'XYZ' + ''.join('1' + p[i + 1] for i in range(1, len(p) - 1))
            neg(e, bad, e['scope'] if e['kind'].startswith('path_') else '',
                'neg:illegal_number')
            if len(e['jp']) > 12:
                altered = e['jp'].replace(NUM, '1')[:-1] + '◆'
                neg(e, altered, e['scope'] if e['kind'].startswith('path_') else '',
                    'neg:altered_literal')
        # 2. path-isolated rows must not leak into an unrelated widget
        for e in ui_paths[:400]:
            neg(e, e['jp'], UNRELATED_PATH, 'neg:unrelated_path')
        # 3. template isolation: the same shape with 9s must resolve to ITS
        #    own translation, never to a different EX/skill template.  This is
        #    a positive guard, not a "must not match" case - the input is a
        #    legal instance of this template.
        for e in ui_templates[:200]:
            p, tp = e['jp'].split(NUM), e['cn'].split(NUM)
            src = p[0] + ''.join('9' + p[j + 1] for j in range(len(p) - 1))
            dst = tp[0] + ''.join('9' + tp[j + 1] for j in range(len(tp) - 1))
            self.emit(e, source=src, expected=dst,
                      path=e['scope'] if e['kind'].startswith('path_') else '',
                      variant='guard:cross_template', engine='ui')
        # 4. untranslated / fabricated Japanese must stay Japanese
        for e in untranslated:
            neg(e, e['jp'], '', 'neg:untranslated')
        fake = {'entry': -1, 'module': 'negative_control',
                'kind': 'control', 'jp': '全く未知の監査用文章９５です', 'cn': '',
                'source_file': '(synthetic)', 'scope': '', 'indexed': False,
                'index_scope': ''}
        neg(fake, '全く未知の監査用文章９５です', '', 'neg:fabricated')
        for n, src in enumerate(['攻撃力abc倍', '自身が％の確率で魔法攻撃を回避',
                                 '味方のHPを回復する\n自身と同じ属性の場合回復量倍上昇']):
            neg(fake, src, '', 'neg:illegal_shape_%d' % n)


def build_cases(out_path):
    c = Corpus()
    c.scan_ui()
    c.scan_story()
    c.scan_prefab()
    g = CaseGen(c)
    cases = g.generate()
    conflicts = {k: sorted(v) for k, v in c.all_values.items() if len(v) > 1}
    meta = {
        'entries': len(c.entries),
        'module_counts': c.module_counts,
        'skip_counts': c.skip_counts,
        'cases': len(cases),
        'case_counts': g.case_counts,
        'exact_percent_keys': c.exact_percent_keys,
        'tagged_entries': c.tagged_entries,
        'multi_num_templates': c.multi_num_templates,
        'subskill_num_templates': c.subskill_num_templates,
        'story_conflict_keys': len(conflicts),
        'ui_index_sha256': c.umap['sha'],
        'story_index_sha256': c.smap['sha'],
        'ui_index_stats': c.umap['stats'],
        'story_index_stats': c.smap['stats'],
        'loader_consistency': loader_consistency(),
        'concatenation': {
            'confirmed_user_groups': sum(group['kind'] == 'user-confirmed' for group in g.concat_groups),
            'structural_groups': sum(group['kind'] == 'structural' for group in g.concat_groups),
            'synthetic_groups': sum(group['kind'] == 'synthetic' for group in g.concat_groups),
            'generated_cases': sum(c['variant'].startswith('concat:') for c in cases),
            'json_adjacency_used': False,
        },
    }
    with gzip.open(out_path, 'wt', encoding='utf-8', newline='\n') as fh:
        fh.write(json.dumps({'_meta': meta}, ensure_ascii=False) + '\n')
        fh.write(json.dumps({'_story_conflicts': conflicts}, ensure_ascii=False) + '\n')
        for case in cases:
            fh.write(json.dumps(case, ensure_ascii=False) + '\n')
    group_manifest = [dict(kind=group['kind'], keys=group['keys'], values=group['values'],
                           scene=group['entry']['scope'], source_file=group['entry']['source_file'],
                           evidence=group['evidence']) for group in g.concat_groups]
    with open(os.path.join(os.path.dirname(out_path), 'phase24_concat_groups.json'), 'w', encoding='utf-8') as fh:
        json.dump(group_manifest, fh, ensure_ascii=False, indent=2)
    return meta, c, cases


if __name__ == '__main__':
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, 'work', 'audit_cases.jsonl.gz')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    m, _, _ = build_cases(out)
    print(json.dumps(m, ensure_ascii=False, indent=1)[:3000])
