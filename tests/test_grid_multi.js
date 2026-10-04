/* Headless-тест: привязка к сетке при создании и при сбросе мультивыделения. */
"use strict";
Object.defineProperty(global, "navigator", { value: { userAgent: "node" }, configurable: true });

function FakeDocument() {
    this.addEventListener = () => {}; this.removeEventListener = () => {};
    this.createElement = (tag) => ({
        tag, style: {}, appendChild() {}, addEventListener() {}, removeEventListener() {},
        classList: { add() {}, remove() {} }, width: 300, height: 300,
        getContext: () => makeCtxStub(),
    });
    this.querySelectorAll = () => []; this.querySelectorAllOrNull = () => [];
    this.body = { appendChild() {}, style: {} };
    this.fullscreenElement = null; this.defaultView = global;
}
const docShim = new FakeDocument();
global.window = global; global.document = docShim;
global.Image = class { constructor() {} set src(v) {} };
global.requestAnimationFrame = () => 0;
function makeCtxStub() {
    return new Proxy({}, {
        get: (t, p) => (p === "measureText" ? () => ({ width: 10 }) : (typeof t[p] !== "undefined" ? t[p] : () => undefined)),
        set: (t, p, v) => { t[p] = v; return true; },
    });
}

const fs = require("fs");
const path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "web", "lib", "litegraph.js"), "utf8");
const factory = new Function("window", "global", "document", "navigator", src +
    "\n;return window.LiteGraph || (typeof module !== 'undefined' && module && module.exports) || null;");
const LiteGraph = factory(global, global, docShim, global.navigator);

function FakeCanvas() {
    this.width = 800; this.height = 600; this.style = {};
    this.localName = "canvas"; this.tagName = "CANVAS"; this.ownerDocument = docShim;
    this.getContext = () => makeCtxStub();
}
FakeCanvas.prototype.getBoundingClientRect = () => ({ left: 0, top: 0, width: 800, height: 600 });
FakeCanvas.prototype.addEventListener = () => {};
FakeCanvas.prototype.removeEventListener = () => {};
FakeCanvas.prototype.focus = () => {};
FakeCanvas.prototype.setAttribute = () => {};
FakeCanvas.prototype.removeAttribute = () => {};
FakeCanvas.prototype.hasAttribute = () => false;

class NodeN extends LGraphNode {
    constructor() { super(); this.addInput("in", "event"); this.addOutput("out", "event");
        this.pos = [100, 100]; this.size = [200, 80]; }
}
LiteGraph.registerNodeType("n", NodeN);

let fails = 0;
function check(name, cond) { console.log((cond ? "PASS" : "FAIL") + ": " + name); if (!cond) fails++; }

const graph = new LGraph();
const fakeCanvas = new FakeCanvas();
const canvas = new LGraphCanvas(fakeCanvas, graph);
canvas.ds.scale = 1; canvas.ds.offset = [0, 0];
graph.start();
canvas.visible_nodes = graph._nodes;

function mkEv(type, x, y) {
    return { type, clientX: x, clientY: y, which: 1, isPrimary: true, button: 0,
        ctrlKey: false, shiftKey: false, altKey: false, metaKey: false,
        preventDefault() {}, stopPropagation() {}, stopImmediatePropagation() {} };
}

// 1. создание ноды встаёт на сетку
graph.config.align_to_grid = true;
canvas.align_to_grid = true;
const a = LiteGraph.createNode("n"); a.pos = [123, 47]; graph.add(a);
check("создание: нода выровнена по сетке (x=%10==0)", a.pos[0] % 10 === 0 && a.pos[1] % 10 === 0);

const b = LiteGraph.createNode("n"); b.pos = [456, 289]; graph.add(b);
check("создание: вторая нода тоже на сетке", b.pos[0] % 10 === 0 && b.pos[1] % 10 === 0);

// 2. сброс мультивыделения: ОБЕ ноды выравниваются
canvas.processMouseDown(mkEv("pointerdown", 60, 60));
canvas.processMouseMove(mkEv("pointermove", 790, 590));
canvas.processMouseUp(mkEv("pointerup", 790, 590));
check("рамка выделила обе ноды", Object.keys(canvas.selected_nodes).length === 2);

a.pos[0] = 111; a.pos[1] = 23; b.pos[0] = 345; b.pos[1] = 67;   // раскашачиваем
canvas.processMouseDown(mkEv("pointerdown", a.pos[0] + 100, a.pos[1] + 40));
canvas.processMouseMove(mkEv("pointermove", a.pos[0] + 153, a.pos[1] + 47));
canvas.processMouseUp(mkEv("pointerup", a.pos[0] + 153, a.pos[1] + 47));
console.log("после сброса: a=", Array.from(a.pos).slice(0, 2), "b=", Array.from(b.pos).slice(0, 2));
check("сброс мультивыделения: обе ноды выровнялись",
      a.pos[0] % 10 === 0 && a.pos[1] % 10 === 0 && b.pos[0] % 10 === 0 && b.pos[1] % 10 === 0);

// 3. без align_to_grid позиции не трогаются
canvas.align_to_grid = false;
graph.config.align_to_grid = false;
const c = LiteGraph.createNode("n"); c.pos = [123, 47]; graph.add(c);
check("сетка выключена: нода НЕ выравнивается", c.pos[0] === 123 && c.pos[1] === 47);

console.log(fails === 0 ? "\nВСЕ ТЕСТЫ ПРОЙДЕНЫ" : "\nЕСТЬ ПРОВАЛЫ: " + fails);
process.exit(fails === 0 ? 0 : 1);
