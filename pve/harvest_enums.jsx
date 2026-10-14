// PVE dropdown readback, runs inside After Effects. harvest_enums.py prepends `var HV = {out, scratch, jobs, text}`.
// jobs: [{i, match, props: [{path: [match names from the effect], name, max, default}]}]. Per candidate: whether it
// takes only Hold keyframes (isInterpolationTypeValid(LINEAR) false = a popup or checkbox), hasMax / maxValue, then
// 1..max set and read back, then 1.5 (an in-range fraction: a popup snaps it, a slider keeps it), default restored.
// Out-of-range values are never sent (they kill an AE script outright). text: also probe every Hold-only 1d property
// of a text layer carrying an animator with all its properties and the three selectors. The JSON goes to Premiere
// over BridgeTalk (AE scripts may not write files here). Pure ASCII.
(function () {
  function js(v) {
    if (v === null || v === undefined) return "null";
    if (typeof v == "number") return isFinite(v) ? String(Math.round(v * 10000) / 10000) : "null";
    if (typeof v == "boolean") return v ? "true" : "false";
    if (typeof v == "string") return '"' + v.replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/[^\u0020-\u007e]/g, function (c) {
      var h = c.charCodeAt(0).toString(16); return "\\u" + "0000".substr(h.length) + h; }) + '"';  // JSON escapes keep non-ASCII
    if (v instanceof Array) { var a = []; for (var i = 0; i < v.length; i++) a.push(js(v[i])); return "[" + a.join(",") + "]"; }
    var o = []; for (var k in v) if (v.hasOwnProperty(k)) o.push(js(String(k)) + ":" + js(v[k])); return "{" + o.join(",") + "}";
  }
  function send(text) {
    var body = 'var f = new File(decodeURIComponent("' + encodeURIComponent(HV.out) + '")); f.encoding = "UTF-8"; f.open("w");' +
               ' f.write(decodeURIComponent("' + encodeURIComponent(text) + '")); f.close();';
    var bt = new BridgeTalk(); bt.target = "premierepro"; bt.body = body; bt.send();
  }
  var reflect = null;
  function probe(p, max, dflt) {
    var r = {};
    try { r.linear_ok = p.isInterpolationTypeValid(KeyframeInterpolationType.LINEAR); } catch (e0) { r.linear_error = String(e0); }
    try { r.has_max = p.hasMax; if (p.hasMax) r.api_max = p.maxValue; if (p.hasMin) r.api_min = p.minValue; } catch (e1) {}
    try { r.units = p.unitsText; } catch (e2) {}
    if (!reflect) {
      reflect = {properties: [], methods: []};
      try { var rp = p.reflect.properties; for (var a = 0; a < rp.length; a++) reflect.properties.push(String(rp[a].name)); } catch (e3) {}
      try { var rm = p.reflect.methods; for (var b = 0; b < rm.length; b++) reflect.methods.push(String(rm[b].name)); } catch (e4) {}
    }
    if (max === undefined || max === null) max = r.api_max;
    if (!max || (r.has_max && max > r.api_max)) { r.error = "no safe max"; return r; }
    try { r.value_text_now = String(p.valueText); } catch (eT) { r.value_text_error = String(eT); }
    try { var pp = p.propertyParameters; if (pp && pp.length !== undefined) { r.parameters = []; for (var q2 = 0; q2 < pp.length; q2++) r.parameters.push(String(pp[q2])); } }
    catch (eP) { r.parameters_error = String(eP); }
    var set = [], v, texts = {};
    for (v = 1; v <= max; v++) {
      try { p.setValue(v); if (Math.abs(p.value - v) < 1e-6) set.push(v); else { r.mismatch = [v, p.value]; break; } }
      catch (e5) { r.refused_at = v; r.error = String(e5); break; }
      try { texts[String(v)] = String(p.valueText); } catch (e8) {}  // 26.0: the label of the current popup item
    }
    r.settable = set;
    r.value_texts = texts;
    if (max >= 2) { try { p.setValue(1.5); r.half = p.value; } catch (e6) { r.half_error = String(e6); } }
    try { p.setValue(dflt || 1); } catch (e7) {}
    return r;
  }
  function byPath(e, path) {
    var p = e;
    for (var k = 1; k < path.length; k++) p = p.property(path[k]);
    return p;
  }
  var proj = app.newProject(), res = {effects: {}, text: [], version: app.version};
  var comp = proj.items.addComp("PVE", 640, 360, 1, 2, 25);
  var layer = comp.layers.addSolid([0.5, 0.5, 0.5], "S", 640, 360, 1, 2);
  for (var j = 0; j < HV.jobs.length; j++) {
    var J = HV.jobs[j], out = {};
    try {
      layer.property("ADBE Effect Parade").addProperty(J.match);
      for (var q = 0; q < J.props.length; q++) {
        var P = J.props[q];
        try {
          var e = layer.property("ADBE Effect Parade").property(1);  // re-fetched: adding invalidates references
          var p = e;
          for (var k = 0; k < P.path.length; k++) p = p.property(P.path[k]);
          var r = probe(p, P.max, P["default"]);
          r.path = P.path; r.name = P.name;
          out[P.name] = r;
        } catch (pe) { out[P.name] = {name: P.name, error: String(pe)}; }
      }
      layer.property("ADBE Effect Parade").property(1).remove();
    } catch (ee) { out._error = String(ee); }
    res.effects[J.match] = out;
  }
  if (HV.text) {
    var tl = comp.layers.addText("PVE 1947");
    var TP = function () { return tl.property("ADBE Text Properties"); };
    TP().property("ADBE Text Animators").addProperty("ADBE Text Animator");
    var adds = ["ADBE Text Anchor Point 3D", "ADBE Text Position 3D", "ADBE Text Scale 3D", "ADBE Text Skew",
                "ADBE Text Rotation", "ADBE Text Opacity", "ADBE Text Fill Color", "ADBE Text Stroke Color",
                "ADBE Text Stroke Width", "ADBE Text Tracking Amount", "ADBE Text Line Anchor", "ADBE Text Line Spacing",
                "ADBE Text Character Offset", "ADBE Text Character Replace", "ADBE Text Blur"];
    for (var a2 = 0; a2 < adds.length; a2++) {
      try { TP().property("ADBE Text Animators").property(1).property("ADBE Text Animator Properties").addProperty(adds[a2]); }
      catch (ae) { res.text.push({name: adds[a2], add_error: String(ae)}); }
    }
    var sels = ["ADBE Text Selector", "ADBE Text Wiggly Selector", "ADBE Text Expressible Selector"];
    for (var s2 = 0; s2 < sels.length; s2++) {
      try { TP().property("ADBE Text Animators").property(1).property("ADBE Text Selectors").addProperty(sels[s2]); }
      catch (se) { res.text.push({name: sels[s2], add_error: String(se)}); }
    }
    var found = [];
    var walk = function (g, names, matches) {
      for (var i = 1; i <= g.numProperties; i++) {
        var c = g.property(i);
        if (c.matchName == "ADBE Text Document") continue;
        var n2 = names.concat([c.name]), m2 = matches.concat([c.matchName]);
        if (c.propertyType != PropertyType.PROPERTY) walk(c, n2, m2);
        else if (c.propertyValueType == PropertyValueType.OneD) {
          var hold = false;
          try { hold = !c.isInterpolationTypeValid(KeyframeInterpolationType.LINEAR); } catch (he) {}
          if (hold) found.push({names: n2, matches: m2});
        }
      }
    };
    walk(TP(), [], []);
    for (var f2 = 0; f2 < found.length; f2++) {
      var F = found[f2], pp = TP();
      try {
        for (var m = 0; m < F.matches.length; m++) pp = pp.property(F.matches[m]);
        var rr = probe(pp, null, pp.value);
        rr.path = F.matches; rr.name = F.names[F.names.length - 1]; rr.names = F.names;
        rr.count = rr.settable ? rr.settable.length : null; rr.discrete = true; rr.match = F.matches[F.matches.length - 1];
        res.text.push(rr);
      } catch (te) { res.text.push({names: F.names, error: String(te)}); }
    }
  }
  res.api_reflect = reflect;  // (a property named "reflect" is ExtendScript's own, read-only)
  send(js(res));
  proj.save(new File(HV.scratch));  // leaves AE clean, so the next batch's newProject() asks nothing
})();
