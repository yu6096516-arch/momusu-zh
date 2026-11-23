/* monmusu_cn_story_index.js - runtime port of Berumic's story matching + scene context.
 *
 * Pure synchronous JS.  Translation data lives here, never in the wasm binary.
 * Node can require() this file; the WebGL loader concatenates it verbatim.
 *
 * C# references:
 *   Services/StoryTranslationIndex.cs       global / fallback / conflict rules
 *   Services/TranslationManager.cs         ResolveStoryScenarioIdCore,
 *                                          NormalizeSubSkillKey,
 *                                          TryTranslateStoryBeforeParseSingle
 *   Services/StorySceneContext.cs          playback vs parsing scopes,
 *                                          candidate narrowing, labels
 *   Services/ScenarioContext.cs            resource-id pattern
 */
(function (root) {
  'use strict';

  var CONTROL_TAG = /<\/?(?:interval|speed|sound)(?:=[^>]*)?>/gi;
  var CONTROL_WORDS = ['interval', 'speed', 'sound'];
  var RESOURCE_NUMBER = /(r18|nml)_(\d+)(?:_|$)/i;
  var SCENARIO_RESOURCE = /^(\d+)[a-z]\d+_(nml|r18)$/i;
  var JAPANESE_RANGES = [[0x3040, 0x30ff], [0x3400, 0x9fff],
                         [0xf900, 0xfaff], [0xff66, 0xff9f]];

  function normalizeKey(v) {
    if (!v || (v.indexOf('\r') < 0 && v.indexOf('\\') < 0)) return v;
    return v.replace(/\r\n/g, '\n').replace(/\r/g, '\n')
            .replace(/\\r\\n/g, '\n').replace(/\\n/g, '\n');
  }
  function hasControlTag(v) {
    if (!v || v.indexOf('<') < 0) return false;
    var low = v.toLowerCase();
    return CONTROL_WORDS.some(function (w) { return low.indexOf(w) >= 0; });
  }
  function stripControlTags(v) {
    return hasControlTag(v) ? v.replace(CONTROL_TAG, '') : v;
  }
  function normalizeDisplayKey(v) {
    if (!v) return v;
    var n = normalizeKey(v);
    return hasControlTag(n) ? stripControlTags(n) : n;
  }
  function containsJapanese(v) {
    for (var i = 0; i < v.length; i++) {
      var o = v.charCodeAt(i);
      for (var j = 0; j < JAPANESE_RANGES.length; j++) {
        if (o >= JAPANESE_RANGES[j][0] && o <= JAPANESE_RANGES[j][1]) return true;
      }
    }
    return false;
  }
  function isSelfMapped(src, tr) {
    if (!containsJapanese(src)) return false;
    if (src === tr) return true;
    if (!hasControlTag(src) && !hasControlTag(tr)) return false;
    return stripControlTags(src) === stripControlTags(tr);
  }

  // ---------------------------------------------------------------- index
  function buildHalf(half, S) {
    var globalMap = new Map();
    for (var i = 0; i < half.global.length; i++) {
      var g = half.global[i];
      globalMap.set(S[g[0]], S[g[1]]);
    }
    var ambiguous = new Map();
    for (i = 0; i < half.ambiguous.length; i++) {
      var a = half.ambiguous[i];
      var sceneMap = new Map();
      for (var j = 0; j < a.s.length; j++) sceneMap.set(a.s[j][0], S[a.s[j][1]]);
      ambiguous.set(S[a.k], {
        scenes: sceneMap,
        fallback: S[a.f],
        globalValue: S[a.g] || '',
        presence: a.p,
        unique: a.u,
        globalNull: !!a.n
      });
    }
    return { global: globalMap, ambiguous: ambiguous };
  }

  function createIndex(doc) {
    if (!doc || doc.schema !== 2) throw new Error('unsupported story index schema');
    var S = doc.strings;
    var parse = buildHalf(doc.parse, S);
    var display = buildHalf(doc.display, S);
    var names = new Map();
    for (var i = 0; i < doc.names.length; i++) names.set(doc.names[i][0], doc.names[i][1]);

    // values already translated -> skip retranslation on repeated calls
    var known = new Set();
    parse.global.forEach(function (v) { known.add(v); });
    display.global.forEach(function (v) { known.add(v); });
    parse.ambiguous.forEach(function (a) {
      known.add(a.fallback); known.add(a.globalValue);
      a.scenes.forEach(function (v) { known.add(v); });
    });
    display.ambiguous.forEach(function (a) {
      known.add(a.fallback); known.add(a.globalValue);
      a.scenes.forEach(function (v) { known.add(v); });
    });
    names.forEach(function (v) { known.add(v); });

    /* A string can be BOTH a translated value and a source key that still
       needs translating ("…………。" is one line's Japanese source and another
       line's Chinese value).  The guard must not swallow such sources: treat a
       value as "already translated" only when it is not itself an indexed
       source key.  Otherwise those lines can never be translated. */
    var sourceKeys = new Set();
    function rememberSourceKeys(map) {
      map.forEach(function (_v, k) { sourceKeys.add(k); });
    }
    rememberSourceKeys(parse.global); rememberSourceKeys(parse.ambiguous);
    rememberSourceKeys(display.global); rememberSourceKeys(display.ambiguous);
    names.forEach(function (_v, k) { sourceKeys.add(k); });

    var scenarioLut = new Map();
    var resourceScenes = new Map();
    for (i = 0; i < doc.scenarios.length; i++) {
      var sc = doc.scenarios[i];
      scenarioLut.set(sc.toLowerCase(), i);
      var m = SCENARIO_RESOURCE.exec(sc);
      if (m) {
        var base = m[1], kind = m[2].toLowerCase();
        [kind + '_' + base, base].forEach(function (key) {
          var list = resourceScenes.get(key);
          if (!list) { list = []; resourceScenes.set(key, list); }
          if (list.indexOf(i) < 0) list.push(i);
        });
      }
    }

    function resolveSingleLeaf(leaf) {
      if (!leaf) return -1;
      leaf = leaf.trim();
      var dot = leaf.lastIndexOf('.');
      if (dot > 0) leaf = leaf.substring(0, dot);
      if (leaf.charAt(0) === '*') leaf = leaf.substring(1);
      var hit = scenarioLut.get(leaf.toLowerCase());
      if (hit !== undefined) return hit;
      var u = leaf.lastIndexOf('_');
      while (u > 0) {
        hit = scenarioLut.get(leaf.substring(0, u).toLowerCase());
        if (hit !== undefined) return hit;
        u = leaf.lastIndexOf('_', u - 1);
      }
      return -1;
    }
    function resolveSegment(seg) {
      if (!seg) return -1;
      seg = seg.trim();
      var c = seg.indexOf(':');
      if (c > 0) {
        var a = resolveSingleLeaf(seg.substring(0, c));
        if (a >= 0) return a;
        return resolveSingleLeaf(seg.substring(c + 1));
      }
      return resolveSingleLeaf(seg);
    }
    function resolveSceneId(value) {
      if (!value || !value.trim()) return -1;
      var candidate = value.trim().replace(/\\/g, '/');
      var exact = scenarioLut.get(candidate.toLowerCase());
      if (exact !== undefined) return exact;
      var segs = candidate.split('/');
      for (var k = segs.length - 1; k >= 0; k--) {
        var r = resolveSegment(segs[k]);
        if (r >= 0) return r;
      }
      var m = RESOURCE_NUMBER.exec(candidate);
      if (m) {
        var kind2 = m[1].toLowerCase(), base2 = m[2];
        var list = resourceScenes.get(kind2 + '_' + base2) || resourceScenes.get(base2);
        if (list && list.length === 1) return list[0];
      }
      return -1;
    }

    function sceneName(si) { return si >= 0 ? doc.scenarios[si] : ''; }

    function isKnownStoryValue(value) {
      if (!value) return false;
      function knownButNotSource(k) {
        return known.has(k) && !sourceKeys.has(k);
      }
      if (knownButNotSource(value)) return true;
      var n = normalizeKey(value);
      if (n !== value && knownButNotSource(n)) return true;
      var trimmed = value.replace(/^[ \t\u3000\n]+|[ \t\u3000\n]+$/g, '');
      return trimmed.length > 0 && trimmed !== value && knownButNotSource(trimmed);
    }

    function getInScene(half, key, sceneIdx) {
      if (sceneIdx >= 0) {
        var amb = half.ambiguous.get(key);
        if (amb) {
          var v = amb.scenes.get(sceneIdx);
          if (v) return v;
        }
      }
      var g = half.global.get(key);
      return g || '';
    }
    function getGlobal(half, key, sceneIdx) {
      var amb = half.ambiguous.get(key);
      if (amb) {
        if (sceneIdx >= 0) {
          var v = amb.scenes.get(sceneIdx);
          if (v) return { hit: true, tr: v };
        }
        if (!amb.globalNull) {
          // author: plain short sentences resolve to the dominant tag-free value
          return amb.globalValue ? { hit: true, tr: amb.globalValue } : { hit: false, tr: '' };
        }
        if (sceneIdx >= 0 && amb.fallback) return { hit: true, tr: amb.fallback };
        return { conflict: true, tr: '' };
      }
      var g = half.global.get(key);
      if (g) return { hit: true, tr: g };
      return { hit: false, tr: '' };
    }

    // punctuation / quote variants (TryLookupPunctuationOrQuoteVariants)
    function variants(target) {
      var out = [];
      if (target.length > 2 && target.charAt(target.length - 1) === '。') {
        var prev = target.charAt(target.length - 2);
        if (prev === '…' || prev === '？' || prev === '！') out.push({ k: target.slice(0, -1), mode: 'plain' });
      } else if (target.length > 1 && target.charAt(target.length - 1) === '…') {
        out.push({ k: target + '。', mode: 'plain' });
      }
      if (target.length > 2) {
        var a = target.charAt(0), b = target.charAt(target.length - 1);
        if ((a === '「' && b === '」') || (a === '『' && b === '』') || (a === '（' && b === '）')) {
          out.push({ k: target.slice(1, -1), mode: 'wrap', open: a, close: b });
        }
      }
      if (target.indexOf('\u301c') >= 0) out.push({ k: target.replace(/\u301c/g, '\uff5e'), mode: 'plain' });
      if (target.indexOf('\uff5e') >= 0) out.push({ k: target.replace(/\uff5e/g, '\u301c'), mode: 'plain' });
      return out;
    }
    function lookupVariant(half, key, sceneIdx) {
      var list = variants(key);
      for (var i = 0; i < list.length; i++) {
        var r = getGlobal(half, list[i].k, sceneIdx);
        if (r.hit && r.tr !== list[i].k) {
          return list[i].mode === 'wrap'
            ? list[i].open + r.tr + list[i].close
            : r.tr;
        }
      }
      return '';
    }

    function inSceneLookup(half, key, sceneIdx) {
      if (sceneIdx < 0) return '';
      var v = getInScene(half, key, sceneIdx);
      if (v && v !== key) return v;
      var trimmed = key.replace(/^[ \t\u3000\n]+|[ \t\u3000\n]+$/g, '');
      if (trimmed && trimmed !== key) {
        v = getInScene(half, trimmed, sceneIdx);
        if (v && v !== trimmed) {
          var at = key.indexOf(trimmed);
          return at >= 0 ? key.slice(0, at) + v + key.slice(at + trimmed.length) : v;
        }
        var vv = lookupVariant(half, trimmed, sceneIdx);
        if (vv) return vv;
      }
      return lookupVariant(half, key, sceneIdx);
    }

    function tryFromCandidates(half, key, candidates) {
      var agreed = '';
      for (var i = 0; i < candidates.length; i++) {
        var amb = half.ambiguous.get(key);
        var v = amb ? (amb.scenes.get(candidates[i]) || '') : (half.global.get(key) || '');
        if (!v) continue;
        if (!agreed) agreed = v;
        else if (agreed !== v) return '';
      }
      return agreed && agreed !== key ? agreed : '';
    }

    /** Full author-priority lookup. Returns the translated string or '' (keep Japanese). */
    function lookup(source, ctx) {
      concatStats.last = null;
      if (!source || isKnownStoryValue(source)) return '';
      var key = normalizeKey(source);
      var displayKey = normalizeDisplayKey(key);
      var tag = hasControlTag(key);
      var differentDisplay = displayKey !== key;
      // Consume pending parsed-cell context exactly once, as before.
      var sceneIdx = resolveSceneId(ctx.parsingScene);
      var allowInference = ctx.allowsInference;

      // 1. active scene table
      if (sceneIdx >= 0) {
        var r = inSceneLookup(parse, key, sceneIdx) ||
                (!tag && inSceneLookup(display, key, sceneIdx));
        if (!r && differentDisplay) {
          r = inSceneLookup(parse, displayKey, sceneIdx) ||
              inSceneLookup(display, displayKey, sceneIdx);
        }
        if (r) { ctx.onMatched(key, sceneIdx); return r; }
      } else if (allowInference && !tag) {
        // 2. candidate scene narrowing
        var amb = parse.ambiguous.get(key);
        var presence = amb ? amb.presence : [];
        if (!presence.length && differentDisplay) {
          var amb2 = parse.ambiguous.get(displayKey);
          presence = amb2 ? amb2.presence : [];
        }
        if (presence.length > 0 && presence.length <= 16) {
          var narrowed = ctx.narrowCandidateScenes(presence);
          if (narrowed.length) {
            var agreed = tryFromCandidates(parse, key, narrowed);
            if (agreed) {
              if (narrowed.length === 1) ctx.notifyUniqueScene(narrowed[0]);
              return agreed;
            }
          }
        }
      }

      // 3. global / fallback
      var g = getGlobal(parse, key, sceneIdx);
      if (g.hit && g.tr !== key) { ctx.onMatched(key, sceneIdx); return g.tr; }
      if (g.conflict) return '';
      if (!tag) {
        var gd = getGlobal(display, key, sceneIdx);
        if (gd.hit && gd.tr !== key) { ctx.onMatched(key, sceneIdx); return gd.tr; }
        if (gd.conflict) return '';
      }
      var vv = lookupVariant(parse, key, sceneIdx);
      if (vv) { ctx.onMatched(key, sceneIdx); return vv; }
      if (!tag) {
        vv = lookupVariant(display, key, sceneIdx);
        if (vv) { ctx.onMatched(key, sceneIdx); return vv; }
      }
      if (differentDisplay) {
        var gp = getGlobal(parse, displayKey, sceneIdx);
        if (gp.hit && gp.tr !== key) { ctx.onMatched(key, sceneIdx); return gp.tr; }
        var gpd = getGlobal(display, displayKey, sceneIdx);
        if (gpd.hit && gpd.tr !== key) { ctx.onMatched(key, sceneIdx); return gpd.tr; }
      }

      // 4. trimmed form
      var trimmed = key.replace(/^[ \t\u3000\n]+|[ \t\u3000\n]+$/g, '');
      if (trimmed && trimmed !== key) {
        var gt = getGlobal(parse, trimmed, sceneIdx);
        if (gt.hit && gt.tr !== trimmed) {
          var at = key.indexOf(trimmed);
          return at >= 0 ? key.slice(0, at) + gt.tr + key.slice(at + trimmed.length) : gt.tr;
        }
      }

      // 5. speaker names (author: TryTranslateName after story lookup)
      var nm = names.get(source) || names.get(key);
      if (nm && nm !== source) return nm;
      // A full-key identity/conflict must not be evaded by splitting it.
      if (parse.global.has(key) || parse.ambiguous.has(key) ||
          display.global.has(displayKey) || display.ambiguous.has(displayKey)) return '';
      return lookupConcatenated(source, key, sceneIdx);
    }

    // Complete source-entry coverage only. No punctuation splitting, substring
    // replacement, UI fallback, speaker-name pieces, or global scene borrowing.
    // One lazy scene trie bounds retained memory and keeps the exact path cheap.
    var concatData = doc.concatenation;
    var concatScene = -1, concatRoot = null;
    var concatStats = { queries: 0, hits: 0, misses: 0, ambiguous: 0,
      budgetRejects: 0, builds: 0, nodes: 0, last: null };
    var concatLimits = { input: 8192, segments: 16, visits: 1024,
      scans: 65536, nodes: 65536, sceneRows: 4096, separator: 64, output: 32768 };
    function sceneTrie(scene) {
      if (concatScene === scene) return concatRoot;
      concatScene = scene; concatRoot = null; concatStats.nodes = 0;
      var rows = concatData && concatData.schema === 1 && concatData.scenes[scene];
      if (!rows || rows.length / 2 > concatLimits.sceneRows) return null;
      var rootNode = { c: new Map(), t: null }, nodes = 1;
      for (var ri = 0; ri < rows.length; ri += 2) {
        var key = S[rows[ri]], value = rows[ri + 1];
        if (names.has(key) || key.length < 3 || key.length > 4096 ||
            !/[\u3040-\u30ff\uff66-\uff9f]/.test(key)) continue;
        var node = rootNode;
        for (var ci = 0; ci < key.length; ci++) {
          var ch = key.charAt(ci), child = node.c.get(ch);
          if (!child) {
            if (++nodes > concatLimits.nodes) return null;
            child = { c: new Map(), t: null }; node.c.set(ch, child);
          }
          node = child;
        }
        node.t = { source: key, translation: typeof value === 'number' && value >= 0 ? S[value] : '',
                   conflict: typeof value !== 'number' || value < 0 };
      }
      concatRoot = rootNode; concatStats.builds++; concatStats.nodes = nodes;
      return concatRoot;
    }
    function isSeparator(ch) { return ch === ' ' || ch === '\t' || ch === '\n' || ch === '\u3000'; }
    // Only separators outside matched entries are copied from raw input. The
    // author's translated entry (including its own whitespace) is untouched.
    function rawOffsets(source, normalized) {
      if (source === normalized) return null;
      var offsets = [], rebuilt = '', i = 0;
      while (i < source.length) {
        offsets.push(i);
        var n = source.substr(i, 4) === '\\r\\n' ? 4 :
          source.substr(i, 2) === '\r\n' || source.substr(i, 2) === '\\n' ? 2 : 1;
        rebuilt += n > 1 || source.charAt(i) === '\r' ? '\n' : source.charAt(i);
        i += n;
      }
      offsets.push(i);
      return rebuilt === normalized ? offsets : false;
    }
    function lookupConcatenated(source, key, scene) {
      concatStats.queries++;
      var record = { rule: 'concatenated-story', scene: sceneName(scene), reason: '',
        segments: [], visits: 0, scans: 0 };
      concatStats.last = record;
      function miss(reason) {
        record.reason = reason; concatStats.misses++;
        if (reason === 'ambiguous-coverage') concatStats.ambiguous++;
        if (reason === 'budget-exceeded') concatStats.budgetRejects++;
        return '';
      }
      if (scene < 0) return miss('scene-required');
      if (key.length < 6 || key.length > concatLimits.input) return miss('input-limit');
      if (known.has(source) || known.has(key)) return miss('known-value');
      var tree = sceneTrie(scene);
      if (!tree) return miss('scene-data-unavailable-or-limit');
      var offsets = rawOffsets(source, key);
      if (offsets === false) return miss('normalization-map-rejected');
      function rawAt(pos) { return offsets ? offsets[pos] : pos; }
      function gap(a, b) { return source.slice(rawAt(a), rawAt(b)); }
      var answers = [], exhausted = false;
      function walk(pos, segments, parts, blocked) {
        if (exhausted || answers.length > 1) return;
        if (++record.visits > concatLimits.visits) { exhausted = true; return; }
        var begin = pos;
        while (pos < key.length && isSeparator(key.charAt(pos))) pos++;
        if (pos - begin > concatLimits.separator) { exhausted = true; return; }
        var nextParts = parts.concat(gap(begin, pos));
        if (pos === key.length) {
          if (segments.length >= 2) answers.push({segments: segments, parts: nextParts, blocked: blocked});
          return;
        }
        if (segments.length >= concatLimits.segments) { exhausted = true; return; }
        var node = tree;
        for (var end = pos; end < key.length; end++) {
          if (++record.scans > concatLimits.scans) { exhausted = true; return; }
          node = node.c.get(key.charAt(end));
          if (!node) break;
          if (node.t) {
            var t = node.t;
            walk(end + 1, segments.concat({start: rawAt(pos), end: rawAt(end + 1),
              source: t.source, translation: t.translation}), nextParts.concat(t.translation), blocked || t.conflict);
            if (answers.length > 1 || exhausted) return;
          }
        }
      }
      walk(0, [], [], false);
      if (exhausted) return miss('budget-exceeded');
      if (answers.length > 1 || (answers.length && answers[0].blocked)) return miss('ambiguous-coverage');
      if (!answers.length) return miss('incomplete-coverage');
      var out = answers[0].parts.join('');
      if (out.length > concatLimits.output) return miss('budget-exceeded');
      if (out === source) return miss('unchanged');
      record.segments = answers[0].segments; record.reason = 'hit';
      concatStats.hits++;
      return out;
    }

    return {
      lookup: lookup, resolveSceneId: resolveSceneId, sceneName: sceneName,
      concatStats: concatStats, concatLimits: concatLimits,
      isKnownStoryValue: isKnownStoryValue, stats: doc.stats,
      commit: doc.commit, corpusSha256: doc.corpusSha256,
      scenarioCount: doc.scenarios.length,
      parse: parse, display: display,
      helpers: { normalizeKey: normalizeKey, normalizeDisplayKey: normalizeDisplayKey,
                 hasControlTag: hasControlTag, isSelfMapped: isSelfMapped,
                 containsJapanese: containsJapanese }
    };
  }

  // ------------------------------------------------------- scene context
  function createSceneContext(index) {
    var playbackId = -1;
    var inferredPlaybackId = -1;
    var candidateScenes = null;
    var scopes = [];
    var labels = new Map();
    var pending = null;
    var cellScene = -1;
    var self = {
      stats: { parseBegins: 0, parseEnds: 0, playbackStarts: 0,
               playbackEnds: 0, cellEvents: 0, uniqueHits: 0 },
      get parsingScene() {
        // A preload/parsing scope must NOT inherit the scene currently playing.
        // Only an inferred (text-derived) scene may leak into a blank scope.
        if (scopes.length) {
          var top = scopes[scopes.length - 1];
          if (!top.allowInference) return index.sceneName(top.scene);
          if (pending && pending.text === self._lastSource) return index.sceneName(pending.scene);
          for (var i = scopes.length - 1; i >= 0; i--) {
            if (scopes[i].scene >= 0) return index.sceneName(scopes[i].scene);
            if (!scopes[i].allowInference) return '';
            if (scopes[i].inferred >= 0) return index.sceneName(scopes[i].inferred);
          }
          if (cellScene >= 0) return index.sceneName(cellScene);
          return index.sceneName(inferredPlaybackId);
        }
        if (pending && pending.text === self._lastSource) {
          var s = pending.scene;
          pending = null;
          return index.sceneName(s);
        }
        if (playbackId >= 0) return index.sceneName(playbackId);
        if (inferredPlaybackId >= 0) return index.sceneName(inferredPlaybackId);
        return index.sceneName(cellScene);
      },
      get allowsInference() { return scopes.length === 0 || scopes[scopes.length - 1].allowInference; },
      get playback() { return playbackId; },
      get inferred() { return inferredPlaybackId; },
      _lastSource: '',
      prepare: function (source) { self._lastSource = source; },
      beginParsing: function (raw) {
        var s = index.resolveSceneId(raw);
        cellScene = -1;                 // per-scenario evidence must not leak
        scopes.push({ scene: s, allowInference: true, inferred: -1 });
        self.stats.parseBegins++;
        if (raw) self.registerLabel(raw, s);
        return s;
      },
      endParsing: function () { if (scopes.length) scopes.pop(); self.stats.parseEnds++; },
      beginPlayback: function (raw) {
        var s = index.resolveSceneId(raw);
        if (s < 0) s = self.resolveLabel(raw);
        if (s >= 0) {
          playbackId = s; inferredPlaybackId = s;
          candidateScenes = new Set([s]);
        }
        self.stats.playbackStarts++;
        return s;
      },
      endPlayback: function () {
        playbackId = -1; inferredPlaybackId = -1;
        candidateScenes = null; pending = null; cellScene = -1;
        self.stats.playbackEnds++;
      },
      registerLabel: function (label, scene) {
        if (!label || scene < 0) return;
        if (labels.has(label)) {
          if (labels.get(label) !== scene) labels.set(label, -1);
        } else labels.set(label, scene);
      },
      resolveLabel: function (label) {
        if (!label) return -1;
        var v = labels.get(label);
        if (v !== undefined && v >= 0) return v;
        if (label.charAt(0) === '*') {
          v = labels.get(label.substring(1));
          if (v !== undefined && v >= 0) return v;
        } else {
          v = labels.get('*' + label);
          if (v !== undefined && v >= 0) return v;
        }
        return -1;
      },
      setCell: function (raw) {
        var s = index.resolveSceneId(raw);
        self.stats.cellEvents++;
        if (s >= 0) {
          cellScene = s;
          for (var i = scopes.length - 1; i >= 0; i--) {
            if (scopes[i].allowInference && scopes[i].scene < 0) scopes[i].scene = s;
          }
        }
        return s;
      },
      setPendingParsedCell: function (text, scene) {
        if (text && scene >= 0) pending = { text: text, scene: scene };
      },
      narrowCandidateScenes: function (sourceScenes) {
        if (!sourceScenes || !sourceScenes.length || !self.allowsInference) return [];
        var next;
        if (!candidateScenes || candidateScenes.size === 0) {
          next = new Set(sourceScenes);
        } else {
          next = new Set();
          candidateScenes.forEach(function (s) { if (sourceScenes.indexOf(s) >= 0) next.add(s); });
          if (next.size === 0) next = new Set(sourceScenes);
        }
        candidateScenes = next;
        var arr = [];
        next.forEach(function (s) { arr.push(s); });
        if (arr.length === 1) self.notifyUniqueScene(arr[0]);
        return arr;
      },
      notifyUniqueScene: function (scene) {
        if (scene < 0) return;
        self.stats.uniqueHits++;
        for (var i = 0; i < scopes.length; i++) {
          if (scopes[i].allowInference) scopes[i].inferred = scene;
        }
        if (!scopes.length || scopes[scopes.length - 1].allowInference) {
          inferredPlaybackId = scene;
          candidateScenes = new Set([scene]);
        }
      },
      onMatched: function (key, sceneIdx) {
        var amb = index.parse.ambiguous.get(key);
        if (amb && amb.unique >= 0) self.notifyUniqueScene(amb.unique);
      },
      effective: function () { return effective(); },
      // for tests / diagnostics
      snapshot: function () {
        return {
          playbackId: index.sceneName(playbackId),
          inferred: index.sceneName(inferredPlaybackId),
          scopes: scopes.map(function (s) { return index.sceneName(s.scene); }),
          candidates: candidateScenes ? Array.from(candidateScenes).map(index.sceneName) : [],
          cellScene: index.sceneName(cellScene),
          stats: self.stats
        };
      }
    };
    function effective() { return playbackId >= 0 ? playbackId : inferredPlaybackId; }
    return self;
  }

  var api = {
    createIndex: createIndex, createSceneContext: createSceneContext,
    normalizeKey: normalizeKey, normalizeDisplayKey: normalizeDisplayKey,
    hasControlTag: hasControlTag, isSelfMapped: isSelfMapped,
    containsJapanese: containsJapanese
  };
  root.monmusuCnStory = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
