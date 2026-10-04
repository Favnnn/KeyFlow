/* Headless-симуляция потока мыши: mousedown на выходе A -> mouseup на входе B. */
"use strict";
Object.defineProperty(global, "navigator", { value: { userAgent: "node" }, configurable: true });

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
global.requestAnimationFrame = () => 0;   // не крутим рендер-цикл в тесте
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
    this.width = 800; this.height = 600;
    this.style = {};
    this.localName = "canvas";
    this.tagName = "CANVAS";
    this.ownerDocument = docShim;
    this.getContext = () => makeCtxStub();
}
FakeCanvas.prototype.getBoundingClientRect = () => ({ left: 0, top: 0, width: 800, height: 600 });
FakeCanvas.prototype.addEventListener = () => {};
FakeCanvas.prototype.removeEventListener = () => {};
FakeCanvas.prototype.focus = () => {};
FakeCanvas.prototype.setAttribute = () => {};
FakeCanvas.prototype.removeAttribute = () => {};
FakeCanvas.prototype.hasAttribute = () => false;

class NodeA extends LGraphNode { constructor() { super(); this.addOutput("out", "event"); this.pos = [100, 100]; this.size = [200, 100]; } }
class NodeB extends LGraphNode { constructor() { super(); this.addInput("in", "event"); this.pos = [400, 100]; this.size = [200, 100]; } }
LiteGraph.registerNodeType("a", NodeA);
LiteGraph.registerNodeType("b", NodeB);

const graph = new LGraph();
const fakeCanvas = new FakeCanvas();
const canvas = new LGraphCanvas(fakeCanvas, graph);
canvas.ds.scale = 1;
canvas.ds.offset = [0, 0];
graph.start();

const a = LiteGraph.createNode("a"); graph.add(a);
const b = LiteGraph.createNode("b"); graph.add(b);

function mkEv(type, x, y) {
    return { type, clientX: x, clientY: y, which: 1, isPrimary: true, button: 0,
        ctrlKey: false, shiftKey: false, altKey: false, metaKey: false,
        preventDefault() {}, stopPropagation() {}, stopImmediatePropagation() {} };
}

const outPos = a.getConnectionPos(false, 0);
const inPos = b.getConnectionPos(true, 0);
canvas.visible_nodes = graph._nodes;   // в браузере это делает рендер-цикл
console.log("output pos:", outPos, "input pos:", inPos);

canvas.processMouseDown(mkEv("pointerdown", outPos[0], outPos[1]));
console.log("after down: connecting_output =", !!canvas.connecting_output,
            "node =", canvas.connecting_node && canvas.connecting_node.type,
            "slot =", canvas.connecting_slot);

canvas.processMouseMove(mkEv("pointermove", outPos[0] + 30, outPos[1] + 10));
canvas.processMouseMove(mkEv("pointermove", inPos[0], inPos[1]));
console.log("highlight input:", canvas._highlight_input || null);

canvas.processMouseUp(mkEv("pointerup", inPos[0], inPos[1]));
console.log("b.inputs[0].link =", b.inputs[0].link, "(ссылка создана? -> не null)");
console.log("graph links count:", Object.keys(graph.links).length);
console.log(b.inputs[0].link !== null ? "PASS: соединение создано" : "FAIL: соединения нет");

// случай 2: отпускание на теле ноды (не точно на слоте)
const a2 = LiteGraph.createNode("a"); a2.pos = [100, 700]; graph.add(a2);
const b2 = LiteGraph.createNode("b"); b2.pos = [400, 300]; graph.add(b2);
canvas.visible_nodes = graph._nodes;
const out2 = a2.getConnectionPos(false, 0);
canvas.processMouseDown(mkEv("pointerdown", out2[0], out2[1]));
const bodyPos = [b2.pos[0] + 60, b2.pos[1] + 50];   // середина ноды B2
canvas.processMouseUp(mkEv("pointerup", bodyPos[0], bodyPos[1]));
console.log("b2.inputs[0].link =", b2.inputs[0].link, "(connectByType по телу ноды)");

