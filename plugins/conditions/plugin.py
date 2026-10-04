# -*- coding: utf-8 -*-
"""KeyFlow «Условия» — ноды-ветвления.

У КАЖДОЙ ноды ДВА выхода:
  * true  — срабатывает при положительном результате;
  * false — срабатывает при отрицательном.

Параметр «Ждать» (переключатель, оба значения выбрать нельзя):
  * ничего — обычная проверка: одно измерение, событие сразу на true/false;
  * true   — ЦИКЛ: нода опрашивает условие каждые 0.25 сек, ПОКА результат
             не станет True, затем событие на «true» и цепочка идёт дальше;
  * false  — то же, но ждёт False.
    Прервать ожидание: кнопка «Стоп» или пока не выполнится условие
    (внутри цикла вызывается api.check_stop()).

Провод, подключённый к входу с именем параметра, подменяет значение
параметра (это делает сам движок — см. core/engine.run_node).
"""
import ast
import ctypes
import ctypes.wintypes as wt
import io
import os
import re
import socket
import struct
import subprocess
import sys
import threading
import time

EVENT_IN = ("in", "event")


# ---------------------------------------------------------------- каркас

def N(nid, en, ru, inputs=(), params=(), desc=None):
    return {
        "id": nid,
        "title": {"en": en, "ru": ru},
        "desc": desc or {},
        "category": "conditions",
        "inputs": [{"name": n, "type": t} for (n, t) in inputs],
        "outputs": [{"name": "true", "type": "event"},
                    {"name": "false", "type": "event"}],
        "params": list(params),
    }


def P(name, ptype, default, en, ru, options=None):
    p = {"name": name, "type": ptype, "default": default,
         "label": {"en": en, "ru": ru}}
    if options is not None:
        p["options"] = list(options)
    return p


WAIT_P = P("wait", "combo", "none", "Wait for", "Ждать",
           ["none", "true", "false"])

WAIT_HELP = ("«Ничего» — одна проверка: событие сразу на true/false. "
             "«Ждать true/false» — цикл: нода опрашивает условие каждые 0.25 с, "
             "пока результат не станет нужным, затем событие и продолжение цепочки. "
             "Прервать — кнопкой «Стоп».")


def _emit_res(emit, ok):
    """Событие на выход по результату: true или false (payload = bool).

    Значение едет по проводу: «Превью» покажет «ИмяНоды: True/False».
    """
    emit("true" if ok else "false", ok)


def _wait_result(api, emit, check_fn, wait):
    """«Ждать»: none — одна проверка; true/false — цикл опроса до результата.

    check_fn() вычисляет условие заново на каждой итерации (каждые 0.25 с),
    поэтому «ждать true» дождётся, когда условие впервые станет истинным.
    api.check_stop() внутри цикла позволяет прервать кнопкой «Стоп».
    """
    if wait not in ("true", "false"):
        _emit_res(emit, bool(check_fn()))
        return
    want = (wait == "true")
    api.log("Жду, пока условие станет %s (опрос каждые 0.25 с)" % wait)
    while True:
        api.check_stop()
        if bool(check_fn()) == want:
            _emit_res(emit, want)   # ветвь соответствует результату
            return
        time.sleep(0.25)


def _str(v):
    return "" if v is None else str(v)


# ---------------------------------------------------------------- Если

_ALLOWED = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not,
             ast.USub, ast.UAdd, ast.BinOp, ast.Add, ast.Sub, ast.Mult,
             ast.Div, ast.FloorDiv, ast.Mod, ast.Pow, ast.Compare, ast.Eq,
             ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Is, ast.IsNot,
             ast.In, ast.NotIn, ast.Load, ast.Name, ast.Constant)


def _safe_eval(expr, api):
    """Безопасное выражение: + - * / % сравнения and/or/not in.

    Имена берутся из переменных KeyFlow (сначала ветви, потом глобальные).
    Никаких функций, атрибутов и импортов — только арифметика/логика.
    """
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise ValueError("Недопустимый элемент выражения: %s"
                             % type(node).__name__)
    env = {"true": True, "false": False, "True": True, "False": False,
           "none": None, "None": None}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id not in env:
            env[node.id] = api.get_var_any(node.id)
    return eval(compile(tree, "<expr>", "eval"), {"__builtins__": {}}, env)


