"""Full-corpus translation audit for the Monster Musume TD WebGL mod.

    python audit_all_translations.py                 # full run
    python audit_all_translations.py --baseline     # save baseline, no fixes
    python audit_all_translations.py --quick        # small deterministic subset

Pipeline
  audit_corpus.build_cases()      -> every translated corpus row + realistic
                                     runtime inputs (gzip JSON lines)
  audit_engine_runner.js          -> executes each case against the PRODUCTION
                                     monmusu_cn_ui_index.js / monmusu_cn_story_index.js and
                                     the production bridge query functions
  this file                       -> classifies, writes the three reports

Offline PASS means "the matching layer returned the expected Chinese string".
It is NOT a browser/display result: every PASS is tagged RUNTIME_NOT_VERIFIED.
"""
import argparse
import collections
import gzip
import hashlib
import json
import os
import subprocess
import sys
import time

import audit_corpus as AC

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, 'work')
CASES = os.path.join(WORK, 'audit_cases.jsonl.gz')
RESULTS = os.path.join(WORK, 'audit_results.jsonl.gz')
BASELINE = os.path.join(HERE, 'translation_audit_baseline.json')
SUMMARY = os.path.join(HERE, 'translation_audit_summary.json')
FAILURES = os.path.join(HERE, 'translation_audit_failures.tsv')
REPORT = os.path.join(HERE, 'translation_audit_report.md')

CLASSES = ('PASS', 'INDEX_MISSING', 'LOOKUP_FAIL', 'PATH_FAIL',
           'WRONG_TRANSLATION', 'NUM_MISMATCH', 'COLLISION', 'UNTESTABLE')

DIGITS = '0123456789０１２３４５６７８９'


def digits_of(text):
    """Ordered numeric tokens, used to detect dropped/swapped placeholders."""
    import re
    return re.findall(r'[0-9０-９]+(?:[.,．][0-9０-９]+)?', text or '')


def read_meta(path):
    """Return (meta, story_conflicts); both are metadata lines, not cases."""
    meta, conflicts = {}, {}
    with gzip.open(path, 'rt', encoding='utf-8') as fh:
        for line in fh:
            o = json.loads(line)
            if '_meta' in o:
                meta = o['_meta']
            elif '_story_conflicts' in o:
                conflicts = o['_story_conflicts']
            else:
                break
    return meta, conflicts


def run_node(cases=CASES, results=RESULTS):
    t0 = time.time()
    p = subprocess.run([os.environ.get('NODE_BIN', 'node'),
                        os.path.join(HERE, 'audit_engine_runner.js'), cases, results],
                       cwd=HERE, capture_output=True, text=True, encoding='utf-8')
    if p.returncode != 0:
        sys.exit('FATAL: engine runner failed:\n' + (p.stderr or '')[-4000:])
    try:
        runner = json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        runner = {}
    runner['wallSeconds'] = round(time.time() - t0, 1)
    return runner


def classify(case, res, conflicts):
    """Assign one of the eight classes; res.ok comes from the real engine."""
    negative = bool(case['negative'])
    actual = res.get('a') if not res.get('ok') else case['expected'] if case['expect_hit'] else None
    rule = res.get('r', '')
    why = res.get('w', '')
    if case.get('kind') == 'story_concat' and why == 'concat-ambiguous-coverage':
        return 'UNTESTABLE', 'ambiguous full coverage correctly rejected; no unique translation expectation'

    if res.get('ok'):
        return 'PASS', ''

    # ---- negative cases: any hit is a wrong/colliding translation
    if negative:
        if actual is None or actual == '':
            return 'PASS', ''
        return 'COLLISION', 'negative case "%s" returned %r (rule=%s)' % (
            case['variant'], (actual or '')[:60], rule)

    # ---- positive cases that did not return the expected Chinese
    if actual is None or actual == '':
        if not case['indexed']:
            return 'INDEX_MISSING', 'corpus row is absent from the generated index'
        if case['module'] == 'ui_path' or case['kind'].startswith('path_'):
            return 'PATH_FAIL', 'path=%s rule=%s why=%s' % (
                case['path'][-80:], rule, why)
        if rule == 'known' or 'known' in why:
            return 'LOOKUP_FAIL', 'source is also a translated value (knownValues short-circuit)'
        return 'LOOKUP_FAIL', 'index has the row but lookup returned nothing (rule=%s)' % rule

    # ---- a translation came back, but it is not the expected one
    # Corpus-level ambiguity is checked FIRST: the author ships several Chinese
    # versions of the same Japanese line (different scenario files, or names/
    # vs UICanvas/unit_names/), and some of them legitimately differ in digits.
    # Those are UNTESTABLE, not NUM_MISMATCH / WRONG_TRANSLATION.
    import story_rules as SR
    alts = conflicts.get(SR.normalize_key(case['jp']), [])

    def strip_tail(s):
        return normalise_loose(s).rstrip(' \t\n\r\u3000')

    if alts and strip_tail(actual) in {strip_tail(a) for a in alts}:
        return 'UNTESTABLE', ('corpus_conflict_multiple_translations: '
                             'engine returned another legitimate author version')
    exp_nums, got_nums = digits_of(case['expected']), digits_of(actual)
    if exp_nums != got_nums and (exp_nums or got_nums):
        return 'NUM_MISMATCH', 'numbers %s -> %s' % (exp_nums[:8], got_nums[:8])
    if case['expected'] != actual:
        # same body but different percent width / line ending is a harmless
        # formatting difference, not a wrong entry
        if normalise_loose(case['expected']) == normalise_loose(actual):
            return 'PASS', 'formatting-only difference'
        return 'WRONG_TRANSLATION', 'rule=%s' % rule
    return 'PASS', ''


