/* Headless-проверка раскладки пинов-параметров (пин у своего ползунка,
   серый виджет при проводе). Использует KFConnect.layoutParamWidgets. */
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

const connSrc = fs.readFileSync(path.join(root, "js", "connect.js"), "utf8");
new Function("window", connSrc)(global);

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

const fails = [];
function check(name, cond) {
    console.log((cond ? "PASS" : "FAIL") + ": " + name);
    if (!cond) fails.push(name);
}

// нода «как скриншот»: in(event), x(number-пин), y(number-пин) + виджеты
class ShotNode extends LiteGraph.LGraphNode {
    constructor() {
        super();
        this.def = {
            params: [
                { name: "region", type: "combo" },
                { name: "x", type: "number" },
                { name: "y", type: "number" },
            ],
        };
        this.title = "S";
        this.addInput("in", "event");
        this.addInput("x", "number");
        this.addInput("y", "number");
        this.addOutput("out", "event");
        this.addWidget("combo", "Область", "full", () => {}, { property: "region", values: ["full", "area"] });
        this.addWidget("number", "X", 0, () => {}, { property: "x" });
        this.addWidget("number", "Y", 0, () => {}, { property: "y" });
        this.pos = [100, 100];
        this.size = [250, 200];
    }
}
LiteGraph.registerNodeType("tw_shot", ShotNode);

const graph = new LiteGraph.LGraph();
const canvas = new LiteGraph.LGraphCanvas(new FakeCanvas(), graph);
const s = LiteGraph.createNode("tw_shot");
if (!s) { console.error("createNode вернул null; типы:", Object.keys(LiteGraph.registered_node_types)); process.exit(1); }
graph.add(s);

const SLOT_H = LiteGraph.NODE_SLOT_HEIGHT, W_H = LiteGraph.NODE_WIDGET_HEIGHT;

// 1) раскладка: пин x/y у своего ползунка
const pinned = global.KFConnect.layoutParamWidgets(s);
check("раскладка привязала 2 пина-параметра", pinned === 2);
const wx = s.widgets[1], wy = s.widgets[2];
check("виджет x в своей строке (y зафиксирован)", typeof wx.y === "number" && wx.last_y === wx.y);
const px = s.getConnectionPos(true, 1);   // вход x
const py = s.getConnectionPos(true, 2);   // вход y
check("пин x стоит у ползунка X (y = w.y + H/2)",
      Math.abs(px[1] - (s.pos[1] + wx.y + W_H * 0.5)) < 0.01);
check("пин y стоит у ползунка Y", Math.abs(py[1] - (s.pos[1] + wy.y + W_H * 0.5)) < 0.01);
check("пин x на левом краю ноды (x = pos + 10)", Math.abs(px[0] - (s.pos[0] + SLOT_H * 0.5)) < 0.01);
check("пины x/y ниже входа события",
      py[1] > s.getConnectionPos(true, 0)[1] && px[1] > s.getConnectionPos(true, 0)[1]);
check("виджеты не серые без провода", wx.disabled !== true && wy.disabled !== true);

// зазор между последним стандартным пином и первым виджетом — меньше одного ряда
const lastStd = Math.max(
    s.getConnectionPos(true, 0)[1] - s.pos[1],
    s.getConnectionPos(false, s.outputs.length - 1)[1] - s.pos[1]);
check("лишнего пространства сверху нет (< 1 ряда)", s.widgets[0].y - lastStd < SLOT_H);
// виджеты вмещаются в ноду
const lastBottom = wy.y + W_H;
check("виджеты не выходят за ноду", s.size[1] >= lastBottom + 10 - 0.01);

// 2) константа -> x: виджет x гаснет, y остаётся активным
class NumNode extends LiteGraph.LGraphNode {
    constructor() { super(); this.title = "N"; this.addOutput("number", "number"); this.pos = [0, 0]; }
}
LiteGraph.registerNodeType("tw_num", NumNode);
const num = LiteGraph.createNode("tw_num"); graph.add(num);
const r = num.connect(0, s, 1);   // -> вход x
check("провод number -> x подключился", !!r);
global.KFConnect.layoutParamWidgets(s);
check("виджет X серый при подключённом проводе", wx.disabled === true);
check("виджет Y активен", wy.disabled !== true);

// 3) отключили провод — виджет снова активен
s.disconnectInput(1);
global.KFConnect.layoutParamWidgets(s);
check("после отсоединения виджет X снова активен", wx.disabled !== true);

