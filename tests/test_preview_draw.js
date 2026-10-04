/* Регресс-тест: отрисовка превью с многострочным значением не должна падать
   (баг: rows.push на числе — TypeError рендера, страница «висла»).
   Запускает setupPreviewNodeClass + onDrawForeground из app.js. */
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
        width: 300, height: 300, getContext: () => makeCtxStub(),
    });
    this.querySelectorAll = () => []; this.querySelectorAllOrNull = () => [];
    this.body = { appendChild() {}, style: {} };
    this.fullscreenElement = null; this.defaultView = global;
}
const docShim = new FakeDocument();
global.window = global;
global.document = docShim;
global.requestAnimationFrame = () => 0;
global.Image = class { constructor() {} set src(v) {} };

const fs = require("fs");
const path = require("path");
const root = path.join(__dirname, "..", "web");

const src = fs.readFileSync(path.join(root, "lib", "litegraph.js"), "utf8");
const factory = new Function("window", "global", "document", "navigator", src +
    "\n;return window.LiteGraph || null;");
const LiteGraph = factory(global, global, docShim, global.navigator);
global.LiteGraph = LiteGraph;

const appSrc = fs.readFileSync(path.join(root, "js", "app.js"), "utf8");

// достаём setupPreviewNodeClass как строку и выполняем в окружении теста
const m = appSrc.match(/function setupPreviewNodeClass\(C\) \{[\s\S]*?\n\}/);
if (!m) { console.error("setupPreviewNodeClass не найден в app.js"); process.exit(1); }

const state = { nodePreviews: {}, settings: { language: "ru" } };
const sandbox = {
    LiteGraph, state,
    TYPE_COLORS: { event: "#ffffff", any: "#9a9a9a", string: "#e8c04a", number: "#5ac8fa", bool: "#ff6b6b", pos: "#7bd88f" },
};
const setupFn = new Function("LiteGraph", "state", "TYPE_COLORS",
    m[0] + "\n;return setupPreviewNodeClass;");
const setupPreviewNodeClass = setupFn(sandbox.LiteGraph, sandbox.state, sandbox.TYPE_COLORS);

function FakeCanvas() {
    this.width = 800; this.height = 600;
    this.style = {};
    this.ownerDocument = docShim;
    this.addEventListener = () => {}; this.removeEventListener = () => {};
    this.setAttribute = () => {};
    this.getContext = () => makeCtxStub();
    this.getBoundingClientRect = () => ({ left: 0, top: 0, width: 800, height: 600 });
    this.localName = "canvas"; this.tagName = "CANVAS";
}

const graph = new LiteGraph.LGraph();
new LiteGraph.LGraphCanvas(new FakeCanvas(), graph);

class PrevNode extends LiteGraph.LGraphNode {
    constructor() {
        super();
        this.def = { params: [] };
        this.title = "Превью";
        this.addInput("in", "event");
        this.addInput("in2", "any");
        this.addOutput("out", "event");
        this.pos = [100, 100];
        this.size = [250, 120];
    }
}
LiteGraph.registerNodeType("reg_preview", PrevNode);
setupPreviewNodeClass(PrevNode);   // как это делает loadDefs для preview/show
const node = LiteGraph.createNode("reg_preview");
graph.add(node);

let fails = 0;
function check(name, fn) {
    try { fn(); console.log("PASS: " + name); }
    catch (e) { console.log("FAIL: " + name + " -> " + e.message); ++fails; }
}

// 1) многострочное значение — не должно быть исключения (было: rows.push is not a function)
check("рисование многострочного значения", () => {
    state.nodePreviews[node.id] = ["Текст: вппвпвпвап\n\nпвпв\nВПВП\nВП\n"];
    node.onDrawForeground(makeCtxStub());
});

// 2) однострочные значения тоже рисуются
check("рисование однострочных значений", () => {
    state.nodePreviews[node.id] = ["Текст: привет", "Число: 5"];
    node.onDrawForeground(makeCtxStub());
});

// 3) пустой список — тихий выход
check("пустое превью", () => {
    state.nodePreviews[node.id] = [];
    node.onDrawForeground(makeCtxStub());
    state.nodePreviews[node.id] = undefined;
    node.onDrawForeground(makeCtxStub());
});

// 3.5) серые точки нумеруются с in1 (слот 1 -> «in1»)
check("нумерация серых точек с in1", () => {
    const b = LiteGraph.createNode("reg_preview");
    graph.add(b);
    b.kfSyncPreviewInputs();
    if (b.inputs[1].name !== "in1") throw new Error("слот 1 = " + b.inputs[1].name);
    if (b.inputs[0].name !== "in") throw new Error("слот 0 = " + b.inputs[0].name);
});

// 4) onConnectionsChange: окраска точки по типу провода (color_on)
check("окраска точки по типу источника", () => {
    const a = LiteGraph.createNode("reg_preview");
    graph.add(a);
    a.addOutput("text", "string");          // выход данных
    a.connect(1, node, 1);                  // text(string) -> in1
    node.onConnectionsChange(1, 1, true, graph.links[node.inputs[1].link]);
    if (node.inputs[1].color_on !== "#e8c04a")
        throw new Error("color_on=" + node.inputs[1].color_on + ", ждали string-жёлтый");
    node.disconnectInput(1);
    node.onConnectionsChange(1, 1, false, null);
    if (node.inputs[1].color_on !== "#9a9a9a")
        throw new Error("после отключения color_on=" + node.inputs[1].color_on);
});

console.log(fails ? "\nПРОВАЛЫ: " + fails : "\nВСЕ ТЕСТЫ ПРОЙДЕНЫ");
process.exit(fails ? 1 : 0);