def exec_if(api, p, inputs, emit):
    api.check_stop()
    if "in" not in inputs:
        return
    expr = _str(p["expr"]).strip()
    if not expr:
        raise ValueError("Введите выражение, например: count > 3")
    _wait_result(api, emit, lambda: bool(_safe_eval(expr, api)), p["wait"])


IF_NODE = N(
    "conditions/if", "If", "Если",
    (EVENT_IN,), [P("expr", "string", "", "Expression: count > 3",
                    "Выражение: count > 3"), WAIT_P],
    {"en": "Evaluates a safe expression (no functions/imports). Names are KeyFlow variables. Wait: loop-poll every 0.25 s until the result is right, then continue.",
     "ru": "Вычисляет безопасное выражение (без функций/импортов). Имена — переменные KeyFlow. «Ждать»: цикл опроса каждые 0.25 с, пока результат не станет нужным, потом продолжение."},
)
IF_NODE["exec"] = exec_if


# ---------------------------------------------------------------- Switch

def parse_cases(cases):
    """Список вариантов из текста: по строке или через запятую."""
    opts = []
    for line in _str(cases).splitlines():
        for chunk in line.split(","):
            chunk = chunk.strip()
            if chunk:
                opts.append(chunk)
    return opts


# имя последнего выхода switch/case: «всё остальное» (как default в C++)
SWITCH_DEFAULT = "default"


def _uniq(lst):
    out = []
    for x in lst:
        if x not in out:
            out.append(x)
    return out


def exec_switch(api, p, inputs, emit):
    api.check_stop()
    if "in" not in inputs:
        return
    # как switch в C++: case <вариант> -> выход с именем варианта,
    # последний выход default — если ни один case не совпал
    opts = _uniq(parse_cases(p["cases"]))
    val = _str(p["value"]).strip()
    hit = -1
    if val in opts:
        hit = opts.index(val)
    else:
        for i, o in enumerate(opts):
            if _num(o) is not None and _num(o) == _num(val):
                hit = i
                break
    if 0 <= hit < len(opts):
        emit(opts[hit], val)          # case сработал
    else:
        emit(SWITCH_DEFAULT, val)     # default: всё остальное


SWITCH_NODE = N(
    "conditions/switch", "Switch / Case", "Switch / Case",
    (EVENT_IN, ("value", "any")),
    [P("value", "string", "", "Value", "Значение"),
     dict(P("cases", "string", "1\n2\n3", "Cases: one per line (output per case + default)",
            "Варианты: по строке (выход на каждый + default)"), multiline=True)],
    {"en": "Like switch in C++: one output per case (named as the case value), the last output 'default' = everything else.",
     "ru": "Как switch в C++: выход на каждый вариант (имя выхода = значение), последний выход «default» — всё остальное."},
)
SWITCH_NODE["outputs"] = [{"name": "default", "type": "event"}]
SWITCH_NODE["exec"] = exec_switch


# ---------------------------------------------------------------- Сравнить