// 4) нода-константа (только параметровые входы) — виджет ниже выходов
class TextNode extends LiteGraph.LGraphNode {
    constructor() {
        super();
        this.def = { params: [{ name: "text", type: "string" }] };
        this.title = "T";
        this.addInput("text", "string");
        this.addOutput("text", "string");
        this.addWidget("text", "Текст", "", () => {}, { property: "text" });
        this.pos = [0, 0]; this.size = [220, 80];
    }
}
LiteGraph.registerNodeType("tw_text", TextNode);
const t = LiteGraph.createNode("tw_text");
global.KFConnect.layoutParamWidgets(t);
const pt = t.getConnectionPos(true, 0);
check("пин text у ползунка, ниже выхода", pt[1] > t.getConnectionPos(false, 0)[1]);

// 5) нода без параметровых входов (hotkey-подобная): виджеты вмещаются, зазор мал
class HotNode extends LiteGraph.LGraphNode {
    constructor() {
        super();
        this.def = { params: [{ name: "keys", type: "string" }, { name: "once", type: "bool" }] };
        this.title = "H";
        this.addOutput("out", "event");
        this.addWidget("text", "Сочетание", "", () => {}, { property: "keys" });
        this.addWidget("toggle", "Один раз", false, () => {}, { property: "once" });
        this.pos = [0, 0]; this.size = [250, 60];
    }
}
LiteGraph.registerNodeType("tw_hot", HotNode);
const h = LiteGraph.createNode("tw_hot");
const oldH = h.size[1];
global.KFConnect.layoutParamWidgets(h);
check("hotkey-нода: первый виджет сразу под выходом (< 1 ряда)",
      h.widgets[0].y - (0.7 * SLOT_H) < SLOT_H);
check("hotkey-нода: высота выросла, виджеты вмещаются",
      h.size[1] >= h.widgets[h.widgets.length - 1].y + W_H + 10 - 0.01 && h.size[1] >= oldH);

// 6) вход без виджета (как «path» у «Скриншота») не повисает в центре:
//    все не-параметровые входы прижаты в верхнюю зону подряд
class ShotPathNode extends LiteGraph.LGraphNode {
    constructor() {
        super();
        this.def = { params: [
            { name: "region", type: "combo" }, { name: "x", type: "number" },
            { name: "y", type: "number" }, { name: "w", type: "number" },
            { name: "h", type: "number" },
        ] };
        this.title = "SP";
        this.addInput("in", "event");
        this.addInput("x", "number");
        this.addInput("y", "number");
        this.addInput("w", "number");
        this.addInput("h", "number");
        this.addInput("path", "string");
        this.addOutput("out", "event");
        this.addWidget("combo", "Область", "full", () => {}, { property: "region", values: ["full", "area"] });
        this.addWidget("number", "X", 0, () => {}, { property: "x" });
        this.addWidget("number", "Y", 0, () => {}, { property: "y" });
        this.addWidget("number", "W", 800, () => {}, { property: "w" });
        this.addWidget("number", "H", 600, () => {}, { property: "h" });
        this.pos = [0, 0]; this.size = [250, 300];
    }
}
LiteGraph.registerNodeType("tw_shotpath", ShotPathNode);
const sp = LiteGraph.createNode("tw_shotpath");
global.KFConnect.layoutParamWidgets(sp);
const pinIn = sp.getConnectionPos(true, 0)[1] - sp.pos[1];    // «in»
const pinPath = sp.getConnectionPos(true, 5)[1] - sp.pos[1];  // «path»
check("path-вход в верхней зоне: ровно на ряд под «in»",
      Math.abs(pinPath - pinIn - SLOT_H) < 0.01);
check("path-вход выше первого виджета (не в центре ноды)",
      pinPath < sp.widgets[0].y);

// 7) автоподгонка высоты: пустота внизу больших нод убирается; ручной размер не трогаем
class BigNode extends LiteGraph.LGraphNode {
    constructor() {
        super();
        this.def = { params: [{ name: "text", type: "string" }] };
        this.title = "B";
        this.addInput("in", "event");
        this.addInput("text", "string");
        this.addOutput("out", "event");
        this.addWidget("text", "Текст", "", () => {}, { property: "text" });
        this.pos = [0, 0]; this.size = [250, 300];   // раздутая высота
    }
}
LiteGraph.registerNodeType("tw_big", BigNode);
const bn = LiteGraph.createNode("tw_big");
global.KFConnect.layoutParamWidgets(bn);
const fitB = bn.widgets[bn.widgets.length - 1].y + W_H + 10;
check("лишняя высота убрана (ровно по виджетам)", Math.abs(bn.size[1] - fitB) < 0.01);
bn._kfUserResized = true;
bn.size[1] = 300;
global.KFConnect.layoutParamWidgets(bn);
check("высота, заданная пользователем, не трогается", bn.size[1] === 300);

console.log(fails.length ? "\nПРОВАЛЫ: " + fails.join("; ") : "\nВСЕ ТЕСТЫ ПРОЙДЕНЫ");
process.exit(fails.length ? 1 : 0);
