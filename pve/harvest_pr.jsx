// PVE harvest, runs inside Premiere Pro (sent over BridgeTalk by harvest_pr.py, which prepends `var PV = {...};`).
// 1. Lists every video/audio effect and transition (QE).
// 2. Effect gallery: one 2 s test clip per video effect (defaults), every property dumped; numeric properties get
//    their range probed (set far out of range, read back the clamp, restore the default).
// 3. Audio effects: one audio clip per effect, properties dumped the same way.
// 4. Transition gallery: clip pairs, one 1 s centred transition per cut.
// Results go to PV.out_dir (written after every item: the last file is the breadcrumb if Premiere dies).
(function () {
  // Premiere's engine has no JSON object outside some contexts: a small serializer for plain data.
  function js(v) {
    if (v === null || v === undefined) return "null";
    if (typeof v == "number") return isFinite(v) ? String(v) : "null";
    if (typeof v == "boolean") return v ? "true" : "false";
    // Pure-ASCII output: Lumetri exposes binary junk strings, and one unencodable character truncates File.write.
    if (typeof v == "string") return '"' + v.replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/[^\u0020-\u007e]/g, function (c) {
      var h = c.charCodeAt(0).toString(16); return "\\u" + "0000".substr(h.length) + h; }) + '"';
    if (v instanceof Array) { var a = []; for (var i = 0; i < v.length; i++) a.push(js(v[i])); return "[" + a.join(",") + "]"; }
    var o = []; for (var k in v) if (v.hasOwnProperty(k)) o.push(js(String(k)) + ":" + js(v[k])); return "{" + o.join(",") + "}";
  }
  function w(name, obj) { var f = new File(PV.out_dir + "/" + name); f.encoding = "UTF-8"; f.open("w"); f.write(js(obj)); f.close(); }
  function sec(s) { var t = new Time(); t.seconds = s; return t; }
  function list(a) { var o = []; for (var i = 0; i < a.length; i++) o.push(a[i]); return o; }
  function dump(comp, probe) {
    var ps = [];
    for (var k = 0; k < comp.properties.numItems; k++) {
      var p = comp.properties[k], rec = {name: p.displayName}, v = null;
      try { v = p.getValue(); } catch (e0) { rec.error = String(e0); }
      if (typeof v == "number" || typeof v == "boolean" || typeof v == "string") rec.value = (typeof v == "string") ? v.substr(0, 200) : v;
      else if (v && v.length !== undefined) { var a = []; for (var j = 0; j < v.length; j++) a.push(v[j]); rec.value = a; }
      if (typeof v == "boolean") rec.type = "bool";
      else if (typeof v == "number" && v > 4294967296) rec.type = "color";
      else if (typeof v == "number") rec.type = "number";
      else if (typeof v == "string") rec.type = (v.charAt(0) == "{" ? "json" : "string");
      else if (v && v.length !== undefined) rec.type = "point";
      if (probe && rec.type == "number") {
        try { p.setValue(1e9, true); rec.max = p.getValue(); p.setValue(-1e9, true); rec.min = p.getValue(); p.setValue(v, true); }
        catch (e1) { rec.probe_error = String(e1); try { p.setValue(v, true); } catch (e2) {} }
      }
      ps.push(rec);
    }
    return ps;
  }
  try {
    app.enableQE();
    var lists = {video_effects: list(qe.project.getVideoEffectList()), video_transitions: list(qe.project.getVideoTransitionList()),
                 audio_effects: list(qe.project.getAudioEffectList()), audio_transitions: list(qe.project.getAudioTransitionList())};
    w("pr_lists.json", lists);
    app.newProject(PV.prproj);
    var proj = app.project, root = proj.rootItem, i, k;
    proj.importFiles([PV.a, PV.b, PV.wav], true, root, false);
    var items = {};
    for (i = 0; i < root.children.numItems; i++) items[root.children[i].name] = root.children[i];
    var A = items[PV.a_name], B = items[PV.b_name], WAV = items[PV.wav_name];
    if (!A || !B || !WAV) throw "import failed";

    // ---- video effect gallery
    var seq = proj.createNewSequenceFromClips("FX_GALLERY", [A], root);
    var st = seq.getSettings(); st.videoFrameWidth = 1920; st.videoFrameHeight = 1080; st.videoFrameRate = sec(1 / 25); seq.setSettings(st);
    while (seq.videoTracks[0].clips.numItems) seq.videoTracks[0].clips[0].remove(false, false);
    while (seq.audioTracks[0].clips.numItems) seq.audioTracks[0].clips[0].remove(false, false);
    proj.openSequence(seq.sequenceID);
    var fx = lists.video_effects, n = Math.min(PV.fx_max || fx.length, fx.length), out = [];
    A.setInPoint(1.0, 4); A.setOutPoint(3.0, 4);
    for (i = 0; i <= n; i++) seq.videoTracks[0].overwriteClip(A, i * 2);  // clip 0 = untouched reference
    var qs = qe.project.getActiveSequence(), qt = qs.getVideoTrackAt(0);
    for (i = 0; i < n; i++) {
      var rec = {i: i, name: fx[i], clip_start: (i + 1) * 2};
      try {
        var qi = null;
        for (k = 0; k < qt.numItems; k++) if (qt.getItemAt(k).type == "Clip" && Math.abs(qt.getItemAt(k).start.secs - (i + 1) * 2) < 0.02) qi = qt.getItemAt(k);
        qi.addVideoEffect(qe.project.getVideoEffectByName(fx[i]));
        var ti = null, tc = seq.videoTracks[0].clips;
        for (k = 0; k < tc.numItems; k++) if (Math.abs(tc[k].start.seconds - (i + 1) * 2) < 0.02) ti = tc[k];
        var c = ti.components[ti.components.numItems - 1];
        rec.component = c.displayName;
        try { rec.match = c.matchName; } catch (em) {}
        rec.props = dump(c, true);
      } catch (err) { rec.error = String(err); }
      out.push(rec);
      w("pr_video_effects.json", out);
    }
    proj.save();
    if (PV.render) seq.exportAsMediaDirect(new File(PV.out_dir + "/fx_gallery.mp4").fsName, new File(PV.epr).fsName, app.encoder.ENCODE_ENTIRE);
    if (PV.video_only) { w("pr_done.json", {ok: true, video_only: true}); return; }

    // ---- audio effects
    var aseq = proj.createNewSequenceFromClips("AUDIO_FX", [WAV], root), afx = lists.audio_effects, aout = [];
    proj.openSequence(aseq.sequenceID);
    while (aseq.audioTracks[0].clips.numItems) aseq.audioTracks[0].clips[0].remove(false, false);
    WAV.setInPoint(0, 4); WAV.setOutPoint(1.5, 4);
    for (i = 0; i < afx.length; i++) aseq.audioTracks[0].overwriteClip(WAV, i * 2);
    var qa = qe.project.getActiveSequence().getAudioTrackAt(0);
    for (i = 0; i < afx.length; i++) {
      var ar = {i: i, name: afx[i]};
      try {
        var qai = null;
        for (k = 0; k < qa.numItems; k++) if (qa.getItemAt(k).type == "Clip" && Math.abs(qa.getItemAt(k).start.secs - i * 2) < 0.02) qai = qa.getItemAt(k);
        qai.addAudioEffect(qe.project.getAudioEffectByName(afx[i]));
        var at = null, ac = aseq.audioTracks[0].clips;
        for (k = 0; k < ac.numItems; k++) if (Math.abs(ac[k].start.seconds - i * 2) < 0.02) at = ac[k];
        var ca = at.components[at.components.numItems - 1];
        ar.component = ca.displayName;
        try { ar.match = ca.matchName; } catch (em2) {}
        ar.props = dump(ca, true);
      } catch (aerr) { ar.error = String(aerr); }
      aout.push(ar);
      w("pr_audio_effects.json", aout);
    }
    proj.save();

    // ---- transition gallery: A (out at 3.0, 0.96 s of handle) | B (in at 1.5), 1 s centred transition on each cut
    var tseq = proj.createNewSequenceFromClips("TR_GALLERY", [A], root), tr = lists.video_transitions, tout = [];
    proj.openSequence(tseq.sequenceID);
    while (tseq.videoTracks[0].clips.numItems) tseq.videoTracks[0].clips[0].remove(false, false);
    while (tseq.audioTracks[0].clips.numItems) tseq.audioTracks[0].clips[0].remove(false, false);
    B.setInPoint(1.5, 4); B.setOutPoint(3.5, 4);
    for (i = 0; i < tr.length; i++) { tseq.videoTracks[0].overwriteClip(A, i * 4); tseq.videoTracks[0].overwriteClip(B, i * 4 + 2); }
    var qtt = qe.project.getActiveSequence().getVideoTrackAt(0);
    for (i = 0; i < tr.length; i++) {
      var trc = {i: i, name: tr[i], cut: i * 4 + 2};
      try {
        var qb = null;
        for (k = 0; k < qtt.numItems; k++) if (qtt.getItemAt(k).type == "Clip" && Math.abs(qtt.getItemAt(k).start.secs - (i * 4 + 2)) < 0.02) qb = qtt.getItemAt(k);
        qb.addTransition(qe.project.getVideoTransitionByName(tr[i]), true, "00;00;01;00", "00;00;00;00", 0.5, false, true);
        var col = tseq.videoTracks[0].transitions, got = null;
        for (k = 0; k < col.numItems; k++) if (Math.abs(col[k].start.seconds - (i * 4 + 1.5)) < 0.6) got = [col[k].start.seconds, col[k].end.seconds];
        trc.placed = got;
      } catch (terr) { trc.error = String(terr); }
      tout.push(trc);
      w("pr_transitions.json", tout);
    }
    proj.save();
    if (PV.render) tseq.exportAsMediaDirect(new File(PV.out_dir + "/tr_gallery.mp4").fsName, new File(PV.epr).fsName, app.encoder.ENCODE_ENTIRE);
    w("pr_done.json", {ok: true});
  } catch (e) { w("pr_done.json", {ok: false, error: String(e), line: e.line}); }
})();
