/* KeyFlow — приложение: node-редактор на litegraph + интеграция с сервером. */
/* global LiteGraph, LGraph, LGraphCanvas, CircuitBoard, t, applyI18n, STR */
"use strict";

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const TYPE_COLORS = {
    event: "#ffffff", any: "#9a9a9a", string: "#e8c04a", number: "#5ac8fa",
    bool: "#ff6b6b", pos: "#7bd88f",
};

/* имя типа для показа человеку: number = int (как называется выход константы) */
const kfTypeName = (tp) => (tp === "number" ? "int" : String(tp || ""));

const CATEGORY_COLORS = {
    triggers: { color: "#7a3b3b", bgcolor: "#452323", boxcolor: "#e0a080" },
    mouse:    { color: "#2b4a66", bgcolor: "#1d3346", boxcolor: "#7db6e0" },
    keyboard: { color: "#4a3b7a", bgcolor: "#2b2349", boxcolor: "#b39be8" },
    screen:   { color: "#2f6653", bgcolor: "#1e4237", boxcolor: "#7bd8a5" },
    variables:{ color: "#6b5a2a", bgcolor: "#42361a", boxcolor: "#e8c04a" },
    logic:    { color: "#5a5a5a", bgcolor: "#373737", boxcolor: "#bbbbbb" },
    system:   { color: "#3d5c85", bgcolor: "#26394f", boxcolor: "#8fc7ff" },
    web:      { color: "#2e6e8a", bgcolor: "#1f455c", boxcolor: "#7fd8ff" },
    conditions:{ color: "#2a6a6a", bgcolor: "#1c4747", boxcolor: "#7fd8c8" },
    group:    { color: "#6a4a8a", bgcolor: "#3c2954", boxcolor: "#c9a8ff" },
};

/* человекочитаемые названия категорий: тег в палитре + сортировка по ним */
const CATEGORY_TITLES = {
    triggers:   { en: "Triggers",   ru: "Триггеры" },
    mouse:      { en: "Mouse",      ru: "Мышь" },
    keyboard:   { en: "Keyboard",   ru: "Клавиатура" },
    screen:     { en: "Screen",     ru: "Экран" },
    variables:  { en: "Variables",  ru: "Переменные" },
    logic:      { en: "Logic",      ru: "Логика" },
    system:     { en: "System",     ru: "Система" },
    web:        { en: "Web",        ru: "Веб" },
    conditions: { en: "Conditions", ru: "Условия" },
    group:      { en: "Groups",     ru: "Группы" },
};

function categoryTitle(def, lang) {
    const t = CATEGORY_TITLES[def && def.category];
    return t ? (t[lang] || t.ru) : (def && def.category) || "";
}

const state = {
    settings: { language: "ru", theme: "dark", link_style: "circuit",
                snap_grid: true, active_workflow: "main", open_browser: true },
    workflows: [],
    current: "main",
    defs: {},                 // node_type -> def
    engineRunning: false,
    nodeStates: {},
    nodePreviews: {},         // node_id -> [текст, ...] для ноды «Превью»
    stack: [],                // редактор групп
    autosaveTimer: null,
    capture: null,
    groupCounter: 0,
    searchTriggersCatOnly: false, // фильтр поиска «только триггеры»
    searchConditionsOnly: false, // фильтр поиска «только условия»
    searchMouseOnly: false,      // фильтр поиска «только мышь»
    searchKeyboardOnly: false,   // фильтр поиска «только клавиатура»
};

let graph = null;
let canvas = null;
let circuit = null;

/* ------------------------------------------------------------------ */
/* API                                                                */
/* ------------------------------------------------------------------ */
async function apiGet(path) {
    const r = await fetch(path, { cache: "no-store" });
    if (!r.ok) throw new Error(path + " -> " + r.status);
    return r.json();
}
async function apiPost(path, body) {
    const r = await fetch(path, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
    });
    return r.json();
}

/* ------------------------------------------------------------------ */
/* Тосты / журнал                                                     */
/* ------------------------------------------------------------------ */
function toast(msg, level) {
    const el = document.createElement("div");
    el.className = "toast " + (level || "info");
    el.textContent = msg;
    $("#toasts").appendChild(el);
    setTimeout(() => el.classList.add("show"), 10);
    setTimeout(() => { el.classList.remove("show"); setTimeout(() => el.remove(), 300); },
               level === "error" ? 7000 : 3500);
}

/* Всплывающее меню пина (ПКМ): отсоединение выбранных связей */
function kfPinMenu(rows, x, y) {
    const old = document.getElementById("kf-pin-menu");
    if (old) old.remove();
    const menu = document.createElement("div");
    menu.id = "kf-pin-menu";
    for (const r of rows) {
        const it = document.createElement("div");
        it.className = "kf-pin-item";
        it.textContent = r.label;
        it.addEventListener("click", () => { cleanup(); r.act(); });
        menu.appendChild(it);
    }
    document.body.appendChild(menu);
    const rect = menu.getBoundingClientRect();
    menu.style.left = Math.max(4, Math.min(x, window.innerWidth - rect.width - 8)) + "px";
    menu.style.top = Math.max(4, Math.min(y, window.innerHeight - rect.height - 8)) + "px";
    const close = (e) => { if (!menu.contains(e.target)) cleanup(); };
    function cleanup() {
        document.removeEventListener("mousedown", close, true);
        if (menu.parentNode) menu.remove();
    }
    setTimeout(() => document.addEventListener("mousedown", close, true), 0);
}

/* Undo / Redo структуры графа */
const kfUndo = { stack: [], redo: [], limit: 50, restoring: false };
function kfSnapshot() {
    if (kfUndo.restoring || !graph) return;
    const snap = JSON.stringify(graph.serialize());
    if (kfUndo.stack.length && kfUndo.stack[kfUndo.stack.length - 1] === snap) return;
    kfUndo.stack.push(snap);
    if (kfUndo.stack.length > kfUndo.limit) kfUndo.stack.shift();
    kfUndo.redo.length = 0;
}
function kfRestore(snap) {
    kfUndo.restoring = true;
    try {
        graph.configure(JSON.parse(snap));
        if (canvas) canvas.setDirty(true, true);
    } finally { kfUndo.restoring = false; }
}
function kfDoUndo() {
    if (!graph) return;
    if (kfUndo.stack.length < 2) { toast("Нечего отменять", "info"); return; }
    kfUndo.redo.push(kfUndo.stack.pop());
    kfRestore(kfUndo.stack[kfUndo.stack.length - 1]);
    toast("Отменено", "info");
}
function kfDoRedo() {
    if (!graph || !kfUndo.redo.length) { toast("Нечего вернуть", "info"); return; }
    const snap = kfUndo.redo.pop();
    kfUndo.stack.push(snap);
    kfRestore(snap);
    toast("Возвращено", "info");
}

function addLog(evt) {
    const box = $("#log-lines");
    const line = document.createElement("div");
    line.className = "log-line " + (evt.level || "info");
    line.textContent = "[" + (evt.ts || "") + "] " + (evt.level || "info").toUpperCase() + ": " + evt.msg;
    box.appendChild(line);
    while (box.childElementCount > 400) box.removeChild(box.firstChild);
    box.scrollTop = box.scrollHeight;
}

/* ------------------------------------------------------------------ */
/* Тема / язык                                                        */
/* ------------------------------------------------------------------ */
function applyTheme() {
    const th = state.settings.theme === "light" ? "light" : "dark";
    document.documentElement.dataset.theme = th;
    if (canvas) canvas.background_color = (th === "light") ? "#e9e9e9" : "#161616";
    if (canvas) canvas.setDirty(true, true);
}

function applyLanguage() {
    window.__KF_LANG = state.settings.language || "ru";
    document.documentElement.lang = window.__KF_LANG;
    applyI18n();
}

/* ------------------------------------------------------------------ */
/* Регистрация типов нод                                              */
/* ------------------------------------------------------------------ */
function plabel(p) {
    const lang = state.settings.language || "ru";
    if (!p.label) return p.name;
    return p.label[lang] || p.label.en || p.name;
}

function addWidgetFor(node, p) {
    const label = plabel(p);
    if (!node.properties) node.properties = {};
    if (node.properties[p.name] === undefined) node.properties[p.name] = p.default;
    const opt = { property: p.name };
    switch (p.type) {
        case "number":
            node.addWidget("number", label, Number(p.default) || 0, () => {}, opt);
            break;
        case "bool":
            node.addWidget("toggle", label, !!p.default, () => {}, opt);
            break;
        case "combo":
            node.addWidget("combo", label, p.default, () => {}, Object.assign(opt, { values: p.options || [] }));
            break;
        case "dynamic":
            // выпадающий список, опции которого сервер отдаёт на лету
            // (например, список микрофонов) — см. fillDynamicOptions
            fillDynamicOptions(node, p);
            break;
        case "hotkey":
            node.addWidget("text", label, p.default, () => {}, opt);
            node.addWidget("button", "⏺ " + t("capture_record"), null,
                () => startCapture(node, p.name));
            break;
        default:
            if (p.multiline) {
                // многострочный параметр (core/text): высокое поле на теле ноды,
                // клик открывает textarea-диалог (как текстовое поле в ComfyUI)
                node.addCustomWidget(kfTextBoxWidget(node, p));
                break;
            }
            node.addWidget("text", label, p.default ?? "", () => {}, opt);
    }
}

/* Динамические комбо-параметры (options_endpoint): опции отдаёт сервер.
   Litegraph умеет values-функцию — список берётся из кэша на момент клика,
   поэтому даже если запрос ещё не дошёл, выпадает актуальный список. */
const _dynamicOptsCache = {};
const DEVICE_DEFAULT = "— по умолчанию —";
const _dynamicOptsAsked = {};
async function preloadDynamicOptions() {
    // собрать endpoints всех динамических параметров и подтянуть их один раз
    for (const def of Object.values(state.defs)) {
        for (const p of def.params || []) {
            if (p.options_endpoint && !_dynamicOptsAsked[p.options_endpoint]) {
                _dynamicOptsAsked[p.options_endpoint] = true;
                apiGet("/api/" + p.options_endpoint).then((r) => {
                    _dynamicOptsCache[p.options_endpoint] =
                        [DEVICE_DEFAULT].concat(r.map((d) => "#" + d.index + " — " + d.name));
                }).catch(() => {});
            }
        }
    }
}
function fillDynamicOptions(node, p) {
    // values-функция: litegraph вызывает её при каждом открытии списка
    node.addWidget("combo", plabel(p), p.default ?? "", () => {},
        { property: p.name, values: () => _dynamicOptsCache[p.options_endpoint] || [DEVICE_DEFAULT] });
}

/* Многострочное текстовое поле (параметр с флагом multiline).
   Кастом-виджет: встроенный «text» litegraph рисует фиксированной высоты
   и не даёт сделать поле выше. Рисуем высокий прямоугольник с текстом,
   клик открывает стандартный textarea-диалог. Ноду можно растянуть за угол —
   поле занимает всю высоту, и видно больше строк (снизу вверх не листаем —
   просто растянули и смотрим). */
function kfTextBoxWidget(node, p) {
    const MIN_ROWS = 4;         // минимальная высота поля (и высота «по умолчанию»)
    const LINE_H = 16;
    const w = {
        type: "kf_textbox",
        name: plabel(p),
        value: node.properties[p.name] !== undefined ? node.properties[p.name] : (p.default ?? ""),
        options: { property: p.name },
        computeSize(width) {
            return [width, LiteGraph.NODE_WIDGET_HEIGHT * MIN_ROWS];
        },
        draw(ctx, n, widget_width, y) {
            // поле тянется до низа ноды: растянул ноду — увидел больше строк
            const h = Math.max(this.computeSize(widget_width)[1],
                               n.size[1] - y - 10);
            ctx.strokeStyle = LiteGraph.WIDGET_OUTLINE_COLOR;
            ctx.fillStyle = LiteGraph.WIDGET_BGCOLOR;
            ctx.beginPath();
            if (ctx.roundRect) ctx.roundRect(15, y, widget_width - 30, h, [6]);
            else ctx.rect(15, y, widget_width - 30, h);
            ctx.fill();
            ctx.save();
            ctx.beginPath();
            ctx.rect(15, y, widget_width - 30, h);
            ctx.clip();
            ctx.fillStyle = LiteGraph.WIDGET_TEXT_COLOR;
            ctx.font = "12px Consolas, monospace";
            ctx.textAlign = "left";
            ctx.textBaseline = "top";
            // переносящие строки: длинная строка делится на куски по ширине поля
            const maxW = widget_width - 44;
            const chunks = [];
            for (const raw of String(this.value ?? "").split("\n")) {
                if (!raw.length) { chunks.push(""); continue; }
                let cur = "";
                for (const word of raw.split(/(\s+)/)) {
                    let piece = cur + word;
                    if (cur && ctx.measureText(piece).width > maxW) {
                        chunks.push(cur);
                        cur = word.trimStart();
                        // очень длинное слово без пробелов — жёстко режем по ширине
                        while (ctx.measureText(cur).width > maxW && cur.length > 1) {
                            let cut = cur.length - 1;
                            while (cut > 1 && ctx.measureText(cur.slice(0, cut)).width > maxW) --cut;
                            chunks.push(cur.slice(0, cut));
                            cur = cur.slice(cut);
                        }
                    } else {
                        cur = piece;
                    }
                }
                if (cur.length) chunks.push(cur);
            }
            // сколько строк влезает в поле
            const maxRows = Math.max(MIN_ROWS, Math.floor((h - 8) / LINE_H));
            const vis = chunks.slice(0, maxRows);
            if (chunks.length > maxRows) {
                vis[maxRows - 1] = "… +" + (chunks.length - maxRows);
            }
            for (let i = 0; i < vis.length; ++i) {
                ctx.fillText(vis[i], 22, y + 8 + i * LINE_H, maxW);
            }
            // ручка ресайза: три полоски по центру нижнего края поля
            ctx.fillStyle = "rgba(128,128,140,0.55)";
            for (let k = 0; k < 3; ++k) {
                ctx.fillRect(15 + (widget_width - 30) / 2 - 14 + k * 11, y + h - 6, 8, 2.5);
                ctx.fillRect(15 + (widget_width - 30) / 2 - 14 + k * 11, y + h - 2, 8, 2.5);
            }
            ctx.restore();
        },
        mouse(event, pos, n) {
            if (event.type !== "mousedown") return true;
            LGraphCanvas.active_canvas.prompt("Value", w.value, (v) => {
                w.value = v;
                n.setProperty(p.name, v);
                n.setDirtyCanvas(true, true);
            }, event, true);
            return true;
        },
    };
    return w;
}

/* «Switch / Case» (как switch в C++): выход на каждый вариант, имя выхода =
   значение варианта («case 5:» -> выход «5»); последний выход «default» —
   «всё остальное». Свободные лишние слоты убираются, занятые проводами не
   трогаются. Вызывается из onPropertyChanged и onConfigure. */
function kfSyncSwitchOutputs(node) {
    if (!node || node.type !== "conditions/switch") return;
    const cases = String(node.properties.cases ?? "");
    const seen = [];
    for (const raw of cases.split(/[\n,]/)) {
        const s = raw.trim();
        if (s && !seen.includes(s)) seen.push(s);
    }
    if (!node.outputs) node.outputs = [];
    // слоты вариантов — всё до хвостового «default»
    let dfltIdx = node.outputs.findIndex(o => o.name === "default");
    const body = dfltIdx < 0 ? node.outputs : node.outputs.slice(0, dfltIdx);
    // 1) убрать лишние СВОБОДНЫЕ слоты с конца (removeOutput берёт индекс)
    for (let i = body.length - 1; i >= seen.length; --i) {
        if (!body[i].links || !body[i].links.length) {
            node.removeOutput(i);
        }
    }
    // 2) переименовать свободные слоты, чьё значение изменилось
    for (let i = 0; i < Math.min(seen.length, node.outputs.length); ++i) {
        const o = node.outputs[i];
        if (o.name === "default") break;
        if (o.name !== seen[i] && (!o.links || !o.links.length)) {
            o.name = seen[i];
        }
    }
    // 3) добавить недостающие перед «default» (или в конец)
    dfltIdx = node.outputs.findIndex(o => o.name === "default");
    const insertAt = dfltIdx < 0 ? node.outputs.length : dfltIdx;
    let have = caseSlots(node).length;
    while (have < seen.length) {
        // имя, которого ещё нет среди выходов (дубликаты уже срезаны)
        const missing = seen.find(s => !node.outputs.some(o => o.name === s));
        node.outputs.splice(insertAt + have, 0,
            { name: missing != null ? missing : seen[have], type: "event", links: null });
        ++have;
        if (node.graph) node.graph._version++;
    }
    // размер ноды пересчитать (computeSize учитывает слоты)
    node.setSize(node.computeSize());
    node.setDirtyCanvas(true, true);
    if (node.graph) node.graph._version++;
}

function caseSlots(node) {
    return (node.outputs || []).filter(o => o.name !== "default");
}

