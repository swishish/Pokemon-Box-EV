/* Overlay the sealed-box shard onto the card EV bundle at read time.
   Card jobs keep writing data.json / data.js. The sealed job writes only
   data/sealed-boxes/. Neither job rewrites the other's files. */
(function (root, factory) {
  var api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.ComposeEv = api;
})(typeof window !== 'undefined' ? window : null, function () {
  var NOTE_KEYS = ['denominator', 'method_denom', 'method_sealed_box'];
  var SET_KEYS = [
    'snkrdunk_shrink_id',
    'snkrdunk_shrink_url',
    'snkrdunk_ns_id',
    'snkrdunk_ns_url',
    'pc_avg_usd',
    'pc_avg_hkd',
    'pc_avg_as_of',
    'pc_avg_source'
  ];

  function applySealedShard(data, shard) {
    if (!data || !shard || typeof shard !== 'object') return data;
    data.notes = Object.assign({}, data.notes || {});
    if (shard.notes) {
      NOTE_KEYS.forEach(function (key) {
        if (shard.notes[key] != null) data.notes[key] = shard.notes[key];
      });
    }
    var asOf = shard.sealed_box_as_of || (shard.notes && shard.notes.sealed_box_as_of);
    if (asOf) data.notes.sealed_box_as_of = asOf;
    if (shard.fx) {
      data.fx = Object.assign({}, data.fx || {});
      if (shard.fx.jpy_per_hkd != null) data.fx.jpy_per_hkd = shard.fx.jpy_per_hkd;
      if (shard.fx.usd_hkd != null) data.fx.usd_hkd = shard.fx.usd_hkd;
    }
    var srcSets = shard.sets || {};
    var dstSets = data.sets || {};
    Object.keys(srcSets).forEach(function (code) {
      var src = srcSets[code];
      var dst = dstSets[code];
      if (!src || !dst) return;
      if (src.landed) dst.landed = Object.assign({}, dst.landed || {}, src.landed);
      if (src.sealed_box) dst.sealed_box = Object.assign({}, dst.sealed_box || {}, src.sealed_box);
      SET_KEYS.forEach(function (key) {
        if (src[key] != null) dst[key] = src[key];
      });
    });
    return data;
  }

  function selfTest() {
    function assert(cond, msg) {
      if (!cond) throw new Error(msg);
    }
    var data = {
      notes: { sealed_box_as_of: 'old', tax: 'keep' },
      fx: { jpy_per_hkd: 1, note: 'stay' },
      sets: {
        M6: {
          landed: { shrink: { landed_yen: 1 }, ns: { landed_yen: 2 } },
          sealed_box: { shrink: { qty: 1 } },
          snkrdunk_shrink_id: 1
        }
      }
    };
    var shard = {
      sealed_box_as_of: 'new',
      fx: { jpy_per_hkd: 20.3 },
      notes: { denominator: 'd' },
      sets: {
        M6: {
          landed: { shrink: { landed_yen: 9 } },
          snkrdunk_ns_id: 5
        },
        NOPE: { landed: { shrink: { landed_yen: 99 } } }
      }
    };
    applySealedShard(data, shard);
    assert(data.notes.tax === 'keep', 'card note kept');
    assert(data.notes.sealed_box_as_of === 'new', 'sealed as_of overlaid');
    assert(data.notes.denominator === 'd', 'denom overlaid');
    assert(data.fx.jpy_per_hkd === 20.3, 'fx rate overlaid');
    assert(data.fx.note === 'stay', 'fx note kept');
    assert(data.sets.M6.landed.shrink.landed_yen === 9, 'shrink price from shard');
    assert(data.sets.M6.landed.ns.landed_yen === 2, 'ns kept when shard omits it');
    assert(data.sets.M6.sealed_box.shrink.qty === 1, 'sealed_box kept');
    assert(data.sets.M6.snkrdunk_shrink_id === 1, 'shrink id kept');
    assert(data.sets.M6.snkrdunk_ns_id === 5, 'ns id from shard');
    assert(!data.sets.NOPE, 'unknown set not invented');
    applySealedShard(data, null);
    assert(data.sets.M6.landed.shrink.landed_yen === 9, 'null shard is a no-op');
    return 'ok';
  }

  return {
    applySealedShard: applySealedShard,
    selfTest: selfTest
  };
});
