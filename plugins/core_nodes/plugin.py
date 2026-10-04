# -*- coding: utf-8 -*-
"""KeyFlow core nodes — базовые ноды программы.

Типы пинов: event | any | string | number | bool | pos({"x","y"})
Параметр param у входа = значение с провода подменяет одноимённый параметр.
"""
import base64
import ctypes
import os
import subprocess
import threading
import time
import uuid

from core.hotkeys import parse_combo_tokens, token_to_pynput


def N(nid, en, ru, category, inputs=(), outputs=(("out", "event"),), params=(),
      trigger=False, desc=None):
    d = {
        "id": nid,
        "title": {"en": en, "ru": ru},
        "desc": desc or {},
        "category": category,
        "inputs": [{"name": n, "type": t} for (n, t) in inputs],
        "outputs": [{"name": n, "type": t} for (n, t) in outputs],
        "params": list(params),
    }
    if trigger:
        d["trigger"] = True
    return d


def P(name, ptype, default, en, ru, options=None):
    p = {"name": name, "type": ptype, "default": default,
         "label": {"en": en, "ru": ru}}
    if options is not None:
        p["options"] = list(options)
    return p


EVENT_IN = ("in", "event")

# --------------------------------------------------------------------------
# Триггеры
# --------------------------------------------------------------------------

def _hotkey_start(handle, node):
    combo = (node.get("properties") or {}).get("combo") or ""
    handle.engine.hotkeys.register_combo(handle.node_id, combo)
    return lambda: handle.engine.hotkeys.unregister(handle.node_id)


def _mouse_start(handle, node):
    pr = node.get("properties") or {}
    handle.engine.hotkeys.register_mouse(handle.node_id,
                                         pr.get("button", "middle"),
                                         pr.get("action", "click"))
    return lambda: handle.engine.hotkeys.unregister(handle.node_id)


def _startup_start(handle, node):
    threading.Thread(target=handle.fire, daemon=True).start()
    return lambda: None


def _interval_start(handle, node):
    pr = node.get("properties") or {}
    try:
        sec = max(0.1, float(pr.get("seconds", 5)))
    except (TypeError, ValueError):
        sec = 5.0
    ev = threading.Event()

    def loop():
        while not ev.wait(sec):
            if handle.engine.stopping:
                break
            try:
                handle.fire()
            except Exception:
                pass

    threading.Thread(target=loop, daemon=True).start()
    return ev.set


HOTKEY_NODE = N(
    "core/hotkey", "Hotkey", "Горячая клавиша", "triggers",
    outputs=(("out", "event"),),
    params=[P("combo", "hotkey", "ctrl+alt+q", "Combination", "Сочетание")],
    trigger=True,
    desc={"en": "Fires when the combination is pressed. Works with global hotkeys (Stream Deck sends hotkeys too).",
          "ru": "Срабатывает при нажатии сочетания. Поддерживает несколько зажатых букв (a+s+d) и модификаторы. Работает с глобальными хоткеями (Stream Deck тоже шлёт хоткеи)."},
)
HOTKEY_NODE["start"] = _hotkey_start

MOUSE_TRIG_NODE = N(
    "core/mouse_trigger", "Mouse button", "Кнопка мыши", "triggers",
    outputs=(("out", "event"),),
    params=[
        P("button", "combo", "middle", "Button", "Кнопка",
          ["left", "right", "middle", "x1", "x2"]),
        P("action", "combo", "click", "Action", "Действие", ["click", "double"]),
    ],
    trigger=True,
    desc={"en": "Fires on a global mouse button press.",
          "ru": "Срабатывает по нажатию кнопки мыши в любом месте."},
)
MOUSE_TRIG_NODE["start"] = _mouse_start

STARTUP_NODE = N(
    "core/startup", "On start", "Первое включение", "triggers",
    outputs=(("out", "event"),), trigger=True,
    desc={"en": "Fires once when the workflow is started (Run button).",
          "ru": "Срабатывает один раз при запуске workflow (кнопка ▶)."},
)
STARTUP_NODE["start"] = _startup_start

INTERVAL_NODE = N(
    "core/interval", "Interval", "Интервал", "triggers",
    inputs=(),
    outputs=(("out", "event"),),
    params=[P("seconds", "number", 10, "Every, sec", "Каждые, сек")],
    trigger=True,
    desc={"en": "Fires every N seconds while running.",
          "ru": "Срабатывает каждые N секунд, пока процесс запущен."},
)
INTERVAL_NODE["start"] = _interval_start


# --------------------------------------------------------------------------
# Мышь
# --------------------------------------------------------------------------

def _coerce_pos(v):
    """Значение переменной -> (x, y)."""
    if isinstance(v, dict):
        return float(v.get("x", 0)), float(v.get("y", 0))
    if isinstance(v, (list, tuple)) and len(v) >= 2:
        return float(v[0]), float(v[1])
    if isinstance(v, str) and "," in v:
        a, b = v.split(",", 1)
        return float(a.strip()), float(b.strip())
    raise ValueError("В переменной нет координат: %r" % (v,))


def _resolve_pos(api, p):
    if p["pos_target"] == "coords":
        return float(p["x"]), float(p["y"])
    return None   # "current" — клик там, где курсор сейчас


def exec_click(api, p, inputs, emit):
    api.check_stop()
    if "in" not in inputs:
        return  # пришёл только провод координат — события ещё не было
    from pynput import mouse as m
    if inputs.get("x") is not None:
        p = dict(p, x=inputs["x"], pos_target="coords")
    if inputs.get("y") is not None:
        p = dict(p, y=inputs["y"], pos_target="coords")
    pos = _resolve_pos(api, p)
    btn = getattr(m.Button, p["button"])
    with api.inject():
        ctl = m.Controller()
        if pos is not None:
            ctl.position = (int(pos[0]), int(pos[1]))
        if p["action"] == "single":
            ctl.click(btn, 1)
        elif p["action"] == "double":
            ctl.click(btn, 2)
        elif p["action"] == "down":
            ctl.press(btn)
        elif p["action"] == "up":
            ctl.release(btn)
    emit("out", None)


