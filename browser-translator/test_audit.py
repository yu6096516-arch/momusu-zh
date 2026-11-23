"""Regression for the audit tooling itself (offline, no browser).

    python -m unittest test_audit -v
"""
import collections
import gzip
import json
import os
import tempfile
import unittest

import audit_all_translations as AA
import audit_corpus as AC


class TestCaseGenerator(unittest.TestCase):
    def test_percent_flip_never_touches_tags(self):
        s = '<indent=-6%><cspace=0>回復量15%上昇'
        out = AC.flip_percent_outside_tags(s)
        self.assertIn('<indent=-6%>', out)          # tag attribute untouched
        self.assertIn('回復量15％上昇', out)         # prose flipped
        self.assertIsNone(AC.flip_percent_outside_tags('<indent=-6%>选项'))

    def test_identity_rows_are_not_misses(self):
        self.assertTrue(AC.is_identity('HP', 'HP'))
        self.assertTrue(AC.is_identity('a\\r\\nb', 'a\nb'))
        self.assertFalse(AC.is_identity('確認', '确认'))

    def test_runtime_path_variants_are_scoped(self):
        p = AC.DETAIL_PREFIX + 'Main/UI/StatusDialog/Text'
        outs = [fn(p) for fn in AC.DETAIL_VARIANTS]
        self.assertTrue(outs[0].startswith('WindowPageStockObject/'))
        self.assertIn('UI_Com_Scene_LeftIn_Center/Control/', outs[1])
        self.assertNotIn(AC.UNRELATED_PATH.split('/')[0], p)

    def test_loader_uses_the_tested_indices(self):
        checks = AC.loader_consistency()
        for k, v in checks.items():
            if k.endswith('_embedded') or k.endswith('_wire_matches'):
                self.assertTrue(v, k)

    def test_number_sets_cover_required_formats(self):
        labels = {k for k, _ in AC.NUM_SETS}
        for need in ('ascii_int', 'decimal', 'fullwidth', 'signed', 'mixed',
                     'distinct_int', 'format_placeholder'):
            self.assertIn(need, labels)
        self.assertGreaterEqual(len(set(AC.NUM_SETS[1][1])), 5,
                                'distinct_int must use different values')


