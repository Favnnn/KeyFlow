# -*- coding: utf-8 -*-
"""Смоук-тесты плагина «Условия»: логика всех 12 нод, моки WinAPI/сети."""
import importlib.util
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

spec = importlib.util.spec_from_file_location(
    "kflow_plugin_conditions",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "plugins", "conditions", "plugin.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

FAILS = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + ((" | " + str(extra)) if (extra and not cond) else ""))
    if not cond:
        FAILS.append(name)


class FakeAPI:
    def __init__(self, variables=None):
        self.vars = dict(variables or {})
        self.logs = []

    def check_stop(self):
        pass

    def log(self, msg, level="info"):
        self.logs.append(str(msg))

    def get_var_any(self, name):
        return self.vars.get(name)

    def node_state(self):
        return {}


def run(nd, api, inputs, props):
    fired = []

    def emit(name, value):
        fired.append((name, value))

    params = {pp["name"]: pp["default"] for pp in nd["params"]}
    params.update(props)
    # движок подменяет параметры одноимёнными проводами
    for k, v in inputs.items():
        if v is not None and k in params:
            params[k] = v
    nd["exec"](api, params, inputs, emit)
    return fired


def names(fired):
    return [n for (n, _v) in fired]


def vals(fired):
    return [v for (_n, v) in fired]


# ---------------------------------------------------------------- Если
api = FakeAPI({"count": 5, "name": "Аня"})
f = run(m.IF_NODE, api, {"in": None}, {"expr": "count > 3"})
check("если: count>3 -> true", names(f) == ["true"] and vals(f) == [True], f)
f = run(m.IF_NODE, api, {"in": None}, {"expr": "count < 3"})
check("если: count<3 -> false", names(f) == ["false"] and vals(f) == [False], f)
f = run(m.IF_NODE, api, {"in": None}, {"expr": 'name == "Аня" and count == 5'})
check("если: строки+and", names(f) == ["true"], f)
try:
    run(m.IF_NODE, api, {"in": None}, {"expr": "__import__('os').system('dir')"})
    check("если: sandbox блокирует функции", False)
except ValueError:
    check("если: sandbox блокирует функции", True)
except Exception as e:
    check("если: sandbox блокирует функции", False, repr(e))
f = run(m.IF_NODE, api, {"in": None}, {"expr": "count > 3", "wait": "true"})
check("если: ждать true — проходим", names(f) == ["true"], f)
# «ждать» с невыполнимым условием: эмулируем «Стоп» через Cancelled-подобное исключение
class _Stop(Exception):
    pass


class StopAPI(FakeAPI):
    def check_stop(self):
        raise _Stop()


try:
    run(m.IF_NODE, StopAPI(), {"in": None}, {"expr": "count < 3", "wait": "true"})
    check("если: ждать true — прерывается «Стоп»", False)
except _Stop:
    check("если: ждать true — прерывается «Стоп»", True)
except Exception as e:
    check("если: ждать true — прерывается «Стоп»", False, repr(e))
# условие выполняется на 3-й итерации: цикл дождётся и выстрелит в true
_real_sleep = m.time.sleep
_seq = {"n": 0}
m.time.sleep = lambda s: None


def _flip():
    _seq["n"] += 1
    return _seq["n"] >= 3


_orig_if = m.IF_NODE["exec"]
m.IF_NODE["exec"] = lambda api, p, inputs, emit: m._wait_result(
    api, emit, _flip, p["wait"])
f = run(m.IF_NODE, api, {"in": None}, {"wait": "true"})
m.IF_NODE["exec"] = _orig_if
m.time.sleep = _real_sleep
check("если: ждать true — дождавшись, стреляет true", names(f) == ["true"], f)
f = run(m.IF_NODE, api, {"in": None}, {"expr": "count < 3", "wait": "false"})
check("если: ждать false — проходим", names(f) == ["false"], f)

# ---------------------------------------------------------------- Switch
f = run(m.SWITCH_NODE, api, {"in": None, "value": "b"}, {"cases": "a\nb\nc"})
check("switch: case b", names(f) == ["b"] and vals(f) == ["b"], f)
f = run(m.SWITCH_NODE, api, {"in": None, "value": "z"}, {"cases": "a, b"})
check("switch: мимо -> default", names(f) == ["default"] and vals(f) == ["z"], f)