def _num(v):
    try:
        return float(_str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _cmp(left, op, right):
    s_l, s_r = _str(left), _str(right)
    if op in ("==", "!=", ">", "<", ">=", "<="):
        nl, nr = _num(s_l), _num(s_r)
        l, r = (nl, nr) if nl is not None and nr is not None else (s_l, s_r)
        return {"==": l == r, "!=": l != r, ">": l > r, "<": l < r,
                ">=": l >= r, "<=": l <= r}[op]
    if op == "contains":
        return s_r in s_l
    if op == "not contains":
        return s_r not in s_l
    if op == "starts":
        return s_l.startswith(s_r)
    if op == "ends":
        return s_l.endswith(s_r)
    if op == "regex":
        try:
            return re.search(s_r, s_l) is not None
        except re.error:
            raise ValueError("Некорректный regex: %s" % s_r)
    if op == "empty":
        return s_l.strip() == ""
    if op == "not empty":
        return s_l.strip() != ""
    return False


OPS = ["==", "!=", ">", "<", ">=", "<=", "contains", "not contains",
       "starts", "ends", "regex", "empty", "not empty"]


def exec_compare(api, p, inputs, emit):
    api.check_stop()
    if "in" not in inputs:
        return
    _wait_result(api, emit, lambda: _cmp(p["left"], p["op"], p["right"]), p["wait"])


COMPARE_NODE = N(
    "conditions/compare", "Compare", "Сравнить переменные",
    (EVENT_IN, ("left", "any"), ("right", "any")),
    [P("left", "string", "", "Left", "Лево"),
     P("op", "combo", "==", "Operation", "Операция", OPS),
     P("right", "string", "", "Right", "Право"),
     WAIT_P],
    {"en": "Compares two values: numbers if both parse, else text. 'empty' checks the LEFT value.",
     "ru": "Сравнивает значения: числа, если оба похожи на числа, иначе текст. «empty» проверяет ЛЕВОЕ значение."},
)
COMPARE_NODE["exec"] = exec_compare


# ---------------------------------------------------------------- Файл

def _unquote_path(s):
    """Срезает парные кавычки по краям пути: «"C:\файл.txt"» -> C:\файл.txt.

    Пути часто копируются из проводника/скриптов уже в кавычках — проверка
    без чистки всегда давала false.
    """
    s = _str(s).strip()
    for q in ('"', "'"):
        if len(s) >= 2 and s.startswith(q) and s.endswith(q):
            s = s[1:-1].strip()
    return s


def exec_file(api, p, inputs, emit):
    api.check_stop()
    if "in" not in inputs:
        return
    path = _unquote_path(p["path"])
    if not path:
        raise ValueError("Укажите путь")
    if p["mode"] == "file":
        fn = os.path.isfile
    elif p["mode"] == "folder":
        fn = os.path.isdir
    else:
        fn = os.path.exists
    _wait_result(api, emit, lambda: fn(path), p["wait"])


FILE_NODE = N(
    "conditions/file_exists", "File exists", "Файл существует",
    (EVENT_IN, ("path", "string")),
    [P("path", "string", "", "Path", "Путь"),
     P("mode", "combo", "exists", "What", "Что",
       ["exists", "file", "folder"]), WAIT_P],
    {"en": "Checks a path: exists / is a file / is a folder.",
     "ru": "Проверяет путь: существует / это файл / это папка."},
)
FILE_NODE["exec"] = exec_file


# ---------------------------------------------------------------- Процессы

def _proc_snapshot():
    """{pid: имя_exe} через CreateToolhelp32Snapshot (stdlib/ctypes)."""
    k32 = ctypes.windll.kernel32

    class PE(ctypes.Structure):
        _fields_ = [("dwSize", ctypes.c_ulong), ("cntUsage", ctypes.c_ulong),
                    ("pid", ctypes.c_ulong), ("heap", ctypes.c_size_t),
                    ("modid", ctypes.c_ulong), ("threads", ctypes.c_ulong),
                    ("parent", ctypes.c_ulong), ("pri", ctypes.c_long),
                    ("flags", ctypes.c_ulong), ("exe", ctypes.c_wchar * 260)]

    snap = k32.CreateToolhelp32Snapshot(2, 0)   # TH32CS_SNAPPROCESS
    out = {}
    if snap == -1:
        return out
    e = PE()
    e.dwSize = ctypes.sizeof(PE)
    ok = k32.Process32FirstW(snap, ctypes.byref(e))
    while ok:
        out[e.pid] = e.exe or ""
        ok = k32.Process32NextW(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    return out


def _proc_running(match):
    m = _str(match).strip()
    if not m:
        return False
    procs = _proc_snapshot()
    if m.isdigit():
        return int(m) in procs
    ml = m.lower()
    return any(ml in exe.lower() for exe in procs.values())


def exec_process(api, p, inputs, emit):
    api.check_stop()
    if "in" not in inputs:
        return
    _wait_result(api, emit, lambda: _proc_running(p["name"]), p["wait"])


PROCESS_NODE = N(
    "conditions/process_running", "Process running", "Процесс запущен",
    (EVENT_IN, ("name", "string")),
    [P("name", "string", "", "Name.exe / PID / path part",
       "Имя.exe / PID / кусок пути"), WAIT_P],
    {"en": "True if a matching process exists (name.exe, PID or part of path).",
     "ru": "true, если запущен подходящий процесс (имя.exe, PID или кусок пути)."},
)
PROCESS_NODE["exec"] = exec_process


# ---------------------------------------------------------------- Активное окно

def _u32():
    return ctypes.windll.user32


def _win_title(hwnd):
    n = _u32().GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    _u32().GetWindowTextW(hwnd, buf, n + 1)
    return buf.value or ""


def _win_class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    _u32().GetClassNameW(hwnd, buf, 256)
    return buf.value or ""


def _win_proc_name(hwnd):
    """Имя exe процесса-владельца окна (или '')."""
    try:
        k32 = ctypes.windll.kernel32
        pid = wt.DWORD(0)
        _u32().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return ""
        h = k32.OpenProcess(0x1000, False, pid.value)   # QUERY_LIMITED
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            sz = wt.DWORD(1024)
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(sz)):
                return os.path.basename(buf.value or "")
            return ""
        finally:
            k32.CloseHandle(h)
    except Exception:
        return ""


def _active_window_info():
    hwnd = _u32().GetForegroundWindow()
    return _win_title(hwnd), _win_class(hwnd), _win_proc_name(hwnd)


def _active_window_ok(p):
    title, wclass, exe = _active_window_info()
    mode = p["mode"]
    match = _str(p["match"])
    if mode == "title":
        cur = title
    elif mode == "class":
        cur = wclass
    else:
        cur = exe
    op = p["op"]
    if op == "equals":
        return cur.strip().lower() == match.strip().lower()
    if op == "regex":
        return re.search(match, cur) is not None
    return match.lower() in cur.lower()


def exec_active_window(api, p, inputs, emit):
    api.check_stop()
    if "in" not in inputs:
        return
    _wait_result(api, emit, lambda: _active_window_ok(p), p["wait"])


ACTIVE_WIN_NODE = N(
    "conditions/active_window", "Active window", "Активное окно",
    (EVENT_IN, ("match", "string")),
    [P("mode", "combo", "title", "What", "Что", ["title", "class", "process"]),
     P("op", "combo", "contains", "Match", "Сравнение",
       ["contains", "equals", "regex"]),
     P("match", "string", "", "Text / class / name.exe",
       "Текст / класс / имя.exe"), WAIT_P],
    {"en": "Checks the foreground window: title, window class or owner process name.",
     "ru": "Проверяет активное окно: заголовок, класс окна или имя процесса-владельца (contains без учёта регистра)."},
)
ACTIVE_WIN_NODE["exec"] = exec_active_window


# ---------------------------------------------------------------- Текст в окне / OCR

WM_GETTEXT = 0x000D
SMTO_ABORTIFHUNG = 0x0002
_ENUM_PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def _child_text(hwnd):
    """Текст дочернего окна чужого процесса (SendMessageTimeout WM_GETTEXT)."""
    buf = ctypes.create_unicode_buffer(4096)
    res = wt.DWORD(0)  # загрузится в отдельном вызове ниже
    u32 = _u32()
    send = u32.SendMessageTimeoutW
    ok = send(hwnd, WM_GETTEXT, 4096, buf, SMTO_ABORTIFHUNG, 200,
              ctypes.byref(res))
    return buf.value if ok else ""


def _active_hwnd():
    return _u32().GetForegroundWindow()


def _window_texts(title_part):
    """Тексты окон: заголовок + всё содержимое (кнопки, подписи, поля).

    title_part: пусто = только активное окно; иначе все видимые окна,
    в заголовке которых есть подстрока.
    """
    u32 = _u32()
    tp = _str(title_part).strip().lower()
    tops = []
    if tp:
        def cb_top(hwnd, _):
            if u32.IsWindowVisible(hwnd):
                t = _win_title(hwnd)
                if t and tp in t.lower():
                    tops.append(hwnd)
            return True
        u32.EnumWindows(_ENUM_PROC(cb_top), 0)
    else:
        tops.append(_active_hwnd())

    parts = []

    def collect(hwnd):
        t = _win_title(hwnd)
        if t:
            parts.append(t)
        ct = _child_text(hwnd)
        if ct:
            parts.append(ct)

        def cb_child(h, _):
            ct2 = _child_text(h)
            if ct2:
                parts.append(ct2)
            return True
        u32.EnumChildWindows(hwnd, _ENUM_PROC(cb_child), 0)

    for h in tops:
        if h:
            collect(h)
    return "\n".join(parts)


_OCR_WINRT_PKGS = ["winrt-Windows.Media.Ocr", "winrt-Windows.Graphics.Imaging",
                   "winrt-Windows.Storage.Streams", "winrt-Windows.Globalization",
                   "winrt-Windows.Foundation",
                   "winrt-Windows.Foundation.Collections"]


def _ocr_packages_ready():
    """Файловая проверка winrt-пакетов БЕЗ импорта (см. плагин «Триггеры»):
    импорт грузит нативные DLL и блокирует их доустановку; если неполон
    хоть один — запрашиваем ВСЕ разом (pip --target сносит общую папку)."""
    rels = ("media/ocr", "graphics/imaging", "storage/streams",
            "foundation/collections", "globalization", "foundation")
    for rel in rels:
        ok = False
        for sp in sys.path:
            if not sp:
                continue
            d = os.path.join(sp, "winrt", "windows", *rel.split("/"))
            if os.path.isfile(os.path.join(d, "__init__.py")):
                ok = True
                break
        if not ok:
            return list(_OCR_WINRT_PKGS)
    for sp in sys.path:
        if not sp:
            continue
        base = os.path.join(sp, "winrt")
        if os.path.isdir(base) and (
                os.path.isfile(os.path.join(base, "runtime", "__init__.py"))
                or any(f.startswith("_winrt") and f.endswith(".pyd")
                       for f in os.listdir(base))):
            return []
    return list(_OCR_WINRT_PKGS)


class NeedPackagesError(RuntimeError):
    def __init__(self, packages):
        RuntimeError.__init__(self, "Нужны пакеты: %s" % " ".join(packages))
        self.packages = list(packages)


def _ocr_ns(name):
    import importlib
    try:
        return importlib.import_module("winrt.windows." + name)
    except ImportError:
        pass
    try:
        return importlib.import_module("winsdk.windows." + name)
    except ImportError:
        return None


def _ocr_recognizer(api, lang):
    """(лениво, на ноду) PIL-картинка -> текст. Windows OCR, офлайн."""
    missing = _ocr_packages_ready()
    if missing:
        raise NeedPackagesError(missing + ["pillow"])
    st = api.node_state()
    if st.get("rec") is None:
        from PIL import ImageGrab  # noqa: F401  (проверка наличия)
        import asyncio
        ocr_mod = _ocr_ns("media.ocr")
        imaging = _ocr_ns("graphics.imaging")
        streams = _ocr_ns("storage.streams")
        if not all((ocr_mod, imaging, streams)):
            raise RuntimeError("Не удалось загрузить модули Windows OCR (winrt). "
                               "Перезапустите run.bat и попробуйте снова.")
        av = ocr_mod.OcrEngine.available_recognizer_languages
        langs = list(av() if callable(av) else av)
        engine = None
        if lang and lang != "auto":
            for l in langs:
                if l.language_tag.lower().startswith(str(lang).lower()):
                    engine = ocr_mod.OcrEngine.try_create_from_language(l)
                    break
        if engine is None:
            for l in langs:
                if l.language_tag.lower().startswith("ru"):
                    engine = ocr_mod.OcrEngine.try_create_from_language(l)
                    break
        if engine is None:
            try:
                engine = ocr_mod.OcrEngine.try_create_from_user_profile_languages()
            except Exception:
                engine = None
        if engine is None:
            raise RuntimeError("Windows OCR недоступен — добавьте русский "
                               "языковой пакет в параметрах Windows.")
        loop = asyncio.new_event_loop()
        threading.Thread(target=loop.run_forever, daemon=True).start()

        def recognize(img):
            # ОДНА coroutine: pywinrt-async объекты awaitable, но не coroutine
            async def go():
                png = io.BytesIO()
                img.save(png, "PNG")
                stm = streams.InMemoryRandomAccessStream()
                writer = streams.DataWriter(stm.get_output_stream_at(0))
                writer.write_bytes(png.getvalue())
                await writer.store_async()
                await writer.flush_async()
                decoder = await imaging.BitmapDecoder.create_async(stm)
                bitmap = await decoder.get_software_bitmap_async()
                return await engine.recognize_async(bitmap)
            res = asyncio.run_coroutine_threadsafe(go(), loop).result()
            return (res.text or "").strip()

        st["rec"] = recognize
    return st["rec"]


def _ocr_grab(x, y, w, h):
    from PIL import ImageGrab
    return ImageGrab.grab(bbox=(int(x), int(y), int(x + w), int(y + h)))


def _needle_ok(text, op, needle):
    t = _str(text)
    n = _str(needle)
    if op == "not contains":
        return n.lower() not in t.lower()
    if op == "regex":
        try:
            return re.search(n, t) is not None
        except re.error:
            raise ValueError("Некорректный regex: %s" % n)
    return n.lower() in t.lower()   # contains


def _tw_text(api, p):
    """Текст для проверки: окно или OCR-снимок области."""
    if p["source"] == "ocr":
        img = _ocr_grab(p["x"], p["y"], p["w"], p["h"])
        rec = _ocr_recognizer(api, p["lang"])
        return rec(img)
    return _window_texts(p["window"])


def exec_text_window(api, p, inputs, emit):
    api.check_stop()
    if "in" not in inputs:
        return
    _wait_result(api, emit, lambda: _needle_ok(_tw_text(api, p), p["op"], p["needle"]),
                 p["wait"])


TEXT_WIN_NODE = N(
    "conditions/text_in_window", "Text in window / OCR", "Текст в окне / OCR",
    (EVENT_IN, ("needle", "string")),
    [P("source", "combo", "window", "Source", "Источник", ["window", "ocr"]),
     P("window", "string", "", "Window title part (empty = active)",
       "Часть заголовка окна (пусто = активное)"),
     P("needle", "string", "", "Text", "Текст"),
     P("op", "combo", "contains", "Match", "Сравнение",
       ["contains", "not contains", "regex"]),
     P("x", "number", 0, "OCR: X", "OCR: X"),
     P("y", "number", 0, "OCR: Y", "OCR: Y"),
     P("w", "number", 400, "OCR: W", "OCR: Ш"),
     P("h", "number", 200, "OCR: H", "OCR: В"),
     P("lang", "string", "auto", "OCR language (ru/en/auto)",
       "Язык OCR (ru/en/auto)"),
     WAIT_P],
    {"en": "Searches text: 'window' = real window texts (title, buttons, labels via WinAPI); 'ocr' = screen region (built-in Windows OCR, offline).",
     "ru": "Ищет текст: «окно» — реальные тексты окна (заголовок, кнопки, подписи через WinAPI); «OCR» — область экрана (встроенный Windows OCR, офлайн; пакеты winrt-* pillow ставятся сами)."},
)
TEXT_WIN_NODE["exec"] = exec_text_window


# ---------------------------------------------------------------- Буфер обмена

def _clipboard_text():
    # Get-Clipboard добавляет хвостовой перевод строки — срезаем его:
    # без этого «равно»/«заканчивается на» никогда не сходились
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "[Console]::OutputEncoding=[Text.Encoding]::UTF8; (Get-Clipboard -Raw).TrimEnd()"],
        capture_output=True, timeout=15)
    return r.stdout.decode("utf-8", errors="replace").rstrip("\r\n")