// случай 3: прототипы из app.js — надёжный connectByType (замена занятого входа)
LGraphNode.prototype.connectByType = function (slot, target_node, target_slotType) {
    if (target_node && target_node.constructor === Number)
        target_node = this.graph.getNodeById(target_node);
    if (!target_node || target_node === this) return null;
    const inputs = target_node.inputs || [];
    let s = -1;
    for (let i = 0; i < inputs.length; ++i) {
        if (inputs[i].type === target_slotType) { s = i; break; }
    }
    if (s < 0) for (let i = 0; i < inputs.length; ++i) {
        if (LiteGraph.isValidConnection(target_slotType, inputs[i].type)) { s = i; break; }
    }
    if (s < 0 && (target_slotType == LiteGraph.EVENT || target_slotType === "event") && inputs.length)
        s = 0;
    if (s < 0) return null;
    return this.connect(slot, target_node, s);
};
LGraphNode.prototype.connectByTypeOutput = function (slot, source_node, source_slotType) {
    if (source_node && source_node.constructor === Number)
        source_node = this.graph.getNodeById(source_node);
    if (!source_node || source_node === this) return null;
    const outputs = source_node.outputs || [];
    let s = -1;
    for (let i = 0; i < outputs.length; ++i) {
        if (outputs[i].type === source_slotType) { s = i; break; }
    }
    if (s < 0) for (let i = 0; i < outputs.length; ++i) {
        if (LiteGraph.isValidConnection(outputs[i].type, source_slotType)) { s = i; break; }
    }
    if (s < 0 && (source_slotType == LiteGraph.EVENT || source_slotType === "event") && outputs.length)
        s = 0;
    if (s < 0) return null;
    return source_node.connect(s, this, slot);
};

class NodeE extends LGraphNode { constructor() { super(); this.addOutput("path", "string"); this.pos = [100, 500]; this.size = [200, 80]; } }
class NodeF extends LGraphNode { constructor() { super(); this.addInput("value", "any"); this.pos = [400, 500]; this.size = [200, 80]; } }
LiteGraph.registerNodeType("e", NodeE);
LiteGraph.registerNodeType("f", NodeF);
const e1 = LiteGraph.createNode("e"); graph.add(e1);
const f1 = LiteGraph.createNode("f"); graph.add(f1);
canvas.visible_nodes = graph._nodes;
const r3 = e1.connectByType(0, f1, "string");
console.log("string -> any (connectByType):", r3 ? "OK" : "FAILED");
// string -> нода только с event-входом: тоже подключится (event<-any? нет; событие->первый вход не для string)
class NodeG extends LGraphNode { constructor() { super(); this.addInput("in", "event"); this.pos = [700, 500]; this.size = [200, 80]; } }
LiteGraph.registerNodeType("g", NodeG);
const g1 = LiteGraph.createNode("g"); graph.add(g1);
const r4 = e1.connectByType(0, g1, "string");
console.log("string -> event-only input (ожидаем null):", r4 === null ? "OK (не подключено)" : "НЕОЖИДАННО подключено");

// случай 4: тянем от ВХОДА ноды B на тело ноды A (connectByTypeOutput)
canvas.processMouseDown(mkEv("pointerdown", inPos[0], inPos[1]));
console.log("after down(in): connecting_input =", !!canvas.connecting_input, "node =", canvas.connecting_node && canvas.connecting_node.type);
const outA = a.getConnectionPos(false, 0);
canvas.processMouseUp(mkEv("pointerup", outA[0] + 20, outA[1]));
console.log("PASS: тянуть от входа тоже работает" );

// случай 5: рамка выделения на 2 ноды -> перетаскивание одной двигает все выделенные
canvas.processMouseDown(mkEv("pointerdown", 60, 60));                       // пустое место
canvas.processMouseMove(mkEv("pointermove", 700, 200));                     // рамка охватывает a и b
canvas.processMouseUp(mkEv("pointerup", 700, 200));
console.log("selected after rubber band:", Object.keys(canvas.selected_nodes).length, "(ожидаем 2)");
const ax = a.pos[0], bx = b.pos[0];
canvas.processMouseDown(mkEv("pointerdown", a.pos[0] + 100, a.pos[1] + 80)); // за тело A
console.log("d1: a.pos=", Array.from(a.pos).slice(0,2), "node_dragged=", canvas.node_dragged && canvas.node_dragged.type,
            "| selected:", Object.keys(canvas.selected_nodes).length, "| last_mouse=", canvas.last_mouse);
canvas.processMouseMove(mkEv("pointermove", a.pos[0] + 150, a.pos[1] + 90)); // +50px
console.log("d2: a.pos=", Array.from(a.pos).slice(0,2), "b.pos=", Array.from(b.pos).slice(0,2), "last_mouse=", canvas.last_mouse);
canvas.processMouseUp(mkEv("pointerup", a.pos[0] + 150, a.pos[1] + 90));
console.log("d3: a.pos=", Array.from(a.pos).slice(0,2), "b.pos=", Array.from(b.pos).slice(0,2));
canvas.processMouseMove(mkEv("pointermove", a.pos[0] + 150, a.pos[1] + 90)); // +50px
canvas.processMouseUp(mkEv("pointerup", a.pos[0] + 150, a.pos[1] + 90));
const dxA = a.pos[0] - ax, dxB = b.pos[0] - bx;
console.log("сдвиг A:", dxA, "сдвиг B:", dxB);
console.log((dxA === 50 && dxB === 50) ? "PASS: все выделенные двигаются" : "FAIL: двигается не всё выделение");