CLICK_NODE = N(
    "core/click", "Mouse click", "Клик мышью", "mouse",
    inputs=(EVENT_IN, ("x", "number"), ("y", "number")),
    outputs=(("out", "event"),),
    params=[
        P("button", "combo", "right", "Button", "Кнопка",
          ["left", "right", "middle", "x1", "x2"]),
        P("action", "combo", "single", "Action", "Действие",
          ["single", "double", "down", "up"]),
        P("pos_target", "combo", "current", "Position", "Позиция",
          ["current", "coords"]),
        P("x", "number", 0, "X", "X"),
        P("y", "number", 0, "Y", "Y"),
    ],
    desc={"en": "Clicks / presses / releases a mouse button at the current position or at coordinates.",
          "ru": "Кликает / зажимает / отпускает кнопку мыши в текущей позиции или по координатам."},
)
CLICK_NODE["exec"] = exec_click


def exec_move_mouse(api, p, inputs, emit):
    api.check_stop()
    if "in" not in inputs:
        return  # пришёл только провод координат — события ещё не было
    from pynput import mouse as m
    if inputs.get("x") is not None:
        p = dict(p, x=inputs["x"])
    if inputs.get("y") is not None:
        p = dict(p, y=inputs["y"])
    tx, ty = int(float(p["x"])), int(float(p["y"]))

    def ease(t):
        # ease-in-out: старт и финиш мягкие, в середине быстрее
        return t * t * (3.0 - 2.0 * t)

    with api.inject():
        ctl = m.Controller()
        if p["mode"] == "smooth":
            try:
                dur = max(0.05, float(p["duration"]))
            except (TypeError, ValueError):
                dur = 0.5
            start = ctl.position
            steps = max(8, int(dur * 120))   # ~120 шагов/сек
            t0 = time.perf_counter()
            for i in range(1, steps + 1):
                api.check_stop()
                api.hotkeys_ping()
                k = i / steps
                # реальное время решает: шаги не «накапливают» задержку
                k = min(1.0, (time.perf_counter() - t0) / dur)
                k = ease(k)
                if i == steps:
                    k = 1.0
                ctl.position = (int(start[0] + (tx - start[0]) * k),
                                int(start[1] + (ty - start[1]) * k))
                # целься в t0 + k*dur, а не «шаг + сон»: без дрейфа
                nxt = t0 + (i / steps) * dur
                d = nxt - time.perf_counter()
                if d > 0:
                    time.sleep(d)
        else:
            ctl.position = (tx, ty)   # рывком: мгновенно
    emit("out", None)


MOVE_NODE = N(
    "core/move_mouse", "Move mouse", "Двигать мышь", "mouse",
    inputs=(EVENT_IN, ("x", "number"), ("y", "number")),
    outputs=(("out", "event"),),
    params=[
        P("mode", "combo", "instant", "Motion", "Движение",
          ["instant", "smooth"]),
        P("x", "number", 0, "X", "X"),
        P("y", "number", 0, "Y", "Y"),
        P("duration", "number", 0.5, "Smooth: seconds", "Плавно: за секунд"),
    ],
    desc={"en": "Moves the cursor to X,Y: instantly or smoothly over N seconds (smooth easing).",
          "ru": "Перемещает курсор в X,Y: рывком или плавно за N секунд (с мягким разгоном и торможением)."},
)
MOVE_NODE["exec"] = exec_move_mouse


def exec_mouse_position(api, p, inputs, emit):
    api.check_stop()
    from pynput import mouse as m
    x, y = m.Controller().position
    emit("position", {"x": x, "y": y})
    emit("out", None)


MOUSE_POS_NODE = N(
    "core/mouse_position", "Mouse position", "Позиция мыши", "mouse",
    inputs=(EVENT_IN,),
    outputs=(("out", "event"), ("position", "pos")),
    desc={"en": "Outputs current cursor position {x, y}. Save it with the 'Set variable' node.",
          "ru": "Выдаёт текущую позицию курсора {x, y}. Сохраните её нодой «Переменная: записать»."},
)
MOUSE_POS_NODE["exec"] = exec_mouse_position


def exec_scroll(api, p, inputs, emit):
    api.check_stop()
    if "in" not in inputs:
        return
    from pynput import mouse as m
    steps = max(1, int(p["steps"]))
    dx, dy = 0, 0
    d = p["direction"]
    if d == "up":
        dy = steps
    elif d == "down":
        dy = -steps
    elif d == "left":
        dx = -steps
    else:
        dx = steps
    with api.inject():
        m.Controller().scroll(dx, dy)
    emit("out", None)


SCROLL_NODE = N(
    "core/scroll", "Scroll", "Прокрутка", "mouse",
    inputs=(EVENT_IN, ("steps", "number")), outputs=(("out", "event"),),
    params=[
        P("direction", "combo", "down", "Direction", "Направление",
          ["up", "down", "left", "right"]),
        P("steps", "number", 3, "Steps", "Шагов"),
    ],
    desc={"en": "Scrolls up/down/left/right by N steps (wire 'steps' wins).",
          "ru": "Крутит колёсико вверх/вниз/влево/вправо на N шагов (провод «steps» сильнее)."},
)
SCROLL_NODE["exec"] = exec_scroll


def exec_drag_drop(api, p, inputs, emit):
    api.check_stop()
    if "in" not in inputs:
        return
    from pynput import mouse as m
    ax = inputs.get("ax")
    if ax is None:
        ax = p["ax"]
    ay = inputs.get("ay")
    if ay is None:
        ay = p["ay"]
    bx = inputs.get("bx")
    if bx is None:
        bx = p["bx"]
    by = inputs.get("by")
    if by is None:
        by = p["by"]
    button = getattr(m.Button, p["button"])
    duration = max(0.0, float(p["duration"]))
    ctl = None
    dragging = False
    try:
        with api.inject():
            ctl = m.Controller()
            ctl.position = (int(ax), int(ay))          # к точке A
            time.sleep(0.15)                           # дать ОС увидеть перенос
            ctl.press(button)                          # зажать
            dragging = True
            if duration <= 0.01:
                ctl.position = (int(bx), int(by))      # рывком
            else:
                start = (int(ax), int(ay))
                steps = max(8, int(duration * 120))
                t0 = time.perf_counter()
                for i in range(1, steps + 1):
                    api.check_stop()
                    api.hotkeys_ping()
                    k = min(1.0, (time.perf_counter() - t0) / duration)  # реальное время
                    k = k * k * (3.0 - 2.0 * k)        # ease-in-out
                    if i == steps:
                        k = 1.0
                    ctl.position = (int(start[0] + (int(bx) - start[0]) * k),
                                    int(start[1] + (int(by) - start[1]) * k))
                    nxt = t0 + (i / steps) * duration
                    d = nxt - time.perf_counter()
                    if d > 0:
                        time.sleep(d)
            time.sleep(0.12)                           # «подраться» на точке B
            ctl.release(button)                        # отпустить
            dragging = False
    except BaseException:
        if ctl is not None and dragging:
            try:
                ctl.release(button)   # не оставлять кнопку зажатой
            except Exception:
                pass
        raise
    emit("out", None)