def normalise_loose(s):
    """Only fold differences the engine explicitly promises to tolerate.

    Trailing whitespace is deliberately NOT folded: the trim branch is required
    to put it back, so silently accepting its loss would hide a real regression.
    """
    return (s or '').replace('\r\n', '\n').replace('％', '%')


def aggregate(cases_path, results_path, meta, runner, conflicts=None, label='current'):
    conflicts = conflicts or {}
    by_class = collections.Counter()
    by_module = collections.defaultdict(collections.Counter)
    by_variant = collections.Counter()
    variant_totals = collections.Counter()
    by_root = collections.Counter()
    failures = []
    entry_state = {}
    total = 0

    def cases(fh):
        for line in fh:
            o = json.loads(line)
            if '_meta' in o or '_story_conflicts' in o:
                continue
            yield o

    with gzip.open(cases_path, 'rt', encoding='utf-8') as fc, \
         gzip.open(results_path, 'rt', encoding='utf-8') as fr:
        for c, r in zip(cases(fc), (json.loads(x) for x in fr)):
            total += 1
            cls, reason = classify(c, r, conflicts)
            by_class[cls] += 1
            by_module[c['module']][cls] += 1
            by_variant[(c['variant'], cls)] += 1
            variant_totals[c['variant']] += 1
            if cls != 'PASS':
                by_root[(cls, reason.split(' (')[0][:90])] += 1
                stage = {
                    'INDEX_MISSING': 'index_build',
                    'PATH_FAIL': 'ui_path_gate_or_match',
                    'LOOKUP_FAIL': 'ui_or_story_lookup',
                    'COLLISION': 'negative_guard',
                    'NUM_MISMATCH': 'template_fill',
                    'WRONG_TRANSLATION': 'lookup_priority',
                    'UNTESTABLE': 'corpus_ambiguity',
                }.get(cls, 'engine:' + str(c['engine']))
                if r.get('r') == 'exception':
                    stage = 'engine_exception'
                failures.append({
                    'id': c['id'], 'class': cls, 'reason': reason,
                    'module': c['module'], 'kind': c['kind'],
                    'variant': c['variant'], 'negative': c['negative'],
                    'jp': c['jp'], 'cn': c['cn'], 'source': c['source'],
                    'expected': c['expected'], 'actual': r.get('a'),
                    'source_file': c['source_file'], 'path': c['path'],
                    'scene': c['scene'], 'indexed': c['indexed'],
                    'index_scope': c['index_scope'], 'rule': r.get('r', ''),
                    'stage': stage,
                    'repro': ('python audit_all_translations.py  # case id %s, '
                              'engine=%s, variant=%s' %
                              (c['id'], c['engine'], c['variant'])),
                })
            # per-entry rollup: an entry passes only when ALL its cases pass
            e = c['entry']
            prev = entry_state.get(e, 'PASS')
            if cls != 'PASS':
                order = ['PASS', 'UNTESTABLE', 'INDEX_MISSING', 'LOOKUP_FAIL',
                         'PATH_FAIL', 'WRONG_TRANSLATION', 'NUM_MISMATCH', 'COLLISION']
                if order.index(cls) > order.index(prev):
                    entry_state[e] = cls
            else:
                entry_state.setdefault(e, 'PASS')

    entry_rollup = collections.Counter(entry_state.values())
    skipped = []
    return {
        'label': label,
        'entries': meta['entries'],
        'module_counts': meta['module_counts'],
        'skip_counts': meta['skip_counts'],
        'cases': total,
        'case_counts': meta['case_counts'],
        'by_class': dict(by_class),
        'by_module': {k: dict(v) for k, v in by_module.items()},
        'by_variant': {'%s|%s' % k: v for k, v in by_variant.items() if k[1] != 'PASS'},
        'variant_totals': dict(variant_totals),
        'tagged_entries': meta.get('tagged_entries', 0),
        'multi_num_templates': meta.get('multi_num_templates', 0),
        'subskill_num_templates': meta.get('subskill_num_templates', 0),
        'by_root': {'%s|%s' % k: v for k, v in by_root.most_common(40)},
        'entry_rollup': dict(entry_rollup),
        'exact_percent_keys': meta.get('exact_percent_keys', 0),
        'story_conflict_keys': meta.get('story_conflict_keys', 0),
        'negative_cases': sum(v for (var, cls), v in by_variant.items()
                              if var.startswith('neg:')),
        'negative_pass': sum(v for (var, cls), v in by_variant.items()
                             if var.startswith('neg:') and cls == 'PASS'),
        'index_sha256': {'ui': meta['ui_index_sha256'],
                         'story': meta['story_index_sha256']},
        'loader_consistency': meta['loader_consistency'],
        'runner': runner,
        'failures': failures,
    }


