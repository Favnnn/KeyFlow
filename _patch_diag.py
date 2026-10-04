# -*- coding: utf-8 -*-
"""press_keys: диагностика для последних 2/50 «м».

Каждое модификаторное сочетание пишет в лог: окно-приёмник (заголовок),
результат SendInput, число попыток. Следующий промах оставит след,
по которому видно, каким путём шло сочетание и что было с фокусом.
"""
import io

P = r"C:\Users\Vad\Desktop\ТТесты2\Hotkey\plugins\core_nodes\plugin.py"
src = io.open(P, encoding="utf-8").read()

def rep(old, new, tag):
    global src
    if old in src:
        src = src.replace(old, new, 1)
        print("OK:", tag)
    else:
        print("FAIL:", tag)
        raise SystemExit(1)

# 1) хелпер заголовка окна
rep(
    "def _key_vk(k):\n",
    "def _fg_title():\n"
    "    \"\"\"Заголовок переднего окна для лога (коротко, без паники при ошибках).\"\"\"\n"
    "    try:\n"
    "        u = ctypes.windll.user32\n"
    "        h = u.GetForegroundWindow()\n"
    "        buf = ctypes.create_unicode_buffer(128)\n"
    "        u.GetWindowTextW(h, buf, 128)\n"
    "        return (buf.value or \"?\")[:60]\n"
    "    except Exception:\n"
    "        return \"?\"\n"
    "\n"
    "\n"
    "def _key_vk(k):\n",
    "fg title helper")

# 2) лог в атомарном пути
rep(
    "                if mode == \"tap\" and not is_alt:\n"
    "                    # АТОМАРНЫЙ путь: ctrl+v/enter/ctrl+s одним батчем —\n"
    "                    # вклинивание физического ввода невозможно по определению\n"
    "                    for attempt in range(3):\n"
    "                        fg0 = user32k.GetForegroundWindow()\n"
    "                        if _send_combo_atomic(keys) and \\\n"
    "                                user32k.GetForegroundWindow() == fg0:\n"
    "                            time.sleep(0.02)   # приёмнику прожевать батч\n"
    "                            api.check_stop()\n"
    "                            emit(\"out\", None)\n"
    "                            return\n"
    "                        time.sleep(0.12)   # фокус мигнул/батч не принят — заново\n"
    "                    # SendInput не прошёл — падаем в обычный путь ниже\n",
    "                if mode == \"tap\" and not is_alt:\n"
    "                    # АТОМАРНЫЙ путь: ctrl+v/enter/ctrl+s одним батчем —\n"
    "                    # вклинивание физического ввода невозможно по определению\n"
    "                    for attempt in range(3):\n"
    "                        fg0 = user32k.GetForegroundWindow()\n"
    "                        sent = _send_combo_atomic(keys)\n"
    "                        if sent and user32k.GetForegroundWindow() == fg0:\n"
    "                            time.sleep(0.02)   # приёмнику прожевать батч\n"
    "                            api.check_stop()\n"
    "                            api.log(\"info\", \"press_keys %s: атомарно -> «%s» (попытка %d)\"\n"
    "                                    % (p[\"combo\"], _fg_title(), attempt + 1))\n"
    "                            emit(\"out\", None)\n"
    "                            return\n"
    "                        api.log(\"warn\", \"press_keys %s: попытка %d не прошла \"\n"
    "                                \"(send=%s, фокус мигнул=%s), окно «%s»\"\n"
    "                                % (p[\"combo\"], attempt + 1, sent,\n"
    "                                   sent, _fg_title()))\n"
    "                        time.sleep(0.12)   # фокус мигнул/батч не принят — заново\n"
    "                    api.log(\"warn\", \"press_keys %s: 3 попытки не прошли, \"\n"
    "                            \"иду по-клавишным путём (окно «%s»)\"\n"
    "                            % (p[\"combo\"], _fg_title()))\n",
    "atomic logging")

io.open(P, "w", encoding="utf-8", newline="").write(src)
print("DONE")