DRAG_NODE = N(
    "core/drag_drop", "Drag and drop", "Перетащить", "mouse",
    inputs=(EVENT_IN, ("ax", "number"), ("ay", "number"),
            ("bx", "number"), ("by", "number")),
    outputs=(("out", "event"),),
    params=[
        P("ax", "number", 100, "From X", "Из X"),
        P("ay", "number", 100, "From Y", "Из Y"),
        P("bx", "number", 300, "To X", "В X"),
        P("by", "number", 300, "To Y", "В Y"),
        P("button", "combo", "left", "Button", "Кнопка",
          ["left", "right", "middle"]),
        P("duration", "number", 0.5, "Drag seconds (0 = instant)", "Секунд на перенос (0 = мгновенно)"),
    ],
    desc={"en": "Presses at point A, drags to point B (instant or smooth over N seconds), releases.",
          "ru": "Зажимает в точке A, тащит в точку B (мгновенно или плавно за N секунд), отпускает."},
)
DRAG_NODE["exec"] = exec_drag_drop


# --------------------------------------------------------------------------
# Клавиатура
# --------------------------------------------------------------------------

def _alt_tab_overlay_appeared(timeout=0.6):
    """Ждать ПОЯВЛЕНИЯ оверлея Alt+Tab (окно класса #32771).
    True — появился (или уже был), False — так и не открылся."""
    user32 = ctypes.windll.user32
    deadline = time.time() + timeout
    while time.time() < deadline:
        if user32.FindWindowW("#32771", None):
            return True
        time.sleep(0.02)
    return False


def _alt_tab_overlay_gone(timeout=2.5):
    """Ждать закрытия оверлея Alt+Tab (окно класса #32771).
    True — закрылся (или не открывался), False — истёк таймаут."""
    user32 = ctypes.windll.user32
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not user32.FindWindowW("#32771", None):
            return True
        time.sleep(0.03)
    return False


def _win_hwnd_eligible(hwnd):
    """Окно пригодно для переключения: видимое, активируемое,
    не инструментальное (панели/тултипы), не «прикрытое» UWP-плащом."""
    user32 = ctypes.windll.user32
    try:
        if not user32.IsWindowVisible(hwnd):
            return False
        if not user32.IsWindowEnabled(hwnd):
            return False
        # WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        if user32.GetWindowLongW(hwnd, -20) & 0x08000080:
            return False
        try:
            cloaked = ctypes.c_uint(0)
            if ctypes.windll.dwmapi.DwmGetWindowAttribute(
                    hwnd, 14, ctypes.byref(cloaked), 4) == 0 and cloaked.value:
                return False
        except Exception:
            pass
        return True
    except Exception:
        return False


def _win_switch_next_window():
    """Мгновенный «alt+tab»: передать фокус следующему окну в Z-порядке
    (то же, что делает оверлей, только без оверлея и пауз).
    Возвращает True, только если переднее окно ФАКТИЧЕСКИ сменилось."""
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        cur = user32.GetForegroundWindow()
        if not cur:
            return False
        hwnd = user32.GetWindow(cur, 2)   # GW_HWNDNEXT
        seen = 0
        while hwnd and seen < 64:
            if _win_hwnd_eligible(hwnd):
                if user32.IsIconic(hwnd):
                    user32.ShowWindow(hwnd, 9)   # SW_RESTORE
                    time.sleep(0.05)
                # 1) системное переключение, как у оболочки для alt+tab:
                #    без Alt-тапов, меню приёмника не задевает
                user32.SwitchToThisWindow(hwnd, True)
                ok = False
                deadline = time.time() + 0.25
                while time.time() < deadline:
                    if user32.GetForegroundWindow() == hwnd:
                        ok = True
                        break
                # 2) запасной путь: присоединиться к потокам ввода и
                #    активировать напрямую (разрешения не требует)
                if not ok:
                    fg_thread = user32.GetWindowThreadProcessId(
                        user32.GetForegroundWindow() or cur, None)
                    my_thread = kernel32.GetCurrentThreadId()
                    user32.AttachThreadInput(my_thread, fg_thread, True)
                    user32.AttachThreadInput(my_thread, fg_thread, False)
                    user32.SetForegroundWindow(hwnd)
                    deadline = time.time() + 0.25
                    while time.time() < deadline:
                        if user32.GetForegroundWindow() == hwnd:
                            ok = True
                            break
                # 3) последнее средство: краткий тап ALT (может открыть
                #    меню приёмника — потому только если всё выше не сработало)
                if not ok:
                    user32.keybd_event(0x12, 0, 0, 0)
                    user32.keybd_event(0x12, 0, 2, 0)
                    user32.SetForegroundWindow(hwnd)
                    deadline = time.time() + 0.5
                    while time.time() < deadline:
                        if user32.GetForegroundWindow() == hwnd:
                            ok = True
                            break
                return hwnd if ok else False
            hwnd = user32.GetWindow(hwnd, 2)
            seen += 1
        return False
    except Exception:
        return False


def _fg_title():
    """Заголовок переднего окна для лога (коротко, без паники при ошибках)."""
    try:
        u = ctypes.windll.user32
        h = u.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(128)
        u.GetWindowTextW(h, buf, 128)
        return (buf.value or "?")[:60]
    except Exception:
        return "?"


def _key_vk(k):
    """vk-код и для KeyCode (.vk), и для Key (.value.vk).
    pynput.Key.ctrl — не KeyCode: у него vk спрятан в .value."""
    vk = getattr(k, "vk", None)
    if not vk and hasattr(k, "value"):
        vk = getattr(k.value, "vk", None)
    return vk


