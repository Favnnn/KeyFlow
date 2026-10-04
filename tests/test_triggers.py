# -*- coding: utf-8 -*-
"""Простые проверки плагина «Триггеры» и переноса «Превью» в ядро.

Запуск: python -X utf8 tests/test_triggers.py
Без графики и глобальных хуков: только чистые функции, загрузка плагинов
и один живой прогон движка с триггером «Первое включение».
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILS = 0


def check(name, fn):
    global FAILS
    try:
        fn()
        print("PASS: " + name)
    except Exception as e:
        print("FAIL: %s -> %r" % (name, e))
        FAILS += 1


# 1) дни недели
def t_days():
    from plugins.triggers.plugin import parse_days
    assert parse_days("пн,ср") == {0, 2}, parse_days("пн,ср")
    assert parse_days("вс") == {6}
    assert parse_days("") == set(range(7))
    assert parse_days("чёрт") == set(range(7))   # неразборчиво = все дни


# 2) cron-парсер
def t_cron():
    from plugins.triggers.plugin import parse_cron
    f = parse_cron("*/30 * * * *")
    assert f is not None and f[0] == {0, 30}, f
    f = parse_cron("0 9-17 * * 1-5")
    assert f is not None and f[1] == set(range(9, 18)) and f[4] == set(range(1, 6))
    assert parse_cron("99 * * * *") is None      # минута вне диапазона
    assert parse_cron("* * * *") is None         # не 5 полей
    assert parse_cron("a b c d e") is None


# 3) сверка cron с датой (0=воскресенье в cron, 0=понедельник в python)
def t_cron_match():
    from plugins.triggers.plugin import parse_cron, _cron_match
    # 8 янв 2024 — понедельник (python tm_wday=0)
    lt = time.struct_time((2024, 1, 8, 12, 30, 0, 0, 8, 0))
    assert _cron_match(parse_cron("30 12 * * *"), lt)
    assert _cron_match(parse_cron("30 12 * * 1"), lt)    # понедельник
    assert not _cron_match(parse_cron("30 12 * * 0"), lt)  # воскресенье
    assert not _cron_match(parse_cron("31 12 * * *"), lt)


# 4) поиск процессов
def t_proc_match():
    from plugins.triggers.plugin import _proc_match
    procs = {100: "notepad.exe", 200: "explorer.exe", 300: "python.exe"}
    assert _proc_match(procs, "notepad.exe") == {100: "notepad.exe"}
    assert _proc_match(procs, "200") == {200: "explorer.exe"}
    assert _proc_match(procs, "NOTE") == {100: "notepad.exe"}
    assert _proc_match(procs, "неттакого") == {}


# 5) снимок папки с фильтром расширений
def t_dir_snapshot():
    import tempfile
    from plugins.triggers.plugin import _dir_snapshot
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "a.txt"), "w").close()
        open(os.path.join(d, "b.png"), "w").close()
        sub = os.path.join(d, "sub")
        os.makedirs(sub)
        open(os.path.join(sub, "c.txt"), "w").close()
        snap = _dir_snapshot(d, False, None)
        assert len(snap) == 2, snap                       # без вложенных
        snap = _dir_snapshot(d, True, (".txt",))
        assert len(snap) == 2 and sub not in snap, snap   # только .txt, но с вложенными


# 6) загрузка плагинов: превью в ядре, 15 триггеров (first_enable удалён)
def t_plugins():
    from core.plugin_manager import PluginManager
    ws = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pm = PluginManager(os.path.join(ws, "plugins"))
    pm.discover()
    ids = set(pm.plugins)
    assert "preview" not in ids, "плагин preview ещё на месте"
    assert "triggers" in ids
    assert "core_nodes" in ids
    st = pm.plugins["triggers"].status
    assert st == "loaded", (st, pm.plugins["triggers"].error)
    defs = pm.collect_defs()
    trig = [k for k in defs if k.startswith("triggers/")]
    assert len(trig) == 15, sorted(trig)
    assert "triggers/first_enable" not in defs, "first_enable должен быть удалён"
    for k in trig:
        d = defs[k]
        assert d.get("trigger") is True, k
        assert d.get("inputs") == [], k
        assert callable(d.get("start")), k
    fc = defs["triggers/file_change"]
    assert [o["name"] for o in fc["outputs"]] == ["out", "path"], fc["outputs"]
    pv = defs.get("preview/show")
    assert pv is not None and pv.get("plugin") == "core_nodes", pv.get("plugin")
    su = defs["core/startup"]
    assert su["title"]["ru"] == "Первое включение", su["title"]


# 7) живой прогон: «Первое включение» (core/startup) -> «Превью» -> «Лог»
def t_engine_first_enable():
    import json
    from core.plugin_manager import PluginManager
    from core.engine import Engine
    ws = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pm = PluginManager(os.path.join(ws, "plugins"))
    pm.discover()
    events = []
    eng = Engine(ws, on_event=events.append)
    eng.defs.update(pm.collect_defs())
    graph = {
        "nodes": [
            {"id": 1, "type": "core/startup", "title": "Первое включение",
             "properties": {}, "inputs": [], "outputs": [{"name": "out"}]},
            {"id": 2, "type": "preview/show", "title": "Превью",
             "properties": {},
             "inputs": [{"name": "in"}, {"name": "in1"}],
             "outputs": [{"name": "out"}]},
            {"id": 3, "type": "core/log", "title": "Лог",
             "properties": {"message": "сработало"},
             "inputs": [{"name": "in"}, {"name": "message"}],
             "outputs": [{"name": "out"}]},
        ],
        "links": [[1, 1, 0, 2, 0], [2, 2, 0, 3, 0]],
    }
    ok, msg = eng.start("test_triggers", graph)
    assert ok, msg
    deadline = time.time() + 5
    previews = []
    logs = []
    while time.time() < deadline:
        previews = [e for e in events if e.get("type") == "node_preview"]
        logs = [e for e in events if e.get("type") == "log" and "сработало" in e.get("msg", "")]
        if previews and logs:
            break
        time.sleep(0.05)
    eng.stop()
    assert previews, "превью не пришло: " + json.dumps(
        [e.get("type") for e in events][:20])
    assert previews[0]["lines"] == ["Из: Первое включение"], previews[0]["lines"]
    assert logs, "лог не пришёл"


check("дни недели (пн,ср / пусто)", t_days)
check("cron-парсер (шаги, диапазоны, мусор)", t_cron)
check("cron сверка с датой (день недели)", t_cron_match)
check("поиск процессов (имя/PID/подстрока)", t_proc_match)
check("снимок папки (вложенность, расширения)", t_dir_snapshot)
check("плагины: 15 триггеров, startup = «Первое включение»", t_plugins)
check("движок: core/startup -> «Превью» -> «Лог»", t_engine_first_enable)

print()
print("ВСЕ ТЕСТЫ ПРОЙДЕНЫ" if not FAILS else "ПРОВАЛОВ: %d" % FAILS)
sys.exit(1 if FAILS else 0)
