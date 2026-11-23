"""Python port of Berumic's story matching rules (for the OFFLINE index builder).

Sources:
  Services/StoryTranslationIndex.cs
  Services/TranslationManager.cs (LoadScenariosCore, NormalizeSubSkillKey,
                                  NormalizeStoryDisplayValue, HasUtage*)
  Services/StorySceneContext.cs (candidate narrowing semantics)

The runtime JS uses the DERIVED index produced here; it does not re-implement
these construction rules.  Keeping them in one place avoids semantic drift.
"""
import re
from collections import defaultdict

UTAGE_CONTROL_TAG = re.compile(r'</?(?:interval|speed|sound)(?:=[^>]*)?>', re.IGNORECASE)
CONTROL_WORDS = ('interval', 'speed', 'sound')

JAPANESE_RANGES = ((0x3040, 0x30FF), (0x3400, 0x9FFF),
                   (0xF900, 0xFAFF), (0xFF66, 0xFF9F))


def normalize_key(v):
    """TranslationManager.NormalizeSubSkillKey"""
    if not v or ('\r' not in v and '\\' not in v):
        return v
    return (v.replace('\r\n', '\n')
             .replace('\r', '\n')
             .replace('\\r\\n', '\n')
             .replace('\\n', '\n'))


def has_control_tag(v):
    """StoryTranslationIndex.HasUtageControlTag"""
    if not v or '<' not in v:
        return False
    low = v.lower()
    return any(w in low for w in CONTROL_WORDS)


def strip_control_tags(v):
    return v if not has_control_tag(v) else UTAGE_CONTROL_TAG.sub('', v)


def normalize_display_key(v):
    """TranslationManager.NormalizeStoryDisplayValue"""
    if not v:
        return v
    n = normalize_key(v)
    return strip_control_tags(n) if has_control_tag(n) else n


def contains_japanese(v):
    for ch in v:
        o = ord(ch)
        if any(lo <= o <= hi for lo, hi in JAPANESE_RANGES):
            return True
    return False


def is_self_mapped(src, tr):
    """StoryTranslationIndex.IsUntranslatedSelfMapping"""
    if not contains_japanese(src):
        return False
    if src == tr:
        return True
    if not has_control_tag(src) and not has_control_tag(tr):
        return False
    return strip_control_tags(src) == strip_control_tags(tr)


class StoryTranslationIndex:
    """Faithful port of Services/StoryTranslationIndex.cs."""

    def __init__(self):
        self.scenes = {}                 # scene(lower) -> {src: tr}
        self.global_ = {}                # src -> tr | None
        self.fallback_global = {}        # src -> tr
        self.global_counts = {}          # src -> {tr: count}
        self.unique_scene = {}           # src -> scene | None
        self.scenes_by_source = {}       # src -> set(scene)
        self.rows_in_scenes = 0

    def record_scene_occurrence(self, scene, src):
        if not scene or not src:
            return
        key = scene.lower()
        if src not in self.unique_scene:
            self.unique_scene[src] = scene
        elif self.unique_scene[src] is not None and self.unique_scene[src].lower() != key:
            self.unique_scene[src] = None
        self.scenes_by_source.setdefault(src, set()).add(scene)

    def add(self, scene, src, tr):
        if not src or not tr:
            return
        self.record_scene_occurrence(scene, src)
        if is_self_mapped(src, tr):
            return
        if scene:
            table = self.scenes.setdefault(scene.lower(), {})
            if src not in table:
                table[src] = tr
                self.rows_in_scenes += 1
        counts = self.global_counts.setdefault(src, {})
        new_count = counts.get(tr, 0) + 1
        counts[tr] = new_count

        src_tag = has_control_tag(src)
        tr_tag = has_control_tag(tr)
        if src not in self.fallback_global:
            self.fallback_global[src] = tr
        else:
            best = self.fallback_global[src]
            best_tag = has_control_tag(best)
            best_count = counts.get(best, 0)
            if not src_tag and best_tag and not tr_tag:
                self.fallback_global[src] = tr
            elif (src_tag or best_tag == tr_tag) and new_count > best_count:
                self.fallback_global[src] = tr

        if src not in self.global_:
            self.global_[src] = tr
        elif self.global_[src] != tr:
            best = self.fallback_global[src]
            if src_tag or has_control_tag(best):
                self.global_[src] = None
            else:
                self.global_[src] = best

    # ---- lookups (used only by the offline self-test / statistics) ----
    def try_get_in_scene(self, src, scene):
        t = self.scenes.get((scene or '').lower(), {}).get(src)
        return (True, t, self.unique_scene.get(src) or '') if t else (False, src, self.unique_scene.get(src) or '')

    def scenes_for_source(self, src):
        return self.scenes_by_source.get(src, set())

    def try_get_from_candidates(self, src, candidates):
        agreed = None
        for c in candidates:
            val = self.scenes.get(c.lower(), {}).get(src)
            if not val:
                continue
            if agreed is None:
                agreed = val
            elif agreed != val:
                return False, src
        return (True, agreed) if agreed else (False, src)

    def try_get(self, src, scene):
        """returns (hit, translation, conflict)"""
        t = self.scenes.get((scene or '').lower(), {}).get(src)
        if t:
            return True, t, False
        if src not in self.global_:
            return False, src, False
        val = self.global_[src]
        if val is None:
            promoted = self.fallback_global.get(src)
            if scene and promoted:
                return True, promoted, False
            return False, src, True
        return True, val, False