def _ensure_key_down(ctl, k, tries=3):
    """Факт-проверка: система реально видит клавишу зажатой.
    При смене фокуса окна первое нажатие модификатора может потеряться —
    тогда приложение получает голую букву («м» вместо ctrl+v). Дожимаем."""
    vk = _key_vk(k)
    if not vk:
        return
    user32 = ctypes.windll.user32
    for _ in range(tries):
        if user32.GetAsyncKeyState(vk) & 0x8000:
            return
        try:
            ctl.press(k)
        except Exception:
            return
        time.sleep(0.03)


_KB_STRUCTS = None


def _kb_input_factory():
    """Структуры SendInput (создаются один раз)."""
    global _KB_STRUCTS
    if _KB_STRUCTS is None:
        from ctypes import wintypes
        PUL = ctypes.POINTER(ctypes.c_ulong)
        class KeyBdInput(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", PUL)]
        class HardwareInput(ctypes.Structure):
            _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.SHORT),
                        ("wParamH", wintypes.WORD)]
        class MouseInput(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", PUL)]
        class InputI(ctypes.Union):
            _fields_ = [("ki", KeyBdInput), ("mi", MouseInput),
                        ("hi", HardwareInput)]
        class Input(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("ii", InputI)]
        _KB_STRUCTS = (KeyBdInput, Input)
    return _KB_STRUCTS


def _send_batch(pairs):
    """Отправить последовательность (vk, flags) ОДНИМ SendInput."""
    try:
        KeyBdInput, Input = _kb_input_factory()
        arr = (Input * len(pairs))()
        for i, (vk, fl) in enumerate(pairs):
            arr[i].type = 1              # INPUT_KEYBOARD
            arr[i].ii.ki = KeyBdInput(vk, 0, fl, 0, None)
        n = ctypes.windll.user32.SendInput(len(pairs), arr, ctypes.sizeof(Input))
        return n == len(pairs)
    except Exception:
        return False


def _send_combo_atomic(keys):
    """Атомарная инъекция сочетания: ВСЯ последовательность (down+up)
    одним SendInput. Система не перемешивает батч с посторонним вводом,
    поэтому физический ctrl пользователя не может вклиниться внутрь.
    True — ОС приняла весь батч."""
    seq = []
    for k in keys:
        vk = _key_vk(k)
        if not vk:
            return False
        seq.append((vk, 0))          # down
    for k in reversed(keys):
        seq.append((_key_vk(k), 2))          # up (KEYEVENTF_KEYUP)
    return _send_batch(seq)


_MOD_VKS = {0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C}


def _send_combo_hold(keys, hold=0.03):
    """Сочетание с удержанием КАЖДОГО модификатора (схема AutoHotkey):
    [мод вниз] -> 30 мс -> факт-проверка (дожать при потере) -> ... ->
    [не-модификаторы + все отпускания одним батчем SendInput].
    Работает и для двойных модификаторов (ctrl+shift+s, ctrl+alt+...):
    каждый получает своё удержание. Без модификаторов — атомарный батч."""
    try:
        u = ctypes.windll.user32
        vks = []
        for k in keys:
            vk = _key_vk(k)
            if not vk:
                return False
            vks.append(vk)
        if not any(v in _MOD_VKS for v in vks):
            return _send_combo_atomic(keys)
        mods = [v for v in vks if v in _MOD_VKS]
        rest = [v for v in vks if v not in _MOD_VKS]
        for vk in mods:
            if not _send_batch([(vk, 0)]):
                return False
            time.sleep(hold)
            if not (u.GetAsyncKeyState(vk) & 0x8000):   # модификатор потерян
                if not _send_batch([(vk, 0)]):
                    return False
                time.sleep(0.02)
        if not rest:
            # тап «только модификаторы»: вниз уже отосланы — отпускаем
            return _send_batch([(v, 2) for v in reversed(mods)])
        seq = [(v, 0) for v in rest]
        seq += [(v, 2) for v in reversed(vks)]   # отпускания: буква, моды
        return _send_batch(seq)
    except Exception:
        return False


def _wait_focus_ready(hwnd, timeout=0.5):
    """Ждать, пока у окна появится ВНУТРЕННИЙ фокус ввода (hwndFocus).
    Foreground-смена ещё не значит, что окно приняло клавиатуру: пока
    идёт активация, первые клавиши могут уйти «в никуда». Не дождались —
    не блокируем навсегда (возвращаем True по таймауту)."""
    user32 = ctypes.windll.user32
    class RECT(ctypes.Structure):
        _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                    ("r", ctypes.c_long), ("b", ctypes.c_long)]
    class GTI(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("flags", ctypes.c_uint),
                    ("hwndActive", ctypes.c_void_p),
                    ("hwndFocus", ctypes.c_void_p),
                    ("hwndCapture", ctypes.c_void_p),
                    ("hwndMenuOwner", ctypes.c_void_p),
                    ("hwndMoveSize", ctypes.c_void_p),
                    ("hwndCaret", ctypes.c_void_p), ("rcCaret", RECT)]
    tid = user32.GetWindowThreadProcessId(hwnd, None)
    deadline = time.time() + timeout
    while time.time() < deadline:
        gti = GTI()
        gti.cbSize = ctypes.sizeof(GTI)
        if user32.GetGUIThreadInfo(tid, ctypes.byref(gti)):
            f = gti.hwndFocus
            if f:
                p = f
                for _ in range(16):   # фокус может быть у потомка
                    if not p:
                        break
                    if p == hwnd:
                        return True
                    p = user32.GetParent(p)
        time.sleep(0.02)
    return True


