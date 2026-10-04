# -*- coding: utf-8 -*-
"""Workflow execution engine.

Модель исполнения — событийная (push):
  * триггер срабатывает -> движок создаёт EventContext и передаёт событие
    по связям вниз по графу;
  * каждый выход ноды может идти в несколько входов — каждая связь
    превращается в задачу пула; несколько проводов, пришедших в одну
    ноду в одной «волне», сливаются — нода исполнится один раз;
  * ГЛОБАЛЬНАЯ ОЧЕРЕДЬ: ноды исполняются строго по одной — следующая
    стартует только после завершения предыдущей (эмуляции клавиш/мыши
    не мешают друг другу); группа исполняется атомарно;
  * данные, лежащие на входах, запоминаются: нода, у которой несколько
    входов, всегда видит последние значения по всем подключённым входам;
  * каждая нода изолирована: исключение в одной ноде не ломает соседние
    ветви и сам движок.
"""
import json
import os
import sys
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

MAX_HOPS = 500          # защита от циклов в графе
MAX_WORKERS = 32        # параллельные ветви

SUBGRAPH = "core/subgraph"


class Cancelled(Exception):
    """Исполнение остановлено (Run -> Stop) во время работы ноды."""


# --------------------------------------------------------------------------
# Контексты
# --------------------------------------------------------------------------

class EventContext:
    """Одно срабатывание триггера: общие переменные ветви и счётчик переходов."""

    def __init__(self, engine, runtime):
        self.engine = engine
        self.runtime = runtime
        self.chain_id = uuid.uuid4().hex[:8]
        self.hops = 0
        self.vars_event = {}

    def log(self, msg, level="info"):
        self.engine.log(level, msg)


class ExecAPI:
    """Интерфейс, который движок передаёт функции exec каждой ноды."""

    def __init__(self, runtime, ctx, node):
        self.runtime = runtime
        self.ctx = ctx
        self.node = node

    # -- управление --------------------------------------------------------
    def check_stop(self):
        if self.runtime.stopped or self.ctx.runtime.stopped:
            raise Cancelled()

    def log(self, msg, level="info"):
        self.engine.log(level, msg)

    @property
    def engine(self):
        return self.ctx.engine

    @property
    def app_dir(self):
        return self.engine.app_dir

    # -- переменные --------------------------------------------------------
    def get_var(self, name, scope="event"):
        store = self.ctx.vars_event if scope == "event" else self.engine.global_vars
        return store.get(name)

    def get_var_any(self, name):
        """Сначала переменные текущей ветви, потом глобальные."""
        if name in self.ctx.vars_event:
            return self.ctx.vars_event[name]
        return self.engine.global_vars.get(name)

    def set_var(self, name, value, scope="event"):
        store = self.ctx.vars_event if scope == "event" else self.engine.global_vars
        store[name] = value

    # -- подавление собственных нажатий -------------------------------------
    def inject(self, sec=None):
        """with api.inject(): ... — синтетические нажатия не будят триггеры.

        sec игнорируется (окно динамическое): пока нода внутри with — молчим.
        """
        return self.engine.injection()

    def hotkeys_ping(self):
        """Ноде-эмулятору: продлить окно молчания (долгие операции)."""
        hk = self.engine.hotkeys
        if hk:
            hk.injection_ping()

    # -- постоянное состояние ноды (живёт, пока запущен движок) --------------
    def node_state(self, default=None):
        """dict ноды, живущий между срабатываниями (очищается при старте)."""
        key = "%s|%s" % (self.node.get("id"), self.node.get("type"))
        engine_states = self.engine.node_states
        st = engine_states.get(key)
        if st is None:
            st = default if isinstance(default, dict) else {}
            engine_states[key] = st
        return st

    def state_lock(self):
        return self.engine.node_states_lock


# --------------------------------------------------------------------------
# Рантайм одного графа
# --------------------------------------------------------------------------