function makeNodeClass(def) {
    const lang = state.settings.language || "ru";
    const Ctor = function () {
        this.def = def;
        this.title = def.title[lang] || def.title.en || def.id;
        const cat = CATEGORY_COLORS[def.category] || CATEGORY_COLORS.system;
        this.color = cat.color; this.bgcolor = cat.bgcolor; this.boxcolor = cat.boxcolor;
        (def.inputs || []).forEach(inp => this.addInput(inp.name, inp.type || "any"));
        (def.outputs || []).forEach(out => this.addOutput(out.name, out.type || "any"));
        (def.params || []).forEach(p => addWidgetFor(this, p));
        // «Switch / Case»: сразу собрать выходы по вариантам из поля
        if (def.id === "conditions/switch") kfSyncSwitchOutputs(this);
        this.serialize_widgets = false; // единственный источник значений — properties
        this.size = this.computeSize();
        if (this.size[0] < 340) this.size[0] = 340;   // чтобы строки типа cron влезали
    };
    Ctor.title = def.title[lang] || def.title.en || def.id;
    Ctor.desc = def.desc ? (def.desc[lang] || def.desc.en || "") : "";

    Ctor.prototype.onConfigure = function () {
        if (this.widgets) for (const w of this.widgets) {
            const prop = w.options && w.options.property;
            if (prop && this.properties && this.properties[prop] !== undefined) w.value = this.properties[prop];
        }
        // «Switch / Case»: пересобрать выходы по сохранённым «cases»
        kfSyncSwitchOutputs(this);
        // высота, заданная пользователем, переживает перезагрузку: сохранённый
        // размер больше подгоночного -> помечаем ноду, автоподгонка не сожмёт её
        if (this.widgets && this.widgets.length) {
            const SLOT_H = LiteGraph.NODE_SLOT_HEIGHT, W_H = LiteGraph.NODE_WIDGET_HEIGHT;
            const params = (this.def && this.def.params) || [];
            const isParam = (inp) => params.some(p => p.name === inp.name);
            const nTop = Math.max(
                (this.inputs || []).filter(i => !isParam(i)).length,
                (this.outputs || []).length, 1);
            let y = nTop * SLOT_H + 4, fit = y;
            for (const w of this.widgets) {
                const h = w.computeSize ? w.computeSize(this.size[0])[1] : W_H;
                fit = y + h; y += h + 4;
            }
            if (this.size[1] > fit + 12) this._kfUserResized = true;
        }
    };

    /* Пины-параметры (x, y, seconds, text...) стоят слева от своего ползунка,
       подписаны как он, а при подключённом проводе виджет гаснет:
       значение берётся из ноды-источника (как в ComfyUI). */
    Ctor.prototype.onDrawBackground = function () {
        KFConnect.layoutParamWidgets(this);
    };

    /* «Switch / Case»: список «cases» меняется — пересобираем выходы
       (выход на каждый вариант + хвостовой «else»; свободные лишние слоты
       убираются, занятые проводами не трогаются). */
    Ctor.prototype.onPropertyChanged = function (name) {
        if (name === "cases" && this.type === "conditions/switch") {
            kfSyncSwitchOutputs(this);
        }
    };

    Ctor.prototype.onDrawForeground = function (ctx) {
        // бейдж «в разработке» — справа в строке заголовка
        if (this.def && this.def.wip) {
            ctx.save();
            ctx.font = "bold 9px Segoe UI";
            const label = t("tag_wip");
            const w = ctx.measureText(label).width + 10;
            const x = this.size[0] - w - 8, y = -LiteGraph.NODE_TITLE_HEIGHT / 2;
            ctx.fillStyle = "rgba(246, 190, 80, .2)";
            ctx.strokeStyle = "rgba(246, 190, 80, .8)";
            ctx.lineWidth = 1;
            if (ctx.roundRect) {
                ctx.beginPath();
                ctx.roundRect(x, y - 7, w, 14, 7);
                ctx.fill(); ctx.stroke();
            } else {
                ctx.fillRect(x, y - 7, w, 14);
                ctx.strokeRect(x, y - 7, w, 14);
            }
            ctx.fillStyle = "#f6be50";
            ctx.textAlign = "center";
            ctx.fillText(label, x + w / 2, y + 3.5);
            ctx.restore();
        }
        // маркер: у ноды есть пины, выведенные на группу
        if (state.stack.length) {
            const gprops = state.stack[state.stack.length - 1].groupNode.properties;
            const pi = (gprops.inputs || []).filter(m => m.node_id === this.id).length;
            const po = (gprops.outputs || []).filter(m => m.node_id === this.id).length;
            if (pi || po) {
                ctx.save();
                ctx.font = "10px Segoe UI";
                ctx.fillStyle = "#c9a8ff";
                ctx.textAlign = "left";
                ctx.fillText(((pi ? "⇤" + pi : "") + (po ? " ⇥" + po : "")).trim(), 6, this.size[1] - 6);
                ctx.restore();
            }
        }
        const st = state.nodeStates[this.id];
        if (!st) return;
        const col = { running: "#ffd32a", ok: "#2ecc71", error: "#e74c3c", cancelled: "#888888" }[st.state];
        if (!col) return;
        // индикатор в правом верхнем углу, в строке заголовка (она выше тела: y < 0)
        const dx = this.size[0] - 12, dy = -LiteGraph.NODE_TITLE_HEIGHT / 2;
        ctx.fillStyle = col;
        ctx.beginPath();
        ctx.arc(dx, dy, 5, 0, Math.PI * 2);
        ctx.fill();
        // тёмная окантовка, чтобы точка была видна на любом цвете заголовка
        ctx.strokeStyle = "rgba(0,0,0,0.45)";
        ctx.lineWidth = 1;
        ctx.stroke();
        if (st.state === "error" && st.error) this.titleToolTip = st.error;
    };

    Ctor.prototype.getExtraMenuOptions = function (graphcanvas, options) {
        const items = [];
        const selCount = Object.keys(graphcanvas.selected_nodes || {}).length;
        if (this.type === "core/subgraph") {
            items.push({ content: t("open_group"), callback: () => enterGroup(this) });
            items.push({ content: t("rename"), callback: () => renameGroup(this) });
            items.push({ content: t("unpack_group"), callback: () => unpackGroup(this) });
        } else if (this.def) {
            if (this.def.trigger) {
                items.push({ content: t("test_fire"), callback: () => testFire(this) });
            }
            if (selCount > 1) {
                items.push({ content: t("frame_group") + " (Ctrl+G)",
                             callback: () => groupSelectionFrame() });
                items.push({ content: t("collapse_node") + " (Ctrl+Shift+G)",
                             callback: () => groupSelectedIntoNode() });
            }
            if (state.stack.length > 0) {
                for (const p of (this.def.params || [])) {
                    items.push({ content: t("promote_widget") + " «" + plabel(p) + "»",
                                 callback: () => promoteWidget(this, p) });
                }
                (this.inputs || []).forEach((inp, i) => {
                    items.push({ content: t("promote_input") + " «" + inp.name + "»",
                                 callback: () => promoteInput(this, i) });
                });
                (this.outputs || []).forEach((out, i) => {
                    items.push({ content: t("promote_output") + " «" + out.name + "»",
                                 callback: () => promoteOutput(this, i) });
                });
            }
        }
        if (items.length) { return items; } // litegraph сам добавит divider сверху
        return null;
    };

    Ctor.prototype.onDblClick = function () {
        if (this.type === "core/subgraph") enterGroup(this);
    };
    return Ctor;
}

/* Встроенный тип группы (не в поиске).
   Как в ComfyUI: никаких group_input/group_output внутри — промоутед
   входы/выходы лежат в properties.inputs/outputs = [{name,type,node_id,slot}]. */
function registerGroupNodes() {
    function SubgraphNode() {
        const cat = CATEGORY_COLORS.group;
        this.color = cat.color; this.bgcolor = cat.bgcolor; this.boxcolor = cat.boxcolor;
        this.properties = {
            title: "Группа", inner: { nodes: [], links: [], groups: [] }, widgets: [],
            inputs: [], outputs: [],
        };
        this.title = this.properties.title;
        this.size = [220, 60];
        this.serialize_widgets = false;
    }
    SubgraphNode.prototype = Object.create(LGraphNode.prototype);
    SubgraphNode.prototype.constructor = SubgraphNode;
    SubgraphNode.title = "Группа нод";

    SubgraphNode.prototype.onConfigure = function () {
        if (this.properties.title) this.title = this.properties.title;
        if (typeof this.properties.inner === "string") {
            try { this.properties.inner = JSON.parse(this.properties.inner || "{}"); }
            catch (e) { this.properties.inner = { nodes: [], links: [], groups: [] }; }
        }
        if (!Array.isArray(this.properties.inputs)) this.properties.inputs = [];
        if (!Array.isArray(this.properties.outputs)) this.properties.outputs = [];
        this.syncPromotedWidgets();
    };

    SubgraphNode.prototype.syncPromotedWidgets = function () {
        this.widgets = (this.widgets || []).filter(w => !(w.options && w.options.__pw));
        const list = this.properties.widgets || [];
        for (let i = 0; i < list.length; ++i) {
            const pw = list[i];
            const idx = i;
            this.addWidget("text", pw.label || pw.name, pw.value ?? "",
                (v) => {
                    const lst = this.properties.widgets;
                    if (!lst || !lst[idx]) return;
                    lst[idx].value = v;
                    syncWidgetToInner(this.properties, idx, v);
                },
                { __pw: true });
        }
        this.size = this.computeSize();
        if (this.size[0] < 250) this.size[0] = 250;
    };

    SubgraphNode.prototype.getExtraMenuOptions = function (graphcanvas, options) {
        return [
            { content: t("open_group"), callback: () => enterGroup(this) },
            { content: t("rename"), callback: () => renameGroup(this) },
            { content: t("unpack_group"), callback: () => unpackGroup(this) },
        ];
    };

    SubgraphNode.prototype.onDblClick = function () { enterGroup(this); };

    /* Маркер-ноды сабграфа (как Input/Output-ноды в ComfyUI): реальные ноды
       внутри группы — их можно таскать, к ним идут настоящие линии.
       id отрицательные (задаются при создании), исполнение их пропускает.
       Тело полупрозрачное (владеют холстом, не мешают нодам за ними),
       пины остаются непрозрачными — их рисует drawNode независимо от фона. */
    function MarkerIONode() {
        const cat = CATEGORY_COLORS.group;
        this.color = cat.color;
        this.bgcolor = "rgba(60,41,84,0.30)";   // полупрозрачное тело
        this.boxcolor = cat.boxcolor;
        this.properties = { side: "input" };
        this.size = [150, 60];
        this.serialize_widgets = false;
    }
    MarkerIONode.prototype = Object.create(LGraphNode.prototype);
    MarkerIONode.prototype.constructor = MarkerIONode;
    MarkerIONode.title = "Входы группы";

    MarkerIONode.prototype.onConfigure = function () {
        const ru = (state.settings.language || "ru") !== "en";
        const isIn = this.properties.side !== "output";
        this.title = isIn ? (ru ? "Входы" : "Inputs") : (ru ? "Выходы" : "Outputs");
        // старые сохранённые группы хранят непрозрачный bgcolor — возвращаем прозрачность
        this.bgcolor = "rgba(60,41,84,0.30)";
    };

    LiteGraph.registerNodeType("core/subgraph/input", MarkerIONode);
    LiteGraph.registerNodeType("core/subgraph/output", MarkerIONode);

    LiteGraph.registerNodeType("core/subgraph", SubgraphNode);
}

function syncWidgetToInner(props, idx, value) {
    const w = (props.widgets || [])[idx];
    const inner = props.inner;
    if (!w || !inner || typeof inner !== "object") return;
    const n = (inner.nodes || []).find(x => x.id === w.node_id);
    if (n) {
        n.properties = n.properties || {};
        n.properties[w.name] = value;
    }
}

/* ------------------------------------------------------------------ */
/* Загрузка defs и типов                                              */
/* ------------------------------------------------------------------ */
async function loadDefs() {
    try {
        const defs = await apiGet("/api/node_defs");
        for (const def of defs) {
            if (def.hidden) continue;   // алиасы старых id — не для поиска
            state.defs[def.id] = def;
            const C = makeNodeClass(def);
            LiteGraph.registerNodeType(def.id, C);
            if (def.id === "core/preview") setupPreviewNodeClass(C);
        }
        preloadDynamicOptions();   // заполнить кэш комбо до создания нод
    } catch (e) {
        toast(t("node_load_fail") + ": " + e.message, "error");
    }
}

/* Нода «Превью» (preview/show): динамические входы + текст на теле.
   - второй вход (in2, in3...) добавляется автоматически, как только
     последний вход занят проводом;
   - лишние свободные входы с конца убираются (отключили — слоты схлопнулись,
     текст оставшихся входов сдвигается на первые позиции);
   - текст приходят по SSE (node_preview): каждый вход — своя текстовая
     позиция, между позициями небольшая серая перегородка. */
function setupPreviewNodeClass(C) {
    const busySlot = (inp) => inp && (inp.link != null || (inp._kfMulti && inp._kfMulti.length));
    /* слот 0 — белый приёмник событий (только из выходов «out»), слоты 1+ — данные */
    C.prototype.kfIsEventSlot = function (slot) { return slot === 0; };
    C.prototype.kfSyncPreviewInputs = function () {
        if (this._kfSyncing) return;
        this._kfSyncing = true;
        try {
            // нормализация старых сохранений: слот 0 = «in», тип event (белый)
            const first = this.inputs[0];
            if (first && (first.name !== "in" || first.type !== "event")) {
                first.name = "in";
                first.type = "event";
            }
            if (first) first.color_on = busySlot(first) ? TYPE_COLORS.event : "#ffffff";
            // серые точки данных нумеруются с in1: слот 1 → «in1», слот 2 → «in2»...
            // (старые сохранения с «in2»..» нормализуем — связи живут по индексу слота)
            for (let i = 1; i < this.inputs.length; ++i) {
                const want = "in" + i;
                if (this.inputs[i].name !== want) this.inputs[i].name = want;
            }
            let used = 0;
            for (let i = 1; i < this.inputs.length; ++i) if (busySlot(this.inputs[i])) ++used;
            // событийный вход in (слот 0) всегда на месте; данные: свободен только
            // хвост — добавляем ещё один (in2 после занятого in1, in3 после in2...)
            while (this.inputs.length < used + 2) {
                this.addInput("in" + this.inputs.length, "any");
            }
            // убираем лишние СВОБОДНЫЕ входы с конца (последний свободный оставляем)
            while (this.inputs.length > 2 &&
                   !busySlot(this.inputs[this.inputs.length - 1]) &&
                   !busySlot(this.inputs[this.inputs.length - 2])) {
                this.removeInput(this.inputs.length - 1);
            }
            this.size[0] = Math.max(this.size[0], 140);
        } finally {
            this._kfSyncing = false;
        }
    };
    /* событийный вход «in» принимает события: имя «out» или событийный тип
       выхода (у «Условий» выходы true/false — тоже события) */
    C.prototype.onConnectInput = function (target_slot, type, output, outNode, outSlot) {
        if (this.kfIsEventSlot(target_slot)) {
            return !!output && (output.name === "out" || output.type === "event");
        }
        return true;
    };
    C.prototype.onConnectionsChange = function (type, slotIndex, connected, linkInfo) {
        // LiteGraph.INPUT — только входы влияют на набор слотов
        if (type !== (LiteGraph.INPUT || 1)) return;
        const self = this;
        setTimeout(() => { if (self.graph) self.kfSyncPreviewInputs(); }, 0);
        // окраска точки входа по типу подключённого провода (data → свой цвет,
        // event → белый): color_on имеет приоритет у рисовалки litegraph
        if (slotIndex >= 0 && slotIndex < this.inputs.length) {
            const inp = this.inputs[slotIndex];
            if (connected && linkInfo) {
                const outNode = this.graph.getNodeById(linkInfo.origin_id);
                const out = outNode && outNode.outputs && outNode.outputs[linkInfo.origin_slot];
                const t = (out && out.type) || "any";
                inp.color_on = TYPE_COLORS[t] || "#cccccc";
            } else {
                // линия снята: событийный снова белый, данные — свой тип any
                inp.color_on = this.kfIsEventSlot(slotIndex) ? "#ffffff" : TYPE_COLORS.any;
            }
        }
    };
    C.prototype.onConfigure = function () {
        // после загрузки графа нормализуем набор входов (in2 подключён — слоты на месте)
        const self = this;
        setTimeout(() => { if (self.graph) self.kfSyncPreviewInputs(); }, 0);
    };
    const baseDraw = C.prototype.onDrawForeground;
    C.prototype.onDrawForeground = function (ctx) {
        if (baseDraw) baseDraw.call(this, ctx);   // индикатор статуса из makeNodeClass
        const lines = state.nodePreviews[this.id];
        if (!lines || !lines.length) return;
        ctx.save();
        ctx.font = "12px Consolas, monospace";
        ctx.textAlign = "left";
        ctx.textBaseline = "top";
        // нода дорастает по ширине под длинный текст (до 480px),
        // всё что шире — переносится на следующую строку: за рамки не выходит
        let maxRowW = 0;
        for (const line of lines) {
            for (const part of String(line ?? "").split("\n")) {
                maxRowW = Math.max(maxRowW, ctx.measureText(part).width);
            }
        }
        const wantW = Math.ceil(maxRowW) + 16;
        if (wantW > this.size[0] && this.size[0] < 480) {
            this.size[0] = Math.min(480, wantW);
            this.setDirtyCanvas(true, true);
        }
        const maxW = this.size[0] - 16;
        const outRows = [];   // { text, first } — first=true у ПЕРВОЙ строки значения
        for (const line of lines) {
            const parts = String(line ?? "").split("\n");
            for (let j = 0; j < parts.length; ++j) {
                let s = parts[j];
                let seg = 0;
                while (ctx.measureText(s).width > maxW && s.length > 1) {
                    let cut = s.length;
                    while (cut > 1 && ctx.measureText(s.slice(0, cut)).width > maxW) --cut;
                    outRows.push({ text: s.slice(0, cut), first: j === 0 && seg === 0 });
                    s = s.slice(cut);
                    ++seg;
                }
                outRows.push({ text: s, first: j === 0 && seg === 0 });
            }
        }
        const w = this.size[0];
        // текст начинается ниже рядов пинов
        const rows = Math.max(this.inputs.length, (this.outputs || []).length, 1);
        let y = rows * LiteGraph.NODE_SLOT_HEIGHT + 6;
        ctx.fillStyle = "#e8e8f0";
        for (let i = 0; i < outRows.length; ++i) {
            if (i > 0 && outRows[i].first) {
                // небольшая серая перегородка между текстовыми позициями
                ctx.strokeStyle = "rgba(150,150,160,0.75)";
                ctx.lineWidth = 1;
                ctx.beginPath();
                ctx.moveTo(7, y - 2);
                ctx.lineTo(w - 7, y - 2);
                ctx.stroke();
            }
            ctx.fillText(outRows[i].text, 8, y + 2);
            y += 17;
        }
        if (y + 4 > this.size[1]) {           // тело дорастает под текст
            this.size[1] = y + 4;
            this.setDirtyCanvas(true, true);
        }
        ctx.restore();
    };
}

