# -*- coding: utf-8 -*-
"""Прямой тест движка: группа subgraph без HTTP/JSON-слоя.

Формат групп как в ComfyUI: без group_input/group_output,
промоутед пины лежат в properties.inputs/outputs = [{name,type,node_id,slot}].
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.engine import Engine

events = []

engine = Engine(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                lambda evt: events.append(evt))

# регаем exec-ноды из плагина вручную
from core import plugin_manager as pm
pmgr = pm.PluginManager(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins"))
pmgr.discover()
engine.defs.update(pmgr.collect_defs())

graph = {
    "nodes": [
        {"id": 1, "type": "core/startup", "pos": [0, 0], "mode": 0,
         "inputs": [], "outputs": [{"name": "out", "type": "event"}], "properties": {}},
        {"id": 5, "type": "core/subgraph", "pos": [200, 0], "mode": 0,
         "inputs": [{"name": "g_in", "type": "event"}],
         "outputs": [{"name": "g_out", "type": "event"}],
         "properties": {
             "title": "G", "widgets": [],
             "inputs": [{"name": "g_in", "type": "event", "node_id": 2, "slot": 0}],
             "outputs": [{"name": "g_out", "type": "event", "node_id": 3, "slot": 0}],
             "inner": {
                 "nodes": [
                     {"id": -10, "type": "core/subgraph/input", "pos": [-300, 0], "mode": 0,
                      "properties": {"side": "input"},
                      "inputs": [],
                      "outputs": [{"name": "g_in", "type": "event", "links": [102]}]},
                     {"id": -100, "type": "core/subgraph/output", "pos": [300, 0], "mode": 0,
                      "properties": {"side": "output"},
                      "inputs": [{"name": "g_out", "type": "event", "link": 103}],
                      "outputs": []},
                     {"id": 2, "type": "core/wait", "pos": [10, 10], "mode": 0,
                      "properties": {"seconds": 0.2},
                      "inputs": [{"name": "in", "type": "event", "link": 102}],
                      "outputs": [{"name": "out", "type": "event", "links": [103]}]},
                     {"id": 3, "type": "core/log", "pos": [20, 20], "mode": 0,
                      "properties": {"message": "INSIDE-GROUP"},
                      "inputs": [{"name": "in", "type": "event"}],
                      "outputs": [{"name": "out", "type": "event"}]},
                 ],
                 "links": [
                     [102, -10, 0, 2, 0, "event"],
                     [103, 2, 0, -100, 0, "event"],
                     [104, 2, 0, 3, 0, "event"],
                 ],
             }},
         },
        {"id": 4, "type": "core/log", "pos": [400, 0], "mode": 0,
         "properties": {"message": "AFTER-GROUP"},
         "inputs": [{"name": "in", "type": "event"}],
         "outputs": [{"name": "out", "type": "event"}]},
    ],
    "links": [[1, 1, 0, 5, 0, "event"], [2, 5, 0, 4, 0, "event"]],
}

ok, msg = engine.start("__pythontest", graph)
print("start:", ok, msg)
time.sleep(1.5)
print("--- statuses:")
for e in events:
    if e["type"] in ("log", "node_status"):
        print(" ", e)

logs = [e.get("msg") for e in events if e["type"] == "log"]
assert "INSIDE-GROUP" in logs, "нет INSIDE-GROUP"
assert "AFTER-GROUP" in logs, "нет AFTER-GROUP"
assert not any("Неизвестный тип" in str(m) for m in logs), "маркер-ноды попали в исполнение"
errs = [e for e in events if e["type"] == "node_status" and e.get("state") == "error"]
assert not errs, "ошибки исполнения: %s" % errs
print("PASS: группа исполняется, вход/выход работают; маркеры пропущены движком")
engine.stop()
