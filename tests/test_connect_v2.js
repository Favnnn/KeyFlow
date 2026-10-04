/* Headless-тест системы соединений v2 (web/js/connect.js). */
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
        classList: { add() {}, remove() {} }, width: 300, height: 300, getContext: () => makeCtxStub(),
    });
    this.querySelectorAll = () => []; this.querySelectorAllOrNull = () => [];
    this.body = { appendChild() {}, style: {} }; this.fullscreenElement = null; this.defaultView = global;
}
const docShim = new FakeDocument();
global.window = global; global.document = docShim;
global.Image = class { constructor() {} set src(v) {} };
global.requestAnimationFrame = () => 0;

const fs = require("fs");
const path = require("path");
const root = path.join(__dirname, "..", "web");

const lgSrc = fs.readFileSync(path.join(root, "lib", "litegraph.js"), "utf8");
const factory = new Function("window", "global", "document", "navigator", lgSrc +
    "\n;return window.LiteGraph || (typeof module !== 'undefined' && module && module.exports) || null;");
const LiteGraph = factory(global, global, docShim, global.navigator);
global.window.LiteGraph = LiteGraph;

// connect.js — чистый модуль
new Function("window", fs.readFileSync(path.join(root, "js", "connect.js"), "utf8"))(global);
const KFConnect = global.KFConnect;
if (!KFConnect) { console.error("KFConnect не загрузился"); process.exit(1); }

// патчи app.js: connectByType / connectByTypeOutput (копия логики)
LGraphNode.prototype.connectByType = function (slot, target_node, target_slotType) {
    if (target_node && target_node.constructor === Number) target_node = this.graph.getNodeById(target_node);
    if (!target_node || target_node === this) return null;
    const inputs = target_node.inputs || [];
    let s = -1;
    for (let i = 0; i < inputs.length; ++i) if (inputs[i].type === target_slotType) { s = i; break; }
    if (s < 0) for (let i = 0; i < inputs.length; ++i)
        if (LiteGraph.isValidConnection(target_slotType, inputs[i].type)) { s = i; break; }
    if (s < 0 && (target_slotType == LiteGraph.EVENT || target_slotType === "event") && inputs.length) s = 0;
    if (s < 0) return null;
    return this.connect(slot, target_node, s);
};
LGraphNode.prototype.connectByTypeOutput = function (slot, source_node, source_slotType) {
    if (source_node && source_node.constructor === Number) source_node = this.graph.getNodeById(source_node);
    if (!source_node || source_node === this) return null;
    const outputs = source_node.outputs || [];
    let s = -1;
    for (let i = 0; i < outputs.length; ++i) if (outputs[i].type === source_slotType) { s = i; break; }
    if (s < 0) for (let i = 0; i < outputs.length; ++i)
        if (LiteGraph.isValidConnection(outputs[i].type, source_slotType)) { s = i; break; }
    if (s < 0 && (source_slotType == LiteGraph.EVENT || source_slotType === "event") && outputs.length) s = 0;
    if (s < 0) return null;
    return source_node.connect(s, this, slot);
};

// граф: hotkey(выход out:event) -> wait(вход in:event) + screenshot(2 выхода: out:event, path:string)
class Hotkey extends LGraphNode { constructor() { super(); this.title = "Горячая клавиша"; this.addOutput("out", "event"); this.pos = [80, 220]; this.size = [220, 80]; } }
class Wait extends LGraphNode { constructor() { super(); this.title = "Подождать"; this.addInput("in", "event"); this.pos = [420, 220]; this.size = [220, 80]; } }
class Screenshot extends LGraphNode { constructor() { super(); this.title = "Скриншот"; this.addInput("in", "event"); this.addOutput("out", "event"); this.addOutput("path", "string"); this.pos = [760, 220]; this.size = [220, 100]; } }
class Var extends LGraphNode { constructor() { super(); this.title = "Переменная"; this.addInput("value", "any"); this.pos = [1100, 220]; this.size = [220, 80]; } }
LiteGraph.registerNodeType("t_hotkey", Hotkey);
LiteGraph.registerNodeType("t_wait", Wait);
LiteGraph.registerNodeType("t_shot", Screenshot);
LiteGraph.registerNodeType("t_var", Var);

const graph = new LGraph();
const hk = LiteGraph.createNode("t_hotkey"); graph.add(hk);
const wt = LiteGraph.createNode("t_wait"); graph.add(wt);
const sc = LiteGraph.createNode("t_shot"); graph.add(sc);
const vr = LiteGraph.createNode("t_var"); graph.add(vr);
const LGP = LiteGraph.LGraphCanvas.prototype;
const fakeCanvas = {
    isOverNodeInput: (n, x, y) => LGP.isOverNodeInput(n, x, y),
    isOverNodeOutput: (n, x, y) => LGP.isOverNodeOutput(n, x, y),
};

let fails = 0;
function check(name, cond) { console.log((cond ? "PASS" : "FAIL") + ": " + name); if (!cond) fails++; }

// 1. findPin: точно на выход hotkey
const outPos = hk.getConnectionPos(false, 0);
const pin1 = KFConnect.findPin(graph._nodes, outPos[0] + 3, outPos[1]);
check("findPin: выход «out» hotkey", pin1 && pin1.isOutput && pin1.node === hk && pin1.slot === 0);

// 2. жест: из выхода hotkey отпустить на вход wait (пин)
let r = KFConnect.resolveOutput(hk, 0, wt, outPos[0], outPos[1], fakeCanvas);
check("hotkey.out → wait.in (пин): " + r.detail, r.ok && wt.inputs[0].link !== null);

