/* monmusu_cn_ui_index.js - runtime UI translation index for the Monster Musume TD WebGL mod.
 *
 * Mirrors the author's Services/TranslationManager.cs + Services/UiStylePolicy.cs:
 *
 *   lookup(source, hierarchyPath)
 *     1. path tables whose path is a segment-aligned part of hierarchyPath,
 *        longest path first  -> exact, then layout-tag-stripped
 *     2. merged global table  -> exact, then stripped, then global[stripped]
 *     3. sub-skill table (translations/subskills) -> exact / stripped
 *     4. names table (translations/names) -> exact
 *     5. number templates (<num>) -> anchored regex, digits copied verbatim
 *     4. miss -> keep Japanese
 *
 * Path tables are NEVER merged into the global map: a path-qualified translation
 * must not leak into an unrelated widget.
 *
 * A rule path does not have to name the text node itself.  The author's
 * PathContains() is a plain segment-aligned substring test, so a rule such as
 * ".../Node/Mission" also governs every text node underneath it.  Candidate
 * selection is therefore a scan over all rules (longest first), not a lookup
 * keyed by the component's own leaf name.
 *
 * Hierarchy paths are expensive to obtain in WebGL (they need native getter
 * calls), so lookupLazy() takes a path *provider* and only invokes it when the
 * source string actually occurs in some path table.  Everything else is served
 * from the global map without ever touching the scene graph.
 *
 * Pure data + pure functions, so the browser build and the Node test harness run
 * the exact same code.
 */