def analyse(rows, strings, scenarios):
    """Rebuild both indexes exactly like LoadScenariosCore, then report stats."""
    parse = StoryTranslationIndex()
    display = StoryTranslationIndex()
    fallback_raw = {}
    promoted = 0
    empty_rows = 0
    self_mapped = 0
    scene_conflict_sources = set()

    for sidx, k_idx, v_idx in rows:
        scene = scenarios[sidx]
        raw_key = strings[k_idx]
        raw_val = strings[v_idx]
        parse_key = normalize_key(raw_key)
        display_key = normalize_display_key(raw_key)
        if not raw_val:
            empty_rows += 1
            parse.record_scene_occurrence(scene, parse_key)
            display.record_scene_occurrence(scene, display_key)
            fallback_raw.setdefault(raw_key, '')
            continue
        val = normalize_key(raw_val)
        if is_self_mapped(parse_key, val):
            self_mapped += 1
        parse.add(scene, parse_key, val)
        display.add(scene, display_key, val)
        if display_key != parse_key:
            display.add(scene, parse_key, val)
        if raw_key not in fallback_raw:
            fallback_raw[raw_key] = val
        elif fallback_raw[raw_key] == '':
            fallback_raw[raw_key] = val
            promoted += 1
        if parse_key != raw_key and (parse_key not in fallback_raw or fallback_raw[parse_key] == ''):
            fallback_raw[parse_key] = val

    conflict_sources = [s for s, v in parse.global_.items() if v is None]
    for s, counts in parse.global_counts.items():
        if len(counts) > 1:
            scene_conflict_sources.add(s)

    stats = {
        'emptyRows': empty_rows,
        'selfMappedRows': self_mapped,
        'promotedOverEmptyRows': promoted,
        'sceneTableRows': parse.rows_in_scenes,
        'displaySceneTableRows': display.rows_in_scenes,
        'distinctSources': len(parse.unique_scene),
        'sourcesWithMultipleScenes': sum(1 for v in parse.scenes_by_source.values() if len(v) > 1),
        'sourcesWithConflictingTranslations': len(scene_conflict_sources),
        'sceneSpecificConflictRowsRetained': sum(
            len(parse.scenes.get(sc.lower(), {}).get(s) and [1] or [])
            for s in scene_conflict_sources for sc in parse.scenes_by_source.get(s, set())
        ),
        'globalNullConflictSources': len(conflict_sources),
        'unresolvableWithoutScene': len(conflict_sources),
    }
    return {'parse': parse, 'display': display, 'stats': stats,
            'fallbackRaw': fallback_raw}


def derive(rows, strings, scenarios):
    """Build a COMPACT runtime index that nevertheless keeps every scene-specific
    translation.

    Observation: for a source with exactly one distinct translation, the author's
    `_scenes[scene][source]` entry is identical to `_global[source]` for every
    scene, so shipping it would be pure duplication.  Only ambiguous sources
    (more than one distinct translation, or a null global) need per-scene rows.
    """
    res = analyse(rows, strings, scenarios)
    parse, display = res['parse'], res['display']

    pool = {s: i for i, s in enumerate(strings)}
    extra = []

    def idx(s):
        if s in pool:
            return pool[s]
        pool[s] = len(strings) + len(extra)
        extra.append(s)
        return pool[s]

    sc_lut = {s.lower(): i for i, s in enumerate(scenarios)}

    def scene_idx(scene):
        return sc_lut.get(scene.lower(), -1)

    def emit(index):
        definite = []
        ambiguous = []
        for src, val in index.global_.items():
            counts = index.global_counts.get(src, {})
            needs_scene = val is None or len(counts) > 1
            if needs_scene:
                scene_rows = []
                for sc, table in index.scenes.items():
                    if src in table:
                        si = scene_idx(sc)
                        if si >= 0:
                            scene_rows.append([si, idx(table[src])])
                presence = sorted({scene_idx(sc) for sc in index.scenes_by_source.get(src, set())})
                presence = [p for p in presence if p >= 0]
                uniq = index.unique_scene.get(src)
                ambiguous.append({
                    'k': idx(src),
                    's': scene_rows,
                    'f': idx(index.fallback_global.get(src, '')),
                    'g': idx(val) if val else idx(''),
                    'p': presence,
                    'u': scene_idx(uniq) if uniq else -1,
                    'n': 1 if val is None else 0,
                })
            else:
                definite.append([idx(src), idx(val)])
        return {'global': definite, 'ambiguous': ambiguous}

    return {
        'parse': emit(parse),
        'display': emit(display),
        'extraStrings': extra,
        'stats': res['stats'],
    }
