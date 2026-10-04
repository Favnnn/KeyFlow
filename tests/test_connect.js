/* Headless-проверка логики соединений litegraph (Node.js). */
"use strict";
global.window = global;
global.document = {
    addEventListener() {}, removeEventListener() {},
    createElement() { return { style: {}, appendChild() {}, addEventListener() {} }; },
    body: { appendChild() {}, style: {} },
};
Object.defineProperty(global, "navigator", { value: { userAgent: "node" }, configurable: true });
const fs = require("fs");
const path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "web", "lib", "litegraph.js"), "utf8");
const factory = new Function("window", "global", "document", "navigator", src +
    "\n;return window.LiteGraph || (typeof module !== 'undefined' && module && module.exports) || null;");
const LiteGraph = factory(global, global, global.document, global.navigator);
if (!LiteGraph || !LiteGraph.EVENT === undefined) {
    console.log("LiteGraph load failed");
    process.exit(1);
}

console.log("EVENT const:", JSON.stringify(LiteGraph.EVENT), "ACTION:", JSON.stringify(LiteGraph.ACTION));
console.log("isValidConnection event-event:", LiteGraph.isValidConnection("event", "event"));
console.log("isValidConnection string-any:", LiteGraph.isValidConnection("string", "any"));
console.log("isValidConnection event-any:", LiteGraph.isValidConnection("event", "any"));
console.log("isValidConnection pos-string:", LiteGraph.isValidConnection("pos", "string"));
console.log("isValidConnection number-string:", LiteGraph.isValidConnection("number", "string"));
console.log("isValidConnection bool-number:", LiteGraph.isValidConnection("bool", "number"));
console.log("isValidConnection string-pos:", LiteGraph.isValidConnection("string", "pos"));
console.log("isValidConnection pos-number (ожидаем false):", LiteGraph.isValidConnection("pos", "number"));
console.log("isValidConnection event-number (ожидаем false):", LiteGraph.isValidConnection("event", "number"));

class NodeA extends LGraphNode { constructor() { super(); this.addOutput("out", "event"); } }
class NodeB extends LGraphNode { constructor() { super(); this.addInput("in", "event"); } }
class NodeC extends LGraphNode { constructor() { super(); this.addOutput("path", "string"); } }
class NodeD extends LGraphNode { constructor() { super(); this.addInput("value", "any"); } }
LiteGraph.registerNodeType("a", NodeA);
LiteGraph.registerNodeType("b", NodeB);
LiteGraph.registerNodeType("c", NodeC);
LiteGraph.registerNodeType("d", NodeD);

const g = new LGraph();
const a = LiteGraph.createNode("a"); g.add(a);
const b = LiteGraph.createNode("b"); g.add(b);
const c = LiteGraph.createNode("c"); g.add(c);
const d = LiteGraph.createNode("d"); g.add(d);

const r1 = a.connect(0, b, 0);
console.log("connect event->event:", r1 ? "OK" : "FAILED");
const r2 = c.connect(0, d, 0);
console.log("connect string->any:", r2 ? "OK" : "FAILED");
console.log("graph links:", Object.keys(g.links).length);
console.log("findInputSlotByType event on B:", b.findInputSlotByType("event", false, true));
console.log("slot_types_in:", JSON.stringify(LiteGraph.slot_types_in));
console.log("slot_types_out:", JSON.stringify(LiteGraph.slot_types_out));
console.log("reg_in event:", JSON.stringify((LiteGraph.registered_slot_in_types["event"] || {}).nodes || "MISSING"));
console.log("reg_out event:", JSON.stringify((LiteGraph.registered_slot_out_types["event"] || {}).nodes || "MISSING"));