def _clipboard_ok(p):
    text = _clipboard_text()
    value = _str(p["value"])
    op = p["op"]
    if p.get("ignore_case") and op != "regex":
        text, value = text.lower(), value.lower()
    if op == "equals":
        return text.strip() == value.strip()
    if op == "starts":
        return text.startswith(value)
    if op == "ends":
        return text.endswith(value)
    if op == "regex":
        try:
            return re.search(value, text) is not None
        except re.error:
            raise ValueError("Некорректный regex: %s" % value)
    return value in text   # contains


def exec_clipboard(api, p, inputs, emit):
    api.check_stop()
    if "in" not in inputs:
        return
    _wait_result(api, emit, lambda: _clipboard_ok(p), p["wait"])


CLIP_NODE = N(
    "conditions/clipboard", "Clipboard", "Буфер обмена",
    (EVENT_IN, ("value", "string")),
    [P("op", "combo", "contains", "Match", "Сравнение",
       ["contains", "equals", "starts", "ends", "regex"]),
     P("value", "string", "", "Text / pattern", "Текст / шаблон"),
     P("ignore_case", "bool", True, "Ignore case", "Без учёта регистра"),
     WAIT_P],
    {"en": "Checks the current clipboard text: contains / equals / starts / ends / regex.",
     "ru": "Проверяет текущий текст буфера обмена: содержит / равно / начинается / заканчивается / regex."},
)
CLIP_NODE["exec"] = exec_clipboard


