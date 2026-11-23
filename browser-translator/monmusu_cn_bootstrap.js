/* monmusu_cn_bootstrap.js - appended to the patched ntd.loader.js.
 *
 * Installs the translation bridge before Unity instantiates the module:
 *   - wraps WebAssembly.instantiate / instantiateStreaming
 *   - waits (bounded) for both translation indices so the first line and the
 *     first menu are already covered
 *   - repurposes TWO existing imports (336 translation, 228 scene context) instead
 *     of adding new ones, so no function index in the module shifts
 *   - hands the exported function table to the bridge for UI path reconstruction
 *   - validates the build signature first; on any mismatch it does nothing and the
 *     game runs untouched
 */
(function () {
  'use strict';

  /* Everything that changes with the official build -- the two import names,
     the import-count fingerprint, the icall addresses, the element-table slots
     and the managed field offsets -- arrives in MONMUSU_CN_RUNTIME, which
     tools/wasm/resolve_runtime.py generates per branch and build_release.py
     embeds in this loader. Nothing of that kind is written here any more: the
     DMM and FANZA Wasm have different element tables, so a hand-kept copy is
     wrong for one of them by construction. */
  var CFG = {
    host: 'assets.game-monmusu-td.net',
    /* The same build id is served on two resource branches. 0 = DMM
       (/0/Build/), 1 = FANZA (/1/Build/). The loader, the translation indexes
       and the assetbundles are shared; only the Wasm and ntd.data differ, and
       the proxy serves the matching pair per branch. The branch is detected
       from the actual script/page URL, never assumed. */
    branches: ['0', '1'],
    indexFile: 'monmusu_cn_story_index.json',     // full Berumic story index (gzip on wire)
    uiIndexFile: 'monmusu_cn_ui_index.json',      // Berumic UI index (menu/quest/names)
    fallbackFile: 'monmusu_cn_translations.json', // prologue-only rollback table
    tableTimeoutMs: 12000,
    uiFont: true,                  // build a Chinese TMP asset at runtime
    /* OFF: both paths caused regressions on the device (font overlap and a
       wasm out-of-bounds in the PlayerLoop).  The fallback-only configuration
       is the one that was browser-verified.  See STABLE_BASELINE.txt. */
    uiFontUnify: false,            // translated UI uses it as the primary font
    uiFontRefreshAll: false,       // dirty every TMP_Text once after install
    debug: true
  };

  if (typeof WebAssembly === 'undefined' || typeof WebAssembly.instantiate !== 'function') return;
  if (typeof monmusuCnCreateBridge !== 'function' || typeof monmusuCnStory === 'undefined') {
    console.warn('[monmusu_cn] bridge code missing, loader not patched correctly');
    return;
  }

  var bridge = monmusuCnCreateBridge();
  function say() {
    if (CFG.debug) console.log.apply(console, ['[monmusu_cn]'].concat([].slice.call(arguments)));
  }
  function warn() {
    console.warn.apply(console, ['[monmusu_cn]'].concat([].slice.call(arguments)));
  }

  /* Resolve the resource branch from a URL that looks like
     https://<host>/web/arl/<build>/<branch>/Build/... — the only shape the
     proxy ever serves. Anything else returns null instead of guessing. */
  var ARL_RE = /^(https?:\/\/[^\/]+\/web\/arl\/\d{10,16}\/([0-9]+)\/Build\/)/;

  function baseFromUrl(url) {
    if (!url) return null;
    var m = ARL_RE.exec(String(url));
    if (!m) return null;
    if (CFG.branches.indexOf(m[2]) < 0) return null;
    return { base: m[1], branch: m[2] };
  }

  var located = null;
  try {
    if (typeof document !== 'undefined') {
      // 1. the loader script itself (bootstrap is appended to it)
      located = baseFromUrl(document.currentScript && document.currentScript.src);
      // 2. any script tag, in case currentScript is unavailable
      if (!located) {
        var tags = document.getElementsByTagName('script');
        for (var i = 0; i < tags.length && !located; i++) {
          located = baseFromUrl(tags[i].src);
        }
      }
    }
  } catch (e) { located = null; }

  if (!located && typeof location !== 'undefined') {
    // 3. the game frame lives at .../<build>/<branch>/index.html
    try {
      located = baseFromUrl(String(location.href).replace(/[^\/]*$/, 'Build/'));
    } catch (e2) { located = null; }
  }

  var base, BRANCH;
  if (located) {
    base = located.base;
    BRANCH = located.branch;
  } else {
    BRANCH = '0';
    base = '';
    warn('could not detect the resource branch from the page; assuming /0/ (DMM)');
  }
  CFG.branch = BRANCH;

  /* The generated entry for the branch actually being served. A loader that
     ships without one is a build mistake, not a runtime condition: refuse to
     translate (loudly) instead of calling into an unverified index space. */
  var RT_ALL = (typeof MONMUSU_CN_RUNTIME !== 'undefined' && MONMUSU_CN_RUNTIME &&
                MONMUSU_CN_RUNTIME.branches) ? MONMUSU_CN_RUNTIME.branches : null;
  var RC = RT_ALL ? RT_ALL[BRANCH] : null;
  var RC_STATE = 'missing';
  if (!RC) {
    warn('no generated runtime config for branch ' + BRANCH +
         ' -> translation disabled (rebuild the Wasm for this branch)');
  } else if (String(RC.branch) !== String(BRANCH)) {
    warn('runtime config is for branch ' + RC.branch + ' but this page is /' + BRANCH +
         '/ -> refusing to patch');
    RC = null;
  } else if (!bridge.applyRuntimeConfig(RC)) {
    var rcNote = bridge.runtimeConfig();
    warn('runtime config rejected: ' + rcNote.why +
         ' -> translation runs, font/path features stay off');
    RC_STATE = 'partial';
  } else {
    RC_STATE = 'ok';
  }
  if (RC && !base) {
    var gb = String(RC.game_build || '');
    if (gb.indexOf('/') > 0) {
      base = 'https://' + CFG.host + '/web/arl/' + gb.split('/')[0] + '/' + BRANCH + '/Build/';
    }
  }
  var indexUrl = base + CFG.indexFile;
  var uiUrl = base + CFG.uiIndexFile;
  var fallbackUrl = base + CFG.fallbackFile;

  function elapsed(t0) {
    return t0 ? Math.round(performance.now() - t0) : -1;
  }
  function getJson(url) {
    return fetch(url, { cache: 'no-store', credentials: 'same-origin' })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      });
  }

  function loadIndex() {
    if (!base) {
      warn('no resource base (page not served from an /web/arl/<build>/<branch>/ ' +
           'path and no runtime config to derive it) -> story index skipped');
      return Promise.resolve(false);
    }
    var t0 = (typeof performance !== 'undefined' && performance.now) ? performance.now() : 0;
    return getJson(indexUrl)
      .then(function (doc) {
        if (!doc || doc.schema !== 2) throw new Error('unexpected index schema');
        bridge.setStoryIndex(doc);
        say('story index loaded in ' + elapsed(t0) + 'ms:',
            doc.stats.scenarioFiles, 'scenario files,',
            doc.stats.scenarioIds, 'scenes,',
            bridge.stats.tableSize, 'source keys, commit=' + doc.commit);
        return true;
      })
      .catch(function (e) {
        warn('full story index unavailable (' + (e && e.message) +
             '), falling back to the prologue-only table');
        return fetch(fallbackUrl, { cache: 'no-store', credentials: 'same-origin' })
          .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
          .then(function (j) { bridge.setTable(j.pairs); say('fallback table:', bridge.stats.tableSize); })
          .catch(function (e2) {
            warn('no translation data available, running untranslated:', e2 && e2.message);
          });
      });
  }

  /* The UI index is independent: if it fails the story path still works, and if
     the page has no UI index the bridge simply keeps every UI string as-is. */
  function loadUiIndex() {
    if (!base) return Promise.resolve(false);
    var t0 = (typeof performance !== 'undefined' && performance.now) ? performance.now() : 0;
    if (typeof monmusuCnUi === 'undefined') {
      warn('monmusu_cn_ui_index.js missing -> UI translation disabled');
      return Promise.resolve(false);
    }
    return getJson(uiUrl)
      .then(function (doc) {
        if (!doc || doc.schema !== 3 || doc.kind !== 'ui') throw new Error('unexpected UI index schema');
        bridge.setUiIndex(doc);
        var st = bridge.getUiIndex().stats;
        say('ui index loaded in ' + elapsed(t0) + 'ms:',
            st.globalEntries, 'global keys,',
            st.pathTables, 'path tables,',
            st.nameEntries, 'names, commit=' + doc.commit);
        return true;
      })
      .catch(function (e) {
        warn('UI index unavailable (' + (e && e.message) + ') -> UI left untranslated');
        return false;
      });
  }

  var ready = Promise.all([loadIndex(), loadUiIndex()]);

  var gate = Promise.race([ready, new Promise(function (res) {
    setTimeout(res, CFG.tableTimeoutMs);
  })]);

  var installed = false;
  var skipped = false;

  function patchImports(imports) {
    if (installed || skipped) return;
    if (!RC || !RC.imports) {
      skipped = true;
      warn('no runtime config -> patch skipped (game runs untranslated)');
      return;
    }
    var im = RC.imports;
    var ns = imports && imports[im.module];
    var trName = im.translation.name, ctxName = im.context.name;
    if (!ns || typeof ns[trName] !== 'function') {
      skipped = true;
      warn('import ' + im.module + '.' + trName + ' not found -> patch skipped');
      return;
    }
    var count = Object.keys(ns).length;
    /* The import count is the cheapest whole-build fingerprint available on the
       JS side: it changes whenever the module's import list changes, and every
       import name is obfuscated per build anyway. It is a build-time resolved
       number, not a constant kept by hand. */
    if (count !== im.count) {
      skipped = true;
      warn('import count ' + count + ' != ' + im.count +
           ' -> the served wasm is not the one this loader was built for; ' +
           'patch skipped');
      return;
    }
    if (typeof ns[ctxName] !== 'function') {
      skipped = true;
      warn('context import ' + im.module + '.' + ctxName + ' not found -> patch skipped');
      return;
    }
    ns[trName] = bridge.wrapImport(ns[trName]);
    ns[ctxName] = bridge.wrapContextImport(ns[ctxName]);
    installed = true;
    say('bridge installed on ' + im.module + '.' + trName + ' (translation, import ' +
        im.translation.index + ') and ' + im.module + '.' + ctxName +
        ' (scene, import ' + im.context.index + ')');
  }

  function adopt(res) {
    var ex = res && (res.instance ? res.instance.exports : res.exports);
    if (ex) {
      bridge.setExports(ex);
      for (var k in ex) {
        var v = ex[k];
        if (v && typeof v.grow === 'function' && v.buffer instanceof ArrayBuffer) {
          bridge.setMemory(v);
          say('memory attached: ' + (v.buffer.byteLength / 1048576) + ' MiB');
          break;
        }
      }
      // UI hierarchy paths need the function table; without it path rules are
      // skipped and only global UI translations apply
      var ok = bridge.exportTableFrom(ex);
      say('function table ' + (ok ? 'attached' : 'unavailable') + ' (UI path rules ' +
          (ok ? 'enabled' : 'off') + ')');
      /* localStorage override, so an experiment can turn the runtime installer
         off without editing this file or rebuilding anything:
             localStorage.setItem('monmusu_cn.uiFont', '0'); location.reload()
         and back with removeItem.  Runs before any font work. */
      try {
        var ov = (typeof localStorage !== 'undefined') ? localStorage.getItem('monmusu_cn.uiFont') : null;
        if (ov === '0' || ov === 'off') {
          CFG.uiFont = false;
          say('runtime TMP font installer disabled by localStorage');
        } else if (ov === '1' || ov === 'on') {
          CFG.uiFont = true;
        }
      } catch (e) { /* private mode / no storage */ }

      /* UI trace, same switch style: record what the hook actually receives.
             localStorage.setItem('monmusu_cn.uiTrace', '1'); location.reload()
         then, after opening the screen:
             __monmusuCnBridge.uiTraceDump()
         and remove the key to stop recording.  Bounded (last 300 strings). */
      try {
        var tr = (typeof localStorage !== 'undefined') ? localStorage.getItem('monmusu_cn.uiTrace') : null;
        if (tr === '1' || tr === 'on') {
          bridge.uiTrace(true);
          say('UI trace enabled (__monmusuCnBridge.uiTraceDump() to read)');
        }
      } catch (e) { /* private mode / no storage */ }

      try {
        var storyTrace = (typeof localStorage !== 'undefined') ? localStorage.getItem('monmusu_cn.storyTrace') : null;
        if (storyTrace === '1' || storyTrace === 'on') {
          bridge.storyTrace(true);
          say('story trace enabled (__monmusuCnBridge.storyTraceDump() to read)');
        }
      } catch (e) { /* private mode / no storage */ }

      if (!CFG.uiFont) {
        bridge.fontDisable();
        say('runtime TMP font installer disabled by config');
      } else {
        if (CFG.uiFontUnify === false) {
          bridge.fontUnify(false);
          say('UI font unification disabled by config (fallback still active)');
        }
        if (CFG.uiFontRefreshAll === false) bridge.fontRefreshAll(false);
      }
    }
    return res;
  }

  var origInstantiate = WebAssembly.instantiate;
  var origStreaming = WebAssembly.instantiateStreaming;

  WebAssembly.instantiate = function (buf, imports) {
    var args = arguments;
    return gate.then(function () {
      patchImports(imports);
      return origInstantiate.apply(WebAssembly, args).then(adopt);
    });
  };

  if (typeof origStreaming === 'function') {
    WebAssembly.instantiateStreaming = function (resp, imports) {
      var args = arguments;
      return gate.then(function () {
        patchImports(imports);
        return origStreaming.apply(WebAssembly, args).then(adopt);
      });
    };
  }

  /* console handle so a real run can be verified quickly:
     __monmusuCnBridge.stats     -> story counters
     __monmusuCnBridge.uiStats   -> UI counters (probes/hits/pathWalks/noPath/...)
     __monmusuCnBridge.pathState -> 'ok' | 'broken' | 'idle'
     __monmusuCnBridge.uiProbe() -> hierarchy path of the last TMP component
     __monmusuCnBridge.scene()   -> current playback / parsing scene state */
  try { window.__monmusuCnBridge = bridge; } catch (e) { /* ignore */ }
  try {
    window.__monmusuCnRuntime = function () {
      var rc = bridge.runtimeConfig ? bridge.runtimeConfig() : null;
      return { branch: BRANCH, configState: RC_STATE, source: RC ? 'generated' : 'none',
               config: rc, imports: RC ? RC.imports : null };
    };
  } catch (e2) { /* ignore */ }

  say('armed for build ' + (RC ? RC.game_build : 'unknown') + ' (config ' + RC_STATE +
      '), index: ' + indexUrl + ', ui: ' + uiUrl);
})();