def exec_press_keys(api, p, inputs, emit):
    api.check_stop()
    from pynput import keyboard as kb
    tokens = parse_combo_tokens(p["combo"])
    keys = [token_to_pynput(t) for t in tokens]
    mode = p["mode"]
    ctl = None
    down = []
    try:
        with api.inject():
            ctl = kb.Controller()
            is_alt = "alt" in tokens
            if mode == "tap" and is_alt and "tab" in tokens:
                # БЫСТРЫЙ путь: прямое переключение окна системным вызовом —
                # мгновенно, без оверлея и пауз. Ждём факта смены фокуса.
                hwnd_new = _win_switch_next_window()
                if hwnd_new:
                    _wait_focus_ready(hwnd_new)   # окно готово принимать ввод
                else:
                    # запасной путь: эмуляция клавиш с ожиданием оверлея
                    ctl.press(keys[0])        # alt
                    down.append(keys[0])
                    for k in keys[1:]:        # tab (и остальные, если есть)
                        api.check_stop()
                        ctl.press(k)
                        down.append(k)
                        time.sleep(0.03)
                    _alt_tab_overlay_appeared()
                    time.sleep(0.12)
                    for k in reversed(keys):  # tab, alt — alt последним
                        api.check_stop()
                        ctl.release(k)
                        if k in down:
                            down.remove(k)
                        time.sleep(0.04)
                    if not _alt_tab_overlay_gone():
                        time.sleep(0.1)
                    time.sleep(0.15)
            else:
                user32k = ctypes.windll.user32
                if mode == "tap":
                    # БЫСТРЫЙ путь: ctrl вниз -> пауза -> факт-проверка ->
                    # буква батчем. Вклинивание физического ввода между
                    # нашими клавишами системой не допускается внутри батчей,
                    # а удержание даёт приложению время увидеть модификатор.
                    for attempt in range(3):
                        fg0 = user32k.GetForegroundWindow()
                        sent = _send_combo_hold(keys)
                        if sent and user32k.GetForegroundWindow() == fg0:
                            time.sleep(0.02)   # приёмнику прожевать батч
                            api.check_stop()
                            api.log("info", "press_keys %s: ok -> «%s» (попытка %d)"
                                    % (p["combo"], _fg_title(), attempt + 1))
                            emit("out", None)
                            return
                        api.log("warn", "press_keys %s: попытка %d не прошла "
                                "(send=%s, окно «%s»)"
                                % (p["combo"], attempt + 1, sent, _fg_title()))
                        time.sleep(0.12)   # фокус мигнул/батч не принят — заново
                    api.log("warn", "press_keys %s: 3 попытки не прошли, "
                            "иду по-клавишному путю (окно «%s»)"
                            % (p["combo"], _fg_title()))
                for attempt in range(3):
                    fg0 = user32k.GetForegroundWindow()
                    if mode in ("tap", "down"):
                        for k in keys:
                            api.check_stop()
                            ctl.press(k)
                            down.append(k)
                            # модификатору нужно время; alt держим как
                            # человек, чтобы оверлей не залипал
                            time.sleep(0.22 if (is_alt and k is not keys[-1]) else 0.02)
                            _ensure_key_down(ctl, k)   # ctrl реально зажат?
                    # фокус не «мигнул» посреди сочетания? если мигнул — ctrl
                    # остался в очереди СТАРОГО окна (GetKeyState потоковый);
                    # отпускаем всё и повторяем сочетание целиком
                    if attempt >= 2 or mode not in ("tap", "down"):
                        break
                    if user32k.GetForegroundWindow() == fg0:
                        break
                    for k in reversed(down):
                        try:
                            ctl.release(k)
                        except Exception:
                            pass
                    down = []
                    time.sleep(0.12)
                if mode in ("tap", "up"):
                    time.sleep((0.45 if is_alt else 0.02) if mode == "tap" else 0)
                    for k in reversed(keys):
                        api.check_stop()
                        ctl.release(k)
                        if k in down:
                            down.remove(k)
                        time.sleep(0.02)
                # Settle-пауза: система (особенно оверлей Alt+Tab) закрывает
                # выбор ПОСЛЕ отпускания alt — даём ей доделать это ДО сигнала
                # «out» следующей ноде.
                if mode in ("tap", "up") and is_alt:
                    if not _alt_tab_overlay_gone():
                        time.sleep(0.1)   # оверлей не найден/не закрылся — хвост
                    time.sleep(0.15)      # запас на Apply/переключение
                elif mode == "tap":
                    time.sleep(0.02)   # хвост; надёжность даёт факт-проверка
    except BaseException:
        # «Стоп»/ошибка посреди сочетания: отпустить всё зажатое,
        # иначе alt/ctrl остаются зажатыми в системе (залипание)
        if ctl is not None and down:
            try:
                for k in reversed(down):
                    ctl.release(k)
            except Exception:
                pass
        raise
    emit("out", None)


PRESS_NODE = N(
    "core/press_keys", "Press keys", "Нажать сочетание клавиш", "keyboard",
    inputs=(EVENT_IN,), outputs=(("out", "event"),),
    params=[
        P("combo", "hotkey", "shift", "Keys", "Клавиши"),
        P("mode", "combo", "tap", "Mode", "Режим", ["tap", "down", "up"]),
    ],
    desc={"en": "Emulates keys: tap (press+release), down (hold), up (release). E.g. hold shift or send ctrl+s.",
          "ru": "Эмулирует клавиши: tap (нажать-отпустить), down (зажать), up (отпустить). Например зажать shift или послать ctrl+s."},
)
PRESS_NODE["exec"] = exec_press_keys


def exec_type_text(api, p, inputs, emit):
    api.check_stop()
    if "in" not in inputs:
        return  # пришёл только провод текста — события ещё не было
    from pynput import keyboard as kb
    text = inputs.get("text")
    if text is None:
        text = p["text"]
    text = str(text or "")
    interval = float(p.get("interval", 0) or 0)
    with api.inject():
        ctl = kb.Controller()
        if interval <= 0:
            ctl.type(text)
        else:
            for ch in text:
                api.check_stop()
                ctl.type(ch)
                time.sleep(interval)
    emit("out", None)


TYPE_NODE = N(
    "core/type_text", "Type text", "Напечатать текст", "keyboard",
    inputs=(EVENT_IN, ("text", "any")), outputs=(("out", "event"),),
    params=[
        P("text", "string", "", "Text", "Текст"),
        P("interval", "number", 0, "Delay, sec (0 = fast)", "Пауза, сек (0 = быстро)"),
    ],
    desc={"en": "Types text into the focused window.", "ru": "Печатает текст в активное окно."},
)
TYPE_NODE["exec"] = exec_type_text


# --------------------------------------------------------------------------
# Экран
# --------------------------------------------------------------------------