// 3. из выхода screenshot (path:string) на тело «Переменной» (any) — заменит занятый/подключит
const scOut = sc.getConnectionPos(false, 1);
const pin3 = KFConnect.findPin(graph._nodes, scOut[0], scOut[1]);
check("findPin: выход «path» screenshot", pin3 && !pin3.isOutput === false && pin3.slot === 1);
r = KFConnect.resolveOutput(sc, 1, vr, scOut[0], scOut[1], fakeCanvas);
check("screenshot.path → variable.value (тело): " + r.detail, r.ok);

// 4. string → event-only нода: корректный отказ
class OnlyEv extends LGraphNode { constructor() { super(); this.title = "Только event"; this.addInput("in", "event"); this.pos = [1400, 220]; this.size = [180, 60]; } }
LiteGraph.registerNodeType("t_onlyev", OnlyEv);
const oe = LiteGraph.createNode("t_onlyev"); graph.add(oe);
r = KFConnect.resolveOutput(sc, 1, oe, 0, 0, fakeCanvas);
check("string → event-only: отказ («" + r.detail + "»)", !r.ok);

// 4b. строгость типов: текст не подключается к числам (x/y), число — к тексту (path)
class NumSrc extends LGraphNode { constructor() { super(); this.title = "int"; this.addOutput("int", "number"); this.pos = [80, 500]; this.size = [200, 60]; } }
class MoveM extends LGraphNode { constructor() { super(); this.title = "Move"; this.addInput("in", "event"); this.addInput("x", "number"); this.addInput("y", "number"); this.pos = [400, 500]; this.size = [200, 80]; } }
class ShotPath extends LGraphNode { constructor() { super(); this.title = "Shot"; this.addInput("in", "event"); this.addInput("path", "string"); this.pos = [700, 500]; this.size = [200, 80]; } }
LiteGraph.registerNodeType("t_numsrc", NumSrc);
LiteGraph.registerNodeType("t_movem", MoveM);
LiteGraph.registerNodeType("t_shotpath", ShotPath);
const ns = LiteGraph.createNode("t_numsrc"); graph.add(ns);
const mv = LiteGraph.createNode("t_movem"); graph.add(mv);
const sp = LiteGraph.createNode("t_shotpath"); graph.add(sp);
r = KFConnect.resolveOutput(ns, 0, mv, 0, 0, fakeCanvas);
check("int → move_mouse.x (тело): подключился", r.ok);
r = KFConnect.resolveOutput(sc, 1, mv, 0, 0, fakeCanvas);
check("text → координаты x/y: отказ («" + r.detail + "»)", !r.ok);
r = KFConnect.resolveOutput(ns, 0, sp, 0, 0, fakeCanvas);
check("int → screenshot.path: отказ («" + r.detail + "»)", !r.ok);
r = KFConnect.resolveOutput(sc, 1, sp, 0, 0, fakeCanvas);
check("text → screenshot.path: подключился", r.ok);

// 5. тянем от входа wait на тело hotkey (реверс)
r = KFConnect.resolveInput(wt, 0, hk, 0, 0, fakeCanvas);
check("wait.in → hotkey.out (реверс): " + r.detail, r.ok);

// 6. во вход события «in» теперь МОЖНО вести несколько линий: старая остаётся в _kfMulti
const oldLinkId = wt.inputs[0].link;
const wtInPos = wt.getConnectionPos(true, 0);
r = KFConnect.resolveOutput(sc, 0, wt, wtInPos[0], wtInPos[1], fakeCanvas);
check("event → занятый вход «in» (мульти-линия): " + r.detail,
      r.ok && wt.inputs[0].link !== null && wt.inputs[0].link !== oldLinkId &&
      Array.isArray(wt.inputs[0]._kfMulti) && wt.inputs[0]._kfMulti.indexOf(oldLinkId) !== -1);
// удаление одной линии мульти-входа не трогает остальные (следующая становится основной)
graph.removeLink(wt.inputs[0].link);
check("удаление основной линии мульти-входа: остальная стала основной",
      wt.inputs[0].link === oldLinkId && graph.links[oldLinkId] !== undefined);
// зачистка: снимаем линии по одной, пока вход не опустеет
let kfGuard = 12;
while (wt.inputs[0].link !== null && kfGuard--) graph.removeLink(wt.inputs[0].link);
check("последовательное снятие линий очищает вход", wt.inputs[0].link === null);

// 7. сериализация: мульти-вход «in» переживает save/load
const s7 = LiteGraph.createNode("t_hotkey"); graph.add(s7);
r = KFConnect.resolveOutput(hk, 0, wt, 0, 0, fakeCanvas);
r = KFConnect.resolveOutput(s7, 0, wt, 0, 0, fakeCanvas);
const ser = JSON.parse(JSON.stringify(graph.serialize()));
const graph2 = new LGraph();
graph2.configure(ser);
const wt2 = graph2.getNodeById(wt.id);
check("мульти-вход восстановлен после configure (link + _kfMulti)",
      wt2 && wt2.inputs[0].link !== null &&
      Array.isArray(wt2.inputs[0]._kfMulti) && wt2.inputs[0]._kfMulti.length >= 1 &&
      Object.keys(graph2.links).length >= 2);

console.log(fails === 0 ? "\nВСЕ ТЕСТЫ ПРОЙДЕНЫ" : "\nЕСТЬ ПРОВАЛЫ: " + fails);
process.exit(fails === 0 ? 0 : 1);
