// Shared After Effects helpers, prepended to ae_build.jsx (edit) and vfx_build.jsx (VFX shots). Pure ASCII.
// AE's "allow scripts to write files" pref is OFF here, so problems are reported as "AB_WARN ... ~" comps.
var AB_WARNS = [];
function warn(m) { for (var k = 0; k < AB_WARNS.length; k++) if (AB_WARNS[k] == m) return; AB_WARNS.push(m); }
function flushWarns(proj) { for (var w = 0; w < AB_WARNS.length; w++) proj.items.addComp(("AB_WARN " + AB_WARNS[w]).substr(0, 190) + " ~", 16, 16, 1, 1, 30); }
function hex(c) { return [parseInt(c.substr(1, 2), 16) / 255, parseInt(c.substr(3, 2), 16) / 255, parseInt(c.substr(5, 2), 16) / 255]; }
function val(v) { return (typeof v == "string" && v.charAt(0) == "#") ? hex(v).concat([1]) : v; }
function preset(l, comp, file, time) {  // keyframed presets start at the comp's current time
  for (var j = 1; j <= comp.numLayers; j++) comp.layer(j).selected = false;
  l.selected = true;
  comp.time = time;
  l.applyPreset(new File(file));
  l.selected = false;
}
// Segment [t0, v0, t1, v1, ease]: lin | smooth (AE Easy Ease = smoothstep, Premiere's dissolve curve)
// | out (fast start) | in (slow start), the last two quadratic.
function seg(p, g) {
  p.setValueAtTime(g[0], val(g[1]));
  if (g[2] == g[0]) return;
  p.setValueAtTime(g[2], val(g[3]));
  if (g[4] == "lin") return;
  var k0 = p.nearestKeyIndex(g[0]), k1 = p.nearestKeyIndex(g[2]), B = KeyframeInterpolationType.BEZIER;
  var n = p.keyInTemporalEase(k0).length, arr = g[1] instanceof Array, i0 = [], i1 = [], T = g[2] - g[0], dist = 0, d;
  if (arr) for (d = 0; d < g[1].length; d++) dist += Math.pow(g[3][d] - g[1][d], 2);
  for (d = 0; d < n; d++) {  // one ease per dimension (scale has 3), or one for spatial props (speed along the path)
    var dv = !arr ? g[3] - g[1] : (n == 1 ? Math.sqrt(dist) : (g[3][d] || 0) - (g[1][d] || 0));
    i0.push(new KeyframeEase(g[4] == "out" ? 2 * dv / T : 0, 33.333));
    i1.push(new KeyframeEase(g[4] == "in" ? 2 * dv / T : 0, 33.333));
  }
  p.setInterpolationTypeAtKey(k0, p.keyInInterpolationType(k0), B);
  p.setInterpolationTypeAtKey(k1, B, p.keyOutInterpolationType(k1));
  p.setTemporalEaseAtKey(k0, p.keyInTemporalEase(k0), i0);
  p.setTemporalEaseAtKey(k1, i1, p.keyOutTemporalEase(k1));
}
// v is a value, or keyframes [[t, value], ...] with t relative to the layer start (negative = from its end).
// An out-of-range value kills an AE script outright (not catchable), so numbers are clamped to the property's range.
function fit(p, v) {
  if (typeof v != "number") return val(v);
  var c = v;
  if (p.hasMin && c < p.minValue) c = p.minValue;
  if (p.hasMax && c > p.maxValue) c = p.maxValue;
  if (c != v) warn(p.name + ": " + v + " clamped to " + c);
  return c;
}
function setp(p, v, t0, dur) {
  if (v instanceof Array && v.length && v[0] instanceof Array) {
    for (var k = 0; k < v.length; k++) p.setValueAtTime(t0 + (v[k][0] < 0 ? dur + v[k][0] : v[k][0]), fit(p, v[k][1]));
  } else p.setValue(fit(p, v));
}
// A group's child by name or match name; "Name#2" = the 2nd child with that name (Parametric EQ repeats band names).
// Header rows (NO_VALUE) can share a name with the real property and come first (APC Vegas 'Segments'); writing to one
// kills the script, so they only match as the middle of a "Group/Prop" path (mid), same as the validator's leaf_props.
function child(g, name, mid) {
  var parts = name.split("#"), nth = parts.length > 1 ? +parts[1] : 1, seen = 0;
  function ok(c) { return mid || c.propertyType != PropertyType.PROPERTY || c.propertyValueType != PropertyValueType.NO_VALUE; }
  if (nth == 1) { try { var direct = g.property(parts[0]); if (direct && ok(direct)) return direct; } catch (e0) {} }
  for (var i = 1; i <= g.numProperties; i++) {
    var c = g.property(i);
    if ((c.name == parts[0] || c.matchName == parts[0]) && ok(c) && ++seen == nth) return c;
  }
  return null;
}
// Effects by display or match name, then .ffx animation presets applied at the given layer time.
function dress(l, comp, x, label, t0, dur) {
  var k, j, fxs = x.effects || [], pss = x.presets || [];
  for (k = 0; k < fxs.length; k++) {
    var fx = fxs[k], e;
    try { e = l.property("ADBE Effect Parade").addProperty(fx.name); } catch (er) { warn(label + ": no AE effect '" + fx.name + "'"); continue; }
    for (var pn in (fx.props || {})) {  // for-in over undefined kills an AE script outright
      // "Group/Property" reaches into effect groups. CC effects only LOOK grouped: "Producer"/"Physics" are flat header
      // rows (PropertyType.PROPERTY), and asking a property for a child kills the script uncatchably, so skip those.
      // Some names contain "/" themselves (Keylight 'Screen Shrink/Grow', Parametric EQ 'Boost/Cut'): whole name first.
      var p = child(e, pn), parts = pn.split("/");
      if (!p && parts.length > 1) for (p = e, j = 0; j < parts.length && p; j++) {
        var q = child(p, parts[j], j < parts.length - 1);
        if (j < parts.length - 1 && q && q.propertyType == PropertyType.PROPERTY) continue;
        p = q;
      }
      if (!p) {
        var have = []; for (j = 1; j <= e.numProperties; j++) have.push(e.property(j).name);
        warn(label + ": " + fx.name + " has no '" + pn + "' (has " + have.join(", ") + ")");
        continue;
      }
      setp(p, fx.props[pn], t0, dur);
    }
  }
  for (k = 0; k < pss.length; k++) preset(l, comp, pss[k].file, t0 + (pss[k].at < 0 ? dur + pss[k].at : pss[k].at));
}
// Text style from scratch: new text inherits the user's Character panel (Optical kerning there opens gaps
// after Devanagari matras), so reset everything and set what we want.
function styleText(tl, t, label) {
  var sp = tl.property("Source Text"), td = sp.value;
  td.resetCharStyle(); td.resetParagraphStyle(); td.autoKernType = AutoKernType.METRIC_KERN;
  td.font = t.font; td.fontSize = t.size; td.applyFill = true; td.fillColor = hex(t.color);
  td.justification = t.just == "left" ? ParagraphJustification.LEFT_JUSTIFY : ParagraphJustification.CENTER_JUSTIFY;
  if (t.tracking) td.tracking = t.tracking;
  if (t.stroke) { td.applyStroke = true; td.strokeColor = hex(t.stroke); td.strokeWidth = t.stroke_width || 4; td.strokeOverFill = false; }
  sp.setValue(td);
  if (sp.value.font != t.font) warn(label + ": font '" + t.font + "' not installed, AE used " + sp.value.font);
  if (t.indic && sp.value.composerEngine != ComposerEngine.UNIVERSAL_TYPE_ENGINE)
    warn("LATIN_ENGINE: set Preferences > Type > Text Engine to South Asian and Middle Eastern");
  return sp;
}