/* ------------------------------------------------------------------ */
/* Поиск нод: превью при наведении + закрытие кликом мимо              */
/* ------------------------------------------------------------------ */
let previewCv = null;
let previewGraph = null;
let previewCanvasObj = null;

function ensurePreviewCanvas() {
    if (!previewCv) {
        previewCv = document.createElement("canvas");
        previewGraph = new LGraph();
        previewCanvasObj = new LGraphCanvas(previewCv, previewGraph);
        previewCanvasObj.editor_alpha = 1;
    }
    return previewCanvasObj;
}

function renderNodePreview(cv, type) {
    const node = LiteGraph.createNode(type);
    if (!node) return false;
    const def = state.defs[type];
    if (def) node.title = def.title[state.settings.language || "ru"] || def.title.en || node.title;
    const c = ensurePreviewCanvas();
    const ctx = cv.getContext("2d");
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    // высота — по содержимому (как на холсте), иначе снизу остаётся серая пустота
    try { KFConnect.layoutParamWidgets(node); } catch (e) { /* превью — не строго */ }
    // поля 6px, сверху — место под заголовок (в этом билде он рисуется НАД телом);
    // масштаб 1.15 — превью чуть крупнее, как просили
    const PAD = 6, TH = LiteGraph.NODE_TITLE_HEIGHT, SC = 1.15;
    const w = Math.round((Math.max(170, node.size[0]) + PAD * 2) * SC);
    const h = Math.round((node.size[1] + TH + PAD * 2) * SC);
    cv.width = w;
    cv.height = h;
    ctx.fillStyle = "#22252b";
    ctx.fillRect(0, 0, w, h);
    // ВАЖНО: drawNode рисует в ТЕКУЩЕМ начале координат — перенос на pos делаем сами
    // (в draw() то же самое: ctx.translate(node.pos) перед drawNode)
    node.pos = [PAD, PAD + TH];
    ctx.scale(SC, SC);
    ctx.translate(node.pos[0], node.pos[1]);
    let ok = false;
    try {
        c.drawNode(node, ctx, true);
        ok = true;
    } catch (e) { ok = false; }
    ctx.restore();
    return ok;
}

