/* KeyFlow — система соединений v2.
   Чистая логика жеста «пин → пин» без зависимости от DOM:
   подключается и браузером (через app.js), и headless-тестами.
   Требует, чтобы к моменту ВЫЗОВА существовали:
   LiteGraph, LGraphNode.connect, connectByType, connectByTypeOutput (патчи app.js). */
(function (global) {
    "use strict";

    const LG = global.LiteGraph || (typeof LiteGraph !== "undefined" ? LiteGraph : null);

    /* имя типа для показа человеку: number = int (как называется выход константы) */
    const TNAME = (t) => (t === "number" ? "int" : String(t || ""));

    /* Ближайший пин к точке (gx, gy) среди всех нод.
       Возвращает {node, isOutput, slot, type, name, pin:[x,y], d} или null. */
    function findPin(nodes, gx, gy) {
        let best = null;
        const list = nodes || [];
        for (let n = 0; n < list.length; ++n) {
            const node = list[n];
            if (!node) continue;
            if (node.outputs) {
                for (let i = 0; i < node.outputs.length; ++i) {
                    const p = node.getConnectionPos(false, i);
                    if (!p) continue;
                    if (gx >= p[0] - 15 && gx <= p[0] + 15 && gy >= p[1] - 10 && gy <= p[1] + 10) {
                        const d = (gx - p[0]) * (gx - p[0]) + (gy - p[1]) * (gy - p[1]);
                        if (!best || d < best.d) {
                            best = { node, isOutput: true, slot: i, type: node.outputs[i].type || "any",
                                     name: node.outputs[i].name || ("out" + i), pin: [p[0], p[1]], d };
                        }
                    }
                }
            }
            if (node.inputs) {
                for (let i = 0; i < node.inputs.length; ++i) {
                    const p = node.getConnectionPos(true, i);
                    if (!p) continue;
                    if (gx >= p[0] - 15 && gx <= p[0] + 15 && gy >= p[1] - 10 && gy <= p[1] + 10) {
                        const d = (gx - p[0]) * (gx - p[0]) + (gy - p[1]) * (gy - p[1]);
                        if (!best || d < best.d) {
                            best = { node, isOutput: false, slot: i, type: node.inputs[i].type || "any",
                                     name: node.inputs[i].name || ("in" + i), pin: [p[0], p[1]], d };
                        }
                    }
                }
            }
        }
        return best;
    }

    /* Тянем из ВЫХОДА: отпустили на ноде (или её пине) — подключить к совместимому входу.
       Возвращает {ok, detail} (detail — человекочитаемо). */
    function resolveOutput(fromNode, slot, target, gx, gy, canvas) {
        if (!fromNode || !target || target === fromNode) return { ok: false, detail: "нет цели" };
        const output = fromNode.outputs && fromNode.outputs[slot];
        if (!output) return { ok: false, detail: "исходный выход не найден" };
        const pinSlot = canvas ? canvas.isOverNodeInput(target, gx, gy) : -1;
        if (pinSlot !== -1) {
            const inp = target.inputs[pinSlot];
            const r = fromNode.connect(slot, target, pinSlot);
            return { ok: !!r, detail: r ? ("вход «" + inp.name + "»") : ("типы «" + TNAME(output.type) + "» → «" + TNAME(inp.type) + "» несовместимы") };
        }
        const r = fromNode.connectByType(slot, target, output.type);
        if (r && typeof r.target_slot === "number" && target.inputs[r.target_slot]) {
            return { ok: true, detail: "вход «" + target.inputs[r.target_slot].name + "»" };
        }
        return { ok: false, detail: "нет совместимого входа (тип «" + TNAME(output.type) + "»)" };
    }

    /* Тянем из ВХОДА: отпустили на ноде — подключить к её совместимому выходу. */
    function resolveInput(fromNode, slot, target, gx, gy, canvas) {
        if (!fromNode || !target || target === fromNode) return { ok: false, detail: "нет цели" };
        const input = fromNode.inputs && fromNode.inputs[slot];
        if (!input) return { ok: false, detail: "исходный вход не найден" };
        const pinSlot = canvas ? canvas.isOverNodeOutput(target, gx, gy) : -1;
        if (pinSlot !== -1) {
            const out = target.outputs[pinSlot];
            const r = target.connect(pinSlot, fromNode, slot);
            return { ok: !!r, detail: r ? ("выход «" + out.name + "»") : ("типы «" + TNAME(out.type) + "» → «" + TNAME(input.type) + "» несовместимы") };
        }
        const r = fromNode.connectByTypeOutput(slot, target, input.type);
        if (r && typeof r.origin_slot === "number" && target.outputs && target.outputs[r.origin_slot]) {
            return { ok: true, detail: "выход «" + target.outputs[r.origin_slot].name + "»" };
        }
        return { ok: false, detail: "нет совместимого выхода (тип «" + TNAME(input.type) + "»)" };
    }

    /* Раскладка «пин рядом со своим ползунком» (как в ComfyUI):
       - параметровым входам (имя совпадает с widget.options.property) задаётся
         node-relative pos: точка слева от ползунка, подпись даёт сам виджет;
       - при подключённом проводе виджет гаснет (disabled) — значение из провода;
       - виджеты начинаются сразу под последним стандартным пином (в этом билде
         заголовок рисуется НАД телом ноды, size[1] — высота только тела);
       - высота ноды гарантированно вмещает виджеты.
       Возвращает количество привязанных пинов. */
    function layoutParamWidgets(node) {
        if (!node || !node.inputs || !node.widgets || !node.def || !LG) return 0;
        const SLOT_H = LiteGraph.NODE_SLOT_HEIGHT, W_H = LiteGraph.NODE_WIDGET_HEIGHT;
        const params = node.def.params || [];
        const isParam = (inp) => params.some(p => p.name === inp.name);
        const nTop = Math.max(
            node.inputs.filter(i => !isParam(i)).length,
            (node.outputs || []).length, 1);
        // стандартные (не параметровые) входы — ровно в верхнюю зону, подряд.
        // Без этого слот с большим индексом (например «path» у «Скриншота»)
        // рисуется по номеру слота и повисает точкой в середине ноды.
        let row = 0;
        for (const inp of node.inputs) {
            if (isParam(inp)) continue;
            inp.pos = [SLOT_H * 0.5, row * SLOT_H + SLOT_H * 0.7];
            ++row;
        }
        let y = nTop * SLOT_H + 4;                // сразу под последним стандартным пином
        let fitBottom = y, pinned = 0;
        for (const w of node.widgets) {
            const h = w.computeSize ? w.computeSize(node.size[0])[1] : W_H;
            w.y = y; w.last_y = y;
            const inp = node.inputs.find(i => i.name === (w.options && w.options.property));
            if (inp) {
                inp.pos = [SLOT_H * 0.5, y + W_H * 0.5];
                inp.label = "";
                w.disabled = inp.link != null;
                ++pinned;
            }
            fitBottom = y + h;
            y += h + 4;
        }
        // высота — ровно по содержимому (пустоту внизу больших нод убираем);
        // ноды, растянутые пользователем за угол (_kfUserResized), только подрастают
        if (!node._kfUserResized) node.size[1] = fitBottom + 10;
        else if (node.size[1] < fitBottom + 10) node.size[1] = fitBottom + 10;
        return pinned;
    }

    global.KFConnect = { findPin, resolveOutput, resolveInput, layoutParamWidgets,
                         isValid: LG ? LG.isValidConnection : null };
})(typeof window !== "undefined" ? window : globalThis);