class NodeRuntime:
    """Парсит сериализованный граф LiteGraph и гонит по нему события."""

    def __init__(self, engine, graph_data, parent=None):
        self.engine = engine
        self.graph = graph_data or {"nodes": [], "links": []}
        self.parent = parent
        self.nodes = {n.get("id"): n for n in self.graph.get("nodes", []) if n.get("id") is not None}
        self.links_by_origin = {}   # (node_id, out_slot) -> [(target_id, in_slot, in_name)]
        for l in self.graph.get("links") or []:
            if not (isinstance(l, (list, tuple)) and len(l) >= 5):
                continue
            _lid, oid, oslot, tid, tslot = l[:5]
            onode = self.nodes.get(oid)
            tnode = self.nodes.get(tid)
            if not onode or not tnode:
                continue
            outs = onode.get("outputs") or []
            ins = tnode.get("inputs") or []
            if oslot >= len(outs) or tslot >= len(ins):
                continue
            iname = ins[tslot].get("name")
            self.links_by_origin.setdefault((oid, oslot), []).append((tid, tslot, iname))
        self.inputs_latest = {}     # node_id -> {input_name: value}
        self.stopped = False
        self.collector = None       # для исполнения групп (core/subgraph)
        self.output_hooks = None    # (node_id, out_slot) -> индекс группового выхода
        self._pending = 0
        self._pending_lock = threading.Lock()
        self._idle = threading.Event()
        self._idle.set()
        self._sched_lock = threading.Lock()
        self._pending_runs = {}     # (chain_id, node_id) -> {"incoming": {...}, "busy": bool}

    # -- распространение события --------------------------------------------
    def emit(self, node_id, out_slot, value, ctx):
        """Разослать значение выхода по всем связям (каждая -> своя задача)."""
        hooks = getattr(self, "output_hooks", None)
        if hooks:
            gslot = hooks.get((node_id, out_slot))
            if gslot is not None and getattr(self, "collector", None) is not None:
                self.collector[gslot] = value
        for (tid, tslot, iname) in self.links_by_origin.get((node_id, out_slot), []):
            onode = self.nodes.get(node_id)
            self.schedule(tid, {iname: value},
                          ctx, (onode.get("title") or ("#" + str(node_id))) if onode else None)

    def schedule(self, node_id, incoming, ctx, src_title=None):
        """Запланировать исполнение ноды.

        Если нода получает несколько проводов одной «волны» (событие + данные),
        они СЛИВАЮТСЯ: нода исполнится один раз со всеми значениями. Разные
        цепочки (другой триггер) выстраиваются в общий порядок: одна нода за раз.
        """
        key = (ctx.chain_id, node_id)
        with self._sched_lock:
            pend = self._pending_runs.get(key)
            if pend is None:
                pend = {"incoming": {}, "busy": False, "__src": {}}
                self._pending_runs[key] = pend
            # None = событие без данных: важно само прибытие провода
            pend["incoming"].update(incoming)
            if src_title is not None:
                for k in incoming:
                    if k != "__src":
                        pend["__src"][k] = src_title
            if pend["busy"]:
                return
            pend["busy"] = True
            snapshot = pend["incoming"]
            snapshot["__src"] = dict(pend["__src"])
            pend["incoming"] = {}
            pend["__src"] = {}
        self._submit(node_id, snapshot, ctx, key)

    def _submit(self, node_id, snapshot, ctx, key):
        with self._pending_lock:
            self._pending += 1
            self._idle.clear()
        try:
            self.engine.executor.submit(self._run_safe, node_id, snapshot, ctx, key)
        except RuntimeError:
            # пул выключается (Stop) — ветвь просто не продолжается
            with self._sched_lock:
                self._pending_runs.pop(key, None)
            with self._pending_lock:
                self._pending -= 1
                if self._pending <= 0:
                    self._idle.set()

    def _run_safe(self, node_id, incoming, ctx, key):
        try:
            self.engine.run_node(self, node_id, incoming, ctx)
        except Cancelled:
            self.engine.node_status(node_id, "cancelled")
        except Exception as e:
            self.engine.log("error", "Нода #%s: %s" % (node_id, e))
            self.engine.log("error", traceback.format_exc(limit=4))
            self.engine.node_status(node_id, "error", str(e))
        finally:
            requeue = None
            with self._sched_lock:
                pend = self._pending_runs.get(key)
                if pend is not None and pend["incoming"]:
                    requeue = pend["incoming"]
                    src = dict(pend.get("__src") or {})
                    pend["incoming"] = {}
                    pend["__src"] = {}
                else:
                    self._pending_runs.pop(key, None)
            if requeue is not None:
                requeue["__src"] = src   # источники уходят вместе с перепостановкой
                self._submit(node_id, requeue, ctx, key)
            with self._pending_lock:
                self._pending -= 1
                if self._pending <= 0:
                    self._idle.set()