function enhanceSearchBox() {
    const esc = (s) => String(s).replace(/[&<>"']/g,
        (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    const orig = LGraphCanvas.prototype.showSearchBox;
    LGraphCanvas.prototype.showSearchBox = function (event, options) {
        // свой фильтр: по названию (RU/EN) и совместимости типов; встроенные
        // селекты и «general»-список отключены (они задваивали результаты)
        options = Object.assign({}, options || {}, { do_type_filter: false });
        const fin = options.type_filter_in || null;
        const fout = options.type_filter_out || null;
        const canvasRef = this;
        const prevSearch = canvasRef.onSearchBox;
        canvasRef.onSearchBox = function (helper, str) {
            const s = (str || "").trim().toLowerCase();
            // частые ноды — в топ пустого запроса («Запуск» — чаще всех)
            const FREQ = ["core/startup", "core/hotkey", "core/click", "core/number",
                "core/text", "core/screenshot", "core/wait", "core/preview", "core/log",
                "core/press_keys", "core/type_text", "core/move_mouse",
                "core/mouse_position", "core/set_variable", "core/get_variable",
                "core/interval", "core/interval_toggle"];
            return Object.keys(LiteGraph.registered_node_types).filter(function (type) {
                const def = state.defs[type];
                if (!def) return false;
                if (state.searchTriggersCatOnly && def.category !== "triggers") return false;
                if (state.searchConditionsOnly && def.category !== "conditions") return false;
                if (state.searchMouseOnly && def.category !== "mouse") return false;
                if (state.searchKeyboardOnly && def.category !== "keyboard") return false;
                if (s) {
                    const hay = (type + " " + (def.title.en || "") + " " +
                                 (def.title.ru || "")).toLowerCase();
                    if (hay.indexOf(s) === -1) return false;
                }
                if (fin) {
                    const ok = (def.inputs || []).some(i => LiteGraph.isValidConnection(fin, i.type || "any"));
                    if (!ok) return false;
                }
                if (fout) {
                    const ok = (def.outputs || []).some(o => LiteGraph.isValidConnection(o.type || "any", fout));
                    if (!ok) return false;
                }
                return true;
            }).sort((a, b) => {
                if (!s) {
                    const ia = FREQ.indexOf(a), ib = FREQ.indexOf(b);
                    if (ia !== -1 || ib !== -1) {
                        return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
                    }
                }
                // группировка по категории (название на языке UI), затем по имени
                const lang = state.settings.language || "ru";
                const ca = categoryTitle(state.defs[a], lang);
                const cb = categoryTitle(state.defs[b], lang);
                if (ca !== cb) return ca < cb ? -1 : 1;
                return a < b ? -1 : a > b ? 1 : 0;
            });
        };

        const dialog = orig.call(this, event, options);
        if (!dialog || !dialog.classList) { canvasRef.onSearchBox = prevSearch; return dialog; }
        const helper = dialog.querySelector(".helper");
        if (!helper) { canvasRef.onSearchBox = prevSearch; return dialog; }
        dialog.classList.add("kf-search-big");
        dialog.style.transform = "none";   // размер окна не зависит от зума холста
        // litegraph при открытии у нижнего края ставит helper маленький ИНЛАЙН
        // max-height (однократно, при создании окна) — из-за этого окно было
        // «то нормальное, то низкое». Сбрасываем: высоту держит CSS
        // (.kf-search-big .helper { max-height: 74vh }); refreshHelper больше
        // maxHeight не трогает, поэтому сброса при открытии достаточно.
        helper.style.maxHeight = "";

        // окно — по центру холста со сдвигом влево на 200px (не закрывает ноды)
        requestAnimationFrame(() => {
            if (!dialog.parentNode) return;
            const c = canvasRef.canvas.getBoundingClientRect();
            const dw = dialog.offsetWidth, dh = dialog.offsetHeight;
            dialog.style.left = Math.max(4, c.left + (c.width - dw) / 2 - 200) + "px";
            dialog.style.top = Math.max(4, c.top + (c.height - dh) / 2) + "px";
        });

        // описание под названием каждой ноды в списке
        const lang = state.settings.language || "ru";
        const decorate = (el) => {
            if (el.dataset && el.dataset.kfDecorated) return;
            let type;
            try { type = unescape(el.dataset.type); } catch (err) { return; }
            const def = state.defs[type];
            if (!def) return;
            el.dataset.kfDecorated = "1";
            const title = def.title[lang] || def.title.en || type;
            const dsc = (def.desc && (def.desc[lang] || def.desc.en)) || "";
            el.innerHTML =
                '<div class="kf-si-title">' + esc(title) +
                (def.wip ? ' <span class="kf-si-tag kf-si-wip">' + esc(t("tag_wip")) + "</span>" : "") +
                ' <span class="kf-si-tag kf-si-cat">' + esc(categoryTitle(def, lang)) + "</span>" +
                ' <span class="kf-si-id">' + esc(type) + "</span></div>" +
                (dsc ? '<div class="kf-si-desc">' + esc(dsc) + "</div>" : "");
        };
        const obs = new MutationObserver((muts) => {
            for (const m of muts) for (const el of m.addedNodes) {
                if (el.classList && el.classList.contains("lite-search-item")) decorate(el);
            }
        });
        obs.observe(helper, { childList: true });
        helper.querySelectorAll(".lite-search-item").forEach(decorate);

        // одиночный клик по пустому месту закрывает поиск
        const closer = (e) => {
            if (dialog.classList.contains("hidden")) return;
            if (!dialog.contains(e.target)) dialog.close();
        };
        setTimeout(() => document.addEventListener("mousedown", closer, true), 0);
        const origClose = dialog.close.bind(dialog);
        dialog.close = function () {
            document.removeEventListener("mousedown", closer, true);
            obs.disconnect();
            if (previewCv && previewCv.parentNode) previewCv.parentNode.removeChild(previewCv);
            canvasRef.onSearchBox = prevSearch;
            origClose();
        };

        // панель превью
        const preview = document.createElement("div");
        preview.className = "kf-search-preview";
        dialog.appendChild(preview);
        const cv = document.createElement("canvas");
        cv.width = 280; cv.height = 110;

        // кнопки-фильтры поиска: «Триггеры» левее, затем «Условия»
        const makeFilterBtn = (cls, activeFlag, label, hint, catMatch) => {
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "kf-trg-btn " + cls + (state[activeFlag] ? " active" : "");
            btn.textContent = label;
            btn.title = hint;
            btn.addEventListener("click", (ev) => {
                ev.preventDefault();
                ev.stopPropagation();
                state[activeFlag] = !state[activeFlag];
                btn.classList.toggle("active", state[activeFlag]);
                // мгновенно скрыть/показать уже отрисованные строки списка
                helper.querySelectorAll(".lite-search-item").forEach((el) => {
                    let tp2 = "";
                    try { tp2 = unescape(el.dataset.type || ""); } catch (err) {}
                    const d2 = state.defs[tp2];
                    el.style.display = (!state[activeFlag] || (d2 && d2.category === catMatch)) ? "" : "none";
                });
                // и перезапросить список у litegraph (debounced refreshHelper)
                const inp = dialog.querySelector("input.value");
                if (inp) inp.dispatchEvent(new KeyboardEvent("keydown", { key: "a" }));
            });
            return btn;
        };
        const catBtn = makeFilterBtn("kf-cat-btn", "searchTriggersCatOnly",
            "⚡ " + t("cat_triggers"), t("cat_triggers_hint"), "triggers");
        const condBtn = makeFilterBtn("kf-cond-btn", "searchConditionsOnly",
            "⑂ " + t("tag_conditions"), t("tag_conditions_hint"), "conditions");
        const mouseBtn = makeFilterBtn("kf-mouse-btn", "searchMouseOnly",
            "🖱 " + t("cat_mouse"), t("cat_mouse_hint"), "mouse");
        const kbBtn = makeFilterBtn("kf-kb-btn", "searchKeyboardOnly",
            "⌨ " + t("cat_keyboard"), t("cat_keyboard_hint"), "keyboard");
        dialog.insertBefore(catBtn, helper);   // «Триггеры» левее «Условий»
        dialog.insertBefore(condBtn, helper);
        dialog.insertBefore(mouseBtn, helper); // затем «Мышь» и «Клавиатура»
        dialog.insertBefore(kbBtn, helper);

        helper.addEventListener("mouseover", (e) => {
            const el = e.target.closest && e.target.closest(".lite-search-item");
            if (!el || !el.dataset || !el.dataset.type) { preview.classList.remove("show"); return; }
            let type;
            try { type = unescape(el.dataset.type); } catch (err) { return; }
            const def = state.defs[type];
            if (!def) { preview.classList.remove("show"); return; }
            const lang = state.settings.language || "ru";
            const cat = CATEGORY_COLORS[def.category] || CATEGORY_COLORS.system;
            preview.innerHTML = "";
            // рисуем превью ноды (канвас подгоняется под размер ноды)
            const ok = renderNodePreview(cv, type);
            preview.appendChild(cv);
            if (!ok) cv.remove();
            const ttl = document.createElement("div");
            ttl.className = "kf-sp-title";
            ttl.textContent = def.title[lang] || def.title.en || type;
            if (def.wip) {
                const wip = document.createElement("span");
                wip.className = "kf-si-tag kf-si-wip";
                wip.style.marginLeft = "6px";
                wip.textContent = t("tag_wip");
                ttl.appendChild(wip);
            }
            preview.appendChild(ttl);
            const c1 = document.createElement("div");
            c1.className = "kf-sp-cat";
            c1.textContent = (def.category || "") + " · " + type;
            preview.appendChild(c1);
            const dsc = document.createElement("div");
            dsc.className = "kf-sp-desc";
            dsc.textContent = (def.desc && (def.desc[lang] || def.desc.en)) || t("search_no_desc");
            preview.appendChild(dsc);
            preview.classList.add("show");
        });
        return dialog;
    };
}

/* Плашка редактирования значения в ноде — ровная вёрстка */
function enhancePrompt() {
    const orig = LGraphCanvas.prototype.prompt;
    LGraphCanvas.prototype.prompt = function (title, value, callback, event, multiline) {
        const dialog = orig.apply(this, arguments);
        if (dialog && dialog.classList) dialog.classList.add("kf-prompt");
        return dialog;
    };
}

// примерная высота ноды для превью (по числу виджетов/пинов)
function nodeSizeFor(def) {
    const nIn = (def.inputs || []).length, nOut = (def.outputs || []).length;
    const nW = (def.params || []).length;
    return 30 + Math.max(nIn, nOut) * 20 + nW * 30;
}

/* ------------------------------------------------------------------ */
/* Холст                                                              */
/* ------------------------------------------------------------------ */
function initCanvas() {
    graph = new LGraph();
    canvas = new LGraphCanvas("#graphcanvas", graph);
    canvas.resize();
    window.addEventListener("resize", () => canvas.resize());
    // Layout может меняться БЕЗ window-resize: хлебные крошки при входе в сабграф,
    // сворачивание боковой панели и т.п. Если буфер холста расходится с CSS-размером,
    // картинка растягивается, а клики/рамка выделения считаются без учёта растяжения —
    // отсюда «неточное выделение». ResizeObserver держит буфер равным CSS-размеру.
    if (window.ResizeObserver) {
        const kfRO = new ResizeObserver(() => { if (canvas) canvas.resize(); });
        kfRO.observe(document.getElementById("canvas-wrap"));
    }
    graph.start();
    graph.onBeforeChange = () => kfSnapshot();   // undo: снимок перед каждым изменением

    canvas.background_color = (state.settings.theme === "light") ? "#e9e9e9" : "#161616";
    canvas.align_to_grid = !!state.settings.snap_grid;
    graph.config.align_to_grid = !!state.settings.snap_grid;   // новые ноды — на сетку

    // ручное растяжение ноды за угол запоминаем — автоподгонка высоты её не трогает.
    // Флаг ставим уже ВО ВРЕМЯ драга (не только на mouseup): иначе автоподгонка
    // каждый кадр возвращает высоту, и тянуть угол «некуда».
    const kfOrigMouseUp = canvas.processMouseUp;
    canvas.processMouseUp = function (e) {
        if (this.resizing_node) this.resizing_node._kfUserResized = true;
        return kfOrigMouseUp.apply(this, arguments);
    };
    const kfOrigMouseMove = canvas.processMouseMove;
    canvas.processMouseMove = function (e) {
        if (this.resizing_node) this.resizing_node._kfUserResized = true;
        return kfOrigMouseMove.apply(this, arguments);
    };

    /* Точечная сетка в тон темы (перекрашивается при смене темы).
       Рисуется ОДНИМ fillRect через pattern — иначе тысячи точек за кадр лагают. */
    let kfGridPattern = null;   // {key, pat}
    canvas.onRenderBackground = (cnv, ctx) => {
        const th = state.settings.theme === "light";
        ctx.save();
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.fillStyle = th ? "#e9e9e9" : "#161616";
        ctx.fillRect(0, 0, cnv.width, cnv.height);
        const step = LiteGraph.CANVAS_GRID_SIZE * canvas.ds.scale;
        if (step >= 8) {
            const st = Math.round(step);
            const key = th + ":" + st;
            if (!kfGridPattern || kfGridPattern.key !== key) {
                const tile = document.createElement("canvas");
                tile.width = tile.height = st;
                const tctx = tile.getContext("2d");
                tctx.fillStyle = th ? "rgba(0,0,0,0.14)" : "rgba(255,255,255,0.07)";
                tctx.fillRect(0, 0, 2, 2);
                kfGridPattern = { key, pat: ctx.createPattern(tile, "repeat") };
            }
            const ox = ((canvas.ds.offset[0] * canvas.ds.scale) % st + st) % st;
            const oy = ((canvas.ds.offset[1] * canvas.ds.scale) % st + st) % st;
            ctx.translate(ox - st, oy - st);
            ctx.fillStyle = kfGridPattern.pat;
            ctx.fillRect(0, 0, cnv.width + st * 2, cnv.height + st * 2);
        }
        ctx.restore();
        return true;   // фон уже нарисован
    };
    canvas.default_connection_color_byType = TYPE_COLORS;
    canvas.default_connection_color = {
        input_on: "#cccccc", input_off: "#888888",
        output_on: "#cccccc", output_off: "#888888",
    };

    /* litegraph не пишет title в JSON, когда он равен дефолту класса — движок
       видел ноды как «#4» (превью печатало «Из: #4»). Всегда сохраняем title. */
    const kfOrigSerialize = LGraphNode.prototype.serialize;
    LGraphNode.prototype.serialize = function () {
        const o = kfOrigSerialize.apply(this, arguments);
        if (this.title != null) o.title = this.title;
        return o;
    };

    circuit = new CircuitBoard();
    circuit.setEnabled(state.settings.link_style === "circuit");
    circuit.init();
    circuit.initOverrides(canvas);

    // ComfyUI-подобное поведение
    LiteGraph.release_link_on_empty_shows_menu = true;  // тянем линию на пустое место -> поиск
    LiteGraph.search_filter_enabled = true;             // фильтр по типу слота в поиске
    canvas.show_info = false;                           // служебный текст в углу холста не нужен
    canvas.render_canvas_border = false;                // синяя рамка вокруг холста не нужна
    // (enhanceSearchBox/enhancePrompt вызываются один раз в boot)

    /* Надёжное соединение (как в ComfyUI):
       отпустили провод на ноде — подключится к первому СОВМЕСТИМОМУ входу/выходу
       (точный тип приоритетнее, существующий провод заменяется), "any" — универсален. */
    LGraphNode.prototype.connectByType = function (slot, target_node, target_slotType) {
        if (target_node && target_node.constructor === Number)
            target_node = this.graph.getNodeById(target_node);
        if (!target_node || target_node === this) return null;
        const inputs = target_node.inputs || [];
        let s = -1;
        for (let i = 0; i < inputs.length; ++i) {
            if (inputs[i].type === target_slotType) { s = i; break; }      // точный тип
        }
        if (s < 0) for (let i = 0; i < inputs.length; ++i) {
            if (LiteGraph.isValidConnection(target_slotType, inputs[i].type)) { s = i; break; }
        }
        if (s < 0 && (target_slotType == LiteGraph.EVENT || target_slotType === "event") && inputs.length)
            s = 0;                                                          // событие -> первый вход
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

    // отказ из-за типов больше не молчаливый
    const origConnect = LGraphNode.prototype.connect;
    LGraphNode.prototype.connect = function (slot, target_node, target_slot) {
        const r = origConnect.apply(this, arguments);
        if ((r === null || r === undefined) && target_node && target_node.inputs) {
            try {
                const s = (typeof slot === "number") ? slot : this.findOutputSlot(slot);
                const out = (this.outputs || [])[s];
                const inp = (target_node.inputs || [])[target_slot];
                if (out && inp && !LiteGraph.isValidConnection(out.type, inp.type)) {
                    toast("Несовместимо: «" + kfTypeName(out.type) + "» → «" + kfTypeName(inp.type) + "»", "error");
                }
            } catch (e) { /* не мешаем основной поток */ }
        }
        return r;
    };

    /* ==== СОЕДИНЕНИЯ v2: свой жест «пин → пин», перехватываем до litegraph ==== */
    function kfLog(msg, level) {
        try {
            console.log("[KeyFlow-связь] " + msg);
            fetch("/api/log", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ level: level || "info", msg: "[связь] " + msg }),
            }).catch(() => {});
        } catch (err) { /* тише едешь */ }
    }
    function kfGraphPos(e) {
        const b = canvas.canvas.getBoundingClientRect();
        return [
            (e.clientX - b.left) / canvas.ds.scale - canvas.ds.offset[0],
            (e.clientY - b.top) / canvas.ds.scale - canvas.ds.offset[1],
        ];
    }
    const kfConn = { active: false, fromNode: null, isOutput: false, slot: -1, type: "any", pin: null, pos: null };

    /* Хит-тест поля виджета (параметра) по графовым координатам.
       Раскладка повторяет KFConnect.layoutParamWidgets (виджеты начинаются
       сразу под последним обычным пином). Возвращает {node, w, p, startClient}
       для полей, привязанных к параметру ноды, иначе null. */
    function kfWidgetHitAt(gx, gy) {
        const nodes = canvas.visible_nodes || graph._nodes || [];
        const SLOT_H = LiteGraph.NODE_SLOT_HEIGHT, W_H = LiteGraph.NODE_WIDGET_HEIGHT;
        for (let i = nodes.length - 1; i >= 0; --i) {
            const node = nodes[i];
            if (!node || !node.widgets || !node.widgets.length || !node.def) continue;
            const params = node.def.params || [];
            const isParam = (inp) => params.some(p => p.name === inp.name);
            const nTop = Math.max(
                node.inputs.filter(x => !isParam(x)).length,
                (node.outputs || []).length, 1);
            let y = nTop * SLOT_H + 4;
            for (const w of node.widgets) {
                const h = w.computeSize ? w.computeSize(node.size[0])[1] : W_H;
                const wy = (w.last_y !== undefined) ? w.last_y : y;
                if (gx >= node.pos[0] + 6 && gx <= node.pos[0] + node.size[0] - 6 &&
                    gy >= node.pos[1] + wy - 2 && gy <= node.pos[1] + wy + h + 2) {
                    const p = params.find(pp => pp.name === (w.options && w.options.property));
                    return p ? { node, w, p } : null;   // обычное поле — не мешаем litegraph
                }
                y += h + 4;
            }
        }
        return null;
    }

    // ЗАХВАТ: pointerdown на пине (перехват на родителе — раньше обработчиков litegraph).
    // Останавливаем и mousedown, и pointerdown: litegraph тянет СВОЮ линию по pointerdown,
    // и без этого при отпускании на пустом месте открываются два окна поиска (их + наше).
    const kfWrap = document.getElementById("canvas-wrap");
    const kfClaimPin = (e) => {
        if (!graph) return;
        // ПКМ по пину: показываем ТОЛЬКО своё меню (litegraph не открывает своё)
        if (e.button === 2) {
            const gpR = kfGraphPos(e);
            const pinR = KFConnect.findPin(canvas.visible_nodes || graph._nodes, gpR[0], gpR[1]);
            if (pinR) e.stopPropagation();   // contextmenu-обработчик ниже покажет kfPinMenu
            return;
        }
        if (e.button !== 0) return;
        if (kfConn.active && e.type === "mousedown") {
            // этот жест уже перехвачен на pointerdown — глушим дубли
            e.preventDefault(); e.stopPropagation();
            return;
        }
        const gp = kfGraphPos(e);
        const pin = KFConnect.findPin(canvas.visible_nodes || graph._nodes, gp[0], gp[1]);
        if (!pin) {
            // тянем от ПОЛЯ ВИДЖЕТА (параметра): вывести параметр на группу.
            // Клик НЕ глушим: litegraph на mousedown открывает окно ввода значения.
            // Запоминаем кандидата: если мышь уйдёт дальше 6px — это уже жест
            // перетаскивания параметра (тогда окно ввода закроем в mousemove).
            const wh = kfWidgetHitAt(gp[0], gp[1]);
            if (wh && state.stack.length && !kfWConn.pending) {
                kfWConn.pending = {
                    node: wh.node, p: wh.p,
                    client: [e.clientX, e.clientY],
                    startWorld: [wh.node.pos[0], wh.node.pos[1] + (wh.w.last_y || 0)],
                };
            }
            return;
        }
        // пины маркера сабграфа — не источник проводов: линия без маппинга была бы «пустой».
        // Тянуть нужно от ноды К маркеру «Входы»/«Выходы»
        if (e.button !== 2 && pin.node &&
            (pin.node.type === "core/subgraph/input" || pin.node.type === "core/subgraph/output")) {
            const ru3 = (state.settings.language || "ru") !== "en";
            toast(ru3 ? "От маркера провод не тянут: тяните провод от ноды К маркеру «Входы»/«Выходы»" :
                          "Don't drag from the marker: drag a wire from a node TO the Inputs/Outputs marker", "error");
            if (e.type === "pointerdown") { e.stopPropagation(); }
            else { e.preventDefault(); e.stopPropagation(); }
            return;
        }
        if (e.type === "pointerdown") {
            // без preventDefault: пусть браузер досылает mousedown/mousemove/mouseup,
            // на которых уже висят наши обработчики ведения линии
            e.stopPropagation();
        } else {
            e.preventDefault();
            e.stopPropagation();
        }
        kfConn.active = true;
        kfConn.fromNode = pin.node;
        kfConn.isOutput = pin.isOutput;
        kfConn.slot = pin.slot;
        kfConn.type = pin.type;
        kfConn.pin = pin.pin;
        kfConn.pos = pin.pin;
        canvas.dirty_canvas = true;
        kfLog("старт: " + (pin.isOutput ? "выход " : "вход ") + "«" + pin.name + "» (" + pin.type + ") ноды #" + pin.node.id +
              " в [" + Math.round(gp[0]) + "," + Math.round(gp[1]) + "]");
    };
    kfWrap.addEventListener("pointerdown", kfClaimPin, true);
    kfWrap.addEventListener("mousedown", kfClaimPin, true);

    /* Надёжный ресайз нод с высоким текстовым полем (kf_textbox): угол захвата
       litegraph всего 10x10 и прячется под полем, клик по полю перехватывает
       жест раньше. Наша зона — весь нижний край такой ноды, включая угол:
       край тянет высоту, правый нижний угол — сразу и высоту, и ширину. */
    const kfResize = { active: false, node: null, startX: 0, startY: 0, startW: 0, startH: 0, corner: false };
    const kfResizeHit = (n, gp) => {
        if (!n || (n.flags && n.flags.collapsed) || !n.widgets ||
            !n.widgets.some(w => w.type === "kf_textbox")) return null;
        if (gp[0] < n.pos[0] || gp[0] > n.pos[0] + n.size[0]) return null;
        const bottom = n.pos[1] + n.size[1];
        if (gp[1] < bottom - 26 || gp[1] > bottom + 4) return null;
        // правый нижний угол — диагональный курсор, тянет в обе стороны
        return (gp[0] >= n.pos[0] + n.size[0] - 22 && gp[1] >= bottom - 22)
            ? "nwse-resize" : "ns-resize";
    };
    kfWrap.addEventListener("mousedown", (e) => {
        if (e.button !== 0 || !graph) return;
        const gp = kfGraphPos(e);
        const nodes = canvas.visible_nodes || graph._nodes || [];
        for (let i = nodes.length - 1; i >= 0; --i) {
            const cur = kfResizeHit(nodes[i], gp);
            if (!cur) continue;
            kfResize.active = true;
            kfResize.node = nodes[i];
            kfResize.startX = e.clientX;
            kfResize.startY = e.clientY;
            kfResize.startW = nodes[i].size[0];
            kfResize.startH = nodes[i].size[1];
            kfResize.corner = cur === "nwse-resize";
            kfWConn.pending = null;   // не путать с жестом выноса параметра на группу
            e.preventDefault();
            e.stopPropagation();
            return;
        }
    }, true);
    kfWrap.addEventListener("mousemove", (e) => {
        if (kfResize.active || !graph) return;
        const gp = kfGraphPos(e);
        const nodes = canvas.visible_nodes || graph._nodes || [];
        let cur = null;
        for (let i = nodes.length - 1; i >= 0; --i) {
            cur = kfResizeHit(nodes[i], gp);
            if (cur) break;
        }
        canvas.canvas.style.cursor = cur || "";
    }, true);
    document.addEventListener("mousemove", (e) => {
        if (!kfResize.active) return;
        const n = kfResize.node;
        if (n) {
            const minH = n.computeSize ? Math.max(30, n.computeSize()[1]) : 30;
            n.size[1] = Math.max(minH, kfResize.startH + (e.clientY - kfResize.startY));
            if (kfResize.corner) {
                n.size[0] = Math.max(120, kfResize.startW + (e.clientX - kfResize.startX));
            }
            n._kfUserResized = true;      // автоподгонка высоты больше не трогает
            canvas.dirty_canvas = true;   // onDrawBackground переразложит виджеты
        }
        canvas.canvas.style.cursor = kfResize.corner ? "nwse-resize" : "ns-resize";
        e.preventDefault();
        e.stopPropagation();
    }, true);
    document.addEventListener("mouseup", (e) => {
        if (!kfResize.active) return;
        const n = kfResize.node;
        kfResize.active = false;
        kfResize.node = null;
        if (n) {
            n.size[0] = Math.round(n.size[0]);
            n.size[1] = Math.round(n.size[1]);
            n._kfUserResized = true;
            n.setDirtyCanvas(true, true);
        }
        canvas.canvas.style.cursor = "";
        e.preventDefault();
        e.stopPropagation();
    }, true);

    // перетаскивание ПАРАМЕТРА (поля виджета) на маркер «Входы» → вывод на группу
    const kfWConn = { active: false, node: null, p: null, pos: null, startWorld: null, pending: null };
    document.addEventListener("mousemove", (e) => {
        // кандидат на жест: активируемся только при смещении ≥ 6px от клика
        if (kfWConn.pending && !kfWConn.active) {
            const dx = e.clientX - kfWConn.pending.client[0];
            const dy = e.clientY - kfWConn.pending.client[1];
            if (dx * dx + dy * dy >= 36) {
                const cand = kfWConn.pending;
                kfWConn.pending = null;
                kfWConn.active = true;
                kfWConn.node = cand.node;
                kfWConn.p = cand.p;
                kfWConn.startWorld = cand.startWorld;
                kfWConn.pos = kfGraphPos(e);
                // litegraph успел открыть окно ввода значения на mousedown — закрываем
                if (canvas.prompt_box) { canvas.prompt_box.close(); canvas.prompt_box = null; }
                canvas.dirty_canvas = true;
                kfLog("старт: тянем параметр «" + plabel(cand.p) + "» ноды #" + cand.node.id);
                e.stopPropagation();
            }
            return;
        }
        if (kfWConn.active) {
            kfWConn.pos = kfGraphPos(e);
            canvas.dirty_canvas = true;
            e.stopPropagation();
        }
    }, true);
    document.addEventListener("mouseup", (e) => {
        kfWConn.pending = null;   // клик без движения: пусть litegraph откроет prompt
        if (!kfWConn.active || e.button !== 0) return;
        e.preventDefault();
        e.stopPropagation();
        kfWConn.active = false;
        canvas.dirty_canvas = true;
        const gp = kfGraphPos(e);
        const nodeUnder = graph.getNodeOnPos(gp[0], gp[1], canvas.visible_nodes);
        const onInputsMarker = nodeUnder && nodeUnder !== kfWConn.node &&
            nodeUnder.type === "core/subgraph/input";
        if (nodeUnder && nodeUnder !== kfWConn.node &&
            (nodeUnder.type === "core/subgraph/input" || nodeUnder.type === "core/subgraph/output")) {
            const ru2 = (state.settings.language || "ru") !== "en";
            if (!onInputsMarker) {
                toast(ru2 ? "Поле параметра выносят на маркер «Входы» (слева)" :
                              "Drag a parameter field to the Inputs marker (left)", "error");
            } else {
                promoteWidget(kfWConn.node, kfWConn.p);
            }
        }
        kfWConn.node = null; kfWConn.p = null;
    }, true);

    // ВЕДЕНИЕ
    document.addEventListener("mousemove", (e) => {
        if (!kfConn.active) return;
        kfConn.pos = kfGraphPos(e);
        canvas.dirty_canvas = true;
        e.stopPropagation();
    }, true);

    // ОТПУСКАНИЕ
    document.addEventListener("mouseup", (e) => {
        if (!kfConn.active || e.button !== 0) return;
        e.preventDefault();
        e.stopPropagation();
        const srcName = kfConn.isOutput
            ? kfConn.fromNode.outputs[kfConn.slot].name
            : kfConn.fromNode.inputs[kfConn.slot].name;
        const from = (kfConn.isOutput ? "выход «" : "вход «") + srcName + "»";
        const gp = kfGraphPos(e);
        const before = graph ? Object.keys(graph.links || {}).length : 0;
        const nodeUnder = graph.getNodeOnPos(gp[0], gp[1], canvas.visible_nodes);
        // бросили провод на маркер-ноду сабграфа («Входы»/«Выходы») → вынести вход/выход на группу
        if (nodeUnder && nodeUnder !== kfConn.fromNode &&
            (nodeUnder.type === "core/subgraph/input" || nodeUnder.type === "core/subgraph/output")) {
            const ru2 = (state.settings.language || "ru") !== "en";
            const onInput = nodeUnder.type === "core/subgraph/input";
            if ((kfConn.isOutput && onInput) || (!kfConn.isOutput && !onInput)) {
                toast(ru2 ? "Пины нод выносят на «Входы» (слева) или на «Выходы» (справа)" :
                              "Node pins go to the Inputs marker (left) or the Outputs marker (right)", "error");
            } else if (kfConn.isOutput) {
                promoteOutput(kfConn.fromNode, kfConn.slot);
            } else {
                promoteInput(kfConn.fromNode, kfConn.slot);
            }
            kfConn.active = false; kfConn.fromNode = null;
            canvas.dirty_canvas = true;
            return;
        }
        let res = null;
        if (nodeUnder && nodeUnder !== kfConn.fromNode) {
            res = kfConn.isOutput
                ? KFConnect.resolveOutput(kfConn.fromNode, kfConn.slot, nodeUnder, gp[0], gp[1], canvas)
                : KFConnect.resolveInput(kfConn.fromNode, kfConn.slot, nodeUnder, gp[0], gp[1], canvas);
        } else if (!nodeUnder) {
            // пустое место → поиск совместимых нод (автоподключение выбранной)
            const slotObj = kfConn.isOutput
                ? kfConn.fromNode.outputs[kfConn.slot]
                : kfConn.fromNode.inputs[kfConn.slot];
            canvas.showSearchBox(e, kfConn.isOutput
                ? { node_from: kfConn.fromNode, slot_from: slotObj, type_filter_in: kfConn.type }
                : { node_to: kfConn.fromNode, slot_from: slotObj, type_filter_out: kfConn.type });
            kfLog("отпускание на пустом месте → поиск нод с типом «" + kfConn.type + "»");
        }
        const after = graph ? Object.keys(graph.links || {}).length : 0;
        if (after > before) {
            kfLog("итог: СВЯЗЬ СОЗДАНА, тянули " + from + (res && res.detail ? " → " + res.detail : ""), "success");
            toast("Связь создана: " + from + (res && res.detail ? " → " + res.detail : ""), "success");
        } else if (nodeUnder) {
            kfLog("итог: НЕ СОЗДАНО, тянули " + from + (res && res.detail ? " — " + res.detail : ""), "error");
            toast("Не соединилось: " + (res && res.detail ? res.detail : "нет совместимого слота"), "error");
        }
        kfConn.active = false;
        kfConn.fromNode = null;
        canvas.dirty_canvas = true;
    }, true);

    // ЛИНИЯ ПЕРЕТАСКИВАНИЯ (экранные координаты, поверх всего).
    // Боковых колонок точек больше нет: вынос входов/выходов — на маркер-ноды
    // «Входы»/«Выходы» внутри группы (как Input/Output-ноды в ComfyUI)
    canvas.onDrawOverlay = function (ctx) {
        if (!graph) return;
        if (kfConn.active && kfConn.pos) {
            // graph-координаты -> пиксели холста: (p + offset) * scale
            const a = canvas.ds.convertOffsetToCanvas(kfConn.pin);
            const b = canvas.ds.convertOffsetToCanvas(kfConn.pos);
            ctx.save();
            ctx.strokeStyle = TYPE_COLORS[kfConn.type] || "#cccccc";
            ctx.lineWidth = 3;
            ctx.lineCap = "round";
            const dir = b[0] >= a[0] ? 1 : -1;
            const off = Math.max(30, Math.abs(b[0] - a[0]) * 0.5) * dir;
            ctx.beginPath();
            ctx.moveTo(a[0], a[1]);
            ctx.bezierCurveTo(a[0] + off, a[1], b[0] - off, b[1], b[0], b[1]);
            ctx.stroke();
            // точка у исходного пина и у курсора
            ctx.fillStyle = "#ffffff";
            for (const d of [a, b]) {
                ctx.beginPath();
                ctx.arc(d[0], d[1], 4, 0, Math.PI * 2);
                ctx.fill();
            }
            ctx.restore();
        }
        // пунктирная линия при перетаскивании поля параметра на маркер
        if (kfWConn.active && kfWConn.pos) {
            const a = canvas.ds.convertOffsetToCanvas(kfWConn.startWorld);
            const b = canvas.ds.convertOffsetToCanvas(kfWConn.pos);
            ctx.save();
            ctx.strokeStyle = "#c9a8ff";
            ctx.lineWidth = 2;
            ctx.setLineDash([6, 5]);
            ctx.lineCap = "round";
            ctx.beginPath();
            ctx.moveTo(a[0], a[1]);
            ctx.lineTo(b[0], b[1]);
            ctx.stroke();
            ctx.setLineDash([]);
            ctx.fillStyle = "#c9a8ff";
            ctx.beginPath();
            ctx.arc(b[0], b[1], 4, 0, Math.PI * 2);
            ctx.fill();
            ctx.restore();
        }
    };

    /* ПКМ по пину: отсоединить связь (каждая — с именем ноды), переименовать выход */
    // удаление линии + МГНОВЕННАЯ перерисовка (иначе линия висит до движения мыши)
    const kfRemoveLink = (lid) => {
        graph.removeLink(lid);
        canvas.draw(true, true);
        scheduleSave();
    };
    document.getElementById("canvas-wrap").addEventListener("contextmenu", (e) => {
        if (!graph) return;
        const gp = kfGraphPos(e);
        const pin = KFConnect.findPin(canvas.visible_nodes || graph._nodes, gp[0], gp[1]);
        if (!pin) return;
        e.preventDefault();
        e.stopPropagation();
        const ru = (state.settings.language || "ru") !== "en";

        const rows = [];
        // пин маркера сабграфа = вынесенная точка входа/выхода: её можно удалить
        const isMarkerPin = pin.node &&
            (pin.node.type === "core/subgraph/input" || pin.node.type === "core/subgraph/output");
        if (isMarkerPin && state.stack.length) {
            // side "input" = маркер «Входы», пины — его ВЫХОДЫ; side "output" — наоборот
            const markerIsInputSide = pin.node.properties && pin.node.properties.side !== "output";
            const pName = (pin.isOutput ? pin.node.outputs[pin.slot] : pin.node.inputs[pin.slot]) || {};
            rows.push({
                label: t("remove_point", { name: pName.name || "" }),
                act: () => kfRemoveGroupPoint(markerIsInputSide, pin.slot),
            });
        }
        if (pin.isOutput) {
            const links = (pin.node.outputs[pin.slot] && pin.node.outputs[pin.slot].links) || [];
            for (const lid of links) {
                const link = graph.links[lid];
                if (!link) continue;
                const tn = graph.getNodeById(link.target_id);
                if (!tn) continue;
                const inName = (tn.inputs[link.target_slot] || {}).name || "";
                rows.push({
                    label: "→ " + (tn.title || "#" + tn.id) + " : " + inName,
                    act: () => kfRemoveLink(lid),
                });
            }
            if (rows.length) {
                rows.push({ label: "✕ " + (ru ? "Отсоединить все" : "Disconnect all") + " (" + rows.length + ")",
                            act: () => { pin.node.disconnectOutput(pin.slot); canvas.draw(true, true); scheduleSave(); } });
            }
            const oname = (pin.node.outputs[pin.slot] || {}).name || "";
            rows.push({
                label: "✎ " + (ru ? "Переименовать выход" : "Rename output") + (oname ? " «" + oname + "»" : ""),
                act: () => {
                    const nn = prompt(ru ? "Новое имя выхода:" : "New output name:", oname);
                    if (nn === null) return;
                    const v = nn.trim();
                    if (!v) return;
                    pin.node.outputs[pin.slot].name = v;
                    pin.node.setDirtyCanvas(true, true);
                    scheduleSave();
                },
            });
        } else {
            const inp = pin.node.inputs[pin.slot] || {};
            const lids = [];
            if (inp.link != null) lids.push(inp.link);
            for (const xl of (inp._kfMulti || [])) if (lids.indexOf(xl) === -1) lids.push(xl);
            for (const lid of lids) {
                const link = graph.links[lid];
                if (!link) continue;
                const sn = graph.getNodeById(link.origin_id);
                const on = (sn && sn.outputs[link.origin_slot] || {}).name || "";
                rows.push({
                    label: "← " + (sn ? (sn.title || "#" + sn.id) : "?") + " : " + on,
                    act: () => kfRemoveLink(lid),
                });
            }
            if (rows.length > 1) {
                rows.push({ label: "✕ " + (ru ? "Отсоединить все" : "Disconnect all") + " (" + rows.length + ")",
                            act: () => { pin.node.disconnectInput(pin.slot); canvas.draw(true, true); scheduleSave(); } });
            }
        }

        if (!rows.length) {
            toast(ru ? "У пина нет подключений" : "Pin has no connections", "info");
            return;
        }
        kfPinMenu(rows, e.clientX, e.clientY);
    }, true);

    canvas.onNodeMoved = () => scheduleSave();
    graph.onAfterChange = () => scheduleSave();

    // изменения виджетов не всегда триггерят onAfterChange — следим за версией графа
    let lastVersion = -1;
    setInterval(() => {
        if (!graph) return;
        if (graph._version !== lastVersion) {
            lastVersion = graph._version;
            scheduleSave();
        }
        // страховка от рассинхрона буфера холста и CSS-размера (неточное выделение):
        // сворачивание панели и прочие изменения layout без window-resize
        if (canvas && canvas.canvas) {
            const wrap = canvas.canvas.parentNode;
            if (wrap && (canvas.canvas.width !== wrap.offsetWidth ||
                         canvas.canvas.height !== wrap.offsetHeight)) {
                canvas.resize();
            }
        }
    }, 2000);

    /* Миникарта (как в ComfyUI): узлы цветами категорий + рамка видимой области */
    const mmBox = document.getElementById("minimap");
    const mm = document.getElementById("minimap-canvas");
    if (mmBox && mm) {
        const mctx = mm.getContext("2d");
        let mmMap = null;   // {minX,minY,k,ox,oy} — для кликов
        let mmSig = "";     // подпись последнего кадра — рисуем только при изменениях
        const mmDraw = () => {
            if (mmBox.classList.contains("hidden") || !graph) return;
            const nodes = graph._nodes || [];
            const ds = canvas.ds;
            // видимая область мира — ds.visible_area (обновляется при каждом кадре)
            const va = (ds.visible_area && ds.visible_area[2]) ? ds.visible_area : null;
            const vw = va ? va[2] : canvas.canvas.width / ds.scale;
            const vh = va ? va[3] : canvas.canvas.height / ds.scale;
            const vx = va ? va[0] : -ds.offset[0], vy = va ? va[1] : -ds.offset[1];
            // перерисовка только при реальных изменениях — без лагов
            let fp = 0;   // дешёвый отпечаток позиций/размеров (нода может двигаться без _version)
            for (const n of nodes) fp += (n.pos[0] | 0) + (n.pos[1] | 0) + (n.size[1] | 0);
            const sig = [nodes.length, graph._version, fp, vx.toFixed(1), vy.toFixed(1),
                         ds.scale.toFixed(3), canvas.canvas.width, canvas.canvas.height].join("|");
            if (sig === mmSig) return;
            mmSig = sig;
            const W = mm.width, H = mm.height;
            mctx.clearRect(0, 0, W, H);
            mctx.fillStyle = "rgba(18,20,26,0.9)";
            mctx.fillRect(0, 0, W, H);
            if (!nodes.length) { mmMap = null; return; }
            let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
            const bb = (n) => n.getBounding ? n.getBounding()
                             : [n.pos[0], n.pos[1], n.size[0], n.size[1]];
            // getBounding возвращает АБСОЛЮТНЫЕ координаты (уже с pos и заголовком)
            for (const n of nodes) {
                const b = bb(n);
                minX = Math.min(minX, b[0]); minY = Math.min(minY, b[1]);
                maxX = Math.max(maxX, b[0] + b[2]); maxY = Math.max(maxY, b[1] + b[3]);
            }
            minX = Math.min(minX, vx); minY = Math.min(minY, vy);
            maxX = Math.max(maxX, vx + vw); maxY = Math.max(maxY, vy + vh);
            const pad = 60;
            minX -= pad; minY -= pad; maxX += pad; maxY += pad;
            const bw = maxX - minX, bh = maxY - minY;
            const k = Math.min(W / bw, H / bh);
            const ox = (W - bw * k) / 2, oy = (H - bh * k) / 2;
            mmMap = { minX, minY, k, ox, oy };
            const toX = (x) => ox + (x - minX) * k;
            const toY = (y) => oy + (y - minY) * k;
            mctx.strokeStyle = "rgba(255,255,255,0.6)";
            mctx.lineWidth = 1;
            mctx.strokeRect(toX(vx), toY(vy), vw * k, vh * k);
            for (const n of nodes) {
                const b = bb(n);
                // CATEGORY_COLORS[x] — ОБЪЕКТ {color,bgcolor,boxcolor}: в fillStyle
                // годится только строка (иначе fillStyle игнорируется → серые ноды)
                const cc = CATEGORY_COLORS[(n.def && n.def.category)] || CATEGORY_COLORS.system;
                mctx.fillStyle = (cc && (cc.boxcolor || cc.color)) || "#888";
                mctx.fillRect(toX(b[0]), toY(b[1]),
                              Math.max(2, b[2] * k), Math.max(2, b[3] * k));
            }
        };
        const mmCenter = (e) => {
            if (!mmMap || !canvas || mmBox.classList.contains("hidden")) return;
            const r = mm.getBoundingClientRect();
            const gx = mmMap.minX + (e.clientX - r.left - mmMap.ox) / mmMap.k;
            const gy = mmMap.minY + (e.clientY - r.top - mmMap.oy) / mmMap.k;
            const ds = canvas.ds;
            ds.offset[0] = canvas.canvas.width / (2 * ds.scale) - gx;
            ds.offset[1] = canvas.canvas.height / (2 * ds.scale) - gy;
            canvas.setDirty(true, true);
        };
        // ведение по миникарте ТОЛЬКО если зажатие началось на самой миникарте:
        // глобальный перехват любого левого перетаскивания «уносил» камеру
        let mmDrag = false;
        mm.addEventListener("mousedown", (e) => {
            mmDrag = true; mmCenter(e);
            e.preventDefault(); e.stopPropagation();
        });
        window.addEventListener("mouseup", () => { mmDrag = false; });
        window.addEventListener("mousemove", (e) => { if (mmDrag) mmCenter(e); });
        // обновление синхронно с перерисовкой холста (ммDraw сам дедуплицирует по сигнатуре)
        const kfOrigCanvasDraw = canvas.draw.bind(canvas);
        canvas.draw = function () {
            const r = kfOrigCanvasDraw.apply(null, arguments);
            mmDraw();
            return r;
        };
        setInterval(mmDraw, 300);   // редкая подстраховка (изменения без перерисовки холста)
    }

    canvas.getExtraMenuOptions = function (graphcanvas, options) {
        const sel = Object.values(canvas.selected_nodes || {});
        return [
            { content: t("frame_group") + " (Ctrl+G)",
              disabled: sel.length === 0,
              callback: () => groupSelectionFrame() },
            { content: t("collapse_node") + " (Ctrl+Shift+G)",
              disabled: sel.length === 0,
              callback: () => groupSelectedIntoNode() },
            null,
        ];
    };
}

/* ------------------------------------------------------------------ */
/* Автосохранение                                                     */
/* ------------------------------------------------------------------ */
function scheduleSave() {
    clearTimeout(state.autosaveTimer);
    state.autosaveTimer = setTimeout(saveNow, 800);
}

function serializeRoot() {
    if (!state.stack.length) return graph.serialize();
    let innerSer = graph.serialize();
    for (let i = state.stack.length - 1; i >= 0; --i) {
        const e = state.stack[i];
        const props = e.groupNode.properties;
        for (const w of (props.widgets || [])) {
            const n = (innerSer.nodes || []).find(x => x.id === w.node_id);
            if (n && n.properties && n.properties[w.name] !== undefined) w.value = n.properties[w.name];
        }
        props.inner = innerSer;
        snapshotGroupNodeInto(e.parentJSON, e.groupNode);
        innerSer = e.parentJSON;
    }
    return innerSer;
}

async function saveNow(silent) {
    clearTimeout(state.autosaveTimer);
    try {
        const ser = serializeRoot();
        // защита от обнуления: пустой граф нельзя записать поверх непустого сохранённого
        if (!(ser.nodes || []).length && state.loadedHadNodes) {
            toast("Сохранение отменено: граф пуст, а в «" + state.current + "» были ноды", "error");
            return;
        }
        await apiPost("/api/workflow/save", { name: state.current, graph: ser });
        if (!silent) toast(t("saved"));
    } catch (e) {
        toast("Save failed: " + e.message, "error");
    }
}

/* ------------------------------------------------------------------ */
/* Workflows                                                          */
/* ------------------------------------------------------------------ */
async function refreshWorkflows() {
    state.workflows = await apiGet("/api/workflows");
    if (!state.workflows.includes(state.current)) state.current = state.workflows[0];
    renderWorkflowList();
}

function renderWorkflowList() {
    const list = $("#wf-list");
    if (!list) return;
    list.innerHTML = "";
    for (const w of state.workflows) {
        const item = document.createElement("div");
        item.className = "wf-item" + (w === state.current ? " active" : "");
        item.innerHTML = '<span class="wf-ico">▦</span><span class="wf-name"></span>';
        item.querySelector(".wf-name").textContent = w;
        item.onclick = async () => {
            if (w === state.current) return;
            clearTimeout(state.autosaveTimer);
            await saveNow(true);
            await loadWorkflow(w);
            await persistSettings({ active_workflow: state.current });
            renderWorkflowList();
        };
        list.appendChild(item);
    }
}

async function loadWorkflow(name) {
    const data = await apiGet("/api/workflow?name=" + encodeURIComponent(name));
    state.current = name;
    state.stack = [];
    updateBreadcrumbs();
    state.nodeStates = {};
    state.loadedHadNodes = !!((data.graph || {}).nodes || []).length;
    graph.clear();
    graph.configure(kfSanitizeGraphData(data.graph || { nodes: [], links: [], groups: [] }));
    const lang = state.settings.language || "ru";
    for (const n of graph._nodes) {
        if (n.type === "core/subgraph") {
            if (n.properties && n.properties.title) n.title = n.properties.title;
        } else if (n.def && n.def.title) {
            n.title = n.def.title[lang] || n.def.title.en || n.title;
        }
    }
    canvas.setDirty(true, true);
}

/* ------------------------------------------------------------------ */
/* Группы (subgraph)                                                  */
/* ------------------------------------------------------------------ */
function uniqueName(existing, base) {
    let name = base, i = 2;
    while (existing.includes(name)) name = base + "_" + (i++);
    return name;
}

/* Рамка-группа как в ComfyUI: вокруг выделения создаётся LGraphGroup.
   Заголовок рамки перетаскивает всё содержимое, угол растягивает рамку,
   ноды внутри остаются обычными нодами холста. */
function groupSelectionFrame() {
    const sel = Object.values(canvas.selected_nodes || {});
    if (!sel.length) { toast(t("select_first"), "error"); return; }
    graph.beforeChange();
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const n of sel) {
        // getBounding возвращает АБСОЛЮТНЫЕ координаты (с pos и заголовком) —
        // прибавлять n.pos нельзя, иначе рамка уезжает «куда-то вбок»
        const b = n.getBounding ? n.getBounding() : [n.pos[0], n.pos[1], n.size[0], n.size[1]];
        minX = Math.min(minX, b[0]);
        minY = Math.min(minY, b[1]);
        maxX = Math.max(maxX, b[0] + b[2]);
        maxY = Math.max(maxY, b[1] + b[3]);
    }
    const g = new LiteGraph.LGraphGroup();
    g.title = t("group");
    // сверху запас под заголовок: он рисуется ВНУТРИ рамки (baseline pos+font_size),
    // при старом отступе 20 ноды «заходили» на надпись шапки
    g.pos = [minX - 20, minY - 48];
    g.size = [maxX - minX + 40, maxY - minY + 48 + 20];
    g.color = "#3a3f52";
    g.font_size = 15;
    graph.add(g);
    g.recomputeInsideNodes();
    graph.afterChange();
    canvas.setDirty(true, true);
    scheduleSave();
}