class TestFullScan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls.tmp.name, 'cases.jsonl.gz')
        cls.meta, cls.corpus, cls.cases = AC.build_cases(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_all_modules_discovered(self):
        mods = set(self.meta['module_counts'])
        for need in ('ui_global', 'ui_path', 'names', 'subskill', 'story',
                     'story_fallback', 'prefab_static'):
            self.assertIn(need, mods, need)
        self.assertGreater(self.meta['module_counts']['story'], 150000)

    def test_entries_vs_cases_are_reported_separately(self):
        # one <num> template generates many cases; never inflate coverage
        self.assertGreater(self.meta['cases'], self.meta['entries'])
        self.assertIn('cases', self.meta)
        self.assertIn('entries', self.meta)

    def test_no_template_placeholder_is_ever_a_runtime_input(self):
        bad = [c for c in self.cases if AC.NUM in c['source'] and
               not c['source'].startswith('(')]
        self.assertEqual(bad, [], '%d cases still feed <num> to the engine' % len(bad))

    def test_every_case_names_its_source_and_expectation(self):
        for c in self.cases:
            self.assertTrue(c['source_file'])
            self.assertIn(c['engine'], ('ui', 'story', 'fallback'))
            if c['expect_hit']:
                self.assertTrue(c['expected'], c['id'])
            else:
                self.assertTrue(c['negative'], c['id'])

    def test_untranslated_rows_are_excluded(self):
        skips = self.meta['skip_counts']
        self.assertGreater(skips.get('story:empty_translation', 0), 0)
        self.assertGreater(skips.get('story:untranslated_self_copy', 0) +
                           skips.get('story:identity_translation', 0), 0)

    def test_negative_cases_exist_and_expect_no_hit(self):
        negs = [c for c in self.cases if c['negative']]
        self.assertGreater(len(negs), 500)
        self.assertTrue(all(not c['expect_hit'] for c in negs))
        kinds = {c['variant'] for c in negs}
        for need in ('neg:illegal_number', 'neg:altered_literal',
                     'neg:unrelated_path', 'neg:untranslated', 'neg:fabricated'):
            self.assertIn(need, kinds, need)

    def test_case_file_is_streamable(self):
        with gzip.open(self.path, 'rt', encoding='utf-8') as fh:
            meta = json.loads(fh.readline())['_meta']
            conflicts = json.loads(fh.readline())['_story_conflicts']
            first = json.loads(fh.readline())
        self.assertEqual(meta['cases'], self.meta['cases'])
        self.assertIsInstance(conflicts, dict)
        self.assertIn('source', first)

    def test_phase23_fragments_and_general_newlines(self):
        real = [c for c in self.cases if c['variant'] == 'phase23_real_fragment']
        self.assertEqual(len(real), 7)
        self.assertTrue(all(c['scene'] == '1101011nml' and c['expect_hit'] for c in real))
        literal = [c for c in self.cases if c['variant'] == 'story_literal_newline']
        self.assertGreater(len(literal), 50000)
        self.assertTrue(all('\\n' in c['source'] for c in literal))

    def test_composition_groups_have_provenance(self):
        counts = self.meta['concatenation']
        self.assertEqual(counts['confirmed_user_groups'], 2)
        self.assertEqual(counts['structural_groups'], 3)
        self.assertEqual(counts['synthetic_groups'], 32)
        self.assertFalse(counts['json_adjacency_used'])
        cases = [c for c in self.cases if c['kind'] == 'story_concat']
        self.assertEqual(len(cases), counts['generated_cases'])
        self.assertTrue(all(c['scene'] and c['expect_hit'] for c in cases))


class TestClassification(unittest.TestCase):
    def base(self, **kw):
        c = {'negative': False, 'expected': '中文', 'jp': '原文', 'module': 'ui_global',
             'kind': 'exact', 'indexed': True, 'path': '', 'scene': '',
             'expect_hit': True, 'index_scope': 'ui.global',
             'variant': 'base', 'source': '原文', 'engine': 'ui'}
        c.update(kw)
        return c

    def test_pass(self):
        self.assertEqual(AA.classify(self.base(), {'ok': True}, {})[0], 'PASS')

    def test_index_missing(self):
        cls, why = AA.classify(self.base(indexed=False), {'ok': False, 'a': None}, {})
        self.assertEqual(cls, 'INDEX_MISSING')

    def test_lookup_fail(self):
        cls, _ = AA.classify(self.base(), {'ok': False, 'a': None, 'r': 'miss'}, {})
        self.assertEqual(cls, 'LOOKUP_FAIL')

    def test_ambiguous_composition_is_not_a_forced_translation(self):
        cls, _ = AA.classify(self.base(kind='story_concat'),
                             {'ok': False, 'a': None, 'w': 'concat-ambiguous-coverage'}, {})
        self.assertEqual(cls, 'UNTESTABLE')
        cls, _ = AA.classify(self.base(kind='story_concat'),
                             {'ok': False, 'a': None, 'w': 'incomplete-coverage'}, {})
        self.assertEqual(cls, 'LOOKUP_FAIL')

    def test_path_fail(self):
        cls, _ = AA.classify(self.base(module='ui_path', kind='path_exact',
                                       path='Canvas/Page/Text'),
                             {'ok': False, 'a': None, 'r': 'miss'}, {})
        self.assertEqual(cls, 'PATH_FAIL')

    def test_num_mismatch(self):
        cls, _ = AA.classify(self.base(expected='攻击力1.5倍'),
                             {'ok': False, 'a': '攻击力5.1倍'}, {})
        self.assertEqual(cls, 'NUM_MISMATCH')

    def test_wrong_translation(self):
        cls, _ = AA.classify(self.base(expected='确认'),
                             {'ok': False, 'a': '取消'}, {})
        self.assertEqual(cls, 'WRONG_TRANSLATION')

    def test_corpus_conflict_is_untestable_not_wrong(self):
        cls, why = AA.classify(
            self.base(module='story', expected='版本甲'),
            {'ok': False, 'a': '版本乙'},
            {'原文': ['版本甲', '版本乙']})
        self.assertEqual(cls, 'UNTESTABLE')
        self.assertIn('corpus_conflict', why)

    def test_negative_hit_is_collision(self):
        cls, _ = AA.classify(self.base(negative=True, expect_hit=False),
                             {'ok': False, 'a': '误译'}, {})
        self.assertEqual(cls, 'COLLISION')

    def test_formatting_difference_is_not_a_wrong_entry(self):
        cls, _ = AA.classify(self.base(expected='回复15%'),
                             {'ok': False, 'a': '回复15％'}, {})
        self.assertEqual(cls, 'PASS')

    def test_trailing_whitespace_loss_is_not_forgiven(self):
        cls, _ = AA.classify(self.base(expected='确认  '),
                             {'ok': False, 'a': '确认'}, {})
        self.assertNotEqual(cls, 'PASS')


if __name__ == '__main__':
    unittest.main(verbosity=2)
