# -*- coding: utf-8 -*-
"""Глобальные слушатели клавиатуры и мыши (pynput) + движок сочетаний.

Особенности:
  * поддерживает «необычные» сочетания — несколько одновременно зажатых
    букв/цифр (например a+s+d) и обычные модификаторные (ctrl+shift+s);
  * срабатывание — в момент, когда зажат последний ключ сочетания;
    повторное срабатывание возможно только после отпускания;
  * пока KeyFlow сам эмулирует нажатия (нода «Нажать клавиши», клики),
    слушатели игнорируют события, чтобы программа не будила сама себя.
"""
import threading
import time

from pynput import keyboard, mouse

MOD_ALIASES = {
    "ctrl_l": "ctrl", "ctrl_r": "ctrl", "control_l": "ctrl", "control_r": "ctrl",
    "alt_l": "alt", "alt_r": "alt", "alt_gr": "alt",
    "shift_l": "shift", "shift_r": "shift",
    "cmd_l": "win", "cmd_r": "win", "cmd": "win",
}
KEY_ALIASES = {
    "escape": "esc", "page_up": "pageup", "page_down": "pagedown",
    "print_screen": "printscreen", "caps_lock": "capslock",
    "scroll_lock": "scrolllock", "num_lock": "numlock",
    "ctrl": "ctrl", "control": "ctrl", "alt": "alt", "shift": "shift",
}

MOD_ORDER = ("ctrl", "alt", "shift", "win")


def normalize_token(name):
    name = (name or "").strip().lower()
    if not name:
        return None
    name = MOD_ALIASES.get(name, name)
    name = KEY_ALIASES.get(name, name)
    return name


def token_from_key(key):
    """pynput key -> наш токен ('a', 'ctrl', 'f5', 'space', ...)."""
    if isinstance(key, keyboard.KeyCode):
        ch = key.char
        if ch:
            o = ord(ch[0])
            if o < 32:
                # Ctrl+буква в Windows приходит УПРАВЛЯЮЩИМ символом
                # (Ctrl+V = \x16): берём физическую клавишу по vk
                pass
            elif o < 127:
                return ch.lower()          # обычная латиница/цифра
            # НЕ-ASCII (русская раскладка «ь», «м»...) — берём физическую
            # клавишу по vk, чтобы хоткей был одним и тем же на любой раскладке
        vk = getattr(key, "vk", None)
        if vk and 32 <= vk < 127:
            return chr(vk).lower()
        return ch.lower() if ch else None
    return normalize_token(getattr(key, "name", None))


def parse_combo_tokens(combo_str):
    """'ctrl+shift+s' | 'a+s+d' -> упорядоченный список токенов."""
    tokens = []
    for part in str(combo_str or "").split("+"):
        t = normalize_token(part)
        if t:
            tokens.append(t)
    if not tokens:
        raise ValueError("Пустое сочетание клавиш")
    return tokens


def parse_combo_set(combo_str):
    return frozenset(parse_combo_tokens(combo_str))


def token_to_pynput(token):
    """Токен -> pynput key (для ноды «Нажать клавиши»)."""
    token = normalize_token(token)
    named = {
        "space": "space", "enter": "enter", "tab": "tab", "esc": "esc",
        "backspace": "backspace", "delete": "delete", "insert": "insert",
        "home": "home", "end": "end", "pageup": "page_up", "pagedown": "page_down",
        "up": "up", "down": "down", "left": "left", "right": "right",
        "printscreen": "print_screen", "capslock": "caps_lock",
        "scrolllock": "scroll_lock", "numlock": "num_lock", "pause": "pause",
        "ctrl": "ctrl", "alt": "alt", "shift": "shift", "win": "cmd",
    }
    if token in named:
        return getattr(keyboard.Key, named[token])
    if len(token) == 1:
        # ВАЖНО: не from_char()! Он шлёт символ как unicode (VK_PACKET),
        # и модификаторы (ctrl/alt) с ним НЕ склеиваются в системе —
        # вместо ctrl+v приложение получает голое «v». Шлём настоящую
        # клавишу по vk-коду (VK_A..VK_Z, VK_0..VK_9).
        if ("a" <= token <= "z") or ("0" <= token <= "9"):
            return keyboard.KeyCode(vk=ord(token.upper()), char=token)
        return keyboard.KeyCode.from_char(token)   # прочие символы как есть
    if token.startswith("f") and token[1:].isdigit():
        return getattr(keyboard.Key, token)  # f1..f24
    if token.startswith("vk") and token[2:].isdigit():
        return keyboard.KeyCode(vk=int(token[2:]))
    raise ValueError("Неизвестная клавиша: «%s»" % token)


def canonical_combo(tokens):
    mods = [t for t in tokens if t in MOD_ORDER]
    rest = [t for t in tokens if t not in MOD_ORDER]
    return "+".join(sorted(mods, key=MOD_ORDER.index) + rest)