function groupSelectedIntoNode() {
    const sel = Object.values(canvas.selected_nodes || {});
    if (!sel.length) { toast(t("select_first"), "error"); return; }
    graph.beforeChange();
    const ser = graph.serialize();
    const selIds = new Set(sel.map(n => n.id));
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const n of sel) {
        minX = Math.min(minX, n.pos[0]); minY = Math.min(minY, n.pos[1]);
        maxX = Math.max(maxX, n.pos[0] + n.size[0]); maxY = Math.max(maxY, n.pos[1] + n.size[1]);
    }
    const innerNodes = ser.nodes.filter(n => selIds.has(n.id))
        .map(n => {
            const c = JSON.parse(JSON.stringify(n));
            // связи восстановим из innerLinks — старые id из родителя ведут в никуда
            (c.inputs || []).forEach(i => { delete i.link; });
            (c.outputs || []).forEach(o => { delete o.links; });
            return c;
        });
    const innerLinks = [];
    const slotInputs = [];   // mappings группы: {name,type,node_id,slot}
    const slotOutputs = [];
    const extInputs = [];    // внешние провода в родителе (только на момент сборки)
    const extOutputs = [];
    let lid = 1;

    for (const l of (ser.links || [])) {
        const [id, oid, oslot, tid, tslot, ltype] = l;
        const oIn = selIds.has(oid), tIn = selIds.has(tid);
        if (oIn && tIn) {
            innerLinks.push([lid++, oid, oslot, tid, tslot, ltype || "any"]);
        } else if (oIn && !tIn) {
            const onode = ser.nodes.find(n => n.id === oid);
            const o = (onode.outputs || [])[oslot] || {};
            slotOutputs.push({
                name: uniqueName(slotOutputs.map(p => p.name), (onode.title || "out") + "_" + (o.name || "out")),
                type: o.type || "any",
                node_id: oid, slot: oslot,
            });
            extOutputs.push({ parentDst: [tid, tslot] });
        } else if (!oIn && tIn) {
            const tnode = ser.nodes.find(n => n.id === tid);
            const inp = (tnode.inputs || [])[tslot] || {};
            slotInputs.push({
                name: uniqueName(slotInputs.map(p => p.name), (tnode.title || "in") + "_" + (inp.name || "in")),
                type: inp.type || "any",
                node_id: tid, slot: tslot,
            });
            extInputs.push({ parentSrc: [oid, oslot] });
        }
    }

    const inner = { nodes: innerNodes, links: innerLinks, groups: [] };

    // маркер-ноды «Входы»/«Выходы» внутри группы + настоящие линии к ним
    const IN_ID = -10, OUT_ID = -100;
    const markerIn = {
        id: IN_ID, type: "core/subgraph/input",
        pos: [minX - 320, minY], size: [150, Math.max(80, 50 + slotInputs.length * 20)],
        properties: { side: "input" },
        flags: {}, order: 0, mode: 0,
        outputs: slotInputs.map(m => ({ name: m.name, type: m.type, links: [] })),
        inputs: [],
    };
    const markerOut = {
        id: OUT_ID, type: "core/subgraph/output",
        pos: [maxX + 90, minY], size: [150, Math.max(80, 50 + slotOutputs.length * 20)],
        properties: { side: "output" },
        flags: {}, order: 0, mode: 0,
        outputs: [], inputs: slotOutputs.map(m => ({ name: m.name, type: m.type, link: null })),
    };
    for (let i = 0; i < slotInputs.length; ++i) {
        const m = slotInputs[i];
        const mL = [lid++, IN_ID, i, m.node_id, m.slot, m.type || "any"];
        innerLinks.push(mL);            // линия маркер→нода (визуальная, движок не исполняет)
        markerIn.outputs[i].links.push(mL[0]);
        const tn = innerNodes.find(n => n.id === m.node_id);
        if (tn && tn.inputs && tn.inputs[m.slot]) tn.inputs[m.slot].link = mL[0];
    }
    for (let i = 0; i < slotOutputs.length; ++i) {
        const m = slotOutputs[i];
        const mL = [lid++, OUT_ID, i, m.node_id, m.slot, m.type || "any"];
        innerLinks.push(mL);
        markerOut.inputs[i].link = mL[0];
        const sn = innerNodes.find(n => n.id === m.node_id);
        if (sn && sn.outputs && sn.outputs[m.slot]) {
            sn.outputs[m.slot].links = sn.outputs[m.slot].links || [];
            sn.outputs[m.slot].links.push(mL[0]);
        }
    }
    innerNodes.push(markerIn, markerOut);
    // счётчики ids: без них при входе в группу last_link_id=0 и новый провод
    // затёр бы связь маркера (graph.links[1] перезаписался бы)
    inner.last_link_id = lid - 1;
    inner.last_node_id = innerNodes.reduce((m, n) => Math.max(m, n.id > 0 ? n.id : 0), 0);

    for (const n of sel) graph.remove(n);

    const gn = LiteGraph.createNode("core/subgraph");
    state.groupCounter += 1;
    gn.properties = {
        title: t("group") + " " + state.groupCounter,
        inner: inner, widgets: [],
        inputs: slotInputs, outputs: slotOutputs,
    };
    for (const pi of slotInputs) gn.addInput(pi.name, pi.type);
    for (const po of slotOutputs) gn.addOutput(po.name, po.type);
    gn.title = gn.properties.title;
    gn.pos = [minX, minY];
    graph.add(gn);

    slotInputs.forEach((pi, idx) => {
        const src = graph.getNodeById(extInputs[idx].parentSrc[0]);
        if (src) src.connect(extInputs[idx].parentSrc[1], gn, idx);
    });
    slotOutputs.forEach((po, idx) => {
        const dst = graph.getNodeById(extOutputs[idx].parentDst[0]);
        if (dst) gn.connect(idx, dst, extOutputs[idx].parentDst[1]);
    });
    gn.setDirtyCanvas(true, true);
    graph.afterChange();
    scheduleSave();
    toast(t("group_created"));
}

