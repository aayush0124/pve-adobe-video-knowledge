// PVE audio map, runs inside Premiere Pro (live_tests.py prepends `var PV = {...};` and relays it through After
// Effects over BridgeTalk). PV: {result, prproj, files: [paths], tests: [{k, src, start, in, dur, effect, set}],
// probe, export, epr}. Every test is a clip on A1 (start / in / dur in s); `effect` (a QE audio effect name) is added
// and its parameters set by display name ("Name#2" = the second with that name). Every effect component is dumped
// after setting (probe: numeric ranges too, by setting far out of range and reading the clamp back), then the
// sequence is exported (audio only, PV.epr) to PV.export. Result JSON at PV.result; PV.result + ".start" first.
(function () {
  function js(v) {
    if (v === null || v === undefined) return "null";
    if (typeof v == "number") return isFinite(v) ? String(v) : "null";
    if (typeof v == "boolean") return v ? "true" : "false";
    if (typeof v == "string") return '"' + v.replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/[^\u0020-\u007e]/g, function (c) {
      var h = c.charCodeAt(0).toString(16); return "\\u" + "0000".substr(h.length) + h; }) + '"';
    if (v instanceof Array) { var a = []; for (var i = 0; i < v.length; i++) a.push(js(v[i])); return "[" + a.join(",") + "]"; }
    var o = []; for (var k in v) if (v.hasOwnProperty(k)) o.push(js(String(k)) + ":" + js(v[k])); return "{" + o.join(",") + "}";
  }
  function w(path, obj) { var f = new File(path); f.encoding = "UTF-8"; f.open("w"); f.write(js(obj)); f.close(); }
  function norm(p) { return p.replace(/\\/g, "/").toLowerCase(); }
  function dump(c, probe) {
    var ps = [];
    for (var k = 0; k < c.properties.numItems; k++) {
      var p = c.properties[k], rec = {name: p.displayName}, v = null;
      try { v = p.getValue(); } catch (e0) { rec.error = String(e0); }
      if (typeof v == "number" || typeof v == "boolean") rec.value = v;
      else if (typeof v == "string") rec.value = v.substr(0, 120);
      if (probe && typeof v == "number") {
        try { p.setValue(1e9, true); rec.max = p.getValue(); p.setValue(-1e9, true); rec.min = p.getValue(); p.setValue(v, true); }
        catch (e1) { rec.probe_error = String(e1); try { p.setValue(v, true); } catch (e2) {} }
      }
      ps.push(rec);
    }
    return ps;
  }
  w(PV.result + ".start", {ok: true});
  try {
    app.newProject(PV.prproj);
    var proj = app.project, root = proj.rootItem, i, k;
    proj.importFiles(PV.files, true, root, false);
    var items = {};
    for (i = 0; i < root.children.numItems; i++) { var mp = root.children[i].getMediaPath(); if (mp) items[norm(mp)] = root.children[i]; }
    var first = items[norm(PV.files[0])];
    if (!first) throw "import failed";
    var seq = proj.createNewSequenceFromClips("AUDIO_MAP", [first], root);
    for (i = 0; i < seq.videoTracks.numTracks; i++) while (seq.videoTracks[i].clips.numItems) seq.videoTracks[i].clips[0].remove(false, false);
    for (i = 0; i < seq.audioTracks.numTracks; i++) while (seq.audioTracks[i].clips.numItems) seq.audioTracks[i].clips[0].remove(false, false);
    proj.openSequence(seq.sequenceID);
    var T = PV.tests, recs = [];
    for (i = 0; i < T.length; i++) {
      var it = items[norm(T[i].src)];
      it.setInPoint(T[i]["in"], 4); it.setOutPoint(T[i]["in"] + T[i].dur, 4);
      seq.audioTracks[0].overwriteClip(it, T[i].start);
    }
    app.enableQE();
    // The tests sit on A1 in order, so the n-th clip is test n: matching by start time missed clips that Premiere had
    // snapped to its frame grid (a start of 3.5 s in a 25 fps timebase landed 0.02 s off).
    var qa = qe.project.getActiveSequence().getAudioTrackAt(0), ac = seq.audioTracks[0].clips, qcl = [];
    for (k = 0; k < qa.numItems; k++) if (qa.getItemAt(k).type == "Clip") qcl.push(qa.getItemAt(k));
    if (qcl.length != T.length || ac.numItems != T.length) throw "placed " + ac.numItems + " clips (QE " + qcl.length + ") for " + T.length + " tests";
    for (i = 0; i < T.length; i++) {
      var t = T[i], rec = {k: t.k};
      try {
        var qi = qcl[i], ti = ac[i];
        rec.start_s = ti.start.seconds;
        if (t.effect) {
          var before = ti.components.numItems;
          qi.addAudioEffect(qe.project.getAudioEffectByName(t.effect));
          var c = ti.components[ti.components.numItems - 1];
          rec.added = ti.components.numItems > before;
          rec.component = c.displayName;
          try { rec.match = c.matchName; } catch (em) {}
          rec.set = {};
          for (var pn in t.set) {
            var parts = pn.split("#"), nth = parts.length > 1 ? +parts[1] : 1, seen = 0, pp = null;
            for (k = 0; k < c.properties.numItems; k++) if (c.properties[k].displayName == parts[0] && ++seen == nth) { pp = c.properties[k]; break; }
            if (!pp) { rec.set[pn] = "no such property"; continue; }
            try { pp.setValue(t.set[pn], true); rec.set[pn] = pp.getValue(); } catch (pe) { rec.set[pn] = "rejected: " + String(pe); }
          }
          rec.props = dump(c, PV.probe);
        }
      } catch (err) { rec.error = String(err); }
      recs.push(rec);
    }
    proj.save();
    var ok = null;
    if (PV["export"]) {
      ok = seq.exportAsMediaDirect(new File(PV["export"]).fsName, new File(PV.epr).fsName, app.encoder.ENCODE_ENTIRE);
      if (!new File(PV["export"]).exists) throw "export failed (" + ok + ")";
    }
    w(PV.result, {ok: true, tests: recs, sequence_end: seq.end ? Number(seq.end) / 254016000000 : null});
  } catch (e) { w(PV.result, {ok: false, error: String(e), line: e.line}); }
})();
