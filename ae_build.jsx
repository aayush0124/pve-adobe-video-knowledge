// Runs inside After Effects. adobe_bridge.py prepends `var S = {...};` + ae_lib.jsx (shared helpers).
// Keep this file pure ASCII: ExtendScript garbles UTF-8. Errors are reported as an "AB_ERROR ... ~" comp.
(function () {
  var proj = null;
  function place(l, c) {  // ae_in/ae_out: a crossfade extends a clip into its handles
    l.startTime = c.start - c["in"];
    l.inPoint = c.ae_in === undefined ? c.start : c.ae_in;
    l.outPoint = c.ae_out === undefined ? c.start + c.dur : c.ae_out;
  }
  try {
    if (app.project && app.project.dirty) {
      alert("AdobeBridge: save or close your current After Effects project, then run again.");
      return;
    }
    proj = app.newProject();
    var main = proj.items.addComp(S.name, S.width, S.height, 1, S.duration, S.fps);
    var cache = {};
    function footage(src) {
      if (!cache[src]) cache[src] = proj.importFile(new ImportOptions(new File(src)));
      return cache[src];
    }
    var i, l;
    // layers.add() stacks on top, so add bottom-up: audio, clips by track, texts.
    for (i = 0; i < S.audio.length; i++) {
      var a = S.audio[i];
      l = main.layers.add(footage(a.src));
      place(l, a);
      if (a.gain_db) l.property("Audio").property("Audio Levels").setValue([a.gain_db, a.gain_db]);
    }
    function solid(s) {  // dip transitions: a colour solid between (or above) the clips
      var sl = main.layers.addSolid(hex(s.color), "TRANSITION " + s.color, S.width, S.height, 1, S.duration);
      sl.inPoint = s["in"]; sl.outPoint = s.out;
      for (var g = 0; g < s.segs.length; g++) seg(sl.property("Opacity"), s.segs[g]);
    }
    var o, j;
    for (o = 0; o < S.clip_order.length; o++) {
      i = S.clip_order[o];
      var c = S.clips[i];
      for (j = 0; j < c.solids.length; j++) if (!c.solids[j].above) solid(c.solids[j]);
      l = main.layers.add(footage(c.src));
      place(l, c);
      var sc = l.property("Scale");
      if (c.zoom != 1) {
        sc.setValueAtTime(c.start, [c.scale, c.scale]);
        sc.setValueAtTime(c.start + c.dur, [c.scale * c.zoom, c.scale * c.zoom]);
      } else sc.setValue([c.scale, c.scale]);
      dress(l, main, c, "clip " + (i + 1), c.start, c.dur);
      for (j = 0; j < c.opacity.length; j++) seg(l.property("Opacity"), c.opacity[j]);
      for (j = 0; j < c.tpresets.length; j++) preset(l, main, c.tpresets[j].file, c.tpresets[j].t);
      for (j = 0; j < c.solids.length; j++) if (c.solids[j].above) solid(c.solids[j]);
    }
    for (i = 0; i < S.texts.length; i++) {
      var t = S.texts[i];
      var tc = proj.items.addComp(t.comp, S.width, S.height, 1, t.dur, S.fps);
      var tl = tc.layers.addBoxText([S.width * 0.86, S.height * 0.5], t.text);
      styleText(tl, t, t.comp);
      var r = tl.sourceRectAtTime(0, false);
      tl.property("Anchor Point").setValue([r.left + r.width / 2, r.top + r.height / 2]);
      tl.property("Position").setValue([S.width / 2, S.height * t.y]);
      if (t.anim) {
        var f = Math.min(0.25, t.dur / 4), op = tl.property("Opacity"), ts = tl.property("Scale");
        op.setValueAtTime(0, 0); op.setValueAtTime(f, 100); op.setValueAtTime(t.dur - f, 100); op.setValueAtTime(t.dur, 0);
        ts.setValueAtTime(0, [85, 85]); ts.setValueAtTime(f, [100, 100]);
      }
      dress(tl, tc, t, t.comp, 0, t.dur);
      l = main.layers.add(tc);
      l.startTime = t.start;
    }
    var rq = proj.renderQueue.items.add(main), om = rq.outputModule(1), tpl = om.templates;
    for (i = 0; i < tpl.length; i++) if (/H\.264/.test(tpl[i])) { om.applyTemplate(tpl[i]); break; }
    om.file = new File(S.ae_mp4);
    main.time = 0;
    main.openInViewer();
  } catch (e) {
    if (!proj) { alert("AdobeBridge: " + String(e)); return; }
    proj.items.addComp(("AB_ERROR " + String(e).replace(/\s+/g, " ") + " line " + e.line).substr(0, 190) + " ~", 16, 16, 1, 1, 30);
  }
  flushWarns(proj);
  // Premiere Dynamic-Links its titles to a copy: it can't render linked comps from the .aep open in this window,
  // and closing/reopening a project from a script makes AE ignore every later `-r` script.
  if (S.texts.length) proj.save(new File(S.titles_aep));
  proj.save(new File(S.aep));
})();