# ---------------------------------------------------------------- Время

_WEEK = {"пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6,
         "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _parse_hhmm(s):
    try:
        h, m = _str(s).strip().split(":")
        return int(h) * 60 + int(m)
    except (TypeError, ValueError):
        return None


def _time_ok(p):
    days = set()
    for part in _str(p["days"]).replace(";", ",").split(","):
        d = _WEEK.get(part.strip().lower()[:3])
        if d is not None:
            days.add(d)
    lt = time.localtime()
    if days and lt.tm_wday not in days:
        return False
    a = _parse_hhmm(p["from"])
    b = _parse_hhmm(p["to"])
    if a is None and b is None:
        return True                      # проверка только дня недели
    now = lt.tm_hour * 60 + lt.tm_min
    a = 0 if a is None else a
    b = 24 * 60 if b is None else b
    return (a <= now <= b) if a <= b else (now >= a or now <= b)  # через полночь


def exec_time(api, p, inputs, emit):
    api.check_stop()
    if "in" not in inputs:
        return
    _wait_result(api, emit, lambda: _time_ok(p), p["wait"])


TIME_NODE = N(
    "conditions/time_range", "Time / weekday", "Время / день недели",
    (EVENT_IN,),
    [P("from", "string", "09:00", "From (HH:MM)", "С (ЧЧ:ММ)"),
     P("to", "string", "18:00", "To (HH:MM)", "До (ЧЧ:ММ)"),
     P("days", "string", "", "Days: пн,вт (empty = all)",
       "Дни: пн,вт (пусто = все)"), WAIT_P],
    {"en": "True if now is inside the range (24h, can cross midnight) and today is one of the days. Empty From/To = any time.",
     "ru": "true, если сейчас внутри диапазона (ЧЧ:ММ, можно через полночь: 22:00–06:00) и сегодня один из указанных дней. Пустые С/До = любое время."},
)
TIME_NODE["exec"] = exec_time


# ---------------------------------------------------------------- Интернет

def _ping(host, timeout_ms):
    """ICMP-ping через iphlpapi (IcmpSendEcho), без внешних программ."""
    try:
        ip = socket.gethostbyname(host)   # домен без DNS -> исключение -> False
    except OSError:
        return False
    iphlpapi = ctypes.windll.iphlpapi
    k32 = ctypes.windll.kernel32

    class REPLY(ctypes.Structure):
        _fields_ = [("address", ctypes.c_ulong), ("status", ctypes.c_ulong),
                    ("rtt", ctypes.c_ulong), ("size", ctypes.c_ushort),
                    ("reserved", ctypes.c_ushort), ("data", ctypes.c_void_p)]

    h = iphlpapi.IcmpCreateFile()
    if not h:
        return False
    try:
        payload = b"keyflow-ping"
        buf = ctypes.create_string_buffer(
            ctypes.sizeof(REPLY) + len(payload) + 64)
        ip_num = struct.unpack("<I", socket.inet_aton(ip))[0]
        n = iphlpapi.IcmpSendEcho(ctypes.c_void_p(h), ctypes.c_ulong(ip_num),
                                  payload, ctypes.c_ushort(len(payload)),
                                  None, buf, ctypes.c_ulong(len(buf)),
                                  ctypes.c_ulong(timeout_ms))
        if not n:
            return False
        rep = ctypes.cast(buf, ctypes.POINTER(REPLY)).contents
        return rep.status == 0
    except Exception:
        return False
    finally:
        k32.CloseHandle(ctypes.c_void_p(h))


def _http_ok(url, timeout):
    import urllib.error
    import urllib.request
    if "://" not in url:
        url = "http://" + url
    req = urllib.request.Request(url, headers={"User-Agent": "KeyFlow/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 400
    except urllib.error.HTTPError:
        return True    # сервер ответил (403/404) — сеть есть
    except Exception:
        return False


def _tcp_ok(host, port, timeout):
    try:
        socket.create_connection((host, int(port)), timeout=timeout).close()
        return True
    except (OSError, TypeError, ValueError):
        return False


def _inet_ok(p):
    host = _str(p["host"]).strip() or "1.1.1.1"
    timeout = max(1, int(p["timeout"]))
    if p["mode"] == "http":
        return _http_ok(host, timeout)
    if p["mode"] == "tcp":
        return _tcp_ok(host, p["port"], timeout)
    return _ping(host, timeout * 1000)


def exec_inet(api, p, inputs, emit):
    api.check_stop()
    if "in" not in inputs:
        return
    _wait_result(api, emit, lambda: _inet_ok(p), p["wait"])


INET_NODE = N(
    "conditions/inet", "Internet available", "Интернет доступен",
    (EVENT_IN,),
    [P("mode", "combo", "ping", "Check", "Проверка", ["ping", "http", "tcp"]),
     P("host", "string", "1.1.1.1", "Host / URL (for http)",
       "Хост / URL (для http)"),
     P("port", "number", 80, "Port (for tcp)", "Порт (для tcp)"),
     P("timeout", "number", 3, "Timeout, sec", "Таймаут, сек"), WAIT_P],
    {"en": "ping: ICMP echo (default 1.1.1.1); http: GET to URL (any server reply counts); tcp: connect to host:port.",
     "ru": "ping: ICMP-эхо (по умолчанию 1.1.1.1); http: GET по URL (любой ответ сервера = сеть есть); tcp: подключение к host:порт."},
)
INET_NODE["exec"] = exec_inet


# ---------------------------------------------------------------- Батарея

def _battery():
    """SystemPowerStatus или None, если батареи нет."""
    class ST(ctypes.Structure):
        _fields_ = [("ac", ctypes.c_ubyte), ("flag", ctypes.c_ubyte),
                    ("life", ctypes.c_ubyte), ("pct", ctypes.c_ubyte),
                    ("sec", ctypes.c_ulong)]
    st = ST()
    if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)) == 0:
        return None
    if st.flag & 128:      # BATTERY_FLAG_NO_BATTERY
        return None
    if st.pct >= 255:      # неизвестно
        return None
    return st.pct


def _battery_ok(p):
    pct = _battery()
    if pct is None:
        return False
    threshold = p["value"]
    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        raise ValueError("Порог заряда должен быть числом")
    return pct > threshold if p["op"] == "above" else pct < threshold


def exec_battery(api, p, inputs, emit):
    api.check_stop()
    if "in" not in inputs:
        return
    if _battery() is None:
        api.log("Батарея не обнаружена (стационарный ПК?)")
    _wait_result(api, emit, lambda: _battery_ok(p), p["wait"])


BATTERY_NODE = N(
    "conditions/battery", "Battery level", "Заряд батареи",
    (EVENT_IN, ("value", "number")),
    [P("op", "combo", "above", "Condition", "Условие", ["above", "below"]),
     P("value", "number", 50, "Threshold, %", "Порог, %"), WAIT_P],
    {"en": "Compares current battery charge with a threshold. No battery = false branch.",
     "ru": "Сравнивает текущий заряд батареи с порогом. Батареи нет — ветвь false."},
)
BATTERY_NODE["exec"] = exec_battery


# ---------------------------------------------------------------- Переменная

def _var_empty_ok(api, p, inputs):
    value = inputs.get("value")
    if value is None:
        name = _str(p["name"]).strip()
        value = api.get_var_any(name) if name else None
    empty = value is None or _str(value).strip() == ""
    return empty if p["op"] == "empty" else not empty


def exec_var_empty(api, p, inputs, emit):
    api.check_stop()
    if "in" not in inputs:
        return
    _wait_result(api, emit, lambda: _var_empty_ok(api, p, inputs), p["wait"])


VAR_EMPTY_NODE = N(
    "conditions/var_empty", "Variable empty", "Переменная пустая",
    (EVENT_IN, ("value", "any")),
    [P("op", "combo", "empty", "Check", "Проверка", ["empty", "not empty"]),
     P("name", "string", "", "Variable name", "Имя переменной"), WAIT_P],
    {"en": "True if the variable is empty (missing, None or whitespace). Wire 'value' wins; else the variable by name.",
     "ru": "true, если переменная пуста (нет, None или одни пробелы). Провод «value» сильнее; иначе переменная по имени."},
)
VAR_EMPTY_NODE["exec"] = exec_var_empty


NODES = [IF_NODE, SWITCH_NODE, COMPARE_NODE, FILE_NODE, PROCESS_NODE,
         ACTIVE_WIN_NODE, TEXT_WIN_NODE, CLIP_NODE, TIME_NODE, INET_NODE,
         BATTERY_NODE, VAR_EMPTY_NODE]