function derefInner(props) {
    let inner = props.inner;
    if (typeof inner === "string") {
        try { inner = JSON.parse(inner || "{}"); } catch (e) { inner = {}; }
    }
    if (!inner || typeof inner !== "object") inner = {};
    if (!Array.isArray(inner.nodes)) inner.nodes = [];
    if (!Array.isArray(inner.links)) inner.links = [];
    return inner;
}

/* Габариты внутреннего графа (маркеры с id<0 не учитываем) */
function kfInnerBBox(inner) {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const n of (inner.nodes || [])) {
        if (n.id < 0) continue;
        const sz = n.size || [120, 60];
        minX = Math.min(minX, n.pos[0]); minY = Math.min(minY, n.pos[1]);
        maxX = Math.max(maxX, n.pos[0] + sz[0]); maxY = Math.max(maxY, n.pos[1] + sz[1]);
    }
    if (!isFinite(minX)) { minX = 200; minY = 200; maxX = 400; maxY = 300; }
    return [minX, minY, maxX, maxY];
}

function kfNextInnerLinkId(inner) {
    let max = 0;
    for (const l of (inner.links || [])) if (l && l[0] > max) max = l[0];
    return max + 1;
}

/* Санитайзер данных графа перед configure. Чинит три вещи, из-за которых
   внутри сабграфа появляются «фантомные» линии (первая↔последняя нода):
   1) дубликаты id связей — при configure обе пишутся в graph.links[id],
      выживает последняя, а ссылки нод обе указывают на неё → линия «не там»;
   2) протухшие ссылки нод (inputs[].link / outputs[].links) — пересобираем
      строго из массива links (он источник истины);
   3) счётчики last_node_id/last_link_id — без них первый же новый провод
      после clear() получает id 1 и ПЕРЕЗАПИСЫВАЕТ существующую связь. */
function kfSanitizeGraphData(data) {
    if (!data || !Array.isArray(data.nodes)) return data;
    if (!Array.isArray(data.links)) data.links = [];
    // миграция: «Превью» переехала в core_nodes (preview/show -> core/preview)
    for (const n of data.nodes) {
        if (n.type === "preview/show") n.type = "core/preview";
    }
    const byId = {};
    let maxNode = 0;
    for (const n of data.nodes) {
        byId[n.id] = n;
        if (n.id > maxNode) maxNode = n.id;
    }
    // 1) дубликаты/кривые id связей — каждому свой
    const used = new Set();
    let nextLid = 1;
    const good = [];
    for (const l of data.links) {
        if (!Array.isArray(l) || l.length < 5) continue;
        if (!Number.isFinite(l[0]) || l[0] < 1 || used.has(l[0])) l[0] = nextLid;
        used.add(l[0]);
        while (used.has(nextLid)) ++nextLid;
        if (l[0] > nextLid) nextLid = l[0];
        good.push(l);
    }
    data.links = good;
    // 2) пересборка ссылок нод строго из массива links
    for (const n of data.nodes) {
        for (const i of (n.inputs || [])) i.link = null;
        for (const o of (n.outputs || [])) o.links = [];
    }
    let maxLid = 0;
    for (const l of data.links) {
        if (l[0] > maxLid) maxLid = l[0];
        const on = byId[l[1]], tn = byId[l[3]];
        if (!on || !tn) continue;
        if (tn.inputs && tn.inputs[l[4]]) tn.inputs[l[4]].link = l[0];
        if (on.outputs && on.outputs[l[2]]) {
            const arr = on.outputs[l[2]].links = on.outputs[l[2]].links || [];
            if (!arr.includes(l[0])) arr.push(l[0]);
        }
    }
    // 3) счётчики обязаны покрывать всё существующее
    data.last_node_id = Math.max(data.last_node_id || 0, maxNode);
    data.last_link_id = Math.max(data.last_link_id || 0, maxLid);
    return data;
}

/* Гарантировать маркер-ноды «Входы»/«Выходы» внутри группы + линии к нодам.
   Старые группы (без маркеров) обновляются при первом входе. */
function kfEnsureMarkers(props) {
    const inner = derefInner(props);
    props.inner = inner;
    const inMaps = props.inputs || [];
    const outMaps = props.outputs || [];
    let inM = inner.nodes.find(n => n.id === -10);
    if (!inM) {
        const b = kfInnerBBox(inner);
        inM = { id: -10, type: "core/subgraph/input", title: "Входы",
                pos: [b[0] - 260, b[1]], size: [150, 60],
                properties: { side: "input" }, flags: {}, order: 0, mode: 0,
                inputs: [], outputs: [] };
        inner.nodes.push(inM);
    }
    let outM = inner.nodes.find(n => n.id === -100);
    if (!outM) {
        const b = kfInnerBBox(inner);
        outM = { id: -100, type: "core/subgraph/output", title: "Выходы",
                 pos: [b[2] + 80, b[1]], size: [150, 60],
                 properties: { side: "output" }, flags: {}, order: 0, mode: 0,
                 inputs: [], outputs: [] };
        inner.nodes.push(outM);
    }
    // слоты маркеров = зеркало маппингов (индекс слота = индекс пина группы)
    inM.outputs = inMaps.map((m, i) => {
        const prev = (inM.outputs || [])[i] || {};
        return { name: m.name, type: m.type || "any", links: prev.links || [] };
    });
    outM.inputs = outMaps.map((m, i) => {
        const prev = (outM.inputs || [])[i] || {};
        return { name: m.name, type: m.type || "any", link: prev.link !== undefined ? prev.link : null };
    });
    inM.size = [150, Math.max(80, 50 + Math.max(inM.outputs.length, 1) * 20)];
    outM.size = [150, Math.max(80, 50 + Math.max(outM.inputs.length, 1) * 20)];
    // линии маркер↔нода: создаём только отсутствующие
    const exists = (oid, os, tid, ts) => (inner.links || []).some(
        l => l && l[1] === oid && l[2] === os && l[3] === tid && l[4] === ts);
    let lid = kfNextInnerLinkId(inner);
    for (let i = 0; i < inMaps.length; ++i) {
        const m = inMaps[i];
        if (exists(-10, i, m.node_id, m.slot)) continue;
        const mL = [lid++, -10, i, m.node_id, m.slot, m.type || "any"];
        inner.links.push(mL);
        inM.outputs[i].links.push(mL[0]);
        const tn = inner.nodes.find(n => n.id === m.node_id);
        if (tn && tn.inputs && tn.inputs[m.slot]) tn.inputs[m.slot].link = mL[0];
    }
    for (let i = 0; i < outMaps.length; ++i) {
        const m = outMaps[i];
        if (exists(m.node_id, m.slot, -100, i)) continue;
        const mL = [lid++, m.node_id, m.slot, -100, i, m.type || "any"];
        inner.links.push(mL);
        outM.inputs[i].link = mL[0];
        const sn = inner.nodes.find(n => n.id === m.node_id);
        if (sn && sn.outputs && sn.outputs[m.slot]) {
            sn.outputs[m.slot].links = sn.outputs[m.slot].links || [];
            sn.outputs[m.slot].links.push(mL[0]);
        }
    }
    inner.last_link_id = Math.max(inner.last_link_id || 0, lid - 1);
    inner.last_node_id = (inner.nodes || []).reduce((mx, n) => Math.max(mx, n.id > 0 ? n.id : 0), inner.last_node_id || 0);
}

function applyPromotedWidgetsIntoLive(props) {
    for (const w of (props.widgets || [])) {
        const n = graph.getNodeById(w.node_id);
        if (n) {
            n.properties = n.properties || {};
            n.properties[w.name] = w.value;
            const wgt = (n.widgets || []).find(x => x.options && x.options.property === w.name);
            if (wgt) wgt.value = w.value;
        }
    }
}

function enterGroup(node) {
    if (node.type !== "core/subgraph") return;
    const parentJSON = graph.serialize();
    state.stack.push({ groupNode: node, groupId: node.id, parentJSON: parentJSON });
    const inner = derefInner(node.properties);
    kfEnsureMarkers(node.properties);   // старые группы получают маркеры при входе
    graph.clear();
    graph.configure(kfSanitizeGraphData(inner));
    applyPromotedWidgetsIntoLive(node.properties);
    updateBreadcrumbs();
    canvas.setDirty(true, true);
}

function exitGroup() {
    if (!state.stack.length) return;
    const top = state.stack[state.stack.length - 1];
    const innerSer = graph.serialize();
    const props = top.groupNode.properties;
    for (const w of (props.widgets || [])) {
        const n = (innerSer.nodes || []).find(x => x.id === w.node_id);
        if (n && n.properties && n.properties[w.name] !== undefined) w.value = n.properties[w.name];
    }
    props.inner = innerSer;
    snapshotGroupNodeInto(top.parentJSON, top.groupNode);
    state.stack.pop();
    graph.clear();
    graph.configure(kfSanitizeGraphData(top.parentJSON));
    updateBreadcrumbs();
    scheduleSave();
    canvas.setDirty(true, true);
}

/* Записать актуальное состояние группы (properties + слоты) в родительский JSON */
function snapshotGroupNodeInto(pj, gn) {
    const idx = (pj.nodes || []).findIndex(n => n.id === gn.id);
    if (idx < 0) return;
    const entry = pj.nodes[idx];
    pj.nodes[idx] = Object.assign({}, entry, {
        properties: JSON.parse(JSON.stringify(gn.properties)),
        inputs: gn.inputs ? JSON.parse(JSON.stringify(gn.inputs)) : entry.inputs,
        outputs: gn.outputs ? JSON.parse(JSON.stringify(gn.outputs)) : entry.outputs,
        size: gn.size ? gn.size.slice() : entry.size,
    });
}

function exitToLevel(level) {
    while (state.stack.length > level) exitGroup();
}