(function (root) {
  'use strict';

  var LAYOUT_TAG = /<\/?(?:cspace|indent|line-indent|line-height|margin|scale|pos|voffset|mspace|space|size)(?:=(?:<num>|<ex>|[^>])*)?>/gi;

  function stripLayoutTags(value) {
    if (!value || value.indexOf('<') < 0) return value || '';
    return value.replace(LAYOUT_TAG, '');
  }

  function normalizeSubSkillKey(value) {
    if (!value || (value.indexOf('\r') < 0 && value.indexOf('\\') < 0)) return value || '';
    return value.replace(/\r\n/g, '\n').replace(/\r/g, '\n')
                .replace(/\\r\\n/g, '\n').replace(/\\n/g, '\n');
  }

  function containsJapanese(value) {
    if (!value) return false;
    for (var i = 0; i < value.length; i++) {
      var c = value.charCodeAt(i);
      if ((c >= 0x3040 && c <= 0x30ff) || (c >= 0x3400 && c <= 0x9fff) ||
          (c >= 0xf900 && c <= 0xfaff) || (c >= 0xff66 && c <= 0xff9f)) return true;
    }
    return false;
  }

  function normalizePath(path) {
    if (!path) return '';
    if (path.indexOf('(Clone)') < 0 && path.indexOf('\\') < 0 &&
        path.indexOf('//') < 0 && path[0] !== '/' && path[path.length - 1] !== '/') {
      return path;
    }
    var parts = path.replace(/\\/g, '/').split('/');
    var out = [];
    for (var i = 0; i < parts.length; i++) {
      if (!parts[i]) continue;
      out.push(parts[i].replace(/\(Clone\)/g, ''));
    }
    return out.join('/');
  }

  /* UiStylePolicy.PathContains: segment-aligned substring search. */
  function pathContains(fullPath, rulePath) {
    if (!fullPath || !rulePath) return false;
    var f = fullPath.toLowerCase(), r = rulePath.toLowerCase();
    if (f === r) return true;
    var from = 0, limit = f.length - r.length;
    while (from <= limit) {
      var idx = f.indexOf(r, from);
      if (idx < 0) return false;
      var okStart = idx === 0 || f.charAt(idx - 1) === '/';
      var end = idx + r.length;
      var okEnd = end === f.length || f.charAt(end) === '/';
      if (okStart && okEnd) return true;
      from = idx + 1;
    }
    return false;
  }

  /* TranslationManager.PathContainsSegmentSequence */
  function pathMatches(fullPath, rulePath) {
    if (pathContains(fullPath, rulePath)) return true;
    // a child prefab populated before SetParent: fullPath is a suffix of rulePath
    if (!fullPath || !rulePath) return false;
    if (fullPath.length >= rulePath.length || fullPath.indexOf('/') <= 0) return false;
    var tail = rulePath.toLowerCase();
    var suffix = '/' + fullPath.toLowerCase();
    return tail.length > suffix.length &&
           tail.substring(tail.length - suffix.length) === suffix;
  }

  function leafOf(path) {
    var i = path.lastIndexOf('/');
    return i < 0 ? path : path.substring(i + 1);
  }

  /* ---------------- <num> number templates ----------------
   *
   * Same contract as TranslationManager.NumberTemplate: split on "<num>", the
   * literal pieces must match the whole string, the captured digits are copied
   * into the translation in order.  Differences from the author, both deliberate:
   *   - a row whose placeholder counts do not match is dropped at build time
   *     (the author leaves a literal "<num>" on screen instead);
   *   - templates are bucketed by the first character of their first literal so a
   *     miss costs a handful of startsWith() calls, not 1,100 regex attempts.
   */
  var NUM_MARK = '<num>';
  /* \d matches ASCII only.  The game prints stats with full-width digits
     ("敵１体", "４５秒") while the corpus template says <num>; without ０-９
     those rows can never match.  Captured text is copied back verbatim, so a
     full-width number stays full-width in the output. */
  var DIGIT = '[0-9０-９]';
  var NUM_RE = '[+\\-−＋]?(?:' + DIGIT + '+(?:[.,．]' + DIGIT + '+)?|\\{\\d+(?::[^}]*)?\\})';
  var INT_RE = '[+\\-−＋]?' + DIGIT + '+';

  function escRe(text) {
    return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  /* The corpus mixes ％ and %: the same template can be written either way and
     the runtime string uses whichever the game's formatter produced (e.g. the
     class trait says 15% while the corpus key says <num>％).  Both widths are
     the same character for matching purposes, so a literal percent becomes a
     two-alternative class.  Nothing else is folded. */
  function escPiece(text) {
    return escRe(text).replace(/[％%]/g, '[％%]');
  }

  function hasPercent(text) {
    return !!text && (text.indexOf('%') >= 0 || text.indexOf('％') >= 0);
  }

  function buildTemplateRe(pieces) {
    var out = '^';
    for (var i = 0; i < pieces.length; i++) {
      out += escPiece(pieces[i]);
      if (i < pieces.length - 1) {
        var next = pieces[i + 1];
        // "1.5" style: the separator is literal, so capture the integer only
        out += (next && (next.charAt(0) === '.' || next.charAt(0) === ',' ||
                         next.charAt(0) === '．')) ? '(' + INT_RE + ')' : '(' + NUM_RE + ')';
      }
    }
    return new RegExp(out + '$');
  }

  function compileTemplates(strings, rows) {
    var list = [];
    for (var i = 0; i < rows.length; i++) {
      var src = strings[rows[i][0]], dst = strings[rows[i][1]];
      if (!src || !dst) continue;
      var pieces = src.split(NUM_MARK);
      var tpieces = dst.split(NUM_MARK);
      if (pieces.length !== tpieces.length || pieces.length < 2) continue;
      var lit = 0;
      for (var j = 0; j < pieces.length; j++) lit += pieces[j].length;
      list.push({ src: src, dst: dst, pieces: pieces, tpieces: tpieces,
                  lit: lit, n: pieces.length - 1, re: null, head: pieces[0] });
    }
    var byHead = new Map();
    var headless = [];
    for (var k = 0; k < list.length; k++) {
      var h = list[k].head;
      if (!h) { headless.push(list[k]); continue; }
      var c = h.charAt(0);
      var arr = byHead.get(c);
      if (!arr) { arr = []; byHead.set(c, arr); }
      arr.push(list[k]);
    }
    return { size: list.length, byHead: byHead, headless: headless };
  }

  function runTemplate(t, source) {
    if (source.length < t.lit + t.n) return null;
    if (t.head && !hasPercent(t.head) && source.lastIndexOf(t.head, 0) !== 0) return null;
    var tail = t.pieces[t.pieces.length - 1];
    if (tail && !hasPercent(tail) &&
        source.substring(source.length - tail.length) !== tail) return null;
    var re = t.re || (t.re = buildTemplateRe(t.pieces));
    var m = re.exec(source);
    if (!m || m.length - 1 !== t.tpieces.length - 1) return null;
    var out = t.tpieces[0];
    for (var i = 1; i < t.tpieces.length; i++) out += m[i] + t.tpieces[i];
    return out;
  }

  function matchTemplates(compiled, source) {
    if (!compiled || !compiled.size) return null;
    var bucket = compiled.byHead.get(source.charAt(0));
    if (bucket) {
      for (var i = 0; i < bucket.length; i++) {
        var hit = runTemplate(bucket[i], source);
        if (hit !== null) return hit;
      }
    }
    for (var j = 0; j < compiled.headless.length; j++) {
      var hit2 = runTemplate(compiled.headless[j], source);
      if (hit2 !== null) return hit2;
    }
    return null;
  }

  function toMap(strings, rows) {
    var m = new Map();
    for (var i = 0; i < rows.length; i++) m.set(strings[rows[i][0]], strings[rows[i][1]]);
    return m;
  }

  function createIndex(doc) {
    if (!doc || doc.schema !== 3 || doc.kind !== 'ui') {
      throw new Error('monmusu_cn_ui_index: unexpected index document');
    }
    var S = doc.strings;
    var global = toMap(S, doc.global);
    var globalStripped = toMap(S, doc.globalStripped || []);
    var subSkills = toMap(S, doc.subskills || []);
    var subSkillsStripped = toMap(S, doc.subskillsStripped || []);
    var names = toMap(S, doc.names || []);
    var numGlobal = compileTemplates(S, doc.numTemplates || []);
    var numSub = compileTemplates(S, doc.numTemplatesSub || []);

    var tables = [];
    for (var i = 0; i < doc.pathTables.length; i++) {
      var raw = doc.pathTables[i];
      tables.push({
        path: raw.p,
        lower: raw.p.toLowerCase(),
        exact: toMap(S, raw.exact),
        stripped: toMap(S, raw.stripped || []),
        num: compileTemplates(S, raw.num || [])
      });
    }
    // most specific rule first, so a deep rule wins over a broad one
    tables.sort(function (a, b) { return b.path.length - a.path.length; });

    // every Chinese value we produced, so we never translate our own output twice
    var knownValues = new Set();
    function remember(map) {
      map.forEach(function (v) { if (v) knownValues.add(v); });
    }
    remember(global); remember(globalStripped); remember(names);
    remember(subSkills); remember(subSkillsStripped);

    // source keys that could possibly be served by a path table; anything else
    // never needs the hierarchy path at all
    var pathKeys = new Set();
    for (var k = 0; k < tables.length; k++) {
      tables[k].exact.forEach(function (v, key) { pathKeys.add(key); });
      tables[k].stripped.forEach(function (v, key) { pathKeys.add(key); });
      remember(tables[k].exact); remember(tables[k].stripped);
    }

    // first literal of every path-scoped template, for the gate above
    var pathNumHeads = [], pathNumHeadless = false, seenHead = {};
    for (var ti = 0; ti < tables.length; ti++) {
      var nt = tables[ti].num;
      if (!nt || !nt.size) continue;
      nt.byHead.forEach(function (arr) {
        for (var ai = 0; ai < arr.length; ai++) {
          var h = arr[ai].pieces[0];
          if (!h) { pathNumHeadless = true; continue; }
          /* A one-character first literal ("敵<num>体に…") is just as real as a
             long one.  Filtering short heads here made every such path-scoped
             template unreachable: couldMatchPath() never let the lookup reach
             lookupPath().  The gate already requires a digit in the probe and
             the final match is still an anchored regex, so admitting short
             heads costs a few extra path walks, never a wrong translation. */
          if (seenHead[h]) continue;
          seenHead[h] = 1;
          pathNumHeads.push(h);
        }
      });
      if (nt.headless.length) pathNumHeadless = true;
    }
    pathNumHeads.sort(function (a, b) { return b.length - a.length; });

    var stats = {
      globalEntries: global.size,
      pathTables: tables.length,
      pathEntries: doc.stats ? doc.stats.pathEntries : 0,
      nameEntries: names.size,
      subSkillEntries: subSkills.size,
      subSkillHits: 0,
      numTemplates: numGlobal.size + numSub.size,
      numHits: 0,
      pathKeys: pathKeys.size,
      lookups: 0, hits: 0, misses: 0, pathLookups: 0, pathHits: 0,
      knownHits: 0, pathResolved: 0, pathBorrowed: 0, lastRule: ''
    };

    /* ordered candidate tables for a runtime hierarchy path (longest first) */
    var MIN_TAIL = 4;      // segments that must agree before a rule is borrowed

    function sharedTail(a, b) {
      var A = a.toLowerCase().split('/'), B = b.toLowerCase().split('/');
      var n = 0;
      while (n < A.length && n < B.length &&
             A[A.length - 1 - n] === B[B.length - 1 - n]) n++;
      return n;
    }

    function matchTables(fullPath) {
      if (!fullPath) return null;
      var out = null;
      for (var i = 0; i < tables.length; i++) {
        if (pathMatches(fullPath, tables[i].path)) {
          if (!out) out = [];
          out.push(tables[i]);
        }
      }
      if (out) return out;

      /* Same widget, different wrapper.  The corpus keys its rules on the
         screen they were recorded on (Unit_Select/.../Unit_StatusView/Class/
         ClassNameBase/ClassNameTitle/ClassNameText) while the detail page
         mounts the same prefab under Unit_Detail_Rework/.../UnitStatusViewArea.
         Neither is a prefix of the other, so nothing matched and every class
         name fell back to Japanese.  Borrow the rule whose *tail* is identical,
         longest first, and only when exactly one rule wins - an ambiguous tail
         is left alone rather than guessed. */
      var best = 0, picked = null, tie = false;
      for (var j = 0; j < tables.length; j++) {
        var n = sharedTail(fullPath, tables[j].path);
        if (n < MIN_TAIL) continue;
        if (n > best) { best = n; picked = [tables[j]]; tie = false; }
        else if (n === best) { picked.push(tables[j]); tie = true; }
      }
      if (!picked || tie) return null;
      stats.pathBorrowed++;
      return picked;
    }

    function lookupPath(source, stripped, list) {
      if (!list) return null;
      for (var i = 0; i < list.length; i++) {
        var v = list[i].exact.get(source);
        if (v) return v;
        /* the rule key may be a *tagged* string (E<indent=20>X<cspace=-6>...),
           so its row lives in the stripped table keyed by the clean text.  A
           runtime string without tags still has to find it, so this lookup
           cannot be skipped when stripped === source. */
        v = list[i].stripped.get(source);
        if (v) return v;
        if (stripped && stripped !== source) {
          v = list[i].exact.get(stripped) || list[i].stripped.get(stripped);
          if (v) return v;
        }
        if (list[i].num && list[i].num.size) {
          v = matchTemplates(list[i].num, source);
          if (v) { stats.numHits++; return v; }
          if (stripped && stripped !== source) {
            v = matchTemplates(list[i].num, stripped);
            if (v) { stats.numHits++; return v; }
          }
        }
      }
      return null;
    }

    /* Could any path table answer this exact string?  Only then is the
       hierarchy path worth computing. */
    function couldMatchPath(source, normalized, stripped) {
      if (pathKeys.has(source)) return true;
      if (normalized !== source && pathKeys.has(normalized)) return true;
      if (stripped && stripped !== source && pathKeys.has(stripped)) return true;
      /* Path-scoped templates never have an exact key: the runtime string has
         numbers in it.  Without this the whole path template table was dead
         code - the lookup was skipped before it could run.
         The gate must look at the same variants the matcher will try: a
         description that arrives with CRLF ends its first line in \r\n, so
         startsWith(head) fails on the raw string even though the normalised
         form matches. */
      if (pathNumHeads.length || pathNumHeadless) {
        var probes = [source];
        if (normalized && normalized !== source) probes.push(normalized);
        if (stripped && stripped !== source) probes.push(stripped);
        for (var p = 0; p < probes.length; p++) {
          if (!/[0-9０-９]/.test(probes[p])) continue;
          for (var i = 0; i < pathNumHeads.length; i++) {
            if (probes[p].lastIndexOf(pathNumHeads[i], 0) === 0) return true;
          }
          if (pathNumHeadless && /^[+\\-−＋0-9０-９]/.test(probes[p])) return true;
        }
      }
      return false;
    }

    /* getPath is called at most once, and only when it can change the answer. */
    function lookupLazy(source, getPath, noTrim) {
      if (!source) return null;
      stats.lookups++;
      stats.lastRule = '';

      // fast path: already one of our translations -> nothing to do
      if (knownValues.has(source)) { stats.knownHits++; stats.lastRule = 'known'; return null; }

      var stripped = stripLayoutTags(source);
      var normalized = normalizeSubSkillKey(source);

      if (getPath && couldMatchPath(source, normalized, stripped)) {
        stats.pathLookups++;
        var full;
        try { full = normalizePath(getPath() || ''); } catch (e) { full = ''; }
        if (full) {
          stats.pathResolved++;
          var list = matchTables(full);
          var hit = lookupPath(source, stripped, list);
          if (!hit && normalized !== source) {
            hit = lookupPath(normalized, stripLayoutTags(normalized), list);
          }
          if (hit) { stats.hits++; stats.pathHits++; stats.lastRule = 'path'; return hit; }
        }
      }

      var v = global.get(source);
      if (v) { stats.hits++; stats.lastRule = 'global'; return v; }
      if (normalized !== source) {
        v = global.get(normalized);
        if (v) { stats.hits++; stats.lastRule = 'global'; return v; }
      }
      if (stripped && stripped !== source) {
        v = global.get(stripped) || globalStripped.get(stripped);
        if (v) { stats.hits++; stats.lastRule = 'global-stripped'; return v; }
      }
      v = globalStripped.get(source);
      if (v) { stats.hits++; stats.lastRule = 'global-stripped'; return v; }

      /* sub-skills sit exactly where the author puts _subSkills in
         TryTranslateUiTextCore: after _uiTexts, before _names. */
      v = subSkills.get(source);
      if (v) { stats.hits++; stats.subSkillHits++; stats.lastRule = 'sub'; return v; }
      if (normalized !== source) {
        v = subSkills.get(normalized);
        if (v) { stats.hits++; stats.subSkillHits++; stats.lastRule = 'sub'; return v; }
      }
      if (stripped && stripped !== source) {
        v = subSkills.get(stripped) || subSkillsStripped.get(stripped);
        if (v) { stats.hits++; stats.subSkillHits++; stats.lastRule = 'sub'; return v; }
      }
      v = subSkillsStripped.get(source);
      if (v) { stats.hits++; stats.subSkillHits++; stats.lastRule = 'sub'; return v; }

      v = names.get(source) || (normalized !== source ? names.get(normalized) : null);
      if (v) { stats.hits++; stats.lastRule = 'name'; return v; }

      /* dynamic descriptions last, so an exact row always beats a template */
      v = matchTemplates(numGlobal, source) || matchTemplates(numSub, source);
      if (!v && stripped && stripped !== source) {
        v = matchTemplates(numGlobal, stripped) || matchTemplates(numSub, stripped);
      }
      /* CRLF / escaped-newline variants: the corpus keys use \n, a description
         built on the Windows side of the engine can arrive with \r\n.  Same
         text, so try the normalised form before giving up. */
      if (!v && normalized !== source) {
        v = matchTemplates(numGlobal, normalized) || matchTemplates(numSub, normalized);
      }
      if (v) { stats.hits++; stats.numHits++; stats.lastRule = 'template'; return v; }

      /* Last resort: the runtime string sometimes carries trailing spaces or
         newlines that the corpus key does not have - the prefab ships both
         "ブロック数" and "ブロック数\n", and "サブスキルがありません\r" exists
         too.  Trimming is only a lookup convenience: the trailing whitespace is
         put back, so layout is unchanged.  Never applied when the whole string
         is whitespace. */
      if (!noTrim) {
        var core = source.replace(/[ \t\r\n\u3000]+$/, '');
        if (core && core !== source) {
          var trimmed = lookupLazy(core, getPath, true);
          if (trimmed) { stats.lastRule = 'trim'; return trimmed + source.slice(core.length); }
        }
      }

      stats.misses++;
      stats.lastRule = 'miss';
      return null;
    }

    /* materialised path variant, used by the offline test harness */
    function lookup(source, fullPath) {
      var path = fullPath || '';
      return lookupLazy(source, path ? function () { return path; } : null);
    }

    /* author's CanPossiblyNeedTranslation, restricted to what this index can serve */
    function canPossiblyNeedTranslation(source) {
      if (!source) return false;
      return containsJapanese(source) || global.has(source) ||
             subSkills.has(source) || names.has(source) ||
             knownValues.has(source);
    }

    /* true when the string is a value this index produces (already translated) */
    function isKnownValue(source) { return knownValues.has(source); }

    /* does the source string only exist inside path tables? (diagnostics) */
    var pathOnly = new Set();
    for (var t2 = 0; t2 < tables.length; t2++) {
      tables[t2].exact.forEach(function (v, key) {
        if (!global.has(key)) pathOnly.add(key);
      });
    }

    return {
      lookup: lookup,
      lookupLazy: lookupLazy,
      matchTables: matchTables,
      canPossiblyNeedTranslation: canPossiblyNeedTranslation,
      isKnownValue: isKnownValue,
      containsJapanese: containsJapanese,
      stripLayoutTags: stripLayoutTags,
      normalizePath: normalizePath,
      pathMatches: pathMatches,
      couldMatchPath: couldMatchPath,
      needsPath: function (source) { return pathOnly.has(source); },
      stats: stats,
      meta: {
        commit: doc.commit, schema: doc.schema,
        globalEntries: global.size, pathTables: tables.length,
        nameEntries: names.size, subSkillEntries: subSkills.size,
        numTemplates: numGlobal.size + numSub.size,
        pathKeys: pathKeys.size, pathBorrowed: stats.pathBorrowed,
        pathNumHeads: pathNumHeads.length
      }
    };
  }

  var api = {
    createIndex: createIndex,
    stripLayoutTags: stripLayoutTags,
    normalizeSubSkillKey: normalizeSubSkillKey,
    containsJapanese: containsJapanese,
    normalizePath: normalizePath,
    pathContains: pathContains,
    pathMatches: pathMatches,
    leafOf: leafOf
  };

  root.monmusuCnUi = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