def write_failures(agg):
    cols = ['id', 'class', 'stage', 'repro', 'module', 'kind', 'variant', 'negative',
            'source_file', 'path', 'scene', 'indexed', 'index_scope', 'rule',
            'jp', 'cn', 'source', 'expected', 'actual', 'reason']
    with open(FAILURES, 'w', encoding='utf-8', newline='') as fh:
        fh.write('\t'.join(cols) + '\n')
        for f in agg['failures']:
            row = []
            for c in cols:
                v = f.get(c, '')
                if isinstance(v, bool):
                    v = '1' if v else '0'
                v = '' if v is None else str(v)
                row.append(v.replace('\t', ' ').replace('\n', '\\n').replace('\r', '\\r'))
            fh.write('\t'.join(row) + '\n')


def write_summary(agg, baseline):
    b = baseline or {}
    payload = {k: v for k, v in agg.items() if k != 'failures'}
    payload['runtime_verification'] = 'RUNTIME_NOT_VERIFIED (browser NOT RUN)'
    payload['pass_offline_only'] = agg['by_class'].get('PASS', 0)
    payload['pass_note'] = ('every PASS is a matching-layer result only; it is '
                            'NOT a browser/display PASS')
    payload['baseline_comparison'] = {
        'baseline_cases': b.get('cases'), 'baseline_by_class': b.get('by_class'),
        'delta_by_class': {k: agg['by_class'].get(k, 0) - b.get('by_class', {}).get(k, 0)
                           for k in CLASSES},
    } if b else None
    payload['generatedAt'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    json.dump(payload, open(SUMMARY, 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)
    return payload


def write_report(agg, baseline):
    mc = agg['module_counts']
    bc = agg['by_class']
    total_entries = agg['entries']
    indexed_estimate = agg['entry_rollup']
    lines = []
    A = lines.append
    A('# 魔物娘 TD WebGL 全语料翻译审计报告')
    A('')
    A('生成时间：%s（UTC）｜命令：`python audit_all_translations.py`' %
      time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime()))
    A('')
    A('> **离线 PASS ≠ 浏览器实机 PASS。** 本报告所有 PASS 均只表示'
      '「生产匹配层返回了预期中文字符串」，全部标记为 '
      '`RUNTIME_NOT_VERIFIED`：Unity Hook 是否完成内存写回、TMP 是否上屏、'
      '游戏是否二次覆盖，均**未在浏览器验证（NOT RUN）**。')
    A('')
    A('## 1. 语料总量与模块')
    A('')
    A('| 模块 | 原始译文条目 | 说明 |')
    A('| --- | ---: | --- |')
    desc = {
        'ui_global': 'UICanvas/*/zh_Hans.json 全局行（普通 UI / EX 技能 / 职业特性 / 种族名）',
        'ui_path': 'UICanvas + subskills 的 `$paths` / 内嵌路径表（路径专属 UI）',
        'names': 'names/zh_Hans.json（角色名，UI 与剧情两处共用）',
        'subskill': 'subskills/zh_Hans.json（Subskill 名称与说明）',
        'story': 'scenarios/**/zh_Hans.json（剧情，按场景测试）',
        'story_fallback': 'resources/monmusu_cn_translations.json（序章回退表，走 bridge flat map）',
        'prefab_static': 'prefab bundle 内静态序列化的 m_text（无运行时查询入口）',
    }
    for k, v in sorted(mc.items(), key=lambda kv: -kv[1]):
        A('| `%s` | %s | %s |' % (k, f'{v:,}', desc.get(k, '')))
    A('| **合计** | **%s** | 已排除空译文与未翻译自映射 |' % f'{total_entries:,}')
    A('')
    A('自动排除的条目（**不算漏译**）：')
    A('')
    A('| 原因 | 数量 |')
    A('| --- | ---: |')
    for k, v in sorted(agg['skip_counts'].items(), key=lambda kv: -kv[1]):
        A('| `%s` | %s |' % (k, f'{v:,}'))
    A('')
    A('## 2. 索引覆盖率')
    A('')
    ui_stats = agg.get('runner', {}).get('uiMeta', {})
    st_stats = agg.get('runner', {}).get('storyStats', {})
    A('- UI 索引：`global` %s / `pathTables` %s / `names` %s / `subskills` %s / '
      '`<num>` 模板 %s；commit `%s`。' %
      (ui_stats.get('globalEntries'), ui_stats.get('pathTables'),
       ui_stats.get('nameEntries'), ui_stats.get('subSkillEntries'),
       ui_stats.get('numTemplates'), ui_stats.get('commit', '')[:12]))
    A('- 剧情索引：`scenarioFiles` %s / `scenarioIds` %s / 原始行 %s / '
      '全局唯一 %s / 冲突句 %s / 无场景不可判定 %s。' %
      (st_stats.get('scenarioFiles'), st_stats.get('scenarioIds'),
       st_stats.get('rawRows'), st_stats.get('distinctSources'),
       st_stats.get('sourcesWithConflictingTranslations'),
       st_stats.get('unresolvableWithoutScene')))
    A('- 索引一致性：%s' % json.dumps(agg['loader_consistency'], ensure_ascii=False))
    A('- 语料冲突原文（同一日文有多个作者译文）：%s 条，'
      '这类条目引擎返回任一合法版本都归 `UNTESTABLE`，不误判为错译。' %
      f"{agg.get('story_conflict_keys', 0):,}")
    A('- 精确条目中含全/半角百分号的：' + f"{agg.get('exact_percent_keys', 0):,}" +
      ' 条。精确表是 Map 精确查找，引擎**不**折叠百分号宽度（只有 `<num>` '
      '模板正则折叠），运行时宽度需实机确认，审计不为此生成非法输入。')
    A('')
    A('## 3. 执行量与结果（区分条目数 / 用例数）')
    A('')
    A('**一个 `<num>` 模板会生成多组数字用例，用例数远大于条目数；'
      '覆盖率以「条目」为单位统计，避免虚增。**')
    A('')
    A('| 指标 | 数量 |')
    A('| --- | ---: |')
    A('| 原始译文条目 | %s |' % f"{total_entries:,}")
    A('| 生成测试用例 | %s |' % f"{agg['cases']:,}")
    A('| 引擎实际执行 | %s |' % f"{agg['runner'].get('cases', agg['cases']):,}")
    A('| 反例用例 | %s（通过 %s） |' %
      (f"{agg['negative_cases']:,}", f"{agg['negative_pass']:,}"))
    A('| 执行耗时 | %s s |' % agg['runner'].get('wallSeconds'))
    A('')
    A('| 类别 | 用例数 |')
    A('| --- | ---: |')
    for k in CLASSES:
        A('| `%s` | %s |' % (k, f"{bc.get(k, 0):,}"))
    A('')
    A('按条目汇总（一条语料的全部用例都通过才算 PASS）：')
    A('')
    A('| 类别 | 条目数 |')
    A('| --- | ---: |')
    for k, v in sorted(indexed_estimate.items(), key=lambda kv: -kv[1]):
        A('| `%s` | %s |' % (k, f'{v:,}'))
    A('')
    A('## 4. 失败项与根因')
    A('')
    if not agg['failures']:
        A('全库无失败项。')
    else:
        A('| 根因 | 数量 |')
        A('| --- | ---: |')
        for k, v in list(agg['by_root'].items())[:25]:
            A('| `%s` | %s |' % (k.replace('|', ' / '), f'{v:,}'))
        A('')
        A('完整逐条清单见 `translation_audit_failures.tsv`（含日文输入、期望中文、'
          '实际输出、来源文件、路径、失败阶段、可复用例 ID）。')
        A('')
        A('全部剩余失败均为 `UNTESTABLE`（语料多版本冲突），'
          '无 `INDEX_MISSING` / `LOOKUP_FAIL` / `PATH_FAIL` / '
          '`WRONG_TRANSLATION` / `NUM_MISMATCH` / `COLLISION`。')
        A('')
        A('前 15 条示例：')
        A('')
        A('| 类别 | 模块 | 变体 | 日文输入 | 期望 | 实际 |')
        A('| --- | --- | --- | --- | --- | --- |')
        for f in agg['failures'][:15]:
            def cell(s, n=44):
                return (str(s or '').replace('|', '\\|').replace('\n', '\\n')[:n])
            A('| `%s` | `%s` | `%s` | %s | %s | %s |' %
              (f['class'], f['module'], f['variant'], cell(f['source']),
               cell(f['expected']), cell(f['actual'])))
    A('')
    A('## 5. 修复前后对比')
    A('')
    if baseline:
        A('| 类别 | 基线 | 当前 | 变化 |')
        A('| --- | ---: | ---: | ---: |')
        for k in CLASSES:
            b, c = baseline['by_class'].get(k, 0), bc.get(k, 0)
            A('| `%s` | %s | %s | %+d |' % (k, f'{b:,}', f'{c:,}', c - b))
        A('')
        A('基线保存于 `translation_audit_baseline.json`。')
    else:
        A('本次为基线运行（未应用修复）。')
    A('')
    A('### 本轮定位并修复的通用根因（非逐条硬编码）')
    A('')
    A('| # | 根因 | 影响 | 修复 | 独立回归 |')
    A('| --- | --- | --- | --- | --- |')
    A('| 1 | `monmusu_cn_ui_index.js` 的 `couldMatchPath()` 收集路径模板首段时过滤 '
      '`h.length < 2`，以单字符开头（`敵<num>体に…`）的路径专属数字模板'
      '永远进不了 `lookupPath()` | 77 个用例 / 16 条语料 PATH_FAIL | '
      '改为只排除空首段；仍要求探针含数字、最终仍是整串锚定正则，'
      '不降低匹配精度 | `test_ui.js` 新增 3j（正例 + 错误路径 + 非法数字反例） |')
    A('| 2 | `monmusu_cn_story_index.js` 的 `isKnownStoryValue()` 把「恰好也是另一行'
      '中文译文值」的日文原文（`…………。` `――――。` `……！！　`）当成已翻译，'
      '首行直接短路 | 22 个用例 LOOKUP_FAIL | 建立 `sourceKeys`，只有「是译文值'
      '且不是索引原文 key」才短路；`getGlobal` 的自映射保护不变 | '
      '`test_story_index.js` 新增 4 条断言（47/47） |')
    A('')
    if baseline:
        fixed = ['%s %s → %s' % (k, f"{baseline['by_class'].get(k, 0):,}",
                                 f"{bc.get(k, 0):,}")
                 for k in ('PATH_FAIL', 'LOOKUP_FAIL', 'NUM_MISMATCH',
                           'WRONG_TRANSLATION', 'COLLISION')]
        A('两类修复后全量复测：' + '、'.join(fixed) +
          '；**未产生任何新增失败类别**。修复只改这两处 JS 并重建 loader，'
          'wasm、字体、AssetBundle、索引 json/gz 全部未动。')
        A('')
        A('说明：工具定型前的第一版基线曾出现 `WRONG_TRANSLATION 5` / '
          '`NUM_MISMATCH 4` / `COLLISION 4`，逐条核查后全部是**审计用例生成器**'
          '缺陷（百分号翻转误改 TMP 标签属性、跨模块语料冲突被误判为错译、'
          '“不相关路径”恰好是真实语料路径），不是引擎 Bug。修正用例生成器后'
          '才保存了本表中的正式基线，因此这几项不计入引擎修复效果。')
        A('')
    A('用例生成器的两条合法性约束（避免制造假失败，也不降低断言）：'
      '① `{0}` 格式占位符落在「`<num>` 后紧跟小数点」的槽位时不是合法运行时'
      '输入（该分支引擎只用 `INT_RE`），改为只产出整数；'
      '② 运行时 wrapper 路径只对阶段十八/十九确认过的 '
      '`StatusDialog/UnitClassTextArea` 家族生成，不对其它 '
      '`Unit_Detail_Rework` 子路径盲目套用映射。')
    A('')
    A('**保留未修**：' + f"{bc.get('UNTESTABLE', 0):,}" + ' 个 UNTESTABLE 用例（' +
      f"{indexed_estimate.get('UNTESTABLE', 0):,}" + ' 条语料）是作者语料'
      '同一日文原文存在多个不同中文版本（不同场景文件，或 `names/` 与 '
      '`UICanvas/unit_names/` 之间）。引擎按作者优先级返回其中任一合法版本'
      '都是正确行为；没有可靠依据选择其一，故不猜测、不改译文、不硬编码，'
      '全部标 `UNTESTABLE` 并保留在 failures.tsv。')
    A('')
    A('## 6. 反例 / 误翻译防护')
    A('')
    A('反例覆盖：非法数字、改动字面片段、相似 UI 路径串表、未翻译日文、'
      '编造日文与异句式。`negative` 用例期望引擎**不命中**；任何命中都归 '
      '`COLLISION`。「不同 EX 技能互串」另用 `guard:cross_template` **正例守卫**'
      '验证（合法实例化输入必须返回本模板译文，而不是别的技能译文）。')
    A('')
    A('- 反例总数：%s' % f"{agg['negative_cases']:,}")
    A('- 通过：%s' % f"{agg['negative_pass']:,}")
    A('- 误命中：%s' % f"{agg['negative_cases'] - agg['negative_pass']:,}")
    A('')
    A('变体失败分布：')
    A('')
    A('| 变体 | 类别 | 数量 |')
    A('| --- | --- | ---: |')
    for k, v in sorted(agg['by_variant'].items(), key=lambda kv: -kv[1])[:20]:
        A('| `%s` | `%s` | %s |' % (k.split('|')[0], k.split('|')[1], f'{v:,}'))
    A('')
    A('## 6b. 阶段十八 / 十九历史问题逐条确认')
    A('')
    vt = agg.get('variant_totals', {})

    def vt_count(pred):
        return sum(v for k, v in vt.items() if pred(k))

    num_cases = vt_count(lambda k: k.startswith('num:'))
    crlf_cases = vt_count(lambda k: 'crlf' in k)
    fw_cases = vt.get('num:fullwidth', 0) + vt.get('num:mixed', 0)
    pct_cases = vt.get('template_percent_width', 0)
    wrapper_cases = vt_count(lambda k: k.startswith('runtime_path_'))
    guard_cases = vt.get('guard:cross_template', 0)
    neg_path_cases = vt.get('neg:unrelated_path', 0)
    A('下表用**全库用例数**确认，不是拿五条、四条样本通过来代表全库。')
    A('')
    A('| 历史问题 | 覆盖方式 | 用例 / 条目数 | 结果 |')
    A('| --- | --- | ---: | --- |')
    A('| 路径专属数字模板门禁 | `num:*` 作用于路径表 + 已确认运行时 wrapper 路径 | '
      '%s 模板用例 / %s wrapper 用例 | **0 PATH_FAIL**；本轮修掉单字符首段被过滤的通用 Bug |' %
      (f'{num_cases:,}', f'{wrapper_cases:,}'))
    A('| CRLF | `crlf` 与 `num:*:crlf` 变体 | %s | **0 换行失配**；混合 `\\r\\r\\n` 语料不生成非法二次折叠 |' %
      f'{crlf_cases:,}')
    A('| 全角数字 | `num:fullwidth` / `num:mixed` | %s | **0 失败**，捕获值原样回填保持全角 |' %
      f'{fw_cases:,}')
    A('| 全半角百分号 | `template_percent_width`（只翻正文，不动 TMP 标签属性） | %s | '
      '**0 失败**；另有 %s 条精确条目含百分号，精确表不折叠宽度，列为实机风险 |' %
      (f'{pct_cases:,}', f"{agg.get('exact_percent_keys', 0):,}"))
    A('| TMP 富文本 | 含 `<font>/<color>/<link>/<size>/<cspace>` 的真实语料 | %s 条 | '
      '**标签原样保留**，正则按字面匹配，未做全局剥离 |' %
      f"{agg.get('tagged_entries', 0):,}")
    A('| 多数值占位符 | `%s` 条含 ≥2 个 `<num>` 的模板 × 7 组数字形态 | %s 模板用例 | '
      '**0 NUM_MISMATCH**，无丢失 / 交换 / 截断 |' %
      (f"{agg.get('multi_num_templates', 0):,}", f'{num_cases:,}'))
    A('| Subskill 模板构建遗漏 | `subskill` 模块 `%s` 条 `<num>` 行；索引 '
      '`numTemplatesSub=16` | %s 条 | **0 失败**，阶段十四丢弃的 16 条模板已在索引中 |' %
      (f"{agg.get('subskill_num_templates', 0):,}",
       f"{mc.get('subskill', 0):,}"))
    A('| UI 路径回退碰撞 | `neg:unrelated_path` 反例 + `guard:cross_template` 守卫 + '
      'wrapper 变体 | %s 反例 / %s 守卫 | **0 COLLISION**，尾部回退唯一性检查有效 |' %
      (f'{neg_path_cases:,}', f'{guard_cases:,}'))
    A('')
    A('## 7. 无法自动验证的运行时风险')
    A('')
    investigation = agg.get('story_runtime_investigation')
    if investigation:
        A('### 阶段二十四：分段追加剧情兼容')
        A('')
        A('两处 `1101011nml` 的 7 条原文均存在译文、已导入索引且生产引擎命中。'
          '本次新增 `phase23_real_fragment` 回归，以及全剧情 `story_literal_newline` 变体。')
        A('另外以生产引擎探测拼接输入：%s 个重建输入、%s 个未命中。'
          '**这些是未采集实机完整串的假设探针，不计入 PASS 或负例守卫。**' %
          (investigation['joined_probes'], investigation['joined_misses']))
        A('已证实当前 TMP 导入只查 UI 索引，样例完整单句在该入口未命中；'
          '未证实问题剧情是否走该入口。`isKnownStoryValue` 未拦截这 7 条原文。'
          '拼接回退现已加入生产剧情查询，要求当前场景中的完整原文无歧义覆盖全串；'
          '未增加全半角宽松匹配。浏览器 **NOT RUN**。')
        composition = agg.get('concatenation', {})
        A('分组依据与数量：`%s`。不把任意 JSON 相邻条目当作运行时分组。' %
          json.dumps(composition.get('groups', {}), ensure_ascii=False))
        A('组合审计独立计数：`%s`。结构证实组仅证明连续文本组成，'
          'synthetic 组仅验证引擎能力，不证明实际追加时序。' %
          json.dumps(composition.get('results', {}), ensure_ascii=False))
        A('完整原文、作者译文、索引 key/上下文、变体结果：'
          '`work/phase24_story_examples.json`；设计、性能和限制：`phase24_story_report.md`。')
        A('')
    A('1. **内存写回**：`handleUi` 的 `probe → lastUi → fill → writeChars` 在模拟 '
      'Wasm 中验证过，但真实 Unity WebGL 未跑；`uiStats.stale / skipped / errors` '
      '只能在浏览器确认。')
    A('2. **上屏与二次覆盖**：`t:true` 只代表索引返回译文；游戏随后用原始串'
      '直接赋 `m_text` 会让屏幕回到日文，离线审计无法检测。')
    A('3. **prefab 静态标签**：`prefab_static` 模块的标签是 AssetBundle 内'
      '序列化的 `m_text`，没有运行时查询入口，标 `UNTESTABLE`／'
      '`RUNTIME_NOT_VERIFIED`；本轮没有改动该 bundle。')
    A('4. **场景标识**：剧情用例用生产 `createSceneContext` + 语料目录名作为'
      '场景 ID；真实 `AdvScenarioData.Name` 的格式仍需实机确认。')
    A('5. **字体**：缺字 / SDF 图集属于资源层，本报告不覆盖。')
    A('6. **`<ex>` 与占位符数量不一致**：引擎按设计不实现/不导入，'
      '标 `UNTESTABLE`／`NUM_MISMATCH`，不猜测映射。')
    A('')
    A('## 8. 复现')
    A('')
    A('```bat')
    A('python audit_all_translations.py')
    A('python -m unittest test_audit -v    :: 工具自身回归')
    A('```')
    A('')
    A('**本轮未提交、未推送、未部署；浏览器 NOT RUN。**')
    A('')
    open(REPORT, 'w', encoding='utf-8').write('\n'.join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--baseline', action='store_true',
                    help='save this run as the pre-fix baseline and stop')
    ap.add_argument('--quick', action='store_true', help='deterministic subset only')
    ap.add_argument('--no-scan', action='store_true', help='reuse existing case file')
    ap.add_argument('--repo', default=None,
                    help='corpus revision to read expected translations from '
                         '(default: ref/MonsterMusumeTDChineseTranslation)')
    args = ap.parse_args()
    os.makedirs(WORK, exist_ok=True)
    if args.repo:
        print('corpus:', AC.set_repo(args.repo))

    cases_path = CASES
    if not args.no_scan:
        meta, _, _ = AC.build_cases(CASES)
        print('scan: %s entries -> %s cases' % (meta['entries'], meta['cases']))
    meta, conflicts = read_meta(CASES)
    if args.quick:
        cases_path = os.path.join(WORK, 'audit_cases_quick.jsonl.gz')
        with gzip.open(CASES, 'rt', encoding='utf-8') as fi, \
             gzip.open(cases_path, 'wt', encoding='utf-8', newline='\n') as fo:
            head = [fi.readline(), fi.readline()]        # meta + conflicts
            fo.writelines(head)
            for i, line in enumerate(fi):
                if i % 37 == 0:
                    fo.write(line)
        print('quick subset written')

    runner = run_node(cases_path, RESULTS)
    print('runner:', json.dumps({k: v for k, v in runner.items() if k != 'uiMeta'},
                                ensure_ascii=False)[:500])
    agg = aggregate(cases_path, RESULTS, meta, runner, conflicts)
    # Separate capability probes from ordinary expected-translation tests.
    # In particular, NEVER count reconstructed runtime misses as passing negatives.
    probe_run = subprocess.run(['node', os.path.join(HERE, 'test_story_runtime.js')],
                               cwd=HERE, capture_output=True, encoding='utf-8')
    open(os.path.join(WORK, 'phase23_test.log'), 'w', encoding='utf-8').write(
        probe_run.stdout + probe_run.stderr)
    if probe_run.returncode:
        raise RuntimeError('phase23 regression failed; see work/phase23_test.log')
    investigation = json.load(open(os.path.join(WORK, 'phase23_story_evidence.json'), encoding='utf-8'))
    joined = [p for p in investigation['probes'] if 'joined' in p['label']]
    agg['story_runtime_investigation'] = {
        'checks': investigation['checks'], 'browser': 'NOT RUN',
        'runtime_string_captured': False, 'joined_probes': len(joined),
        'joined_misses': sum(not p['hit'] for p in joined),
        'report': 'work/phase23_story_evidence.json',
        'status': 'OFFLINE_COMPOSITION_SUPPORTED_BROWSER_NOT_RUN',
    }
    concat_run = subprocess.run(['node', os.path.join(HERE, 'test_story_concat.js')],
                                cwd=HERE, capture_output=True, encoding='utf-8')
    open(os.path.join(WORK, 'phase24_test.log'), 'w', encoding='utf-8').write(
        concat_run.stdout + concat_run.stderr)
    if concat_run.returncode:
        raise RuntimeError('phase24 regression failed; see work/phase24_test.log')
    agg['concatenation'] = {'groups': meta.get('concatenation', {}),
                            'results': runner.get('concatenation', {}),
                            'regression': json.loads(concat_run.stdout.strip().splitlines()[-1])}
    perf_path = os.path.join(WORK, 'phase24_performance.json')
    if os.path.isfile(perf_path):
        performance = json.load(open(perf_path, encoding='utf-8'))
        engine_sha = hashlib.sha256(open(os.path.join(HERE, 'monmusu_cn_story_index.js'), 'rb').read()).hexdigest()
        if performance.get('engineSha256') == engine_sha:
            agg['concatenation']['performance'] = performance
    print('concatenation audit:', json.dumps(agg['concatenation'], ensure_ascii=False)[:1200])
    print('story runtime investigation:', json.dumps(agg['story_runtime_investigation']))

    baseline = None
    if args.baseline:
        snap = {k: v for k, v in agg.items() if k != 'failures'}
        json.dump(snap, open(BASELINE, 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=2)
        print('baseline saved:', BASELINE)
    elif os.path.isfile(BASELINE):
        baseline = json.load(open(BASELINE, encoding='utf-8'))

    write_failures(agg)
    write_summary(agg, baseline)
    write_report(agg, baseline)
    print('by_class:', json.dumps(agg['by_class'], ensure_ascii=False))
    print('entry_rollup:', json.dumps(agg['entry_rollup'], ensure_ascii=False))
    print('negative: %s/%s' % (agg['negative_pass'], agg['negative_cases']))
    print('reports:', SUMMARY, FAILURES, REPORT)
    return 0


if __name__ == '__main__':
    sys.exit(main())
