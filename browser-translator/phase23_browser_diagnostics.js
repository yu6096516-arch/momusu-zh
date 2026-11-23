// Paste into the DevTools console of the frame containing #unity-canvas
// and window.__monmusuCnBridge. Works with the existing phase-22 loader too.
// Read-only logging; no reload, Wasm patch, object scan, or font operation.
(() => {
  const b = window.__monmusuCnBridge;
  if (!b) throw new Error('Select the Unity game iframe: window.__monmusuCnBridge must exist');
  if (window.__monmusuCnStoryDiag) window.__monmusuCnStoryDiag.stop();
  const index = b.getIndex(), native = typeof b.storyTrace === 'function';
  const queries = [], original = index && index.lookup;
  const counters = () => ({story: {...b.stats}, ui: {...b.uiStats}, context: b.scene()});
  const before = counters();
  b.uiTraceClear(); b.uiTrace(true);
  if (native) { b.storyTraceClear(); b.storyTrace(true); }
  else if (index) {
    index.lookup = function (source, ctx) {
      const context = ctx.snapshot();
      const result = original.apply(this, arguments);
      queries.push({time: Date.now(), source, context, enteredStoryQuery: true,
        hit: !!result, translation: result || '', knownValue: index.isKnownStoryValue(source),
        hook: 'query-origin-unverified', write: 'NOT_OBSERVED'});
      if (queries.length > 300) queries.shift();
      return result;
    };
  }
  window.__monmusuCnStoryDiag = {
    dump: () => JSON.stringify({url: location.href, mode: native ? 'import-trace' : 'legacy-query-trace',
      commit: index && index.commit, corpusSha256: index && index.corpusSha256,
      before, after: counters(), story: native ? b.storyTrace() : queries,
      ui: b.uiTrace(), finalDisplay: 'NOT_OBSERVED'}, null, 2),
    stop: () => {
      b.uiTrace(false);
      if (native) b.storyTrace(false);
      else if (index) index.lookup = original;
    }
  };
  return {mode: native ? 'import-trace' : 'legacy-query-trace', indexLoaded: !!index,
    next: 'Replay only 1101011nml; then copy(__monmusuCnStoryDiag.dump()); __monmusuCnStoryDiag.stop()'};
})();