def exec_screenshot(api, p, inputs, emit):
    api.check_stop()
    if "in" not in inputs:
        return  # пришёл только провод области — события ещё не было
    if inputs.get("x") is not None:
        p = dict(p, x=inputs["x"], region="area")
    if inputs.get("y") is not None:
        p = dict(p, y=inputs["y"], region="area")
    if inputs.get("w") is not None:
        p = dict(p, w=inputs["w"], region="area")
    if inputs.get("h") is not None:
        p = dict(p, h=inputs["h"], region="area")
    save_to = inputs.get("path")
    if save_to:
        save_to = str(save_to)
        if save_to.lower().endswith(".png"):
            path = save_to
            out_dir = os.path.dirname(save_to)
        else:
            out_dir = save_to
            path = os.path.join(out_dir, "shot_%s_%s.png"
                                % (time.strftime("%Y%m%d_%H%M%S"), uuid.uuid4().hex[:4]))
    else:
        out_dir = os.path.join(api.app_dir, "screenshots")
        path = os.path.join(out_dir, "shot_%s_%s.png"
                            % (time.strftime("%Y%m%d_%H%M%S"), uuid.uuid4().hex[:4]))
    os.makedirs(out_dir or ".", exist_ok=True)
    region = p.get("region", "full")
    try:
        import mss
        import mss.tools
        with mss.mss() as sct:
            if region == "area":
                monitor = {"left": int(p["x"]), "top": int(p["y"]),
                           "width": int(p["w"]), "height": int(p["h"])}
            elif region == "primary":
                monitor = sct.monitors[1]
            else:
                monitor = sct.monitors[0]
            img = sct.grab(monitor)
            mss.tools.to_png(img.rgb, img.size, output=path)
    except ImportError:
        # mss недоступен — запасной путь через Pillow
        from PIL import ImageGrab
        if region == "area":
            box = (int(p["x"]), int(p["y"]),
                   int(p["x"] + p["w"]), int(p["y"] + p["h"]))
            img = ImageGrab.grab(bbox=box)
        else:
            img = ImageGrab.grab(all_screens=(region != "primary"))
        img.save(path)
    api.log("Скриншот сохранён: %s" % path)
    emit("out", None)


SCREENSHOT_NODE = N(
    "core/screenshot", "Screenshot", "Скриншот", "screen",
    inputs=(EVENT_IN, ("x", "number"), ("y", "number"), ("w", "number"), ("h", "number"),
            ("path", "string")),
    outputs=(("out", "event"),),
    params=[
        P("region", "combo", "full", "Region", "Область",
          ["full", "primary", "area"]),
        P("x", "number", 0, "X", "X"),
        P("y", "number", 0, "Y", "Y"),
        P("w", "number", 800, "Width", "Ширина"),
        P("h", "number", 600, "Height", "Высота"),
    ],
    desc={"en": "Takes a screenshot into the screenshots/ folder, or into the path from the 'path' wire (folder or .../*.png).",
          "ru": "Делает скриншот в папку screenshots/, либо по пути из провода «path» (папка или .../*.png)."},
)
SCREENSHOT_NODE["exec"] = exec_screenshot


# --------------------------------------------------------------------------
# Переменные
# --------------------------------------------------------------------------

def exec_set_variable(api, p, inputs, emit):
    api.check_stop()
    if "in" not in inputs:
        return  # пришёл только провод value — события ещё не было
    v = inputs.get("value")
    api.set_var(p["name"], v, "global")
    api.log("Переменная %s = %r" % (p["name"], v))
    emit("value", v)
    emit("out", None)


SET_VAR_NODE = N(
    "core/set_variable", "Set variable", "Переменная: записать", "variables",
    inputs=(EVENT_IN, ("value", "any")), outputs=(("out", "event"), ("value", "any")),
    params=[P("name", "string", "A", "Name", "Название переменной")],
    desc={"en": "Stores whatever comes through the 'value' wire (any type: int, text, position...). Passes the value through.",
          "ru": "Сохраняет всё, что пришло проводом «value» (любой тип: int, текст, позиция...). Значение проходит насквозь."},
)
SET_VAR_NODE["exec"] = exec_set_variable


def exec_get_variable(api, p, inputs, emit):
    api.check_stop()
    v = api.get_var(p["name"], "global")
    emit("value", v)
    emit("out", None)   # цепочка после «прочитать» продолжается


GET_VAR_NODE = N(
    "core/get_variable", "Get variable", "Переменная: прочитать", "variables",
    inputs=(EVENT_IN,), outputs=(("out", "event"), ("value", "any")),
    params=[P("name", "string", "A", "Name", "Название переменной")],
    desc={"en": "Reads a variable (stored between all chains of the workflow). Outputs whatever was saved: int, text, position...",
          "ru": "Читает переменную (общую для всех цепочек workflow). На выходе то, что сохранили: int, текст, позиция..."},
)
GET_VAR_NODE["exec"] = exec_get_variable


# --------------------------------------------------------------------------
# Логика
# --------------------------------------------------------------------------

def _to_num(v):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Система
# --------------------------------------------------------------------------

def exec_wait(api, p, inputs, emit):
    if "in" not in inputs:
        return  # пришёл только провод seconds — события ещё не было
    sec = inputs.get("seconds")
    if sec is None:
        sec = p["seconds"]
    total = max(0.0, float(sec))
    end = time.time() + total
    while True:
        api.check_stop()
        remain = end - time.time()
        if remain <= 0:
            break
        time.sleep(min(0.05, remain))
    emit("out", None)


WAIT_NODE = N(
    "core/wait", "Wait", "Подождать", "system",
    inputs=(EVENT_IN, ("seconds", "number")), outputs=(("out", "event"),),
    params=[P("seconds", "number", 5, "Seconds", "Секунд")],
    desc={"en": "Pauses for N seconds before continuing the chain.",
          "ru": "Пауза N секунд перед продолжением цепочки."},
)
WAIT_NODE["exec"] = exec_wait