# ---------------------------------------------------------------- Сравнить
f = run(m.COMPARE_NODE, api, {"in": None}, {"left": "10", "op": ">", "right": "9"})
check("сравнить: 10>9", names(f) == ["true"], f)
f = run(m.COMPARE_NODE, api, {"in": None}, {"left": "abc", "op": "contains", "right": "b"})
check("сравнить: contains", names(f) == ["true"], f)
f = run(m.COMPARE_NODE, api, {"in": None}, {"left": "a1", "op": "regex", "right": r"\d"})
check("сравнить: regex", names(f) == ["true"], f)
f = run(m.COMPARE_NODE, api, {"in": None}, {"left": "  ", "op": "empty", "right": ""})
check("сравнить: empty", names(f) == ["true"], f)
f = run(m.COMPARE_NODE, api, {"in": None}, {"left": "5", "op": "==", "right": "5.0"})
check("сравнить: 5==5.0 числами", names(f) == ["true"], f)

# ---------------------------------------------------------------- Файл
f = run(m.FILE_NODE, api, {"in": None}, {"path": os.path.dirname(__file__), "mode": "folder"})
check("файл: папка", names(f) == ["true"], f)
f = run(m.FILE_NODE, api, {"in": None}, {"path": "Q:/нет/такого.txt", "mode": "exists"})
check("файл: нет пути", names(f) == ["false"], f)

# ---------------------------------------------------------------- Процесс
orig_snap = m._proc_snapshot
m._proc_snapshot = lambda: {123: "explorer.exe", 456: "chrome.exe"}
f = run(m.PROCESS_NODE, api, {"in": None}, {"name": "chrome.exe"})
check("процесс: по имени", names(f) == ["true"], f)
f = run(m.PROCESS_NODE, api, {"in": None}, {"name": "456"})
check("процесс: по PID", names(f) == ["true"], f)
f = run(m.PROCESS_NODE, api, {"in": None}, {"name": "nope.exe"})
check("процесс: нет", names(f) == ["false"], f)
m._proc_snapshot = orig_snap

# ---------------------------------------------------------------- Активное окно
orig_info = m._active_window_info
m._active_window_info = lambda: ("Блокнот — file.txt", "Notepad", "notepad.exe")
f = run(m.ACTIVE_WIN_NODE, api, {"in": None}, {"mode": "title", "op": "contains", "match": "блокнот"})
check("окно: заголовок содержит (регистр)", names(f) == ["true"], f)
f = run(m.ACTIVE_WIN_NODE, api, {"in": None}, {"mode": "class", "op": "equals", "match": "notepad"})
check("окно: класс equals", names(f) == ["true"], f)
f = run(m.ACTIVE_WIN_NODE, api, {"in": None}, {"mode": "process", "op": "contains", "match": "PAD.EXE"})
check("окно: процесс (регистр)", names(f) == ["true"], f)
m._active_window_info = orig_info

# ---------------------------------------------------------------- Текст в окне
orig_texts = m._window_texts
m._window_texts = lambda part: "Кнопка ОК\nПоле ввода"
f = run(m.TEXT_WIN_NODE, api, {"in": None}, {"source": "window", "needle": "ок", "op": "contains"})
check("текст окна: содержит", names(f) == ["true"], f)
f = run(m.TEXT_WIN_NODE, api, {"in": None}, {"source": "window", "needle": r"\d+", "op": "regex"})
check("текст окна: regex мимо", names(f) == ["false"], f)
m._window_texts = orig_texts

# OCR-ветка: подменяем граб и распознаватель, проверяем логику выбора ветки
orig_grab, orig_rec = m._ocr_grab, m._ocr_recognizer
m._ocr_grab = lambda x, y, w, h: "IMG"
m._ocr_recognizer = lambda api, lang: (lambda img: "Итог 123")
f = run(m.TEXT_WIN_NODE, api, {"in": None}, {"source": "ocr", "needle": "123", "op": "contains"})
check("текст окна: OCR содержит", names(f) == ["true"], f)
m._ocr_grab, m._ocr_recognizer = orig_grab, orig_rec

# ---------------------------------------------------------------- Буфер
orig_clip = m._clipboard_text
m._clipboard_text = lambda: "Привет мир"
f = run(m.CLIP_NODE, api, {"in": None}, {"op": "contains", "value": "мир"})
check("буфер: содержит", names(f) == ["true"], f)
f = run(m.CLIP_NODE, api, {"in": None}, {"op": "regex", "value": r"м[и]р$"})
check("буфер: regex", names(f) == ["true"], f)
f = run(m.CLIP_NODE, api, {"in": None}, {"op": "equals", "value": "привет МИР"})
check("буфер: equals (регистр выкл)", names(f) == ["true"], f)
m._clipboard_text = orig_clip

