/* Разовая проверка: все связи main.json валидны по новой матрице типов. */
"use strict";
const fs = require("fs");
const path = require("path");

function makeCtxStub() {
    return new Proxy({}, {
        get: (t, p) => (p === "measureText" ? () => ({ width: 10 }) : (typeof t[p] !== "undefined" ? t[p] : () => undefined)),
        set: (t, p, v) => { t[p] = v; return true; },
    });
}
function FakeDocument() {
    this.addEventListener = () => {}; this.removeEventListener = () => {};
    this.createElement = (tag) => ({
        tag, style: {}, appendChild() {}, addEventListener() {}, removeEventListener() {},
        classList: { add() {}, remove() {} }, width: 300, height: 300, getContext: () => makeCtxStub(),
    });
    this.querySelectorAll = () => []; this.querySelectorAllOrNull = () => [];
    this.body = { appendChild() {}, style: {} }; this.fullscreenElement = null; this.defaultView = global;
}
const docShim = new FakeDocument();
Object.defineProperty(global, "navigator", { value: { userAgent: "node" }, configurable: true });
global.window = global; global.document = docShim;
global.Image = class { constructor() {} set src(v) {} };
global.requestAnimationFrame = () => 0;

const lgSrc = fs.readFileSync(path.join(__dirname, "..", "web", "lib", "litegraph.js"), "utf8");
const factory = new Function("window", "global", "document", "navigator", lgSrc +
    "\n;return window.LiteGraph || null;");
const LiteGraph = factory(global, global, docShim, global.navigator);
global.window.LiteGraph = LiteGraph;

const data = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "workflows", "main.json"), "utf8"));
const g = new LiteGraph.LGraph();
g.configure(data.graph || data);
let bad = 0;
for (const id in g.links) {
    const l = g.links[id];
    const a = g._nodes_by_id[l.origin_id], b = g._nodes_by_id[l.target_id];
    if (!a || !b) { console.log("СБРОШЕНА (нет ноды):", id); bad++; continue; }
    const oa = a.outputs[l.origin_slot], ib = b.inputs[l.target_slot];
    if (!oa || !ib) { console.log("СБРОШЕНА (нет слота):", id); bad++; continue; }
    const ok = LiteGraph.isValidConnection(oa.type, ib.type);
    if (!ok) {
        bad++;
        console.log("НЕВАЛИДНА: " + a.title + "." + oa.name + " (" + oa.type + ") -> " +
                    b.title + "." + ib.name + " (" + ib.type + ")");
    }
}
console.log(bad === 0 ? "OK: все связи main.json совместимы по типам" : ("ПРОБЛЕМ: " + bad));
process.exit(bad === 0 ? 0 : 1);