def exec_run_program(api, p, inputs, emit):
    api.check_stop()
    path = str(p["path"] or "").strip()
    if not path:
        raise ValueError("Укажите путь к программе")
    args = str(p["args"] or "").strip()
    cmd = '"%s"' % path + ((" " + args) if args else "")
    flags = 0x08000000 if p.get("hidden") else 0   # CREATE_NO_WINDOW
    proc = subprocess.Popen(cmd, shell=False, creationflags=flags,
                            cwd=os.path.dirname(path) or None)
    if p.get("wait"):
        while True:
            api.check_stop()
            try:
                code = proc.wait(timeout=0.2)
                api.log("Программа завершилась, код %s" % code)
                break
            except subprocess.TimeoutExpired:
                continue
    emit("out", None)


RUN_PROG_NODE = N(
    "core/run_program", "Run program", "Запустить программу", "system",
    inputs=(EVENT_IN,), outputs=(("out", "event"),),
    params=[
        P("path", "string", "", "Path (exe)", "Путь (exe)"),
        P("args", "string", "", "Arguments", "Аргументы"),
        P("wait", "bool", False, "Wait for exit", "Ждать завершения"),
        P("hidden", "bool", False, "Hide console window", "Скрыть окно консоли"),
    ],
    desc={"en": "Launches an executable.", "ru": "Запускает исполняемый файл."},
)
RUN_PROG_NODE["exec"] = exec_run_program


def exec_log(api, p, inputs, emit):
    api.check_stop()
    if "in" not in inputs:
        return  # пришёл только провод сообщения — события ещё не было
    msg = inputs.get("message")
    if msg is None:
        msg = p["message"]
    api.log(str(msg or ""))
    emit("out", None)


LOG_NODE = N(
    "core/log", "Log", "В лог", "system",
    inputs=(EVENT_IN, ("message", "any")), outputs=(("out", "event"),),
    params=[P("message", "string", "Сработало!", "Message", "Сообщение")],
    desc={"en": "Writes a message into the console and the UI log.",
          "ru": "Пишет сообщение в консоль и в журнал интерфейса."},
)
LOG_NODE["exec"] = exec_log


def exec_clipboard_set(api, p, inputs, emit):
    api.check_stop()
    if "in" not in inputs:
        return  # пришёл только провод текста — события ещё не было
    text = inputs.get("text")
    if text is None:
        text = p["text"]
    text = "" if text is None else str(text)
    b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
    cmd = ("[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('%s')) | Set-Clipboard" % b64)
    subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                   capture_output=True, timeout=15)
    emit("out", None)


CLIP_SET_NODE = N(
    "core/clipboard_set", "Clipboard: set", "Буфер: записать", "system",
    inputs=(EVENT_IN, ("text", "any")), outputs=(("out", "event"),),
    params=[P("text", "string", "", "Text", "Текст")],
    desc={"en": "Puts text into the clipboard. The 'text' wire overrides the parameter.",
          "ru": "Кладёт текст в буфер обмена. Провод «text» подменяет параметр."},
)
CLIP_SET_NODE["exec"] = exec_clipboard_set


def exec_clipboard_get(api, p, inputs, emit):
    api.check_stop()
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Clipboard -Raw"],
        capture_output=True, timeout=15)
    text = r.stdout.decode("utf-8", errors="replace")
    emit("text", text)
    emit("out", None)


CLIP_GET_NODE = N(
    "core/clipboard_get", "Clipboard: get", "Буфер: прочитать", "system",
    inputs=(EVENT_IN,), outputs=(("out", "event"), ("text", "string")),
    desc={"en": "Reads text from the clipboard.", "ru": "Читает текст из буфера обмена."},
)
CLIP_GET_NODE["exec"] = exec_clipboard_get


# --------------------------------------------------------------------------
# Значения / интервал-переключатель
# --------------------------------------------------------------------------

def exec_number(api, p, inputs, emit):
    api.check_stop()
    v = inputs.get("value")
    if v is None:
        v = p["value"]
    emit("int", v)


NUMBER_NODE = N(
    "core/number", "Number / int", "Число / int", "variables",
    inputs=(("value", "number"),),
    outputs=(("int", "number"),),
    params=[P("value", "number", 1, "Value", "Значение")],
    desc={"en": "Constant number. Wire it into 'seconds', 'x', 'y' and other numeric inputs (like ComfyUI constants).",
          "ru": "Постоянное число. Подключайте проводом к «seconds», «x», «y» и другим числовым входам — как константы в ComfyUI."},
)
NUMBER_NODE["exec"] = exec_number
NUMBER_NODE["constant"] = True   # движок выдаёт значение один раз при старте


def exec_text(api, p, inputs, emit):
    api.check_stop()
    v = inputs.get("text")
    if v is None:
        v = p["text"]
    emit("text", v)


TEXT_NODE = N(
    "core/text", "Text (string)", "Текст (String)", "variables",
    inputs=(("text", "string"),),
    outputs=(("text", "string"),),
    params=[dict(P("text", "string", "", "Text", "Текст"), multiline=True)],
    desc={"en": "Constant text. Wire it into 'message', 'text', 'url' and other text inputs. Also passes an incoming 'text' wire through.",
          "ru": "Постоянный текст. Подключайте проводом к «message», «text», «url» и другим текстовым входам. Пришедший провод «text» проходит насквозь."},
)
TEXT_NODE["exec"] = exec_text
TEXT_NODE["constant"] = True     # движок выдаёт значение один раз при старте


def exec_pos_decode(api, p, inputs, emit):
    api.check_stop()
    v = inputs.get("position")
    if v is None:
        return
    try:
        x, y = _coerce_pos(v)
    except (ValueError, TypeError):
        return
    emit("x", x)
    emit("y", y)


POS_DECODE_NODE = N(
    "core/pos_decode", "Decode position", "Разбор позиции", "variables",
    inputs=(("position", "pos"),),
    outputs=(("x", "number"), ("y", "number")),
    params=[],
    desc={"en": "Splits a position (e.g. from 'Mouse position') into numeric x and y.",
          "ru": "Раскладывает позицию (например, от «Позиции мыши») на числа x и y."},
)
POS_DECODE_NODE["exec"] = exec_pos_decode