function updateBreadcrumbs() {
    const bar = $("#breadcrumbs");
    const wasHidden = bar.classList.contains("hidden");
    if (!state.stack.length) { bar.classList.add("hidden"); bar.innerHTML = ""; }
    else { bar.classList.remove("hidden"); bar.innerHTML = ""; }
    // появление/скрытие крошек меняет высоту canvas-wrap — буфер холста обязан
    // ресайзиться следом, иначе выделение «съезжает» (CSS px ≠ буфер px).
    // Синхронного вызова мало: flex-раскладка может досчитаться позже —
    // дублируем через rAF (resize с теми же размерами просто выйдет по early-return).
    if (canvas) {
        canvas.resize();
        requestAnimationFrame(() => { if (canvas) canvas.resize(); });
    }
    if (!state.stack.length) return;
    const mk = (label, level) => {
        const s = document.createElement("span");
        s.className = "crumb" + (level === state.stack.length ? " cur" : "");
        s.textContent = label;
        s.onclick = () => exitToLevel(level);
        bar.appendChild(s);
        if (level < state.stack.length) {
            const sep = document.createElement("span");
            sep.className = "crumb-sep"; sep.textContent = "›";
            bar.appendChild(sep);
        }
    };
    mk("🏠 " + t("root_crumb"), 0);
    state.stack.forEach((e, i) => mk(e.groupNode.properties.title || "группа", i + 1));
}

function renameGroup(node) {
    const name = prompt(t("rename") + ":", node.properties.title || "");
    if (name === null) return;
    node.properties.title = name || t("group");
    node.title = node.properties.title;
    node.setDirtyCanvas(true, true);
    updateBreadcrumbs();
    scheduleSave();
}

/* --- промоушен внутрь группы (как в ComfyUI subgraph) ------------- */

/* Пин-параметр ставим сразу под обычными входами (перед виджетами),
   как это делает раскладка layoutParamWidgets. Живая связь переносится. */
function kfMoveParamPinAfterInputs(node, inputIndex) {
    const inp = node.inputs[inputIndex];
    if (!inp) return;
    let lastInput = -1;
    for (let i = 0; i < node.inputs.length; ++i) {
        if (!node.inputs[i]) continue;
        const w = (node.widgets || []).find(x => x.options && x.options.property === node.inputs[i].name);
        if (!w) lastInput = i;
    }
    const target = lastInput + 1;
    if (lastInput < 0 || target >= inputIndex) return;   // уже стоит раньше виджетов
    let origin = null;
    if (inp.link != null && graph.links[inp.link]) {
        const link = graph.links[inp.link];
        origin = { id: link.origin_id, slot: link.origin_slot };
    }
    node.disconnectInput(inputIndex);        // чисто разорвёт связь (удалит из graph.links)
    node.inputs.splice(inputIndex, 1);
    node.inputs.splice(target, 0, inp);
    if (origin) {
        const src = graph.getNodeById(origin.id);
        if (src) src.connect(origin.slot, node, target);
    }
}

/* Вывести ПАРАМЕТР (поле виджета) на группу, полный вариант:
   1) у ноды появляется пин с именем параметра (значение с провода теперь
      подменяет параметр — и на группе, и внутри);
   2) на корпусе группы появляется редактируемое поле этого значения;
   3) на корпусе группы появляется точка (вход), к которой можно подключить
      значение снаружи — оно сильнее поля. */
function promoteWidget(node, p) {
    const top = state.stack[state.stack.length - 1];
    if (!top) return;
    const props = top.groupNode.properties;
    props.widgets = props.widgets || [];
    if (props.widgets.some(w => w.node_id === node.id && w.name === p.name)) {
        toast(t("already_promoted"), "error"); return;
    }
    // тип пина по типу параметра (для совместимости проводов)
    const pinType = p.type === "number" ? "number" : (p.type === "bool" ? "bool" : "any");
    // 1) пин-параметр на ноде (если такого входа ещё нет)
    let inputIndex = (node.inputs || []).findIndex(i => i.name === p.name);
    if (inputIndex < 0) {
        node.addInput(p.name, pinType);
        inputIndex = node.inputs.length - 1;
    }
    kfMoveParamPinAfterInputs(node, inputIndex);
    inputIndex = node.inputs.findIndex(i => i.name === p.name);   // индекс после сортировки
    // 2) запись + поле на корпусе группы (рисует syncPromotedWidgets)
    props.widgets.push({
        node_id: node.id, name: p.name, type: p.type,
        label: plabel(p), value: node.properties[p.name] ?? p.default,
    });
    // 3) точка на группе — как promoteInput (маппинг + маркер + линия)
    props.inputs = props.inputs || [];
    const gName = uniqueName(props.inputs.map(x => x.name), (node.title || "node") + "_" + p.name);
    kfEnsureMarkers(props);
    const gslot = props.inputs.length;
    top.groupNode.addInput(gName, "any");
    props.inputs.push({ name: gName, type: "any", node_id: node.id, slot: inputIndex });
    const inner = derefInner(props);
    const inM = inner.nodes.find(n => n.id === -10);
    if (inM) {
        const prev = (inM.outputs || [])[gslot] || {};
        inM.outputs[gslot] = { name: gName, type: "any", links: prev.links || [] };
        const mL = [kfNextInnerLinkId(inner), -10, gslot, node.id, inputIndex, "any"];
        inner.links.push(mL);
        inM.outputs[gslot].links.push(mL[0]);
        const tn = inner.nodes.find(n => n.id === node.id);
        if (tn && tn.inputs && tn.inputs[inputIndex]) tn.inputs[inputIndex].link = mL[0];
        const live = graph.getNodeById(-10);
        if (live) {
            if (!live.outputs[gslot]) live.outputs[gslot] = { name: gName, type: "any", links: [] };
            live.connect(gslot, node, inputIndex);
        }
    }
    top.groupNode.syncPromotedWidgets();
    applyPromotedWidgetsIntoLive(props);
    node.setDirtyCanvas(true, true);
    top.groupNode.setDirtyCanvas(true, true);
    toast(t("promoted") + plabel(p));
    scheduleSave();
}

function promoteInput(node, inputIndex) {
    const top = state.stack[state.stack.length - 1];
    if (!top) return;
    const inp = (node.inputs || [])[inputIndex];
    if (!inp) return;
    if (inp.link != null) { toast(t("input_connected"), "error"); return; }
    const props = top.groupNode.properties;
    props.inputs = props.inputs || [];
    const name = uniqueName(props.inputs.map(p => p.name), (node.title || "node") + "_" + inp.name);
    kfEnsureMarkers(props);
    const gslot = props.inputs.length;   // слот пина группы = индекс нового маппинга
    top.groupNode.addInput(name, inp.type || "any");
    props.inputs.push({ name: name, type: inp.type || "any", node_id: node.id, slot: inputIndex });
    // маркер + линия маркер→нода (визуальная) + живое соединение на холсте
    const inner = derefInner(props);
    const inM = inner.nodes.find(n => n.id === -10);
    if (inM) {
        const prev = (inM.outputs || [])[gslot] || {};
        inM.outputs[gslot] = { name: name, type: inp.type || "any", links: prev.links || [] };
        const mL = [kfNextInnerLinkId(inner), -10, gslot, node.id, inputIndex, inp.type || "any"];
        inner.links.push(mL);
        inM.outputs[gslot].links.push(mL[0]);
        const live = graph.getNodeById(-10);
        if (live) {
            // на живом маркере слот должен существовать ДО connect (иначе связь не создастся)
            if (!live.outputs[gslot]) {
                live.outputs[gslot] = { name: name, type: inp.type || "any", links: [] };
            }
            live.connect(gslot, node, inputIndex);
        }
    }
    top.groupNode.setDirtyCanvas(true, true);
    toast(t("promoted") + name);
    scheduleSave();
}

function promoteOutput(node, outputIndex) {
    const top = state.stack[state.stack.length - 1];
    if (!top) return;
    const out = (node.outputs || [])[outputIndex];
    if (!out) return;
    const props = top.groupNode.properties;
    props.outputs = props.outputs || [];
    const name = uniqueName(props.outputs.map(p => p.name), (node.title || "node") + "_" + out.name);
    kfEnsureMarkers(props);
    const gslot = props.outputs.length;
    top.groupNode.addOutput(name, out.type || "any");
    props.outputs.push({ name: name, type: out.type || "any", node_id: node.id, slot: outputIndex });
    const inner = derefInner(props);
    const outM = inner.nodes.find(n => n.id === -100);
    if (outM) {
        const prev = (outM.inputs || [])[gslot] || {};
        outM.inputs[gslot] = { name: name, type: out.type || "any", link: prev.link || null };
        const mL = [kfNextInnerLinkId(inner), node.id, outputIndex, -100, gslot, out.type || "any"];
        inner.links.push(mL);
        outM.inputs[gslot].link = mL[0];
        const live = graph.getNodeById(-100);
        if (live) {
            // слот на живом маркере — до connect
            if (!live.inputs[gslot]) {
                live.inputs[gslot] = { name: name, type: out.type || "any", link: null };
            }
            node.connect(outputIndex, live, gslot);
        }
    }
    top.groupNode.setDirtyCanvas(true, true);
    toast(t("promoted") + name);
    scheduleSave();
}

/* --- удаление точки входа/выхода группы (ПКМ по её пину) ------------
   Вырезаем слот i одновременно во всех местах, где он живёт:
   - props.inputs/outputs (маппинги движка) — источник истины;
   - живой маркер внутри группы (removeOutput/removeInput сами сдвигают
     origin_slot/target_slot у оставшихся живых связей);
   - пин на корпусе группы (inputs/outputs) + внешние провода родителя
     (в parentJSON: связь на удалённый слот срезается, старшие — сдвигаются);
   - inner JSON (срез связи маркера + сдвиг индексов старших связей). */
function kfRemoveGroupPoint(isInput, gslot) {
    const top = state.stack[state.stack.length - 1];
    if (!top) return;
    const props = top.groupNode.properties;
    const maps = isInput ? (props.inputs || []) : (props.outputs || []);
    if (gslot < 0 || gslot >= maps.length) return;
    const name = maps[gslot].name;

    // 1) живой маркер: litegraph сам разорвёт связь и пересчитает индексы слотов
    const live = graph.getNodeById(isInput ? -10 : -100);
    if (live) {
        if (isInput) live.removeOutput(gslot); else live.removeInput(gslot);
        live.size = [150, Math.max(80, 50 + Math.max((isInput ? live.outputs : live.inputs).length, 1) * 20)];
    }
    // 2) маппинги движка
    maps.splice(gslot, 1);
    // 3) пин на корпусе группы + внешние провода в снимке родителя
    const gn = top.groupNode;
    if (isInput) {
        if (gn.inputs && gslot < gn.inputs.length) gn.inputs.splice(gslot, 1);
    } else {
        if (gn.outputs && gslot < gn.outputs.length) gn.outputs.splice(gslot, 1);
    }
    const pj = top.parentJSON;
    const gid = top.groupId;
    if (pj && Array.isArray(pj.links)) {
        pj.links = pj.links.filter((l) => {
            if (!Array.isArray(l) || l.length < 5) return true;
            if (isInput) return !(l[3] === gid && l[4] === gslot);
            return !(l[1] === gid && l[2] === gslot);
        });
        for (const l of pj.links) {
            if (!Array.isArray(l) || l.length < 5) continue;
            if (isInput && l[3] === gid && l[4] > gslot) l[4] -= 1;
            if (!isInput && l[1] === gid && l[2] > gslot) l[2] -= 1;
        }
    }
    // 4) inner JSON: срез связи маркера + сдвиг старших индексов + пересборка ссылок
    const inner = derefInner(props);
    inner.links = (inner.links || []).filter((l) => {
        if (!Array.isArray(l) || l.length < 5) return true;
        if (isInput) return !(l[1] === -10 && l[2] === gslot);
        return !(l[3] === -100 && l[4] === gslot);
    });
    for (const l of inner.links) {
        if (!Array.isArray(l) || l.length < 5) continue;
        if (isInput && l[1] === -10 && l[2] > gslot) l[2] -= 1;
        if (!isInput && l[3] === -100 && l[4] > gslot) l[4] -= 1;
    }
    kfSanitizeGraphData(inner);   // пересоберёт inputs[].link/outputs[].links из массива
    kfEnsureMarkers(props);       // зеркальные слоты маркера + размеры заново

    canvas.draw(true, true);
    scheduleSave();
    toast(t("point_removed") + name);
}

function unpackGroup(node) {
    graph.beforeChange();
    const ser = graph.serialize();
    const props = node.properties;
    const inner = derefInner(props);
    const gid = node.id;
    const extIn = (ser.links || []).filter(l => l[3] === gid).map(l => ({ src: [l[1], l[2]], slot: l[4], type: l[5] }));
    const extOut = (ser.links || []).filter(l => l[1] === gid).map(l => ({ dst: [l[3], l[4]], slot: l[2], type: l[5] }));
    const inMaps = props.inputs || [];
    const outMaps = props.outputs || [];

    let maxId = (ser.nodes || []).reduce((m, n) => Math.max(m, n.id), 0);
    let maxLid = (ser.links || []).reduce((m, l) => Math.max(m, l[0]), 0);
    const idMap = {};
    for (const n of inner.nodes) {
        if (n.id < 0) continue;   // маркер-ноды сабграфа не распаковываем
        idMap[n.id] = ++maxId;
    }

    let minX = Infinity, minY = Infinity;
    for (const n of inner.nodes) {
        if (n.id < 0) continue;   // иначе офсет посчитается от маркера
        minX = Math.min(minX, n.pos[0]); minY = Math.min(minY, n.pos[1]);
    }
    const offX = node.pos[0] - (isFinite(minX) ? minX : 0);
    const offY = node.pos[1] - (isFinite(minY) ? minY : 0);

    const newNodes = ser.nodes.filter(n => n.id !== gid);
    for (const n of inner.nodes) {
        if (n.id < 0) continue;   // маркеры остаются в props.inner, на холст не выходят
        const c = JSON.parse(JSON.stringify(n));
        c.id = idMap[n.id];
        c.pos = [c.pos[0] + offX, c.pos[1] + offY];
        (c.inputs || []).forEach(i => { delete i.link; });
        (c.outputs || []).forEach(o => { delete o.links; });
        newNodes.push(c);
    }

    const newLinks = (ser.links || []).filter(l => l[1] !== gid && l[3] !== gid).map(l => l.slice());
    const addLink = (oid, os, tid, ts, type) => newLinks.push([++maxLid, oid, os, tid, ts, type || "any"]);

    // внешний провод -> внутренняя нода (по mapping'ам)
    for (const e of extIn) {
        const m = inMaps[e.slot];
        if (m && idMap[m.node_id] !== undefined) addLink(e.src[0], e.src[1], idMap[m.node_id], m.slot, e.type);
    }
    for (const e of extOut) {
        const m = outMaps[e.slot];
        if (m && idMap[m.node_id] !== undefined) addLink(idMap[m.node_id], m.slot, e.dst[0], e.dst[1], e.type);
    }
    for (const l of (inner.links || [])) {
        const noid = idMap[l[1]], ntid = idMap[l[3]];
        if (noid === undefined || ntid === undefined) continue;
        addLink(noid, l[2], ntid, l[4], l[5]);
    }

    graph.clear();
    graph.configure(kfSanitizeGraphData({ nodes: newNodes, links: newLinks, groups: ser.groups || [] }));
    graph.afterChange();
    scheduleSave();
    canvas.setDirty(true, true);
}

/* ------------------------------------------------------------------ */
/* Запись хоткеев (оверлей)                                           */
/* ------------------------------------------------------------------ */
function keyToken(e) {
    const k = e.key;
    if (k === "Control") return "ctrl";
    if (k === "Alt" || k === "AltGraph") return "alt";
    if (k === "Shift") return "shift";
    if (k === "Meta") return "win";
    if (k === " ") return "space";
    if (k === "Escape") return "esc";
    if (k === "Enter") return "enter";
    if (k === "Tab") return "tab";
    if (k === "Backspace") return "backspace";
    if (k === "Delete") return "delete";
    if (k === "Insert") return "insert";
    if (k === "Home") return "home";
    if (k === "End") return "end";
    if (k === "PageUp") return "pageup";
    if (k === "PageDown") return "pagedown";
    if (k === "ArrowUp") return "up";
    if (k === "ArrowDown") return "down";
    if (k === "ArrowLeft") return "left";
    if (k === "ArrowRight") return "right";
    if (k === "Dead") return null;
    if (/^F\d{1,2}$/.test(k)) return k.toLowerCase();
    if (k && k.length === 1) return k.toLowerCase();
    return null;
}

function canonicalCombo(order) {
    const mods = ["ctrl", "alt", "shift", "win"];
    const m = order.filter(x => mods.includes(x)).sort((a, b) => mods.indexOf(a) - mods.indexOf(b));
    const rest = order.filter(x => !mods.includes(x));
    return m.concat(rest).join("+");
}

function startCapture(node, propName) {
    endCapture(false);
    const cap = { node, propName, order: [], pressed: new Set() };
    state.capture = cap;
    $("#capture").classList.remove("hidden");
    $("#capture-combo").textContent = "—";
    // Основной режим — запись на СЕРВЕРЕ (глобальный слушатель pynput):
    // видит даже Alt+Tab и системные сочетания, которые браузер перехватывает.
    // Фолбэк — старая браузерная запись, если серверный режим недоступен.
    startServerCapture(cap);
}

/* Запись хоткея на сервере: старт -> опрос каждые 150 мс -> завершение,
   когда все клавиши отпущены и есть хотя бы одна. Esc на сервере сбрасывает.
   На время записи страница блокирует клавиатуру: фокус на canvas, blur у
   полей — иначе Ctrl+V вставляет текст/ноды прямо во время назначения. */
