/* Headless-проверка рендера связей: цикл/зависшие связи не должны прятать линии. */
"use strict";
Object.defineProperty(global, "navigator", { value: { userAgent: "node" }, configurable: true });

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
        classList: { add() {}, remove() {} },
        width: 300, height: 300,
        getContext: () => makeCtxStub(),
    });
    this.querySelectorAll = () => [];
    this.querySelectorAllOrNull = () => [];
    this.body = { appendChild() {}, style: {} };
    this.fullscreenElement = null;
    this.defaultView = global;
}
const docShim = new FakeDocument();
global.window = global;
global.document = docShim;
global.Image = class { constructor() {} set src(v) {} };
global.requestAnimationFrame = () => 0;

const fs = require("fs");
const path = require("path");
const root = path.join(__dirname, "..", "web");

const src = fs.readFileSync(path.join(root, "lib", "litegraph.js"), "utf8");
const factory = new Function("window", "global", "document", "navigator", src +
    "\n;return window.LiteGraph || (typeof module !== 'undefined' && module && module.exports) || null;");
const LiteGraph = factory(global, global, docShim, global.navigator);

// links.js — как в браузере (IIFE, требует LiteGraph и LGraphCanvas в window)
global.window.LiteGraph = LiteGraph;
const linksSrc = fs.readFileSync(path.join(root, "js", "links.js"), "utf8");
new Function("window", "document", linksSrc)(global, docShim);
const CircuitBoard = global.CircuitBoard || (global.window && global.window.CircuitBoard);
if (typeof CircuitBoard !== "function") { console.error("CircuitBoard не загрузился из links.js"); process.exit(1); }

function FakeCanvas() {
    this.width = 800; this.height = 600;
    this.style = {};
    this.ownerDocument = docShim;
    this.addEventListener = () => {};
    this.removeEventListener = () => {};
    this.setAttribute = () => {};
    this.getContext = () => makeCtxStub();
    this.getBoundingClientRect = () => ({ left: 0, top: 0, width: 800, height: 600 });
    this.localName = "canvas"; this.tagName = "CANVAS";
}
const graph = new LiteGraph.LGraph();
const canvasEl = new FakeCanvas();
const canvas = new LiteGraph.LGraphCanvas(canvasEl, graph);
canvas.default_connection_color = { input_on: "#fff" };
canvas.default_connection_color_byType = { event: "#e91e63", any: "#aaa" };

class NA extends LiteGraph.LGraphNode { constructor() { super(); this.title = "A"; this.addOutput("out", "event"); this.pos = [80, 200]; this.size = [180, 60]; } }
class NB extends LiteGraph.LGraphNode { constructor() { super(); this.title = "B"; this.addInput("in", "event"); this.addOutput("out", "event"); this.pos = [400, 200]; this.size = [180, 60]; } }
class NC extends LiteGraph.LGraphNode { constructor() { super(); this.title = "C"; this.addInput("in", "event"); this.addOutput("out", "event"); this.pos = [700, 200]; this.size = [180, 60]; } }
class ND extends LiteGraph.LGraphNode { constructor() { super(); this.title = "D"; this.addInput("in", "number"); this.pos = [400, 400]; this.size = [180, 60]; } }
LiteGraph.registerNodeType("ra", NA);
LiteGraph.registerNodeType("rb", NB);
LiteGraph.registerNodeType("rc", NC);
LiteGraph.registerNodeType("rd", ND);

const a = LiteGraph.createNode("ra"); graph.add(a);
const b = LiteGraph.createNode("rb"); graph.add(b);
const c = LiteGraph.createNode("rc"); graph.add(c);
const d = LiteGraph.createNode("rd"); graph.add(d);

// цепь A -> B -> C -> B (ЦИКЛ) + A -> D (числовая связь)
a.connect(0, b, 0);
b.connect(0, c, 0);
c.connect(0, b, 0);   // цикл!
const numNode = LiteGraph.createNode("ra"); graph.add(numNode);
numNode.addOutput("value", "number");
numNode.connect(1, d, 0);

const totalLinks = Object.keys(graph.links).length;
console.log("ссылок в графе:", totalLinks);

const cb = new CircuitBoard();
cb.canvas = canvas;
cb.recalcMapLinks();
const paths = cb.mapLinks ? cb.mapLinks.paths : [];
console.log("отрисованных путей:", paths.length);

// доказательство бага: топологическая сортировка теряет ноду в цикле
const topo = graph.computeExecutionOrder(false, true) || [];
console.log("в computeExecutionOrder нод:", topo.length, "из", graph._nodes.length,
    "(цикловые выпадают — их линии раньше не рисовались)");

let covered = 0;
for (const node of graph._nodes) {
    if (!node.outputs) continue;
    for (let s = 0; s < node.outputs.length; ++s) {
        const out = node.outputs[s];
        if (!out.links || !out.links.length) continue;
        for (const lid of out.links) {
            const found = paths.some(p => p.node === node && p.slot === s &&
                graph.links[lid] && p.targetNode.id === graph.links[lid].target_id);
            if (found) covered++;
        }
    }
}
console.log("покрыто связей путями:", covered, "/", totalLinks);
if (covered === totalLinks) {
    console.log("PASS: все связи имеют путь отрисовки (цикл не прячет линии)");
} else {
    console.log("FAIL: часть связей не отрисуется");
    process.exit(1);
}