def exec_interval_toggle(api, p, inputs, emit):
    """Первое срабатывание включает интервал, второе выключает."""
    from core.engine import Cancelled
    api.check_stop()
    if "in" not in inputs:
        return  # пришёл только провод seconds — события ещё не было
    st = api.node_state()
    lock = api.state_lock()
    with lock:
        if st.get("ev") is not None:
            st["ev"].set()
            st["ev"] = None
            st.pop("thread", None)
            api.log("Интервал-переключатель: ВЫКЛ")
            return
    sec = inputs.get("seconds")
    if sec is None:
        sec = p["seconds"]
    try:
        sec = max(0.1, float(sec))
    except (TypeError, ValueError):
        sec = 5.0
    ev = threading.Event()
    with lock:
        st["ev"] = ev

    def loop():
        while not ev.wait(sec):
            try:
                api.check_stop()
            except Cancelled:
                break
            emit("out", None)
        with lock:
            if st.get("ev") is ev:
                st["ev"] = None
                st.pop("thread", None)

    t = threading.Thread(target=loop, daemon=True)
    with lock:
        st["thread"] = t
    t.start()
    api.log("Интервал-переключатель: ВКЛ (каждые %.1f сек)" % sec)


INTERVAL_TOGGLE_NODE = N(
    "core/interval_toggle", "Interval: on/off", "Интервал-переключатель", "triggers",
    inputs=(EVENT_IN, ("seconds", "number")),
    outputs=(("out", "event"),),
    params=[P("seconds", "number", 5, "Every, sec", "Каждые, сек")],
    trigger=True,
    desc={"en": "First trigger press starts the interval ('out' fires every N sec), second press stops it. The 'seconds' wire overrides the parameter.",
          "ru": "Первое нажатие сочетания включает интервал (выход «out» срабатывает каждые N сек), второе — выключает. Провод «seconds» подменяет параметр."},
)
INTERVAL_TOGGLE_NODE["exec"] = exec_interval_toggle


# --------------------------------------------------------------------------
# Превью (перенесено из плагина preview)
# --------------------------------------------------------------------------

def _to_text(v):
    """int/bool -> строка, dict с position -> "x y", float без хвостовых нулей."""
    import json as _json
    if v is None:
        return ""
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, dict):
        if "x" in v and "y" in v:
            return "%s %s" % (_to_text(v.get("x")), _to_text(v.get("y")))
        try:
            return _json.dumps(v, ensure_ascii=False)
        except Exception:
            return str(v)
    if isinstance(v, (list, tuple)):
        return " ".join(_to_text(x) for x in v)
    return str(v)

def exec_preview(api, p, inputs, emit):
    api.check_stop()
    eng = api.engine
    # Превью обновляется ТОЛЬКО когда в этой волне пришёл сигнал в белый «in».
    # Данные, пришедшие сами по себе (константы на старте), рендер не будят —
    # они подхватятся ближайшим событием (значения копятся движком).
    if "in" not in inputs:
        return
    # порядок входов — по слотам ноды (in, in1, in2...), значения по именам.
    # Слот 0 («in») — событие: источник события показываем, только если данных нет.
    node = api.node
    src = inputs.get("__src") or {}
    lines = []          # строки значений «Имя: значение»
    from_line = None    # событийный вход (слот 0): «Из: ИмяНоды»
    for i, o in enumerate(node.get("inputs") or []):
        name = o.get("name")
        if name not in inputs:
            continue
        v = inputs.get(name)
        if i == 0:
            if v is not None:
                # событие со значением (например True/False из «Условий»):
                # печатаем как строку «Имя: значение», как обычные данные
                lines.append("%s: %s" % (src.get(name) or "?", _to_text(v)))
            else:
                # чистое событие без значения — фиксируем источник
                from_line = "Из: %s" % (src.get(name) or "—")
            continue
        if v is None:
            continue   # событие без данных не печатаем
        lines.append("%s: %s" % (src.get(name) or "?", _to_text(v)))
    if not lines:
        if from_line:
            # пришло чистое событие (без данных) — показываем его источник
            lines.append(from_line)
        else:
            return
    eng.node_preview(api.node.get("id"), lines)
    emit("out", None)   # можно ставить в середину цепочки


PREVIEW_NODE = {
    # был "preview/show" (отдельный плагин), нода переехала в core_nodes:
    # новый id "core/preview", старые сохранения мигрирует веб-санитайзер
    "id": "core/preview",
    "title": {"en": "Preview", "ru": "Превью"},
    "desc": {
        "en": "Shows incoming values as text right on the canvas. Supports int, text, bool, positions.",
        "ru": "Показывает значения входов текстом прямо на холсте. Поддерживает числа, текст, bool, позиции.",
    },
    "category": "logic",
    # слот 0 — «in»: событийный (белый), принимает только линии из выходов «out»;
    # слоты данных нумеруются с in1 (веб-сторона нормализует старые сохранения)
    "inputs": [{"name": "in", "type": "event"},
               {"name": "in1", "type": "any"}],
    "outputs": [{"name": "out", "type": "event"}],
    "params": [],
}
PREVIEW_NODE["exec"] = exec_preview


# нода переехала в core_nodes: держим алиас для старых сохранений, пока
# веб-санитайзер не мигрирует их на новый id
LEGACY_PREVIEW_ID = "preview/show"
PREVIEW_NODE_LEGACY = dict(PREVIEW_NODE)
PREVIEW_NODE_LEGACY["id"] = LEGACY_PREVIEW_ID
PREVIEW_NODE_LEGACY["hidden"] = True   # не показывать в поиске

# --------------------------------------------------------------------------

NODES = [
    HOTKEY_NODE,
    MOUSE_TRIG_NODE,
    STARTUP_NODE,
    INTERVAL_NODE,
    INTERVAL_TOGGLE_NODE,
    CLICK_NODE,
    MOVE_NODE,
    MOUSE_POS_NODE,
    SCROLL_NODE,
    DRAG_NODE,
    PRESS_NODE,
    TYPE_NODE,
    SCREENSHOT_NODE,
    SET_VAR_NODE,
    GET_VAR_NODE,
    NUMBER_NODE,
    TEXT_NODE,
    WAIT_NODE,
    RUN_PROG_NODE,
    LOG_NODE,
    CLIP_SET_NODE,
    CLIP_GET_NODE,
    POS_DECODE_NODE,
    PREVIEW_NODE,
    PREVIEW_NODE_LEGACY,   # алиас preview/show: движок поймёт старые схемы
]


def register(api):
    for nd in NODES:
        api.add_node(nd)
