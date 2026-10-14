// Runs inside Premiere Pro, delivered by After Effects over BridgeTalk (Premiere has no command-line script flag).
// adobe_bridge.py prepends `var S = {...};`. Result ("OK" / "ERR ...", then one warning per line) goes to S.pr_result.
// Premiere's ExtendScript has no JSON unless a panel (Premiere Composer) happened to load one first: a fresh Premiere
// failed every .mogrt text cue with "JSON is undefined" (2026-10-07). ES3 fallback for the .mogrt text-param objects.
if (typeof JSON === "undefined") {
  JSON = {
    parse: function (s) { return eval("(" + s + ")"); },
    stringify: function (v) {
      var i, out = [];
      if (v === null || v === undefined) return "null";
      if (typeof v == "number") return isFinite(v) ? String(v) : "null";
      if (typeof v == "boolean") return String(v);
      if (typeof v == "string") return '"' + v.replace(/[\\"\x00-\x1f]/g, function (c) {
        var m = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"};
        return m[c] || "\\u" + ("0000" + c.charCodeAt(0).toString(16)).slice(-4);
      }) + '"';
      if (v instanceof Array) { for (i = 0; i < v.length; i++) out.push(JSON.stringify(v[i])); return "[" + out.join(",") + "]"; }
      for (i in v) if (v.hasOwnProperty(i) && typeof v[i] != "function") out.push(JSON.stringify(String(i)) + ":" + JSON.stringify(v[i]));
      return "{" + out.join(",") + "}";
    }
  };
}
(function () {
  var warns = [];
  var f0 = new File(S.pr_result + ".start"); f0.open("w"); f0.write("1"); f0.close();  // tells the bridge the job arrived
  function done(msg) { var f = new File(S.pr_result); f.encoding = "UTF-8"; f.open("w"); f.write([msg].concat(warns).join("\n")); f.close(); }
  // v is a value, "#rrggbb", or keyframes [[t, value], ...] with t relative to the clip start (negative = from its end).
  function setp(p, v, k0, dur) {
    if (typeof v == "string" && v.charAt(0) == "#")
      return p.setColorValue(255, parseInt(v.substr(1, 2), 16), parseInt(v.substr(3, 2), 16), parseInt(v.substr(5, 2), 16), true);
    if (v instanceof Array && v.length && v[0] instanceof Array) {
      p.setTimeVarying(true);
      for (var k = 0; k < v.length; k++) {
        var kt = k0 + (v[k][0] < 0 ? dur + v[k][0] : v[k][0]);
        p.addKey(kt); p.setValueAtKey(kt, v[k][1], true);
      }
    } else p.setValue(v, true);
  }
  function norm(p) { return p.replace(/\\/g, "/").toLowerCase(); }
  function sec(s) { var t = new Time(); t.seconds = s; return t; }
  function comp(ti, name) {
    for (var k = 0; k < ti.components.numItems; k++) if (ti.components[k].displayName == name) return ti.components[k];
    throw "no " + name + " component on " + ti.name;
  }
  function prop(cp, name) {
    for (var k = 0; k < cp.properties.numItems; k++) if (cp.properties[k].displayName == name) return cp.properties[k];
    throw "no " + name + " property";
  }
  try {
    app.newProject(S.prproj);
    var proj = app.project, root = proj.rootItem, i, k;
    proj.importFiles(S.files, true, root, false);
    var items = {};
    for (i = 0; i < root.children.numItems; i++) {
      var p = root.children[i].getMediaPath();
      if (p) items[norm(p)] = root.children[i];
    }
    function item(src) {
      var it = items[norm(src)];
      if (!it) { var ks = []; for (var x in items) ks.push(x); throw "not imported: " + src + " | project " + proj.path + " has " + ks.join(", "); }
      return it;
    }
    function at(track, t) {
      for (var j = 0; j < track.clips.numItems; j++) if (Math.abs(track.clips[j].start.seconds - t) < 0.5 / S.fps) return track.clips[j];
      throw "clip not found at " + t;
    }
    function put(track, it, c) {
      if (!c.still) { it.setInPoint(c["in"], 4); it.setOutPoint(c["in"] + c.dur, 4); }
      track.overwriteClip(it, c.start);
      var ti = at(track, c.start);
      ti.end = sec(c.start + c.dur);
      return ti;
    }

    var seq = proj.createNewSequenceFromClips(S.name, [item(S.clips[0].src)], root);
    var st = seq.getSettings();
    st.videoFrameWidth = S.width; st.videoFrameHeight = S.height; st.videoFrameRate = sec(1 / S.fps);
    seq.setSettings(st);
    for (i = 0; i < seq.videoTracks.numTracks; i++) while (seq.videoTracks[i].clips.numItems) seq.videoTracks[i].clips[0].remove(false, false);
    for (i = 0; i < seq.audioTracks.numTracks; i++) while (seq.audioTracks[i].clips.numItems) seq.audioTracks[i].clips[0].remove(false, false);
    proj.openSequence(seq.sequenceID);
    app.enableQE();
    var nv = seq.videoTracks.numTracks, na = seq.audioTracks.numTracks;
    if (nv < S.video_tracks || na < S.audio_tracks)  // layered motion graphics need more than the default 3+3
      qe.project.getActiveSequence().addTracks(Math.max(0, S.video_tracks - nv), nv, Math.max(0, S.audio_tracks - na), 1, na, 0, 0, 0);
    if (seq.videoTracks.numTracks < S.video_tracks) throw "could not add video tracks (have " + seq.videoTracks.numTracks + ", need " + S.video_tracks + ")";

    for (i = 0; i < S.clips.length; i++) {
      var c = S.clips[i], ti = put(seq.videoTracks[c.track - 1], item(c.src), c);
      var sc = prop(comp(ti, "Motion"), "Scale"), k0 = ti.inPoint.seconds, zf = c.zoom_from || 1;
      if (c.zoom != zf) {  // push-in / pull-out over the clip (multipliers of the cover-fit scale)
        sc.setTimeVarying(true);
        sc.addKey(k0); sc.setValueAtKey(k0, c.scale * zf, true);
        sc.addKey(k0 + c.dur); sc.setValueAtKey(k0 + c.dur, c.scale * c.zoom, true);
      } else sc.setValue(c.scale * zf, true);
      if (c.pan) {  // drift: Motion Position [[x, y] at start, [x, y] at end], frame fractions (0.5, 0.5 = centre)
        var ps = prop(comp(ti, "Motion"), "Position");
        ps.setTimeVarying(true);
        ps.addKey(k0); ps.setValueAtKey(k0, c.pan[0], true);
        ps.addKey(k0 + c.dur); ps.setValueAtKey(k0 + c.dur, c.pan[1], true);
      }
      if (c.opacity_pct !== undefined) setp(prop(comp(ti, "Opacity"), "Opacity"), c.opacity_pct, k0, c.dur);  // ("opacity" is the AE crossfade plan)
    }
    for (i = 0; i < S.audio.length; i++) {
      var a = S.audio[i], at_ = put(seq.audioTracks[a.track - 1], item(a.src), a);
      // Premiere's Volume "Level" is linear with 0 dB = 10^(-15/20).
      var lv = Math.pow(10, ((a.gain_db || 0) - 15) / 20), lp = prop(comp(at_, "Volume"), "Level"), a0 = at_.inPoint.seconds;
      if (a.fade_in || a.fade_out) {  // linear fades (seconds) at the clip's ends
        lp.setTimeVarying(true);
        var fz = Math.pow(10, -90 / 20), ks = [[0, a.fade_in ? fz : lv], [a.fade_in || 0, lv], [a.dur - (a.fade_out || 0), lv], [a.dur, a.fade_out ? fz : lv]];
        for (k = 0; k < ks.length; k++) { lp.addKey(a0 + ks[k][0]); lp.setValueAtKey(a0 + ks[k][0], ks[k][1], true); }
      } else if (a.gain_db) lp.setValue(lv, true);
    }
    if (S.texts.length) {
      var names = [];
      for (i = 0; i < S.texts.length; i++) names.push(S.texts[i].comp);
      proj.importAEComps(S.titles_aep, names, root);
      for (i = 0; i < S.texts.length; i++) {
        var t = S.texts[i], ti = null;
        for (k = 0; k < root.children.numItems; k++) if (root.children[k].name.indexOf(t.comp) == 0) ti = root.children[k];
        if (!ti) throw "AE comp not linked: " + t.comp;
        seq.videoTracks[S.text_track - 1].overwriteClip(ti, t.start);
      }
    }
    // AE comps for heavy VFX shots, Dynamic-Linked (live: edit in AE, Premiere updates).
    var linked = {};
    for (i = 0; i < S.aecomps.length; i++) {
      var ac = S.aecomps[i];
      if (!linked[ac.aep]) { proj.importAEComps(ac.aep, S.aecomp_names[ac.aep], root); linked[ac.aep] = true; }
      var ai = null;
      for (k = 0; k < root.children.numItems; k++) if (root.children[k].name.indexOf(ac.comp) == 0) ai = root.children[k];
      if (!ai) throw "AE comp not linked: " + ac.comp;
      ai.setInPoint(ac["in"], 4); ai.setOutPoint(ac["in"] + ac.dur, 4);
      seq.videoTracks[ac.track - 1].overwriteClip(ai, ac.start);
      at(seq.videoTracks[ac.track - 1], ac.start).end = sec(ac.start + ac.dur);
    }
    // Motion graphics templates (AE-authored .mogrt): text runs, colours, any named parameter, stretched to fit.
    // "Name" or "Name#2" (2nd property with that display name). Templates reuse names ("Text" is a group, the text
    // and its colour), so a colour value only matches properties that hold a colour (packed ARGB64 > 2^32).
    function mparam(mc, name, colour) {
      var parts = name.split("#"), nth = parts.length > 1 ? +parts[1] : 1, seen = 0;
      for (var q = 0; q < mc.properties.numItems; q++) {
        var pq = mc.properties[q];
        if (pq.displayName != parts[0]) continue;
        if (colour && !(Number(pq.getValue()) > 4294967296)) continue;
        if (++seen == nth) return pq;
      }
      return null;
    }
    for (i = 0; i < S.mgts.length; i++) {
      var g = S.mgts[i], lbl = "mgt " + (i + 1) + " " + g.name;
      var mt = seq.importMGT(g.file, String(Math.round(g.start * 254016000000)), g.track - 1, 0);
      if (!mt) { warns.push(lbl + ": importMGT failed"); continue; }
      var mc = mt.getMGTComponent();
      if (!mc) { warns.push(lbl + ": no editable parameters (not an After Effects template)"); continue; }
      var du = mparam(mc, "Duration");
      if (du) du.setValue(g.dur, true);
      mt.end = sec(g.start + g.dur);
      if (g.dump) for (k = 0; k < mc.properties.numItems; k++)  // "dump": true lists a template's defaults as warnings
        warns.push(lbl + " | " + mc.properties[k].displayName + " = " + String(mc.properties[k].getValue()).substr(0, 120));
      var tps = [];
      for (k = 0; k < mc.properties.numItems; k++) {
        var tv = String(mc.properties[k].getValue());
        if (tv.charAt(0) == "{" && tv.indexOf("textEditValue") > 0) tps.push(mc.properties[k]);
      }
      for (k = 0; k < g.text.length; k++) {  // {"text", "param"?: field name (default: k-th text field), "font"?, "size"?}
        var tx = g.text[k], tp = tps[k];
        if (tx.param) { tp = null; for (var q2 = 0; q2 < tps.length; q2++) if (tps[q2].displayName == tx.param) tp = tps[q2]; }
        if (!tp) { warns.push(lbl + ": no text field " + (tx.param || k + 1) + " (has " + tps.length + ")"); continue; }
        var o = JSON.parse(tp.getValue());
        o.textEditValue = tx.text;
        o.fontTextRunLength = [tx.text.length];  // one style run covering the new text
        if (tx.font) o.fontEditValue = [tx.font];
        if (tx.size) o.fontSizeEditValue = [tx.size];
        tp.setValue(JSON.stringify(o), true);
      }
      for (var pn in g.params) {
        var pv = g.params[pn], pp = mparam(mc, pn, typeof pv == "string" && pv.charAt(0) == "#");
        if (!pp) { warns.push(lbl + ": no parameter '" + pn + "'"); continue; }
        if (typeof pv == "string" && pv.charAt(0) == "#")
          pp.setColorValue(255, parseInt(pv.substr(1, 2), 16), parseInt(pv.substr(3, 2), 16), parseInt(pv.substr(5, 2), 16), true);
        else pp.setValue(pv, true);
      }
    }
    proj.openSequence(seq.sequenceID);
    // Effects go on through the (undocumented) QE DOM, then get their parameters through the normal DOM.
    app.enableQE();
    var qs = qe.project.getActiveSequence(), known = {}, knownT = {}, fl = qe.project.getVideoEffectList();
    for (k = 0; k < fl.length; k++) known[fl[k]] = true;  // get...ByName returns an object even for unknown names
    fl = qe.project.getVideoTransitionList();
    for (k = 0; k < fl.length; k++) knownT[fl[k]] = true;
    if (S.dump_lists) {  // "dump_lists": true reports every effect / transition name Premiere has
      var el = qe.project.getVideoEffectList(), names2 = [];
      for (k = 0; k < el.length; k++) names2.push(el[k]);
      warns.push("EFFECTS " + names2.join("|"));
      names2 = [];
      for (k = 0; k < fl.length; k++) names2.push(fl[k]);
      warns.push("TRANSITIONS " + names2.join("|"));
    }
    function qitem(cl) {
      var qt = qs.getVideoTrackAt(cl.track - 1);
      for (var m = 0; m < qt.numItems; m++) if (qt.getItemAt(m).type == "Clip" && Math.abs(qt.getItemAt(m).start.secs - cl.start) < 0.5 / S.fps) return qt.getItemAt(m);
      throw "QE item not found at " + cl.start;
    }
    function tc(frames) {
      var f = Math.round(S.fps), s = Math.floor(frames / f), r = frames % f;
      return "00;00;" + (s < 10 ? "0" : "") + s + ";" + (r < 10 ? "0" : "") + r;
    }
    function addFx(x, label) {  // x: {track, start, dur, effects: [{pr_name, pr_props}]}; keys relative to the item's in point
      // Add every effect first, then give the nth spec of a name to the nth component of that name. Looking each one up
      // right after its add (last component with that name) put a repeated effect's values on the EARLIER instance and
      // left the new one at its defaults (two Strobe Lights: render-verified).
      var qi = qitem(x), pre = {}, seenN = {}, todo = [], t0 = at(seq.videoTracks[x.track - 1], x.start), e, fx;
      for (k = 0; k < t0.components.numItems; k++) pre[t0.components[k].displayName] = (pre[t0.components[k].displayName] || 0) + 1;
      for (e = 0; e < x.effects.length; e++) {
        fx = x.effects[e];
        if (!known[fx.pr_name]) { warns.push(label + ": no Premiere effect '" + fx.pr_name + "'"); continue; }
        qi.addVideoEffect(qe.project.getVideoEffectByName(fx.pr_name));
        seenN[fx.pr_name] = (seenN[fx.pr_name] || 0) + 1;
        todo.push({fx: fx, nth: (pre[fx.pr_name] || 0) + seenN[fx.pr_name]});
      }
      var tii = at(seq.videoTracks[x.track - 1], x.start);
      for (e = 0; e < todo.length; e++) {
        fx = todo[e].fx;
        var cp = null, cnt = 0;
        for (k = 0; k < tii.components.numItems && !cp; k++) if (tii.components[k].displayName == fx.pr_name && ++cnt == todo[e].nth) cp = tii.components[k];
        if (!cp) { warns.push(label + ": " + fx.pr_name + " #" + todo[e].nth + " added but not found on the clip"); continue; }
        for (var pn in fx.pr_props) {  // "Name" = first property with that name, "Name#2" = second (Lumetri repeats names)
          var pp = null, have = [], want = pn.split("#"), nth = want.length > 1 ? +want[1] : 1, seen = 0;
          for (k = 0; k < cp.properties.numItems; k++) {
            if (!pp && cp.properties[k].displayName == want[0] && ++seen == nth) pp = cp.properties[k];
            if (cp.properties[k].displayName) have.push(cp.properties[k].displayName);
          }
          if (!pp) { warns.push(label + ": " + fx.pr_name + " has no '" + pn + "' (has " + have.join(", ") + ")"); continue; }
          try { setp(pp, fx.pr_props[pn], tii.inPoint.seconds, x.dur); }
          catch (pe) { warns.push(label + ": " + fx.pr_name + " '" + pn + "' = " + fx.pr_props[pn] + " rejected (" + pe + ")"); }
        }
      }
    }
    for (i = 0; i < S.mgts.length; i++) if (S.mgts[i].effects.length) addFx(S.mgts[i], "mgt " + (i + 1) + " " + S.mgts[i].name);
    for (i = 0; i < S.aecomps.length; i++) if (S.aecomps[i].effects.length) addFx(S.aecomps[i], "AE comp " + S.aecomps[i].comp);
    for (i = 0; i < S.clips.length; i++) {
      var cl = S.clips[i], label = "clip " + (i + 1);
      addFx(cl, label);
      if (cl.dump) {  // "dump": true lists the clip's components and their properties (after its effects)
        var tid = at(seq.videoTracks[cl.track - 1], cl.start);
        for (k = 0; k < tid.components.numItems; k++) {
          var cpd = tid.components[k];
          for (var m2 = 0; m2 < cpd.properties.numItems; m2++)
            warns.push(label + " | " + cpd.displayName + " > " + cpd.properties[m2].displayName + " = " + String(cpd.properties[m2].getValue()).substr(0, 80));
        }
      }
      for (e = 0; e < cl.trans.length; e++) {
        var tr = cl.trans[e];
        if (!knownT[tr.name]) { warns.push(tr.label + ": no Premiere transition '" + tr.name + "'"); continue; }
        // (transition, at clip start?, duration, offset, alignment 0.5 = centre on the cut, single-sided, align to video)
        // one-sided ("single"): start at the cut going in, end at it going out, so a neighbouring item is never touched
        qitem(cl).addTransition(qe.project.getVideoTransitionByName(tr.name), tr.side == "in", tc(tr.frames), "00;00;00;00",
                                tr.single ? (tr.side == "in" ? 0 : 1) : 0.5, tr.single, true);
      }
    }
    // Read back where Premiere put each transition: AE builds the same window, so a mismatch means the apps differ.
    for (i = 0; i < S.clips.length; i++) {
      for (e = 0; e < S.clips[i].trans.length; e++) {
        var want = S.clips[i].trans[e], col = seq.videoTracks[S.clips[i].track - 1].transitions, got = [], hit = false;
        if (!knownT[want.name]) continue;
        for (k = 0; k < col.numItems; k++) {
          var ts0 = col[k].start.seconds, ts1 = col[k].end.seconds;
          if (Math.abs(ts0 - want.t0) < 0.5 / S.fps && Math.abs(ts1 - want.t1) < 0.5 / S.fps) hit = true;
          got.push(ts0.toFixed(3) + "-" + ts1.toFixed(3));
        }
        if (!hit) warns.push(want.label + ": Premiere placed transitions at [" + got.join(", ") + "], planned " + want.t0.toFixed(3) + "-" + want.t1.toFixed(3));
      }
    }
    proj.save();
    if (S.render) {
      // Needs native backslash paths; forward slashes fail with "Unknown Error".
      var r = seq.exportAsMediaDirect(new File(S.pr_mp4).fsName, new File(S.epr).fsName, app.encoder.ENCODE_ENTIRE);
      if (!new File(S.pr_mp4).exists) throw "export failed (" + r + ")";
    }
    done("OK");
  } catch (e) { done("ERR " + String(e).replace(/\s+/g, " ") + " line " + e.line); }
})();
