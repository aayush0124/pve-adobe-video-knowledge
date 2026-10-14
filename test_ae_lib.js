// Offline check for ae_lib.jsx child()/dress() (no After Effects needed): node test_ae_lib.js
// Every harvested AE effect becomes a mock property tree; dress() sets every leaf validate_cards.leaf_props accepts
// (headers skipped, repeats as "Name#2") and each value must land on that leaf, never on a header row or a group.
var fs = require("fs"), path = require("path"), vm = require("vm");
var ctx = vm.createContext({ PropertyType: { PROPERTY: 1, INDEXED_GROUP: 2, NAMED_GROUP: 3 },
                             PropertyValueType: { NO_VALUE: 10, OneD: 11 } });
vm.runInContext(fs.readFileSync(path.join(__dirname, "ae_lib.jsx"), "utf8"), ctx);
var PT = ctx.PropertyType, killed = null;

function mock(h) {  // h = harvested node {kind: prop|group|igroup, name, match, type, props}
  var n = { name: h.name, matchName: h.match };
  if (h.kind == "prop") {
    n.propertyType = PT.PROPERTY; n.propertyValueType = h.type == "none" ? 10 : 11;
    n.property = function () { killed = killed || "asked property '" + h.name + "' for a child"; };  // uncatchable in AE
    n.setValue = function (v) { if (h.type == "none") killed = killed || "wrote to header '" + h.name + "'"; n.got = v; };
    return n;
  }
  n.propertyType = h.kind == "igroup" ? PT.INDEXED_GROUP : PT.NAMED_GROUP;
  n.kids = (h.props || []).map(mock);
  n.numProperties = n.kids.length;
  n.property = function (k) {
    if (typeof k == "number") return n.kids[k - 1];
    for (var j = 0; j < n.kids.length; j++) if (n.kids[j].name == k || n.kids[j].matchName == k) return n.kids[j];
    return null;
  };
  n.setValue = function () { killed = killed || "wrote to group '" + h.name + "'"; };  // groups have no setValue in AE
  return n;
}

// Same walk and keys as prepare_batches.flat_props + validate_cards.leaf_props.
function leaves(g, h, pre, keys, seen) {
  (h.props || []).forEach(function (c, i) {
    if (c.match == "ADBE Effect Built In Params") return;
    if (c.kind != "prop") return leaves(g.kids[i], c, pre + c.name + "/", keys, seen);
    var n = pre + c.name;
    if (!c.name || c.type == "none") return;
    seen[n] = (seen[n] || 0) + 1;
    keys[seen[n] == 1 ? n : n + "#" + seen[n]] = g.kids[i];
  });
  return keys;
}

var effects = 0, count = 0, fails = [];
JSON.parse(fs.readFileSync(path.join(__dirname, "pve/harvest/ae_effects.json"), "utf8")).forEach(function (h) {
  if (!h.name || !h.props) return;
  var root = mock({ kind: "group", name: h.name, match: h.match, props: h.props });
  var keys = leaves(root, h, "", {}, {}), props = {}, key;
  for (key in keys) props[key] = { k: key };
  killed = null; vm.runInContext("AB_WARNS = []", ctx);
  var layer = { property: function () { return { addProperty: function () { return root; } }; } };
  ctx.dress(layer, {}, { effects: [{ name: h.name, props: props }] }, h.name, 0, 1);
  effects++;
  for (key in keys) {
    count++;
    if (!keys[key].got || keys[key].got.k != key) fails.push(h.name + " '" + key + "' got " + JSON.stringify(keys[key].got));
  }
  if (killed) fails.push(h.name + ": " + killed);
  if (ctx.AB_WARNS.length) fails.push(ctx.AB_WARNS.join(" | "));
});
// The same effect three times in one stack (two Wave Warps, two Glows in the recipes): every add returns a new
// instance and, as in AE, leaves the references to the instances added before it stale. Each instance must get its
// own values, written before the next add, never an earlier or later instance's.
var made = [], stack = [["#00FF00", 1], ["#0000FF", 0.5], ["#FF0000", 0.25]];
var parade = { addProperty: function () {
  made.forEach(function (m) { m.stale = true; });
  var e = mock({ kind: "group", name: "Fill", match: "ADBE Fill", props: [
    { kind: "prop", name: "Color", match: "ADBE Fill-0002", type: "color" },
    { kind: "prop", name: "Opacity", match: "ADBE Fill-0005", type: "1d" }] });
  e.kids.forEach(function (k) {
    var set = k.setValue;
    k.setValue = function (v) { if (e.stale) fails.push("Fill: a value went to a stale (earlier) instance"); set(v); };
  });
  made.push(e);
  return e;
} };
vm.runInContext("AB_WARNS = []", ctx);
ctx.dress({ property: function () { return parade; } }, {}, { effects: stack.map(function (s) {
  return { name: "ADBE Fill", props: { Color: s[0], Opacity: s[1] } }; }) }, "dup", 0, 1);
if (made.length != stack.length) fails.push("repeated Fill: " + made.length + " instances added, want " + stack.length);
stack.forEach(function (s, i) {
  var e = made[i] || { kids: [{}, {}] }, want = JSON.stringify([ctx.hex(s[0]).concat([1]), s[1]]);
  var got = JSON.stringify([e.kids[0].got, e.kids[1].got]);
  if (got != want) fails.push("repeated Fill #" + (i + 1) + " got " + got + ", want " + want);
});
if (fails.length) { console.log(fails.join("\n")); process.exit(1); }
console.log("ok: " + count + " properties across " + effects + " AE effects; a 3x repeated effect keeps each instance's values");