# ---------------------------------------------------------------- Время
import time as _t
lt = _t.localtime()
mk = lambda h, mi: "%02d:%02d" % (h, mi)
inside = (mk(lt.tm_hour, lt.tm_min), mk(lt.tm_hour, lt.tm_min))
f = run(m.TIME_NODE, api, {"in": None}, {"from": inside[0], "to": inside[1]})
check("время: сейчас внутри [сейчас;сейчас]", names(f) == ["true"], f)
f = run(m.TIME_NODE, api, {"in": None}, {"from": "00:00", "to": "23:59"})
check("время: весь день", names(f) == ["true"], f)
days_all = "пн,вт,ср,чт,пт,сб,вс"
f = run(m.TIME_NODE, api, {"in": None}, {"from": "00:00", "to": "23:59", "days": days_all})
check("время: все дни", names(f) == ["true"], f)
bad_day = next(("пн", "вт", "ср", "чт", "пт", "сб", "вс")[i] for i in range(7) if i != lt.tm_wday)
f = run(m.TIME_NODE, api, {"in": None}, {"from": "00:00", "to": "23:59", "days": bad_day})
check("время: не тот день", names(f) == ["false"], f)
f = run(m.TIME_NODE, api, {"in": None}, {"from": "00:00", "to": "23:59", "days": "нет_такого"})
check("время: мусор в днях = все", names(f) == ["true"], f)

# ---------------------------------------------------------------- Интернет
orig_ping, orig_http, orig_tcp = m._ping, m._http_ok, m._tcp_ok
m._ping = lambda host, ms: True
f = run(m.INET_NODE, api, {"in": None}, {"mode": "ping"})
check("инет: ping ok", names(f) == ["true"], f)
m._ping = lambda host, ms: False
f = run(m.INET_NODE, api, {"in": None}, {"mode": "ping"})
check("инет: ping fail", names(f) == ["false"], f)
m._http_ok = lambda url, t: True
f = run(m.INET_NODE, api, {"in": None}, {"mode": "http"})
check("инет: http ok", names(f) == ["true"], f)
m._tcp_ok = lambda h, p, t: False
f = run(m.INET_NODE, api, {"in": None}, {"mode": "tcp"})
check("инет: tcp fail", names(f) == ["false"], f)
m._ping, m._http_ok, m._tcp_ok = orig_ping, orig_http, orig_tcp

# ---------------------------------------------------------------- Батарея
orig_bat = m._battery
m._battery = lambda: 80
f = run(m.BATTERY_NODE, api, {"in": None}, {"op": "above", "value": 50})
check("батарея: 80>50", names(f) == ["true"], f)
f = run(m.BATTERY_NODE, api, {"in": None}, {"op": "below", "value": 50})
check("батарея: 80<50 — нет", names(f) == ["false"], f)
m._battery = lambda: None
f = run(m.BATTERY_NODE, api, {"in": None}, {"op": "above", "value": 50})
check("батарея: нет батареи -> false", names(f) == ["false"], f)
m._battery = orig_bat

# ---------------------------------------------------------------- Переменная
f = run(m.VAR_EMPTY_NODE, api, {"in": None}, {"name": "count", "op": "empty"})
check("переменная: count=5 не пуста", names(f) == ["false"], f)
f = run(m.VAR_EMPTY_NODE, api, {"in": None}, {"name": "missing", "op": "empty"})
check("переменная: нет -> пуста", names(f) == ["true"], f)
f = run(m.VAR_EMPTY_NODE, api, {"in": None, "value": "  "}, {"op": "not empty"})
check("переменная: пробелы -> пуста", names(f) == ["false"], f)
f = run(m.VAR_EMPTY_NODE, api, {"in": None, "value": "x"}, {"op": "not empty"})
check("переменная: провод value", names(f) == ["true"], f)

# ---------------------------------------------------------------- Каркас
for nd in m.NODES:
    outs = [o["name"] for o in nd["outputs"]]
    has_wait = any(pp["name"] == "wait" for pp in nd["params"])
    if nd["id"] == "conditions/switch":
        # switch/case в стиле C++: default в шаблоне, выходы = варианты;
        # «Ждать» у switch нет (убрали)
        check("каркас: %s default + без Ждать" % nd["id"],
              nd["category"] == "conditions" and outs == [m.SWITCH_DEFAULT]
              and not any(pp["name"] == "wait" for pp in nd["params"]))
        continue
    check("каркас: %s true/false + Ждать" % nd["id"],
          outs == ["true", "false"] and has_wait and nd["category"] == "conditions")

print()
if FAILS:
    print("ИТОГ: FAIL (%d): %s" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("ИТОГ: OK")