async function startServerCapture(cap) {
    try {
        const r = await apiPost("/api/hotkey_capture/start");
        if (!r || r.ok === false) throw new Error(r && r.message || "start fail");
    } catch (e) {
        startBrowserCapture(cap);   // сервер недоступен — фолбэк
        return;
    }
    cap.server = true;
    const active = document.activeElement;
    if (active && active !== document.body && active.blur) active.blur();
    try { canvas.focus(); } catch (e) { /* ignore */ }
    const finish = () => {
        if (state.capture !== cap) return;
        endServerCapture(true);
    };
    cap.timer = setInterval(async () => {
        if (state.capture !== cap) { clearInterval(cap.timer); return; }
        try {
            const st = await apiPost("/api/hotkey_capture/poll");
            if (!st || st.ok === false) { clearInterval(cap.timer); endServerCapture(false); return; }
            cap.order = st.tokens || [];
            $("#capture-combo").textContent = canonicalCombo(cap.order) || "—";
            if (cap.order.length && !st.held) finish();
        } catch (e) { /* сеть дёрнулась — попробуем на следующем тике */ }
    }, 150);
    // страница глушит клавиши на время записи (см. ниже), чтобы Ctrl+V
    // не вставлял ноды и текст в интерфейс мимо модалки
    cap.blockKeys = (e) => {
        e.preventDefault();
        e.stopPropagation();
    };
    window.addEventListener("keydown", cap.blockKeys, true);
    window.addEventListener("keyup", cap.blockKeys, true);
    // cancel-capability: если модалку закрыли (endCapture), таймер сам уберётся
}

async function endServerCapture(save) {
    const cap = state.capture;
    if (!cap) return;
    if (cap.timer) clearInterval(cap.timer);
    if (cap.blockKeys) {
        window.removeEventListener("keydown", cap.blockKeys, true);
        window.removeEventListener("keyup", cap.blockKeys, true);
    }
    state.capture = null;
    $("#capture").classList.add("hidden");
    try { await apiPost("/api/hotkey_capture/cancel"); } catch (e) { /* ignore */ }
    if (save && cap.order && cap.order.length) {
        const combo = canonicalCombo(cap.order);
        cap.node.properties[cap.propName] = combo;
        const wgt = (cap.node.widgets || []).find(w => w.options && w.options.property === cap.propName);
        if (wgt) wgt.value = combo;
        cap.node.setDirtyCanvas(true, true);
        scheduleSave();
    }
}

/* Фолбэк: старая браузерная запись (Tab/Alt недоступны — Windows перехватывает) */
function startBrowserCapture(node, propName) {
    // совместимость: startServerCapture зовёт с cap-объектом
    if (typeof node === "object" && node !== null && node.propName !== undefined) {
        const cap = node;
        node = cap.node; propName = cap.propName;
    }
    endCapture(false);
    const cap2 = { node, propName, order: [], pressed: new Set() };
    state.capture = cap2;
    $("#capture").classList.remove("hidden");
    $("#capture-combo").textContent = "—";
    cap2.onKeyDown = (e) => {
        e.preventDefault(); e.stopPropagation();
        const tok = keyToken(e);
        if (!tok) return;
        if (tok === "esc") { endCapture(false); return; }
        if (tok === "enter" && cap2.order.length) { endCapture(true); return; }
        if (!cap2.order.includes(tok)) { cap2.order.push(tok); cap2.pressed.add(tok); }
        $("#capture-combo").textContent = canonicalCombo(cap2.order) || "—";
    };
    cap2.onKeyUp = (e) => {
        e.preventDefault(); e.stopPropagation();
        const tok = keyToken(e);
        if (tok) cap2.pressed.delete(tok);
        if (cap2.order.length && cap2.pressed.size === 0) endCapture(true);
    };
    window.addEventListener("keydown", cap2.onKeyDown, true);
    window.addEventListener("keyup", cap2.onKeyUp, true);
}

function endCapture(save) {
    const cap = state.capture;
    if (!cap) return;
    state.capture = null;
    window.removeEventListener("keydown", cap.onKeyDown, true);
    window.removeEventListener("keyup", cap.onKeyUp, true);
    if (cap.server && cap.timer) clearInterval(cap.timer);
    if (cap.server && cap.blockKeys) {
        window.removeEventListener("keydown", cap.blockKeys, true);
        window.removeEventListener("keyup", cap.blockKeys, true);
    }
    $("#capture").classList.add("hidden");
    if (cap.server) {
        // серверная запись: снять режим на сервере (без сохранения — это отмена)
        apiPost("/api/hotkey_capture/cancel").catch(() => {});
    }
    if (save && cap.order.length) {
        const combo = canonicalCombo(cap.order);
        cap.node.properties[cap.propName] = combo;
        const wgt = (cap.node.widgets || []).find(w => w.options && w.options.property === cap.propName);
        if (wgt) wgt.value = combo;
        cap.node.setDirtyCanvas(true, true);
        scheduleSave();
    }
}

/* ------------------------------------------------------------------ */
/* Движок: старт/стоп/тест                                            */
/* ------------------------------------------------------------------ */
async function refreshEngineStatus() {
    try {
        const st = await apiGet("/api/engine/status");
        setEngineState(st.running);
    } catch (e) { /* ignore */ }
}

function setEngineState(running) {
    state.engineRunning = running;
    const pill = $("#engine-state");
    pill.classList.toggle("on", running);
    pill.classList.toggle("off", !running);
    pill.textContent = running ? t("engine_on") : t("engine_off");
    const btn = $("#btn-run");
    btn.classList.toggle("running", running);
    $("#btn-run-label").textContent = running ? t("stop") : t("run");
}

async function toggleEngine() {
    try {
        await saveNow(true);
        const r = state.engineRunning
            ? await apiPost("/api/engine/stop")
            : await apiPost("/api/engine/start", { name: state.current });
        if (r && r.ok === false) toast(r.message || "error", "error");
        else toast(state.engineRunning ? t("stop_ok") : t("start_ok"));
        await refreshEngineStatus();
    } catch (e) {
        toast(e.message, "error");
    }
}

async function testFire(node) {
    try {
        await saveNow(true);
        await apiPost("/api/engine/test_fire", { node_id: node.id });
        toast(t("test_sent"));
    } catch (e) {
        toast(e.message, "error");
    }
}

/* ------------------------------------------------------------------ */
/* SSE                                                                */
/* ------------------------------------------------------------------ */
function connectSSE() {
    const es = new EventSource("/api/events");
    es.onmessage = (ev) => {
        let data;
        try { data = JSON.parse(ev.data); } catch (e) { return; }
        switch (data.type) {
            case "log": addLog(data); break;
            case "node_status": {
                state.nodeStates[data.node_id] = { state: data.state, error: data.error };
                const n = graph.getNodeById(data.node_id);
                if (n) n.setDirtyCanvas(true, false);
                if (data.state === "error") toast("Нода #" + data.node_id + ": " + (data.error || "ошибка"), "error");
                break;
            }
            case "node_preview": {
                // текст для ноды «Превью»: обновили — перерисовали только её
                state.nodePreviews[data.node_id] = data.lines || [];
                const pn = graph.getNodeById(data.node_id);
                if (pn) pn.setDirtyCanvas(true, false);
                break;
            }
            case "engine": setEngineState(data.running); break;
            case "hello": refreshEngineStatus(); break;
        }
    };
    es.onerror = () => { /* авто-переподключение */ };
}

/* ------------------------------------------------------------------ */
/* Настройки / плагины                                                */
/* ------------------------------------------------------------------ */
function openSettings() {
    $("#dlg-settings").classList.remove("hidden");
    $("#set-language").value = state.settings.language;
    $("#set-theme").value = state.settings.theme;
    $("#set-links").value = state.settings.link_style;
    $("#set-snap").checked = !!state.settings.snap_grid;
    refreshPlugins();
}

async function persistSettings(patch) {
    Object.assign(state.settings, patch);
    try { await apiPost("/api/settings", patch); } catch (e) { /* offline? */ }
}

async function refreshPlugins() {
    const list = $("#plugins-list");
    list.innerHTML = "";
    let plugins = [];
    try { plugins = await apiGet("/api/plugins"); } catch (e) { return; }
    for (const p of plugins) {
        const row = document.createElement("div");
        row.className = "plugin-row";
        const badge = p.status === "loaded" ? '<span class="badge ok">' + t("status_ok") + '</span>'
            : p.status === "disabled" ? '<span class="badge off">' + t("status_disabled") + '</span>'
            : '<span class="badge err">' + t("status_error") + '</span>';
        row.innerHTML =
            '<div class="plugin-main"><div class="plugin-name">' + p.name + ' <span class="ver">v' + p.version + '</span> ' + badge + '</div>' +
            '<div class="plugin-desc">' + (p.description || "") + '</div>' +
            '<div class="plugin-nodes">' + (p.nodes || []).length + ' ' + t("nodes_count") + ': ' + (p.nodes || []).join(", ") + '</div>' +
            (p.error ? '<pre class="plugin-error">' + String(p.error).replace(/</g, "&lt;") + '</pre>' : "") + '</div>';
        const controls = document.createElement("div");
        controls.className = "plugin-ctl";
        const chk = document.createElement("input");
        chk.type = "checkbox"; chk.checked = p.enabled; chk.title = t("enable");
        chk.onchange = async () => {
            const r = await apiPost("/api/plugins/toggle", { id: p.id, enabled: chk.checked });
            if (r.plugins) toast(p.name + ": " + (chk.checked ? "ON" : "OFF"));
            refreshPlugins();
        };
        const btnR = document.createElement("button");
        btnR.className = "btn mini"; btnR.textContent = "⟳"; btnR.title = t("reload");
        btnR.onclick = async () => {
            await apiPost("/api/plugins/reload", { id: p.id });
            refreshPlugins();
        };
        const btnE = document.createElement("a");
        btnE.className = "btn mini"; btnE.textContent = "⤓"; btnE.title = t("plugin_export");
        btnE.href = "/api/plugins/export?id=" + encodeURIComponent(p.id);
        btnE.setAttribute("download", p.id + ".zip");
        btnE.style.textDecoration = "none";
        controls.appendChild(chk);
        controls.appendChild(btnR);
        controls.appendChild(btnE);
        row.appendChild(controls);
        list.appendChild(row);
    }
}

/* ------------------------------------------------------------------ */
/* Привязка UI                                                        */
/* ------------------------------------------------------------------ */
function bindUI() {
    $("#btn-run").onclick = toggleEngine;
    $("#btn-save").onclick = () => saveNow();
    $("#btn-logs").onclick = () => $("#log-drawer").classList.toggle("hidden");
    $("#btn-log-close").onclick = () => $("#log-drawer").classList.add("hidden");
    $("#btn-log-clear").onclick = () => { $("#log-lines").innerHTML = ""; toast(t("log_cleared")); };

    // скрыть/показать линии связей
    $("#btn-links").onclick = () => {
        circuit.hideLinks = !circuit.hideLinks;
        $("#btn-links").classList.toggle("active", circuit.hideLinks);
        $("#btn-links").textContent = circuit.hideLinks ? "🚫" : "🔗";
        canvas.setDirty(true, true);
    };
    // миникарта
    $("#btn-minimap").onclick = () => {
        const box = document.getElementById("minimap");
        box.classList.toggle("hidden");
        $("#btn-minimap").classList.toggle("active", !box.classList.contains("hidden"));
    };

    // экспорт текущего workflow в .json
    $("#btn-wf-export").onclick = async () => {
        try {
            const data = await apiGet("/api/workflow?name=" + encodeURIComponent(state.current));
            const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
            const a = document.createElement("a");
            a.href = URL.createObjectURL(blob);
            a.download = state.current + ".json";
            a.click();
            setTimeout(() => URL.revokeObjectURL(a.href), 5000);
        } catch (e) { toast("Export: " + e.message, "error"); }
    };
    // импорт workflow из .json
    const wfFile = $("#wf-file");
    $("#btn-wf-import").onclick = () => wfFile.click();
    wfFile.onchange = async () => {
        const f = wfFile.files && wfFile.files[0];
        wfFile.value = "";
        if (!f) return;
        try {
            const data = JSON.parse(await f.text());
            const gr = data.graph || data;
            if (!gr || !Array.isArray(gr.nodes)) { toast(t("wf_import_bad"), "error"); return; }
            const defName = (data.name || f.name || "imported").replace(/\.json$/i, "");
            const name = prompt(t("wf_new_name"), uniqueName(state.workflows, defName));
            if (!name) return;
            clearTimeout(state.autosaveTimer);
            await saveNow(true);
            await apiPost("/api/workflow/save", { name, graph: gr });
            await refreshWorkflows();
            await loadWorkflow(name);
            await persistSettings({ active_workflow: state.current });
            toast(t("wf_imported"), "success");
        } catch (e) { toast(t("wf_import_bad") + ": " + e.message, "error"); }
    };
    $("#btn-settings").onclick = openSettings;
    $("#btn-set-close").onclick = () => $("#dlg-settings").classList.add("hidden");

    $$(".tab").forEach(tab => tab.onclick = () => {
        $$(".tab").forEach(x => x.classList.remove("active"));
        tab.classList.add("active");
        $$(".tab-body").forEach(x => x.classList.add("hidden"));
        $("#tab-" + tab.dataset.tab).classList.remove("hidden");
    });

    $("#set-language").onchange = async (e) => {
        await saveNow(true);   // язык применяется перезагрузкой — фиксируем граф на диске
        await persistSettings({ language: e.target.value });
        location.reload();
    };
    $("#set-theme").onchange = async (e) => {
        await persistSettings({ theme: e.target.value });
        state.settings.theme = e.target.value;
        applyTheme();
    };
    $("#set-links").onchange = async (e) => {
        await persistSettings({ link_style: e.target.value });
        circuit.setEnabled(e.target.value === "circuit");
        canvas.setDirty(true, true);
    };
    $("#set-snap").onchange = async (e) => {
        await persistSettings({ snap_grid: e.target.checked });
        canvas.align_to_grid = e.target.checked;
        graph.config.align_to_grid = e.target.checked;
    };

    $("#btn-wf-new").onclick = async () => {
        const name = prompt(t("wf_new_name"), "workflow2");
        if (!name) return;
        clearTimeout(state.autosaveTimer);
        await saveNow(true);
        state.current = name;
        await apiPost("/api/workflow/save", { name, graph: { nodes: [], links: [], groups: [] } });
        await refreshWorkflows();
        await loadWorkflow(name);
        await persistSettings({ active_workflow: state.current });
    };
    $("#btn-wf-del").onclick = async () => {
        if (!confirm(t("wf_delete_q") + " «" + state.current + "»?")) return;
        await apiPost("/api/workflow/delete", { name: state.current });
        toast(t("wf_deleted"));
        await refreshWorkflows();
        await loadWorkflow(state.current);
        await persistSettings({ active_workflow: state.current });
    };

    $("#btn-sidebar").onclick = () => $("#sidebar").classList.toggle("collapsed");

    // импорт плагина (.zip)
    $("#btn-plug-import").onclick = () => $("#plug-file").click();
    $("#plug-file").onchange = async (e) => {
        const file = e.target.files[0];
        e.target.value = "";
        if (!file) return;
        const fd = new FormData();
        fd.append("file", file, file.name);
        try {
            const r = await fetch("/api/plugins/import", { method: "POST", body: fd });
            const res = await r.json();
            if (!res.ok) { toast(res.message || "Import failed", "error"); return; }
            toast(t("plugin_imported"), "success");
            setTimeout(() => location.reload(), 1200);
        } catch (err) {
            toast(err.message, "error");
        }
    };

    window.addEventListener("keydown", (e) => {
        if (state.capture) return; // оверлей сам перехватывает
        const ae = document.activeElement;
        const typing = ae && (ae.tagName === "INPUT" || ae.tagName === "TEXTAREA");
        if (e.ctrlKey && !e.shiftKey && e.key.toLowerCase() === "g") {
            e.preventDefault(); e.stopPropagation();
            groupSelectionFrame();
        } else if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === "g") {
            e.preventDefault(); e.stopPropagation();
            groupSelectedIntoNode();
        } else if (e.ctrlKey && !e.shiftKey && e.key.toLowerCase() === "s") {
            e.preventDefault(); e.stopPropagation();
            saveNow();
        } else if (e.ctrlKey && !e.shiftKey && !typing && e.key.toLowerCase() === "z") {
            e.preventDefault(); e.stopPropagation();
            kfDoUndo();
        } else if (e.ctrlKey && !typing &&
                   ((e.shiftKey && e.key.toLowerCase() === "z") || e.key.toLowerCase() === "y")) {
            e.preventDefault(); e.stopPropagation();
            kfDoRedo();
        } else if (e.key === "Escape") {
            if (!$("#dlg-settings").classList.contains("hidden")) $("#dlg-settings").classList.add("hidden");
            else if (state.stack.length) exitGroup();
        }
    });
}

/* ------------------------------------------------------------------ */
/* Boot                                                               */
/* ------------------------------------------------------------------ */
async function boot() {
    try { state.settings = await apiGet("/api/settings"); } catch (e) { /* defaults */ }
    state.current = state.settings.active_workflow || "main";
    applyLanguage();
    registerGroupNodes();
    enhanceSearchBox();
    enhancePrompt();
    await loadDefs();
    initCanvas();
    applyTheme();
    try {
        await refreshWorkflows();
        await loadWorkflow(state.current);
    } catch (e) {
        toast("Workflow load: " + e.message, "error");
    }
    connectSSE();
    await refreshEngineStatus();
    bindUI();
    setInterval(refreshEngineStatus, 10000);
}

boot();
