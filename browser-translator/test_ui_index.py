"""Build-side tests for build_ui_index.py.

Covers what test_ui.js cannot: that the *builder* picks up new data without any
code change.  Each case builds the index from a throw-away copy of the corpus
with one row injected, so "just rebuild" is proven end to end.

    python test_ui_index.py
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build_ui_index as B  # noqa: E402

REPO = os.path.join(HERE, 'ref', 'MonsterMusumeTDChineseTranslation')
INDEX = os.path.join(HERE, 'resources', 'monmusu_cn_ui_index.json')
FILES = ['UICanvas/quest_text/zh_Hans.json', 'UICanvas/ui_text/zh_Hans.json',
         'UICanvas/unit_names/zh_Hans.json', 'UICanvas/unit_skill/zh_Hans.json',
         'UICanvas/unit_speech/zh_Hans.json', 'UICanvas/unit_weapon/zh_Hans.json',
         'names/zh_Hans.json', 'subskills/zh_Hans.json']

fails = 0


def ok(msg):
    print('  ok   ' + msg)


def bad(msg):
    global fails
    print('  FAIL ' + msg)
    fails += 1


def eq(got, want, msg):
    (ok if got == want else bad)('%s = %r' % (msg, got) if got == want
                                 else '%s: got %r want %r' % (msg, got, want))


def make_repo(tmp):
    """Copy the files the UI builder reads, with a rewritten manifest."""
    man = json.load(open(os.path.join(REPO, 'translations', 'manifest.json'),
                         encoding='utf-8'))
    for rel in FILES:
        dst = os.path.join(tmp, 'translations', rel.replace('/', os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(os.path.join(REPO, 'translations', rel.replace('/', os.sep)), dst)
    json.dump(man, open(os.path.join(tmp, 'translations', 'manifest.json'), 'w',
                        encoding='utf-8'), ensure_ascii=False)
    return man


def refresh_manifest(tmp, man, rel):
    raw = open(os.path.join(tmp, 'translations', rel.replace('/', os.sep)), 'rb').read()
    man['files'][rel] = {'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)}
    json.dump(man, open(os.path.join(tmp, 'translations', 'manifest.json'), 'w',
                        encoding='utf-8'), ensure_ascii=False)


def build(tmp, name='out.json'):
    out = os.path.join(tmp, name)
    doc, encoded = B.build(tmp, out, check_only=True)
    return doc


def templates(doc):
    S = doc['strings']
    return {S[a]: S[b] for a, b in doc['numTemplates']}


def main():
    print('1) the committed index is self-consistent')
    doc = json.load(open(INDEX, encoding='utf-8'))
    eq(len(doc['numTemplates']), doc['stats']['numTemplatesGlobal'],
       'numTemplates rows == stats')
    eq(len(doc['numTemplatesSub']), doc['stats']['numTemplatesSub'],
       'sub-skill template rows == stats')
    path_total = sum(len(t.get('num') or []) for t in doc['pathTables'])
    eq(path_total, doc['stats']['numTemplatesPath'], 'path template rows == stats')
    eq(len(doc['subskills']), doc['stats']['subSkillEntries'], 'sub-skill rows == stats')
    eq(len(doc['subSkillConflicts']), doc['stats']['subSkillConflicts'],
       'reported conflicts == stats')
    eq(doc['stats']['numTemplatesSkipped'], 6,
       'placeholder-count mismatches are skipped')
    # stage 18: sub-skill rows with <num> used to be dropped before the template
    # collector ran, so numTemplatesSub was 0 and no numeric sub-skill
    # description could ever match.  Both sides were 0, so the equality above
    # passed vacuously - assert the table is actually populated.
    eq(len(doc['numTemplatesSub']) > 0, True,
       'sub-skill <num> rows reach the template table')
    S0 = doc['strings']
    sub_tpl = {S0[a]: S0[b] for a, b in doc['numTemplatesSub']}
    eq('自身が<num>％の確率で魔法攻撃を回避' in sub_tpl, True,
       'the ether template is collected')
    eq('自身がスキル発動時、全敵を高確率で毒状態にする\n配置中、全味方に毒状態の敵へ'
       '与えるダメージ<num>倍を付与（継続ダメージは適用されない）' in sub_tpl, True,
       'the poison template is collected')
    eq(any('<num>' in S0[a] for a, _ in doc['subskills']), False,
       'template rows are not duplicated into the exact sub-skill table')
    tpl = templates(doc)
    for key in ('攻撃力+<num>％', '<num>秒、攻撃力<num>倍'):
        if any(k.startswith(key.split('<num>')[0]) for k in tpl):
            ok('template present: %r' % key)
        else:
            bad('template missing: %r' % key)

    print('\n2) a new <num> row needs only a rebuild')
    tmp = tempfile.mkdtemp(prefix='bpui-')
    try:
        man = make_repo(tmp)
        base = build(tmp, 'a.json')
        n0 = len(base['numTemplates'])
        rel = 'UICanvas/ui_text/zh_Hans.json'
        path = os.path.join(tmp, 'translations', rel.replace('/', os.sep))
        ui = json.load(open(path, encoding='utf-8'))
        ui['検証用の攻撃力+<num>％'] = '验证用攻击力+<num>%'
        json.dump(ui, open(path, 'w', encoding='utf-8'), ensure_ascii=False)
        refresh_manifest(tmp, man, rel)
        after = build(tmp, 'b.json')
        eq(len(after['numTemplates']), n0 + 1, 'one more template after rebuild')
        eq(templates(after).get('検証用の攻撃力+<num>％'), '验证用攻击力+<num>%',
           'the new row is served')

        print('\n3) rows that must NOT become templates')
        ui['件数違い+<num>+<num>'] = '数量不符+<num>'          # 2 vs 1 placeholders
        ui['タグだけ<cspace=-<num>>テスト'] = '只有标签测试'      # <num> inside a tag
        ui['<ex>任意</ex>+<num>'] = '任意<num>'                 # <ex> engine, out of scope
        json.dump(ui, open(path, 'w', encoding='utf-8'), ensure_ascii=False)
        refresh_manifest(tmp, man, rel)
        after2 = build(tmp, 'c.json')
        tpl2 = templates(after2)
        eq('件数違い+<num>+<num>' in tpl2, False, 'placeholder mismatch skipped')
        eq('タグだけ<cspace=-<num>>テスト' in tpl2, False, 'tag-only <num> is not a template')
        eq('<ex>任意</ex>+<num>' in tpl2, False, '<ex> rows are out of scope')
        eq(len(after2['numTemplates']), n0 + 1, 'neither of them added a template')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print('')
    if fails:
        print('%d check(s) FAILED' % fails)
        sys.exit(1)
    print('UI INDEX BUILD TEST PASSED')


if __name__ == '__main__':
    main()