def build_params(node_def, node):
    """Собрать параметры ноды: defaults из определения + properties из UI."""
    props = node.get("properties") or {}
    out = {}
    for p in node_def.get("params", []):
        name = p["name"]
        ptype = p.get("type", "string")
        val = props.get(name, p.get("default"))
        try:
            if ptype == "number":
                out[name] = float(val)
            elif ptype == "bool":
                out[name] = bool(val)
            else:
                out[name] = "" if val is None else str(val)
        except (TypeError, ValueError):
            out[name] = p.get("default")
    return out


# --------------------------------------------------------------------------
# Движок
# --------------------------------------------------------------------------

class Engine:
    def __init__(self, app_dir, on_event=None):
        self.app_dir = app_dir
        self.on_event = on_event or (lambda evt: None)
        self.defs = {}                 # node_type -> def (из плагинов + встроенные)
        self.executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="kflow")
        self.node_gate = threading.Semaphore(1)  # глобальная очередь: одна нода за раз
        self.runtime = None            # NodeRuntime активного workflow
        self.running = False
        self.stopping = False
        self.workflow_name = None
        self.trigger_stoppers = []
        self.hotkeys = None            # HotkeyService (создаётся на Start)
        self._cap_hk = None            # временный слушатель записи хоткея
        self.global_vars = {}
        self.node_states = {}          # node_id -> произвольный dict (interval_toggle и т.п.)
        self.node_states_lock = threading.RLock()
        self._status_last = {}
        self._preview_last = {}       # node_id -> текст превью (дедупликация SSE)
        self._lock = threading.RLock()
        self._pkg_queue = []          # [(package, node_id)] — очередь автопостановки
        self._pkg_lock = threading.Lock()
        # встроенная нода-группа
        self.defs[SUBGRAPH] = {"id": SUBGRAPH, "title": "Группа", "params": []}

    # -- события/лог ---------------------------------------------------------
    def _emit(self, evt):
        try:
            self.on_event(evt)
        except Exception:
            pass

    def log(self, level, msg):
        line = "[%s] %s: %s" % (time.strftime("%H:%M:%S"), level.upper(), msg)
        try:
            print(line, flush=True)
        except Exception:
            pass
        self._emit({"type": "log", "level": level, "msg": msg,
                    "ts": time.strftime("%H:%M:%S")})

    def node_status(self, node_id, state, error=None):
        key = (node_id, state, error)
        if self._status_last.get(node_id) == key and state not in ("error",):
            return
        self._status_last[node_id] = key
        self._emit({"type": "node_status", "node_id": node_id,
                    "state": state, "error": error})

    def node_preview(self, node_id, lines):
        """Текст ноды «Превью»: разослать на веб-панель (SSE), без дубликатов."""
        key = tuple(lines or [])
        if self._preview_last.get(node_id) == key:
            return
        self._preview_last[node_id] = key
        self._emit({"type": "node_preview", "node_id": node_id, "lines": list(key)})

    @contextmanager
    def injection(self, sec=0.0):
        """Пока мы сами эмулируем нажатия — глобальные триггеры молчат.

        Окно молчания динамическое: пока нода внутри with-блока — молчим;
        после выхода окно тухнет само за <=0.5 с (хвост синтетики).
        """
        hk = self.hotkeys
        if hk:
            hk.injection_begin()
        try:
            yield
        finally:
            if hk:
                hk.injection_end()

    # -- исполнение ----------------------------------------------------------
    def fire(self, node_id, out_values=None):
        """Запустить цепочку от триггера (или из «Тест» в UI)."""
        with self._lock:
            rt = self.runtime
        if rt is None:
            return
        node = rt.nodes.get(node_id)
        if node is None:
            return
        ctx = EventContext(self, rt)
        out_values = out_values or {}
        self.node_status(node_id, "running")
        outs = node.get("outputs") or []
        # данные — РАНЬШЕ события: обе рассылки синхронные и сливаются в одну
        # волну, поэтому превью сразу показывает «path: …» вместе с срабатыванием
        data_outs = [(i, o) for i, o in enumerate(outs) if o.get("type") != "event"]
        event_outs = [(i, o) for i, o in enumerate(outs) if o.get("type") == "event"]
        for i, o in data_outs + event_outs:
            name = o.get("name")
            if name in out_values or not out_values:
                rt.emit(node_id, i, out_values.get(name), ctx)
        self.node_status(node_id, "ok")

    # -- глобальная очерёдность нод ------------------------------------------
    GATE_TIMEOUT = 120.0   # столько нода ждёт своей очереди, дальше — без неё

    def _gate_acquire(self, node_id):
        """Взять «проход»: в системе работает ОДНА нода одновременно.

        Страховка от вечного зависания: если проход не освободился за
        GATE_TIMEOUT секунд — нода идёт дальше без прохода (с предупреждением).
        """
        ok = self.node_gate.acquire(timeout=self.GATE_TIMEOUT)
        if not ok:
            self.log("warn", "Нода #%s ждала очереди %d с — предыдущая, видимо, зависла; продолжаю без очереди."
                     % (node_id, int(self.GATE_TIMEOUT)))
            return False
        return True

    def _gate_release(self, node_id):
        try:
            self.node_gate.release()
        except Exception:
            pass

    def run_node(self, rt, node_id, incoming, ctx):
        node = rt.nodes.get(node_id)
        if node is None or node.get("mode") == 2:      # mode 2 = muted
            return
        if ctx.hops >= MAX_HOPS:
            self.log("error", "Цепочка остановлена: достигнут лимит %d переходов (возможен цикл)." % MAX_HOPS)
            return
        ctx.hops += 1
        if rt.stopped:
            raise Cancelled()

        latest = rt.inputs_latest.setdefault(node_id, {})
        wave = {}
        src = incoming.get("__src") or {}   # имя ноды-источника для каждого провода
        for k, v in incoming.items():
            if k == "__src":
                continue
            wave[k] = v
            if v is not None:
                latest[k] = v
        # имена источников живут вместе со значениями: константа выдаёт данные
        # один раз при старте, а событие приходит отдельной волной позже
        if src:
            latest.setdefault("__src", {}).update(src)
        inputs = dict(latest)
        inputs.update(wave)  # в wave важен и None (событие без данных)
        if src:
            inputs["__src"] = dict(latest.get("__src") or {})

        ntype = node.get("type")
        if ntype in ("core/subgraph/input", "core/subgraph/output"):
            return  # ноды-маркеры сабграфа исполнения не требуют
        self.node_status(node_id, "running")

        def emit_by_name(out_name, value):
            for i, o in enumerate(node.get("outputs") or []):
                if o.get("name") == out_name:
                    rt.emit(node_id, i, value, ctx)
                    return

        # Глобальная очередь: следующая нода стартует после завершения
        # предыдущей. Ноды ВНУТРИ группы проход не берут — группа держит
        # его целиком (иначе дедлок: группа ждёт их, они ждут группу).
        held = False
        if not getattr(ctx, "_gate_bypass", False):
            held = self._gate_acquire(node_id)
        try:
            if ntype == SUBGRAPH:
                ctx._gate_bypass = True
                try:
                    self._exec_subgraph(rt, ctx, node, inputs, emit_by_name)
                finally:
                    ctx._gate_bypass = False
            else:
                d = self.defs.get(ntype)
                if d is None:
                    self.node_status(node_id, "error",
                                     "Неизвестный тип ноды «%s» (плагин выключен?)" % ntype)
                    return
                params = build_params(d, node)
                # значение с провода подменяет одноимённый параметр
                # (вход, выведенный из поля виджета на группу, см. run_node)
                for k, v in inputs.items():
                    if v is not None and k in params:
                        params[k] = v
                d["exec"](ExecAPI(rt, ctx, node), params, inputs, emit_by_name)
        finally:
            if held:
                self._gate_release(node_id)
        self.node_status(node_id, "ok")

    def _exec_subgraph(self, rt, ctx, node, inputs, emit):
        """Исполнение группы (как в ComfyUI subgraph).

        properties.inputs  = [{name,type,node_id,slot}, ...] — промоутед входы
        properties.outputs = [{name,type,node_id,slot}, ...] — промоутед выходы
        Значение с внешнего входа подаётся прямо на провод внутренней ноды;
        срабатывание промоутед выхода перехватывается хуком emit().
        """
        props = node.get("properties") or {}
        inner_data = props.get("inner") or {}
        if isinstance(inner_data, str):
            try:
                inner_data = json.loads(inner_data)
            except Exception:
                raise ValueError("Внутри группы некорректный JSON")
        inner = NodeRuntime(self, inner_data, parent=rt)
        collector = {}
        inner.collector = collector

        # значения выведенных полей (properties.widgets) пишем в свойства
        # внутренних нод — build_params возьмёт их как обычные параметры
        # (если к тому же входу подключён провод, провод сильнее: см. run_node)
        for w in props.get("widgets") or []:
            try:
                wn = inner.nodes.get(int(w.get("node_id")))
            except (TypeError, ValueError):
                wn = None
            name = w.get("name")
            if wn is not None and name:
                pr = wn.get("properties")
                if pr is None:
                    pr = wn["properties"] = {}
                pr[name] = w.get("value")

        out_maps = props.get("outputs") or []
        hooks = {}
        for gslot, m in enumerate(out_maps):
            try:
                hooks[(int(m.get("node_id")), int(m.get("slot", 0)))] = gslot
            except (TypeError, ValueError):
                pass
        inner.output_hooks = hooks

        in_maps = props.get("inputs") or []
        for gslot, m in enumerate(in_maps):
            name = m.get("name")
            if gslot < len(node.get("inputs") or []):
                name = (node.get("inputs") or [{}])[gslot].get("name") or name
            if name not in inputs:
                continue
            try:
                in_node = inner.nodes.get(int(m.get("node_id")))
            except (TypeError, ValueError):
                in_node = None
            if in_node is None:
                continue
            slot = int(m.get("slot", 0))
            slots = in_node.get("inputs") or []
            if slot >= len(slots):
                continue
            iname = slots[slot].get("name")
            if iname:
                inner.schedule(int(m.get("node_id")), {iname: inputs[name]}, ctx)

        waited = 0
        while not inner._idle.wait(0.05):
            if rt.stopped or ctx.runtime.stopped:
                raise Cancelled()
            waited += 1
            if waited > 6000:      # ~5 минут на группу, защита от зависания
                self.log("error", "Группа «%s» не завершилась за 5 минут." % props.get("title", node_id))
                break

        for i, gout in enumerate(node.get("outputs") or []):
            name = gout.get("name")
            if i in collector:
                emit(name, collector[i])

    # -- старт/стоп ----------------------------------------------------------
    def start(self, workflow_name, graph_data):
        with self._lock:
            if self.running:
                return False, "Уже запущено"
            self.runtime = NodeRuntime(self, graph_data)
            self.workflow_name = workflow_name
            self.global_vars = {}
            with self.node_states_lock:
                self.node_states = {}
            self._status_last = {}
            self._preview_last = {}
            self.stopping = False
            self.running = True
            self.node_gate = threading.Semaphore(1)   # свежая очередь на запуск
            from core.hotkeys import HotkeyService
            self.hotkeys = HotkeyService(self)
            try:
                self.hotkeys.start()
            except Exception as e:
                self.log("error", "Не удалось запустить глобальные слушатели: %s" % e)
            started = 0
            # константы (number/text): один раз выдать данные в провода
            for node in self.runtime.nodes.values():
                if node.get("mode") == 2:
                    continue
                d = self.defs.get(node.get("type"))
                if d and d.get("constant"):
                    self.executor.submit(self._run_constant, node.get("id"))
            for node in self.runtime.nodes.values():
                if node.get("mode") == 2:
                    continue
                d = self.defs.get(node.get("type"))
                if not d or not d.get("trigger") or not d.get("start"):
                    continue
                # ноды с skip_start_if_wired (интервал) не стартуют сами, если в цепочке
                if d.get("skip_start_if_wired"):
                    ins = node.get("inputs") or []
                    if any((i or {}).get("link") is not None for i in ins):
                        self.log("info", "Нода #%s «%s» в цепочке — самозапуск пропущен"
                                 % (node.get("id"), d.get("title", {}).get("ru", "")))
                        continue
                handle = TriggerHandle(self, node.get("id"))
                self._stop_node_triggers(node.get("id"))   # не оставлять дублей
                try:
                    stopper = d["start"](handle, node)
                    self.trigger_stoppers.append((node.get("id"), stopper))
                    started += 1
                except Exception as e:
                    self.node_status(node.get("id"), "error", str(e))
                    self.log("error", "Триггер #%s не запущен: %s" % (node.get("id"), e))
                    # готовая команда для копирования, если авто-установка не смогла
                    if type(e).__name__ == "NeedPackagesError" and getattr(e, "packages", None):
                        tgt = self._plugin_packages_dir(node.get("type")) or \
                            os.path.join(self.app_dir, "_packages")
                        self.log("info", "Автоустановка не удалась? Скопируйте в консоль (cmd/PowerShell):\r\n"
                                 "%s -m pip install --upgrade --target \"%s\" %s"
                                 % (sys.executable, tgt, " ".join(e.packages)))
                    # автопостановка недостающего пакета в очередь установщика
                    if type(e).__name__ == "NeedPackagesError":
                        self._queue_packages(e.packages, node.get("id"), node.get("type"))
            self.log("info", "▶ Workflow «%s» запущен. Активных триггеров: %d" % (workflow_name, started))
            self._emit({"type": "engine", "running": True, "workflow": workflow_name})
            self._install_queued_packages()   # ставим недостающее и перезапускаем ноды
            return True, "ok"

    def request_packages(self, packages, node_id=None, node_type=None):
        """Публичный API для плагинов: поставить пакеты и перезапустить ноду."""
        self._queue_packages(packages, node_id, node_type)
        self._install_queued_packages()

    def _plugin_packages_dir(self, node_type):
        """Папка зависимостей плагина, которому принадлежит нода (или None)."""
        d = self.defs.get(node_type) if node_type else None
        pid = d.get("plugin") if d else None
        p = self.pm.plugins.get(pid) if getattr(self, "pm", None) else None
        if not p:
            return None
        pd = os.path.join(p.path, "_packages")
        try:
            os.makedirs(pd, exist_ok=True)
        except Exception:
            return None
        return pd

    def _queue_packages(self, packages, node_id=None, node_type=None):
        """Недостающие пакеты -> очередь установщика (без дублей)."""
        with self._pkg_lock:
            for p in packages:
                key = (str(p).strip(), node_type, node_id)
                if key not in self._pkg_queue:
                    self._pkg_queue.append(key)

    def _install_queued_packages(self):
        """Фоном: pip install --target в папку _packages плагина-владельца
        ноды + перезапуск нод, ждавших пакеты."""
        if not self._pkg_queue:
            return
        with self._pkg_lock:
            jobs = list(self._pkg_queue)
            self._pkg_queue = []
        by_target = {}
        for pkg, node_type, node_id in jobs:
            tgt = self._plugin_packages_dir(node_type) or os.path.join(self.app_dir, "_packages")
            info = by_target.setdefault(tgt, {"packages": set(), "node_ids": set()})
            info["packages"].add(pkg)
            if node_id is not None:
                info["node_ids"].add(node_id)
        node_ids = sorted({nid for v in by_target.values() for nid in v["node_ids"]})

        def worker():
            all_pkgs = sorted({p for v in by_target.values() for p in v["packages"]})
            self.log("info", "Устанавливаю недостающие пакеты: %s (это может занять минуту)..."
                     % ", ".join(all_pkgs))
            failed_nodes = set()
            for tgt, info in by_target.items():
                pkgs = sorted(info["packages"])
                where = "общее окружение" if tgt == os.path.join(self.app_dir, "_packages") \
                    else os.path.relpath(tgt, self.app_dir)
                try:
                    import subprocess
                    # ВАЖНО: --upgrade обязателен. pip с --target видит уже
                    # существующий dist-info и молча пропускает установку,
                    # даже если файлов нет (полу-установленный пакет).
                    # Вывод НЕ перехватываем: pip пишет прогресс-бары прямо
                    # в консоль сервера.
                    self.log("info", "pip → %s (вывод с прогрессом ниже):" % where)
                    env = dict(os.environ, PYTHONUNBUFFERED="1")
                    r = subprocess.run(
                        [sys.executable, "-m", "pip", "install",
                         "--disable-pip-version-check", "--upgrade",
                         "--progress-bar", "on",
                         "--target", tgt, *pkgs],
                        timeout=600, cwd=self.app_dir, env=env)
                    ok = (r.returncode == 0)
                    if ok and tgt not in sys.path:
                        sys.path.insert(0, tgt)
                    if not ok:
                        # ФАЙЛЫ ЗАНЯТЫ (WinError 5): сервер держит DLL в
                        # памяти, pip не может заменить их в _packages.
                        # Решение — ставим в ЧИСТУЮ соседнюю папку
                        # _packages_2 и поднимаем её приоритет: свежие
                        # файлы используются, заблокированные не трогаем.
                        self.log("info",
                                 "pip не смог обновить %s (вероятно, файлы "
                                 "заняты работающим сервером) — ставлю в "
                                 "чистую папку _packages_2..." % where)
                        tgt2 = tgt + "_2"
                        try:
                            env2 = dict(os.environ, PYTHONUNBUFFERED="1")
                            r2 = subprocess.run(
                                [sys.executable, "-m", "pip", "install",
                                 "--disable-pip-version-check",
                                 "--progress-bar", "on",
                                 "--target", tgt2, *pkgs],
                                timeout=600, cwd=self.app_dir, env=env2)
                            ok = (r2.returncode == 0)
                            if ok:
                                if tgt2 not in sys.path:
                                    sys.path.insert(0, tgt2)
                                self.log("info",
                                         "Пакеты установлены в %s — "
                                         "используется она." %
                                         os.path.relpath(tgt2, self.app_dir))
                        except Exception as e2:
                            self.log("error", "pip (_packages_2): %s" % e2)
                        if not ok:
                            self.log("error",
                                     "pip не смог установить (%s) — "
                                     "подробности в выводе выше." % where)
                            failed_nodes.update(info["node_ids"])
                    else:
                        self.log("info", "pip (%s): готово." % where)
                except Exception as e:
                    self.log("error", "pip (%s): %s" % (where, e))
                    failed_nodes.update(info["node_ids"])

            done_ids = [nid for nid in node_ids if nid not in failed_nodes]
            if not done_ids:
                return

            def retry_nodes():
                if not self.running:
                    return
                self.log("info", "Пакеты установлены — перезапускаю ноды: %s"
                         % ", ".join("#%s" % n for n in done_ids))
                for nid in done_ids:
                    if not self.running:
                        return
                    with self._lock:
                        rt = self.runtime
                    if rt is None:
                        return
                    node = rt.nodes.get(nid)
                    if node is None:
                        continue
                    d = self.defs.get(node.get("type"))
                    if not d or not d.get("trigger") or not d.get("start"):
                        continue
                    handle = TriggerHandle(self, nid)
                    self._stop_node_triggers(nid)   # не оставлять дублей
                    try:
                        stopper = d["start"](handle, node)
                        self.trigger_stoppers.append((nid, stopper))
                        self.node_status(nid, "ok")
                        self.log("info", "Нода #%s запущена после установки пакетов" % nid)
                    except Exception as e:
                        self.node_status(nid, "error", str(e))
                        self.log("error", "Нода #%s снова не запустилась: %s" % (nid, e))

            self.executor.submit(retry_nodes)

        threading.Thread(target=worker, daemon=True).start()

    def _run_constant(self, node_id):
        """Разовый тихий запуск ноды-константы: события не рассылаются, только данные."""
        with self._lock:
            rt = self.runtime
        if rt is None or rt.stopped:
            return
        node = rt.nodes.get(node_id)
        if node is None:
            return
        d = self.defs.get(node.get("type"))
        if not d or not d.get("exec"):
            return
        ctx = EventContext(self, rt)
        try:
            self.node_status(node_id, "running")
            params = build_params(d, node)
            outs = node.get("outputs") or []

            def quiet_emit(name, value):
                for i, o in enumerate(outs):
                    if o.get("name") == name and o.get("type") != "event":
                        rt.emit(node_id, i, value, ctx)
                        return

            d["exec"](ExecAPI(rt, ctx, node), params, {}, quiet_emit)
            self.node_status(node_id, "ok")
        except Cancelled:
            pass
        except Exception as e:
            self.node_status(node_id, "error", str(e))
            self.log("error", "Константа #%s: %s" % (node_id, e))

    def _stop_node_triggers(self, node_id):
        """Остановить все старые слушатели ноды перед её перезапуском —
        иначе после авто-рестарта нода срабатывает дважды."""
        with self._lock:
            keep, stopped = [], []
            for nid, stopper in self.trigger_stoppers:
                if nid == node_id:
                    stopped.append(stopper)
                else:
                    keep.append((nid, stopper))
            self.trigger_stoppers = keep
        for stopper in stopped:
            try:
                stopper()
            except Exception:
                pass

    def _stop_all_triggers(self):
        for _nid, stopper in self.trigger_stoppers:
            try:
                stopper()
            except Exception:
                pass
        self.trigger_stoppers = []

    def stop(self):
        with self._lock:
            if not self.running:
                return False, "Не запущено"
            self.stopping = True
            if self.runtime:
                self.runtime.stopped = True
            self._stop_all_triggers()
            if self.hotkeys:
                try:
                    self.hotkeys.stop()
                except Exception:
                    pass
                self.hotkeys = None
            try:
                self.executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
            self.executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="kflow")
            self.runtime = None
            self.running = False
            self.stopping = False
            self.log("info", "■ Workflow остановлен.")
            self._emit({"type": "engine", "running": False})
            return True, "ok"

    def status(self):
        return {"running": self.running, "workflow": self.workflow_name}

    def ensure_runtime(self, workflow_name, graph_data):
        """Рантайм для «Тест»-запуска триггера без общего Start."""
        with self._lock:
            if self.runtime is not None:
                return self.runtime
            self.runtime = NodeRuntime(self, graph_data)
            self.workflow_name = workflow_name
            return self.runtime

    def dry_stop(self):
        """Убрать dry-рантайм, если движок так и не был запущен."""
        with self._lock:
            if not self.running and self.runtime is not None:
                self.runtime.stopped = True
                self.runtime = None
                self.workflow_name = None


class TriggerHandle:
    """То, что плагин-триггер получает на старте: как выстрелить событием."""

    def __init__(self, engine, node_id):
        self.engine = engine
        self.node_id = node_id

    def fire(self, value=None):
        self.engine.fire(self.node_id, {"out": value})