class HotkeyService:
    """Слушает клавиатуру/мышь глобально и сверяет зарегистрированные сочетания."""

    def __init__(self, engine):
        self.engine = engine
        self.lock = threading.RLock()
        self.combos = {}          # node_id -> frozenset(tokens)
        self.mouse_binds = {}     # node_id -> (button, action)
        self._last_click = {}     # (node_id, button) -> ts
        self.pressed = set()
        self.fired = set()
        self.injection_until = 0.0
        self._inj_depth = 0       # динамическое подавление: пока нода жмёт клавиши
        self._inj_lock = threading.Lock()
        self._capture = None      # режим записи хоткея: {"tokens": [...], "pressed": set()}
        self._kb = None
        self._ms = None

    # -- регистрация ---------------------------------------------------------
    def register_combo(self, node_id, combo_str):
        fs = parse_combo_set(combo_str)
        with self.lock:
            self.combos[node_id] = fs

    def register_mouse(self, node_id, button, action):
        button = str(button or "middle").lower()
        action = str(action or "click").lower()
        if button not in ("left", "right", "middle", "x1", "x2"):
            raise ValueError("Неизвестная кнопка мыши: %s" % button)
        if action not in ("click", "double"):
            raise ValueError("Неизвестное действие: %s" % action)
        with self.lock:
            self.mouse_binds[node_id] = (button, action)

    def unregister(self, node_id):
        with self.lock:
            self.combos.pop(node_id, None)
            self.mouse_binds.pop(node_id, None)

    # -- жизненный цикл -------------------------------------------------------
    def start(self):
        self._kb = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._kb.start()
        self._ms = mouse.Listener(on_click=self._on_click)
        self._ms.start()

    def stop(self):
        for l in (self._kb, self._ms):
            try:
                if l:
                    l.stop()
            except Exception:
                pass
        self._kb = self._ms = None
        with self.lock:
            self.combos.clear()
            self.mouse_binds.clear()
            self.pressed.clear()
            self.fired.clear()

    def begin_injection(self, sec=0.8):
        self.injection_until = time.time() + sec

    # -- динамическое подавление ----------------------------------------------
    def injection_begin(self):
        """Нода начинает эмуляцию: триггеры молчат, пока она не закончит."""
        with self._inj_lock:
            self._inj_depth += 1
        self.injection_until = time.time() + 0.5  # и продлевается ниже

    def injection_ping(self):
        """Нода всё ещё жмёт (долгая эмуляция): продлить окно молчания."""
        with self._inj_lock:
            if self._inj_depth:
                self.injection_until = time.time() + 0.5

    def injection_end(self):
        with self._inj_lock:
            self._inj_depth = max(0, self._inj_depth - 1)
        # окно истечёт само через <=0.5 с — хвост синтетики не проскочит

    # -- режим записи хоткея (сервер) ------------------------------------------
    def capture_start(self):
        """Начать запись: слушатель копит токены, HTTP-опрос забирает.

        Глобальный слушатель видит даже Alt+Tab (браузер — нет).
        """
        with self._inj_lock:
            if self._capture:
                return False
            self._capture = {"tokens": [], "pressed": set()}
            return True

    def capture_poll(self):
        """Забрать текущее состояние записи (токены в порядке нажатия)."""
        with self._inj_lock:
            cap = self._capture
            if not cap:
                return None
            return list(cap["tokens"]), bool(cap["pressed"])

    def capture_cancel(self):
        with self._inj_lock:
            self._capture = None

    def _suppressed(self):
        return time.time() < self.injection_until

    # -- события ---------------------------------------------------------------
    def _on_press(self, key):
        try:
            token = token_from_key(key)
            if not token:
                return
            to_fire = []
            with self.lock:
                if self._capture is not None:
                    cap = self._capture
                    if token == "esc":      # Esc во время записи = сброс
                        cap["tokens"] = []
                        cap["pressed"] = set()
                        return
                    cap["pressed"].add(token)
                    if token not in cap["tokens"]:
                        cap["tokens"].append(token)
                    return   # во время записи обычные триггеры не срабатывают
                if self._suppressed():
                    return
                self.pressed.add(token)
                for nid, combo in self.combos.items():
                    if combo <= self.pressed and nid not in self.fired:
                        self.fired.add(nid)
                        to_fire.append(nid)
            for nid in to_fire:
                self.engine.fire(nid, {"out": None})
        except Exception:
            pass  # слушатель не должен никогда падать

    def _on_release(self, key):
        try:
            token = token_from_key(key)
            if not token:
                return
            with self.lock:
                if self._capture is not None:
                    self._capture["pressed"].discard(token)
                self.pressed.discard(token)
                for nid, combo in self.combos.items():
                    if token in combo and not combo <= self.pressed:
                        self.fired.discard(nid)
        except Exception:
            pass

    def _on_click(self, x, y, button, pressed):
        try:
            if not pressed:
                return
            name = getattr(button, "name", None)
            if not name:
                return
            to_fire = []
            with self.lock:
                if self._suppressed():
                    return
                now = time.time()
                for nid, (btn, action) in self.mouse_binds.items():
                    if btn != name:
                        continue
                    if action == "click":
                        to_fire.append(nid)
                    else:  # double
                        key = (nid, btn)
                        if now - self._last_click.get(key, 0) < 0.45:
                            self._last_click[key] = 0
                            to_fire.append(nid)
                        else:
                            self._last_click[key] = now
            for nid in to_fire:
                self.engine.fire(nid, {"out": None})
        except Exception:
            pass
