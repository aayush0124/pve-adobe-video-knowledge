// PVE harvest, runs inside After Effects. harvest_ae.py prepends `var HV = {i0, i1, out, scratch};`.
// Applies effects app.effects[i0..i1) one at a time to a solid, records every property (name, matchName, type,
// range, default), removes the effect, and ships the JSON to Premiere over BridgeTalk (AE scripts may not write
// files here; Premiere may). Pure ASCII.
(function () {
  var PT = {}; PT[PropertyType.PROPERTY] = "prop"; PT[PropertyType.INDEXED_GROUP] = "igroup"; PT[PropertyType.NAMED_GROUP] = "group";
  var VT = {};
  VT[PropertyValueType.NO_VALUE] = "none"; VT[PropertyValueType.ThreeD_SPATIAL] = "3d_spatial"; VT[PropertyValueType.ThreeD] = "3d";
  VT[PropertyValueType.TwoD_SPATIAL] = "2d_spatial"; VT[PropertyValueType.TwoD] = "2d"; VT[PropertyValueType.OneD] = "1d";
  VT[PropertyValueType.COLOR] = "color"; VT[PropertyValueType.CUSTOM_VALUE] = "custom"; VT[PropertyValueType.MARKER] = "marker";
  VT[PropertyValueType.LAYER_INDEX] = "layer"; VT[PropertyValueType.MASK_INDEX] = "mask"; VT[PropertyValueType.SHAPE] = "shape";
  VT[PropertyValueType.TEXT_DOCUMENT] = "text";
  function esc(s) { return String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/[\r\n\t]/g, " "); }
  function num(v) { return (typeof v == "number" && isFinite(v)) ? String(Math.round(v * 10000) / 10000) : "null"; }
  function val(v) {
    if (v === undefined || v === null) return "null";
    if (typeof v == "number") return num(v);
    if (typeof v == "boolean") return v ? "true" : "false";
    if (v instanceof Array) { var a = []; for (var i = 0; i < v.length; i++) a.push(num(v[i])); return "[" + a.join(",") + "]"; }
    return '"' + esc(v) + '"';
  }
  function props(g, depth) {
    var out = [];
    for (var i = 1; i <= g.numProperties; i++) {
      var p = g.property(i), s = '{"name":"' + esc(p.name) + '","match":"' + esc(p.matchName) + '","kind":"' + PT[p.propertyType] + '"';
      if (p.propertyType == PropertyType.PROPERTY) {
        s += ',"type":"' + (VT[p.propertyValueType] || p.propertyValueType) + '"';
        try { if (p.hasMin) s += ',"min":' + num(p.minValue); } catch (e1) {}
        try { if (p.hasMax) s += ',"max":' + num(p.maxValue); } catch (e2) {}
        if (p.propertyValueType != PropertyValueType.NO_VALUE && p.propertyValueType != PropertyValueType.CUSTOM_VALUE)
          try { s += ',"default":' + val(p.value); } catch (e3) {}
        try { if (p.isDropdownEffect) s += ',"dropdown":true'; } catch (e4) {}
        try { s += ',"animatable":' + (p.canVaryOverTime ? "true" : "false"); } catch (e5) {}
      } else if (depth < 4) s += ',"props":[' + props(p, depth + 1).join(",") + "]";
      out.push(s + "}");
    }
    return out;
  }
  function send(text) {
    var body = 'var f = new File(decodeURIComponent("' + encodeURIComponent(HV.out) + '")); f.encoding = "UTF-8"; f.open("w");' +
               ' f.write(decodeURIComponent("' + encodeURIComponent(text) + '")); f.close();';
    var bt = new BridgeTalk(); bt.target = "premierepro"; bt.body = body; bt.send();
  }
  var proj = app.newProject();
  var comp = proj.items.addComp("PVE", 1920, 1080, 1, 2, 25);
  var layer = comp.layers.addSolid([0.5, 0.5, 0.5], "S", 1920, 1080, 1, 2);
  var all = app.effects, recs = [];
  for (var i = HV.i0; i < Math.min(HV.i1, all.length); i++) {
    var ef = all[i], r = '{"i":' + i + ',"name":"' + esc(ef.displayName) + '","match":"' + esc(ef.matchName) + '","category":"' +
        esc(ef.category) + '","version":"' + esc(ef.version) + '"';
    if (HV.skip && HV.skip[String(i)]) { recs.push(r + ',"error":"skipped: crashed AE during harvest"}'); continue; }
    try {
      var e = layer.property("ADBE Effect Parade").addProperty(ef.matchName);
      r += ',"props":[' + props(e, 0).join(",") + "]";
      layer.property("ADBE Effect Parade").property(1).remove();
    } catch (err) { r += ',"error":"' + esc(err) + '"'; }
    recs.push(r + "}");
  }
  send('{"count":' + all.length + ',"i0":' + HV.i0 + ',"effects":[' + recs.join(",") + "]}");
  proj.save(new File(HV.scratch));  // leaves AE clean, so the next batch's newProject() asks nothing
})();
