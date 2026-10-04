# -*- coding: utf-8 -*-
"""Прямой тест движка: константы, новые ноды int/pos_decode/переменные,
скриншот с входом path и интервал-источник."""
import json
import os
import sys
import time
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.engine import Engine   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

fails = []
events = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ((" | " + str(extra)) if extra and not cond else ""))
    if not cond:
        fails.append(name)


engine = Engine(ROOT, lambda e: events.append(e))

from core import plugin_manager as pm   # noqa: E402
pmgr = pm.PluginManager(os.path.join(ROOT, "plugins"))
pmgr.discover()
engine.defs.update(pmgr.collect_defs())

tmpdir = tempfile.mkdtemp(prefix="kf_shots_")

# Схема:
#   startup -> screenshot(x=111 из константы, path из текстовой константы) -> log("SHOT")
#   text("123.4,56") -> pos_decode -> set_variable("P") ; startup -> get_variable("P")
#   -> log(message из value)
#   interval(0.2) -> log("TICK")  (источник, самозапуск)
graph = {
    "last_node_id": 13, "last_link_id": 12,
    "nodes": [
        {"id": 1, "type": "core/startup", "pos": [0, 0], "mode": 0,
         "inputs": [], "outputs": [{"name": "out", "type": "event"}], "properties": {}},
        {"id": 2, "type": "core/number", "pos": [0, 100], "mode": 0,
         "inputs": [{"name": "value", "type": "number"}],
         "outputs": [{"name": "int", "type": "number"}],
         "properties": {"value": 111}},
        {"id": 3, "type": "core/text", "pos": [0, 200], "mode": 0,
         "inputs": [{"name": "text", "type": "string"}],
         "outputs": [{"name": "text", "type": "string"}],
         "properties": {"text": tmpdir}},
        {"id": 4, "type": "core/screenshot", "pos": [250, 0], "mode": 0,
         "inputs": [{"name": "in", "type": "event"}, {"name": "x", "type": "number"},
                    {"name": "path", "type": "string"}],
         "outputs": [{"name": "out", "type": "event"}],
         "properties": {"region": "area", "x": 0, "y": 0, "w": 40, "h": 40}},
        {"id": 5, "type": "core/log", "pos": [500, 0], "mode": 0,
         "inputs": [{"name": "in", "type": "event"}, {"name": "message", "type": "any"}],
         "outputs": [{"name": "out", "type": "event"}],
         "properties": {"message": "not-used"}},
        {"id": 6, "type": "core/text", "pos": [0, 300], "mode": 0,
         "inputs": [{"name": "text", "type": "string"}],
         "outputs": [{"name": "text", "type": "string"}],
         "properties": {"text": "123.4,56"}},
        {"id": 7, "type": "core/pos_decode", "pos": [250, 300], "mode": 0,
         "inputs": [{"name": "position", "type": "pos"}],
         "outputs": [{"name": "x", "type": "number"}, {"name": "y", "type": "number"}],
         "properties": {}},
        {"id": 8, "type": "core/set_variable", "pos": [500, 300], "mode": 0,
         "inputs": [{"name": "in", "type": "event"}, {"name": "value", "type": "any"}],
         "outputs": [{"name": "out", "type": "event"}, {"name": "value", "type": "any"}],
         "properties": {"name": "P"}},
        {"id": 13, "type": "core/wait", "pos": [250, 420], "mode": 0,
         "inputs": [{"name": "in", "type": "event"}],
         "outputs": [{"name": "out", "type": "event"}],
         "properties": {"seconds": 0.4}},
        {"id": 9, "type": "core/get_variable", "pos": [750, 300], "mode": 0,
         "inputs": [{"name": "in", "type": "event"}],
         "outputs": [{"name": "out", "type": "event"}, {"name": "value", "type": "any"}],
         "properties": {"name": "P"}},
        {"id": 10, "type": "core/log", "pos": [1000, 300], "mode": 0,
         "inputs": [{"name": "in", "type": "event"}, {"name": "message", "type": "any"}],
         "outputs": [{"name": "out", "type": "event"}],
         "properties": {"message": "not-used2"}},
        {"id": 11, "type": "core/interval", "pos": [0, 420], "mode": 0,
         "inputs": [],
         "outputs": [{"name": "out", "type": "event"}],
         "properties": {"seconds": 0.2}},
        {"id": 12, "type": "core/log", "pos": [250, 420], "mode": 0,
         "inputs": [{"name": "in", "type": "event"}],
         "outputs": [{"name": "out", "type": "event"}],
         "properties": {"message": "TICK"}},
    ],
    "links": [
        [1, 1, 0, 4, 0, "event"],   # startup -> screenshot.in
        [2, 2, 0, 4, 1, "number"],  # int(111) -> screenshot.x
        [3, 3, 0, 4, 2, "string"],  # text(папка) -> screenshot.path
        [4, 4, 0, 5, 0, "event"],   # screenshot.out -> log SHOT
        [5, 6, 0, 7, 0, "string"],  # text("123.4,56") -> pos_decode.position
        [6, 1, 0, 13, 0, "event"],  # startup -> wait(0.4) — даём константам догнать
        [11, 13, 0, 8, 0, "event"], # wait -> set_variable.in
        [7, 7, 0, 8, 1, "number"],  # pos_decode.x -> set_variable.value
        [8, 8, 0, 9, 0, "event"],   # set_variable.out -> get_variable.in
        [9, 9, 1, 10, 1, "any"],    # get_variable.value -> log.message
        [10, 9, 0, 10, 0, "event"], # get_variable.out -> log.in
        [11, 11, 0, 12, 0, "event"],# interval -> log TICK
    ],
}

ok, msg = engine.start("__pythontest_const", graph)
check("start", ok)

deadline = time.time() + 6
while time.time() < deadline:
    logs = [e.get("msg", "") for e in events if e.get("type") == "log"]
    shots = [m for m in logs if m.startswith("Скриншот сохранён")]
    ticks = [m for m in logs if m == "TICK"]
    varlogs = [m for m in logs if "123.4" in m]
    if shots and len(ticks) >= 2 and varlogs:
        break
    time.sleep(0.1)

logs = [e.get("msg", "") for e in events if e.get("type") == "log"]
shots = [m for m in logs if m.startswith("Скриншот сохранён")]
check("скриншот сделан", len(shots) >= 1, logs)
check("скриншот сохранён по входу path (в tmpdir)",
      bool(shots) and tmpdir in shots[0], shots[:1])
check("int-константа -> screenshot.x = 111",
      bool(shots) and "00000_000111" not in shots[0], shots[:1])

varlogs = [m for m in logs if "123.4" in m]
check("pos_decode -> set_variable -> get_variable -> log(value)",
      len(varlogs) >= 1, logs)
exact = [m for m in logs if m.strip() == "123.4"]
check("get_variable.out продолжает цепочку (log-нода сработала)",
      len(exact) >= 1, logs)

ticks = [m for m in logs if m == "TICK"]
check("интервал-источник самозапускается (>=2 тика)", len(ticks) >= 2, len(ticks))

saved = os.listdir(tmpdir)
check("файл скриншота лежит в заданной папке", len(saved) >= 1, saved)

engine.stop()

print()
print("PY FAILS:", fails if fails else "0")
sys.exit(1 if fails else 0)
