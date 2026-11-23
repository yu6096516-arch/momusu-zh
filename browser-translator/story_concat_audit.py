"""Composition inputs with explicit provenance; JSON adjacency is never evidence.

Structural groups must spell an existing full source AND its authored translation
exactly. Synthetic samples from other files test the engine only, not game timing.
"""
from collections import defaultdict
from functools import lru_cache
import re
import story_rules as SR

KANA = re.compile('[\u3040-\u30ff\uff66-\uff9f]')


def discover_groups(entries):
    files = defaultdict(dict)
    for e in entries:
        if e['module'] == 'story' and not e['skip']:
            k, v = SR.normalize_key(e['jp']), SR.normalize_key(e['cn'])
            if 3 <= len(k) <= 4096 and KANA.search(k) and not k[0].isspace():
                files[(e['source_file'], e['scope'])][k] = (v, e)
    groups = []
    main = next((d for (f, s), d in files.items() if s == '1101011nml'), {})
    for starts in [('自由な少年との', 'リン・クロミに加え', 'いやいやながら幼馴染み'),
                   ('違う違う違う、', '私は！', 'ケンタウロス娘じゃ！', 'なーい！！')]:
        keys = [next(k for k in main if k.startswith(start)) for start in starts]
        groups.append(dict(kind='user-confirmed', keys=keys, values=[main[k][0] for k in keys],
                           entry=main[keys[0]][1], evidence='phase24 user-confirmed segmented append'))

    for (file, scene), data in sorted(files.items()):
        buckets = defaultdict(list)
        for k in data:
            buckets[k[0]].append(k)
        for full in data:
            visits = [0]
            @lru_cache(None)
            def split(pos):
                visits[0] += 1
                if visits[0] > 1024:
                    return []
                if pos == len(full):
                    return [()]
                out = []
                for key in buckets[full[pos]]:
                    if key == full or not full.startswith(key, pos):
                        continue
                    for tail in split(pos + len(key)):
                        if len(tail) < 16:
                            out.append((key,) + tail)
                        if len(out) > 1:
                            return out
                return out
            found = split(0)
            if (visits[0] <= 1024 and len(found) == 1 and len(found[0]) >= 2 and
                    ''.join(data[k][0] for k in found[0]) == data[full][0]):
                groups.append(dict(kind='structural', keys=list(found[0]),
                                   values=[data[k][0] for k in found[0]], entry=data[full][1],
                                   evidence='contiguous decomposition of existing full JP and CN entries'))

    synthetic = 0
    for (file, scene), data in sorted(files.items()):
        if scene == '1101011nml':
            continue
        keys = sorted(k for k in data if 20 <= len(k) <= 180 and '<' not in k and
                      not k[-1].isspace())
        if len(keys) < 2:
            continue
        a, b = keys[0], keys[-1]
        if a + b in data:
            continue
        groups.append(dict(kind='synthetic', keys=[a, b], values=[data[a][0], data[b][0]],
                           entry=data[a][1], evidence='constructed same-file input; NOT a verified runtime group'))
        synthetic += 1
        if synthetic >= 32:
            break
    return groups


def group_cases(groups):
    for gi, group in enumerate(groups):
        for count in range(1, len(group['keys']) + 1):
            for label, sep in [('direct', ''), ('lf', '\n'), ('space', ' '),
                               ('fullwidth', '\u3000'), ('mixed-crlf', ' \r\n\u3000')]:
                yield group, dict(source=sep.join(group['keys'][:count]),
                                  expected=sep.join(group['values'][:count]),
                                  variant='concat:%s:%d:%d:%s' % (group['kind'], gi, count, label))
