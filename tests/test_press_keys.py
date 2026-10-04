# -*- coding: utf-8 -*-
"""Тест ноды «Нажать клавиши»: inject() принимал лишний аргумент -> TypeError.
Плюс полный прогон через движок на безобидной клавише F15."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fails = []
def check(name, cond):
    print(("PASS" if cond else "FAIL") + ": " + name)
    if not cond:
        fails.append(name)

# 1) хелперы разбора сочетаний
from core.hotkeys import parse_combo_tokens, token_to_pynput
check("разбор 'ctrl+shift+s'", parse_combo_tokens("ctrl+shift+s") == ["ctrl", "shift", "s"])
check("разбор 'a+s+d'", parse_combo_tokens("a+s+d") == ["a", "s", "d"])
try:
    token_to_pynput("ctrl"); token_to_pynput("s"); token_to_pynput("f5"); token_to_pynput("space")
    check("токены -> pynput", True)
except Exception as e:
    check("токены -> pynput (%s)" % e, False)

# 2) ExecAPI.inject(sec) — раньше был TypeError
from core.engine import Engine, ExecAPI, EventContext
from core import plugin_manager as pm
pmgr = pm.PluginManager(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins"))
pmgr.discover()

events = []
engine = Engine(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                lambda evt: events.append(evt))
engine.defs.update(pmgr.collect_defs())

graph = {
    "nodes": [
        {"id": 1, "type": "core/startup", "pos": [0, 0], "mode": 0,
         "inputs": [], "outputs": [{"name": "out", "type": "event"}], "properties": {}},
        {"id": 2, "type": "core/press_keys", "pos": [200, 0], "mode": 0,
         "inputs": [{"name": "in", "type": "event"}],
         "outputs": [{"name": "out", "type": "event"}],
         "properties": {"combo": "f15", "mode": "tap"}},
        {"id": 3, "type": "core/log", "pos": [400, 0], "mode": 0,
         "inputs": [{"name": "in", "type": "event"}],
         "outputs": [{"name": "out", "type": "event"}],
         "properties": {"message": "PRESSED-OK"}},
    ],
    "links": [[1, 1, 0, 2, 0, "event"], [2, 2, 0, 3, 0, "event"]],
}

events = []
engine.defs.update(pmgr.collect_defs())  # обновить после пересоздания engine

ok, msg = engine.start("__pythontest_press", graph)
check("start", ok)

deadline = time.time() + 4
statuses = {}
pressed = False
while time.time() < deadline and not pressed:
    for e in events:
        if e.get("type") == "node_status" and e.get("node_id") == 2:
            statuses[e.get("state")] = e
        if e.get("type") == "log" and e.get("msg") == "PRESSED-OK":
            pressed = True
    time.sleep(0.05)

check("press_keys: статус ok (нет ошибки inject)", "error" not in statuses and "ok" in statuses)
check("press_keys: цепочка дошла до лога", pressed)

engine.stop()
print("\nВСЕ ТЕСТЫ ПРОЙДЕНЫ" if not fails else "\nПРОВАЛЫ: %s" % fails)
sys.exit(0 if not fails else 1)
