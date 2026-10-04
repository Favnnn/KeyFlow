# -*- coding: utf-8 -*-
"""KeyFlow triggers — ноды-триггеры: БЕЗ входов, только выход «out».

Архитектура как у core/startup и core/interval:
  * каждая нода = def с "trigger": True и "start"(handle, node);
  * start возвращает функцию-стоппер, движок вызывает её на Stop;
  * событие = handle.fire(value) — значение уходит в провод «out».

Ноды: первое включение, мышь в области, расписание (время/дни/cron),
запуск/закрытие программы, смена активного окна, файлы/папки (с тумблером
и полем расширения), буфер обмена, сеть (интернет/порт), USB-устройства,
батарея, пиксель экрана, OCR области, голосовая команда, громкость,
элемент UI Automation. Всё на stdlib; OCR/голос/громкость/UIA — при наличии
необязательных пакетов (ошибка старта ноды подскажет, что доустановить).
"""

import ctypes
import io
import os
import socket
import sys
import threading
import time
from ctypes import wintypes

# DPI: работаем в ЕДИНОЙ виртуальной системе координат (как курсор и координаты
# в большинстве программ при масштабе Windows). PER_MONITOR-aware давал
# «съезд» координат пикселя на масштабе 125% (+35/+27 при 0.8 коэффициенте).
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)   # SYSTEM_AWARE: виртуальные координаты
except Exception:
    pass   # остаёмся в режиме по умолчанию — тоже виртуальные координаты

# ---------------------------------------------------------------- утилиты


def P(name, ptype, default, en, ru, options=None, options_endpoint=None):
    p = {"name": name, "type": ptype, "default": default,
         "label": {"en": en, "ru": ru}}
    if options is not None:
        p["options"] = list(options)
    if options_endpoint:
        p["options_endpoint"] = options_endpoint
    return p


def _prop(node, name, default=None):
    v = (node.get("properties") or {}).get(name, default)
    return default if v is None else v


def _fnum(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _err_once(handle, state, err):
    """Ошибка фонового потока — один раз в лог, потом молчим."""
    if not state.get("_err_logged"):
        state["_err_logged"] = True
        try:
            handle.engine.log("error", "Триггер #%s: %s" % (handle.node_id, err))
        except Exception:
            pass


class NeedPackagesError(RuntimeError):
    """Триггеру нужны пакеты: движок напечатает готовую команду для копирования."""

    def __init__(self, packages):
        self.packages = list(packages)
        super().__init__(" ".join(self.packages))


def _poll(handle, interval, fn, setup=None):
    """Фоновый опрос триггера: fn(state, handle) каждые interval сек."""
    ev = threading.Event()

    def loop():
        state = {}
        if setup:
            try:
                setup(state)
            except Exception:
                pass
        while not ev.wait(interval):
            if handle.engine.stopping:
                break
            try:
                fn(state, handle)
            except Exception as e:
                _err_once(handle, state, e)

    threading.Thread(target=loop, daemon=True).start()
    return ev.set


def _u32():
    return ctypes.windll.user32


# --------------------------------------------------------- время и cron

_WEEK = {"пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6,
         "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def parse_days(s):
    """«пн,ср» -> {0,2}; пусто/неразборчиво -> все дни."""
    out = set()
    for part in (s or "").replace(";", ",").split(","):
        d = _WEEK.get(part.strip().lower()[:3])
        if d is not None:
            out.add(d)
    return out or set(range(7))


def parse_cron(expr):
    """Мини-cron «мин час день мес день_нед» (*, списки, шаги, диапазоны).

    day_of_week: 0/7 = воскресенье (как в cron). None = не разобрали.
    """
    parts = (expr or "").split()
    if len(parts) != 5:
        return None

    def field(p, lo, hi, dow=False):
        vals = set()
        for chunk in p.split(","):
            chunk = chunk.strip()
            if not chunk:
                return None
            step = 1
            if "/" in chunk:
                chunk, s = chunk.split("/", 1)
                try:
                    step = max(1, int(s))
                except ValueError:
                    return None
            if chunk in ("*", ""):
                a, b = lo, hi
            elif "-" in chunk:
                try:
                    a, b = chunk.split("-", 1)
                    a, b = int(a), int(b)
                except ValueError:
                    return None
            else:
                try:
                    a = int(chunk)
                except ValueError:
                    return None
                b = hi if step > 1 else a
            if dow and b == 7:
                b = 6
            if a < lo or b > hi or a > b:
                return None
            vals.update(range(a, b + 1, step))
        return vals or None

    f = (field(parts[0], 0, 59), field(parts[1], 0, 23),
         field(parts[2], 1, 31), field(parts[3], 1, 12),
         field(parts[4], 0, 6, dow=True))
    return None if None in f else f


def _cron_match(f, lt):
    if not (lt.tm_min in f[0] and lt.tm_hour in f[1] and lt.tm_mon in f[3]
            and lt.tm_mday in f[2]):
        return False
    return ((lt.tm_wday + 1) % 7) in f[4]      # cron 0=вс -> python 0=пн


# --------------------------------------------------------- WinAPI-мелочи

def _cursor_pos():
    pt = ctypes.c_long * 2
    p = pt()
    _u32().GetCursorPos(ctypes.byref(p))
    return int(p[0]), int(p[1])


def _window_title(hwnd):
    n = _u32().GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    _u32().GetWindowTextW(hwnd, buf, n + 1)
    return buf.value or ""


def _window_class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    _u32().GetClassNameW(hwnd, buf, 256)
    return buf.value or ""


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


def _proc_match(procs, m):
    """m: «имя.exe» | «123» (PID) | кусок пути. -> {pid: exe} подходящих."""
    out = {}
    m = (m or "").strip().lower()
    if not m:
        return out
    if m.isdigit():
        pid = int(m)
        if pid in procs:
            out[pid] = procs[pid]
        return out
    for pid, exe in procs.items():
        if exe.lower() == m:
            out[pid] = exe
    if out:
        return out
    for pid, exe in procs.items():
        if m in exe.lower():
            out[pid] = exe
    return out


def _pixel(x, y):
    """Цвет пикселя по ВИРТУАЛЬНЫМ координатам (как у курсора).

    GetPixel читает физическую поверхность экрана без DPI-виртуализации,
    поэтому виртуальные координаты переводим в физические через реальный DPI
    (при масштабе 130%: виртуальная 100 = физическая 130)."""
    u32 = _u32()
    gdi = ctypes.windll.gdi32
    scale = 1.0
    try:
        dpi = u32.GetDpiForSystem()
        if dpi:
            scale = dpi / 96.0
    except Exception:
        pass
    hdc = u32.GetDC(0)
    try:
        c = gdi.GetPixel(hdc, int(round(x * scale)), int(round(y * scale)))
    finally:
        u32.ReleaseDC(0, hdc)
    return (c & 0xFF, (c >> 8) & 0xFF, (c >> 16) & 0xFF)


def _clipboard_change():
    """(kind, value) нового содержимого буфера или None (нечего/ошибка).

    ВАЖНО: у функций WinAPI объявлены restype/argtypes (HANDLE = 64 бита).
    Без них ctypes обрезает дескрипторы до 32 бит — GlobalLock по обрезанному
    хэндлу роняет процесс (access violation) — «сервер крашится».
    """
    u32 = _u32()
    k32 = ctypes.windll.kernel32
    sh32 = ctypes.windll.shell32

    CF_UNICODETEXT, CF_DIB, CF_HDROP = 13, 8, 15
    HANDLE = ctypes.c_void_p

    u32.IsClipboardFormatAvailable.argtypes = [ctypes.c_uint]
    u32.IsClipboardFormatAvailable.restype = ctypes.c_int
    u32.GetClipboardData.argtypes = [ctypes.c_uint]
    u32.GetClipboardData.restype = HANDLE
    u32.OpenClipboard.argtypes = [HANDLE]
    u32.OpenClipboard.restype = ctypes.c_int
    u32.CloseClipboard.argtypes = []
    u32.CloseClipboard.restype = ctypes.c_int
    k32.GlobalLock.argtypes = [HANDLE]
    k32.GlobalLock.restype = HANDLE
    k32.GlobalUnlock.argtypes = [HANDLE]
    k32.GlobalUnlock.restype = ctypes.c_int
    sh32.DragQueryFileW.argtypes = [HANDLE, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_uint]
    sh32.DragQueryFileW.restype = ctypes.c_uint

    if not u32.OpenClipboard(None):
        return None
    try:
        if u32.IsClipboardFormatAvailable(CF_HDROP):
            h = u32.GetClipboardData(CF_HDROP)
            if h:
                n = sh32.DragQueryFileW(h, 0xFFFFFFFF, None, 0)
                files = []
                for i in range(n):
                    buf = ctypes.create_unicode_buffer(520)
                    sh32.DragQueryFileW(h, i, buf, 520)
                    files.append(buf.value)
                if files:
                    return ("files", "\n".join(files))
        if u32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            h = u32.GetClipboardData(CF_UNICODETEXT)
            if h:
                ptr = k32.GlobalLock(h)
                if ptr:
                    try:
                        value = ctypes.cast(ptr, ctypes.c_wchar_p).value or ""
                    except Exception:
                        value = ""
                    finally:
                        k32.GlobalUnlock(h)
                    return ("text", value)
        if u32.IsClipboardFormatAvailable(CF_DIB):
            return ("image", "")
    except Exception:
        return None      # любые проблемы буфера — не роняем движок
    finally:
        u32.CloseClipboard()
    return None


def _net_ok(host, port):
    """host:порт доступен? (режим «Порт»; host по умолчанию 127.0.0.1)"""
    try:
        socket.create_connection((host, int(port)), timeout=0.8).close()
        return True
    except OSError:
        return False


_INET_PROBE = (("1.1.1.1", 443), ("8.8.8.8", 443), ("208.67.222.222", 443))


def _nlm_internet():
    """Вердикт Windows о доступе в интернет (Network List Manager, COM).

    Тот же сигнал, что у значка сети в трее: моментальный (без сетевых
    запросов), одинаковый для кабеля и Wi-Fi, и — в отличие от
    probe-подключений — не обманывается DPI-перехватом TCP (прозрачный
    прокси провайдера «отвечает» на handshake 443 даже при мёртвом
    интернете). None — COM недоступен (сервис Network List Service)."""
    try:
        ole32 = ctypes.windll.ole32
    except Exception:
        return None

    class _GUID(ctypes.Structure):
        _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD),
                    ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]

    PVOID = ctypes.c_void_p
    HRESULT = ctypes.HRESULT
    try:
        try:
            ole32.CoInitializeEx(None, 0)   # COINIT_MULTITHREADED
        except Exception:
            pass
        clsid = _GUID()
        iid = _GUID()
        # CLSID_INetworkListManager / IID_INetworkListManager
        if ole32.CLSIDFromString(
                ctypes.c_wchar_p(
                    "{DCB00C01-570F-4A9B-8D69-199FDBA5723B}"),
                ctypes.byref(clsid)) != 0:
            return None
        if ole32.CLSIDFromString(
                ctypes.c_wchar_p(
                    "{DCB00000-570F-4A9B-8D69-199FDBA5723B}"),
                ctypes.byref(iid)) != 0:
            return None
        punk = PVOID()
        if ole32.CoCreateInstance(ctypes.byref(clsid), None, 23,   # CLSCTX_ALL
                                  ctypes.byref(iid),
                                  ctypes.byref(punk)) != 0 or not punk.value:
            return None
        try:
            vtbl = PVOID.from_address(punk.value).value

            def slot(n):
                return PVOID.from_address(
                    vtbl + n * ctypes.sizeof(PVOID)).value

            # дальше только ФИКСИРОВАННЫЙ контракт IUnknown/IDispatch —
            # никаких угадываний слотов методов интерфейса
            rel = ctypes.WINFUNCTYPE(wintypes.ULONG, PVOID)(slot(2))
            getids = ctypes.WINFUNCTYPE(
                HRESULT, PVOID, ctypes.POINTER(_GUID),
                ctypes.POINTER(wintypes.LPCWSTR), wintypes.UINT,
                wintypes.DWORD, ctypes.POINTER(wintypes.LONG))(slot(5))
            invoke = ctypes.WINFUNCTYPE(
                HRESULT, PVOID, wintypes.LONG, ctypes.POINTER(_GUID),
                wintypes.WORD, wintypes.WORD, PVOID, PVOID, PVOID,
                ctypes.POINTER(wintypes.UINT))(slot(6))
            name = (wintypes.LPCWSTR * 1)("IsConnectedToInternet")
            dispid = wintypes.LONG(0)
            if getids(punk, ctypes.byref(iid), name, 1, 0x0409,
                      ctypes.byref(dispid)) != 0:
                return None
            dp = ctypes.create_string_buffer(32)    # DISPPARAMS: нет аргументов
            var = ctypes.create_string_buffer(32)   # VARIANT-результат
            argerr = wintypes.UINT(0)
            # wFlags 3 = METHOD | PROPERTYGET
            if invoke(punk, dispid, ctypes.byref(iid), 0x0409, 3,
                      dp, var, None, ctypes.byref(argerr)) != 0:
                return None
            vt = ctypes.cast(
                var, ctypes.POINTER(wintypes.USHORT)).contents.value
            if vt != 11:   # VT_BOOL
                return None
            # boolVal лежит в начале union → offset 8 (x64)
            b = ctypes.cast(ctypes.byref(var, 8),
                            ctypes.POINTER(wintypes.VARIANT_BOOL)
                            ).contents.value
            return bool(b)
        finally:
            rel(punk)
    except Exception:
        return None


def _inet_up():
    """Есть ли интернет. Сначала — вердикт Windows (NLM): моментально и
    честно для кабеля и Wi-Fi. COM недоступен — fallback на TCP 443
    anycast-хостов. Предыстория: старая проверка «1.1.1.1:53» врала в обе
    стороны (DPI перехватывает 53-й порт), из-за чего «Интернет
    появился/пропал» не срабатывали вовсе; голый TCP-handshake на 443
    «проходил» через прозрачные прокси провайдера даже без интернета —
    «пропал» не приходил, «появился» запаздывал на таймауты."""
    r = _nlm_internet()
    if r is not None:
        return r
    for host, port in _INET_PROBE:
        try:
            socket.create_connection((host, port), timeout=0.5).close()
            return True
        except OSError:
            continue
    return False


# Чистые вендорские/системные метки: не годятся как имя устройства
GENERIC_NAMES = {
    "Microsoft", "Майкрософт",
    "Realtek Semiconductor Corp.",
    "(Стандартные системные устройства)",
    "(Стандартный USB хост-контроллер)",
}


def _removable_drives():
    """Съёмные диски (USB-флешки и т.п.): ['E:\\', ...]."""
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    out = []
    for i in range(26):
        if bitmask & (1 << i):
            letter = "%s:\\" % chr(ord("A") + i)
            if ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(letter)) == 2:
                out.append(letter)
    return out


def _dir_snapshot(root, recursive, exts):
    """{путь: mtime} — база для сравнения «создан/изменён/удалён/переименован»."""
    out = {}
    exts = tuple(exts) if exts else None

    def want(name):
        return not exts or name.lower().endswith(exts)

    if recursive:
        for dirpath, _d, files in os.walk(root):
            for f in files:
                if want(f):
                    p = os.path.join(dirpath, f)
                    try:
                        out[p] = os.path.getmtime(p)
                    except OSError:
                        pass
    else:
        try:
            for f in os.listdir(root):
                p = os.path.join(root, f)
                if os.path.isfile(p) and want(f):
                    try:
                        out[p] = os.path.getmtime(p)
                    except OSError:
                        pass
        except OSError:
            pass
    return out


# ---------------------------------------------------------------- стартеры

def _st_mouse_area(handle, node):
    x = _fnum(_prop(node, "x", 0), 0)
    y = _fnum(_prop(node, "y", 0), 0)
    w = _fnum(_prop(node, "w", 100), 100)
    h = _fnum(_prop(node, "h", 100), 100)

    def check(state, h2):
        cx, cy = _cursor_pos()
        inside = (x <= cx < x + w) and (y <= cy < y + h)
        if "in" not in state:
            state["in"] = inside   # позиция на момент старта — не событие
            return
        if inside and not state.get("in"):
            h2.fire({"x": cx, "y": cy})
        state["in"] = inside

    return _poll(handle, 0.15, check)


def _st_schedule(handle, node):
    mode = _prop(node, "mode", "time") or "time"
    hm = str(_prop(node, "time", "12:00") or "")
    days = parse_days(_prop(node, "days", ""))
    cron = parse_cron(_prop(node, "cron", "*/30 * * * *"))
    if mode == "cron" and cron is None:
        raise RuntimeError("Не понимаю cron «%s» (мин час день мес день_нед)" % _prop(node, "cron"))
    if mode == "time" and ":" not in hm:
        raise RuntimeError("Время надо в формате ЧЧ:ММ, а не «%s»" % hm)

    def check(state, h2):
        lt = time.localtime()
        key = time.strftime("%Y%m%d%H%M", lt)
        if key in state.setdefault("fired", set()):
            return
        ok = (mode == "time"
              and ":" in hm
              and int(hm.split(":")[0]) == lt.tm_hour
              and int(hm.split(":")[1]) == lt.tm_min
              and lt.tm_wday in days) or \
             (mode == "cron" and _cron_match(cron, lt))
        if ok:
            state["fired"].add(key)
            h2.fire(time.strftime("%H:%M", lt) if mode == "time" else str(_prop(node, "cron")))

    return _poll(handle, 1.0, check)


def _st_proc_start(handle, node):
    m = str(_prop(node, "match", "") or "")

    def check(state, h2):
        procs = _proc_snapshot()
        found = set(_proc_match(procs, m))
        prev = state.setdefault("pids", set(found))   # базовая линия при старте
        for pid in sorted(found - prev):
            h2.fire(pid)
        state["pids"] = found

    return _poll(handle, 0.4, check)


def _st_proc_exit(handle, node):
    m = str(_prop(node, "match", "") or "")

    def check(state, h2):
        procs = _proc_snapshot()
        found = set(_proc_match(procs, m))
        prev = state.setdefault("pids", set(found))   # базовая линия при старте
        for pid in sorted(prev - found):
            h2.fire(pid)
        state["pids"] = found

    return _poll(handle, 0.4, check)


def _st_window_active(handle, node):
    title = str(_prop(node, "title", "") or "").lower()
    cls = str(_prop(node, "cls", "") or "").lower()

    def check(state, h2):
        hwnd = _u32().GetForegroundWindow()
        if not hwnd or hwnd == state.get("hwnd"):
            return
        first = state.get("hwnd") is None
        state["hwnd"] = hwnd
        if first:
            return   # окно на момент запуска — не событие
        t = _window_title(hwnd)
        if title and title not in t.lower():
            return
        if cls and cls not in _window_class(hwnd).lower():
            return
        h2.fire(t)

    return _poll(handle, 0.15, check)


def _st_file_change(handle, node):
    path = str(_prop(node, "path", "") or "").strip()
    if not path or not os.path.isdir(path):
        raise RuntimeError("Папка не найдена: «%s»" % path)
    use_ext = bool(_prop(node, "filter_ext", False))
    exts = None
    if use_ext:
        exts = tuple("." + x.strip().lstrip(".").lower()
                     for x in str(_prop(node, "ext", "") or "").split(",") if x.strip())
        if not exts:
            raise RuntimeError("Фильтр расширений включён, но список пуст (например: txt, png)")
    recursive = bool(_prop(node, "recursive", False))
    kind = _prop(node, "kind", "all") or "all"

    def _ext_ok(p):
        return not exts or str(p).lower().endswith(exts)

    def _fire(value):
        # «out» — событие; «path» — полный путь к файлу (текст) отдельным выходом
        handle.engine.fire(handle.node_id, {"out": None, "path": str(value)})

    def classify(k, value):
        if kind in ("all", k):
            _fire(value)

    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except Exception:
        Observer = None

    if Observer is None:
        # watchdog не установлен: сейчас будет медленный опрос (до 1-2 сек).
        # Просим движок установить пакет и перезапустить эту ноду — тогда
        # события станут мгновенными. Предупреждаем один раз.
        try:
            handle.engine.log(
                "info",
                "Нода #%s «Изменение файла/папки»: нет watchdog — пока медленный "
                "опрос. Ставлю пакет и перезапущу ноду автоматически."
                % handle.node_id)
            handle.engine.request_packages(["watchdog"], handle.node_id,
                                           "triggers/file_change")
        except Exception:
            pass

    if Observer is not None:
        # мгновенные события из файловой системы (pip install watchdog)
        class H(FileSystemEventHandler):
            def on_created(self, ev):
                if os.path.isfile(ev.src_path) and _ext_ok(ev.src_path):
                    classify("created", ev.src_path)

            def on_deleted(self, ev):
                if _ext_ok(ev.src_path):
                    classify("deleted", ev.src_path)

            def on_modified(self, ev):
                if os.path.isfile(ev.src_path) and _ext_ok(ev.src_path):
                    classify("changed", ev.src_path)

            def on_moved(self, ev):
                if _ext_ok(ev.dest_path):
                    classify("renamed", ev.dest_path)

        obs = Observer()
        obs.schedule(H(), path, recursive=recursive)
        obs.daemon = True
        obs.start()

        def stop():
            try:
                obs.stop()
                obs.join(timeout=1.5)
            except Exception:
                pass

        return stop

    # запасной путь: опрос раз в секунду (watchdog не установлен)
    def check(state, h2):
        cur = _dir_snapshot(path, recursive, exts)
        prev = state.get("snap")
        state["snap"] = cur
        if prev is None:
            return   # первый снимок — база, без событий
        created = [f for f in cur if f not in prev]
        deleted = [f for f in prev if f not in cur]
        changed = [f for f in cur if f in prev and cur[f] != prev[f]]

        def go(value, k):
            classify(k, value)

        for f in created:
            rename = next((d for d in deleted if prev[d] == cur[f]), None)
            if rename is not None and kind in ("all", "renamed"):
                deleted.remove(rename)
                go(f, "renamed")
            else:
                go(f, "created")
        for f in deleted:
            go(f, "deleted")
        for f in changed:
            go(f, "changed")

    # 0.3с: в 3 раза быстрее прежнего; с watchdog и так мгновенно
    return _poll(handle, 0.3, check)


def _st_clipboard(handle, node):
    mode = _prop(node, "mode", "any") or "any"
    flt = str(_prop(node, "text_contains", "") or "").strip()
    flt_low = flt.lower()   # как в conditions/clipboard: без учёта регистра

    def check(state, h2):
        seq = _u32().GetClipboardSequenceNumber()
        prev = state.setdefault("seq", seq)
        if seq == prev:
            return
        state["seq"] = seq   # первое же Ctrl+C после запуска — событие
        ch = _clipboard_change()
        if not ch:
            return
        kind, value = ch
        if mode == "files" and kind != "files":
            return
        if mode == "text" and kind != "text":
            return
        if mode == "image" and kind != "image":
            return
        # фильтр «текст содержит»: не сошлось — не огонь (пусто = любое)
        if flt_low:
            hay = value if kind == "text" else ""
            if kind == "files":
                try:
                    hay = "\n".join(value)
                except Exception:
                    hay = ""
            if flt_low not in str(hay).lower():
                return
        h2.fire(value if kind != "image" else "изображение")

    return _poll(handle, 0.25, check)


def _st_network(handle, node):
    mode = _prop(node, "mode", "inet_up") or "inet_up"
    host = str(_prop(node, "host", "127.0.0.1") or "127.0.0.1")
    port = str(_prop(node, "port", "53") or "53")

    def check(state, h2):
        if mode in ("inet_up", "inet_down"):
            raw = _nlm_internet()
            src = "NLM"
            if raw is None:   # COM недоступен — уходим на TCP-пробу
                raw = _inet_up()
                src = "TCP"
            up = bool(raw)
            if not state.get("nlogged"):
                state["nlogged"] = True
                h2.engine.log("info", "Сеть: детект=%s (%s)" % (up, src))
            if up != state.get("nlm_last"):
                if "nlm_last" in state:
                    h2.engine.log("info", "Сеть: интернет %s (%s)"
                                  % ("появился" if up else "пропал", src))
                state["nlm_last"] = up
        else:
            up = _net_ok(host, port)
        prev = state.setdefault("up", up)
        state["up"] = up
        if prev == up:
            return
        if mode == "port_open":
            if up:
                h2.fire("%s:%s" % (host, port))
        elif mode == "inet_up" and up:
            h2.fire("up")
        elif mode == "inet_down" and not up:
            h2.fire("down")

    return _poll(handle, 2.0, check)


def _usb_devices():
    """{instance_id: человекочитаемое имя} подключённых USB-устройств.

    Перечисляет ВСЕ присутствующие USB-устройства (GUID_DEVINTERFACE_
    USB_DEVICE): флешки, мыши, наушники, телефоны — включая устройства
    без буквы диска. Имя: FriendlyName/DeviceDesc родителя, затем детей.
    При недоступности API — None (владелец решает, что это значит)."""
    try:
        setupapi = ctypes.windll.setupapi
        cfgmgr = ctypes.windll.cfgmgr32
    except Exception:
        return None

    class GUID(ctypes.Structure):
        _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD),
                    ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]

    # GUID_DEVINTERFACE_USB_DEVICE {A5DCBF10-6530-11D2-901F-00C04FB951ED}
    guid = GUID(0xA5DCBF10, 0x6530, 0x11D2,
                (ctypes.c_ubyte * 8)(0x90, 0x1F, 0x00, 0xC0,
                                     0x4F, 0xB9, 0x51, 0xED))

    class IFACE(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD),
                    ("guid", GUID),
                    ("flags", wintypes.DWORD),
                    ("reserved", ctypes.c_size_t)]

    class DEVINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD),
                    ("cls", GUID),
                    ("devinst", wintypes.DWORD),
                    ("reserved", ctypes.c_size_t)]

    # prototypes: БЕЗ них ctypes обрезает 64-битный HDEVINFO до c_int —
    # хэндл портится и перечисление падает с ERROR_INVALID_HANDLE (6)
    HANDLE = wintypes.HANDLE
    setupapi.SetupDiGetClassDevsW.restype = HANDLE
    setupapi.SetupDiGetClassDevsW.argtypes = (
        ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD)
    setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
    setupapi.SetupDiEnumDeviceInterfaces.argtypes = (
        HANDLE, ctypes.c_void_p, ctypes.POINTER(GUID),
        wintypes.DWORD, ctypes.POINTER(IFACE))
    setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
    setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = (
        HANDLE, ctypes.POINTER(IFACE), ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(DEVINFO))
    setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = (HANDLE,)
    for fn in ("CM_Get_Child", "CM_Get_Sibling"):
        getattr(cfgmgr, fn).restype = wintypes.DWORD
        getattr(cfgmgr, fn).argtypes = (
            ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, wintypes.ULONG)
    cfgmgr.CM_Get_DevNode_Registry_PropertyW.restype = wintypes.DWORD
    cfgmgr.CM_Get_DevNode_Registry_PropertyW.argtypes = (
        wintypes.DWORD, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG),
        ctypes.c_void_p, ctypes.POINTER(wintypes.ULONG), wintypes.ULONG)

    def devname(devinst):
        # FriendlyName (12) -> DeviceDesc (1); CM сам разворачивает
        # строки вида @inf,%id%;Текст. Пусто/мусор — пробуем дальше.
        for prop in (12, 1):
            buf = ctypes.create_unicode_buffer(512)
            cb = wintypes.ULONG(1024)
            try:
                if cfgmgr.CM_Get_DevNode_Registry_PropertyW(
                        devinst, prop, None, buf,
                        ctypes.byref(cb), 0) == 0 and buf.value:
                    v = buf.value
                    if not v.upper().startswith("USB\\"):
                        return v
            except Exception:
                pass
        return ""

    def children(devinst):
        kids = []
        child = wintypes.DWORD(0)
        if cfgmgr.CM_Get_Child(ctypes.byref(child), devinst, 0) == 0:
            while True:
                kids.append(child.value)
                sib = wintypes.DWORD(0)
                if cfgmgr.CM_Get_Sibling(
                        ctypes.byref(sib), child, 0) != 0:
                    break
                child = sib
        return kids

    cfgmgr.CM_Get_Device_IDW.restype = wintypes.DWORD
    cfgmgr.CM_Get_Device_IDW.argtypes = (
        wintypes.DWORD, wintypes.LPWSTR, wintypes.ULONG, wintypes.ULONG)

    def descendants(devinst, depth=3):
        # instance-id'ы потомков: дети -> внуки -> правнуки (depth уровней)
        ids = []
        stack = [(c, 1) for c in children(devinst)]
        while stack:
            d, lvl = stack.pop()
            idb = ctypes.create_unicode_buffer(512)
            if cfgmgr.CM_Get_Device_IDW(d, idb, 512, 0) == 0:
                ids.append(idb.value.upper())
            if lvl < depth:
                stack.extend((c, lvl + 1) for c in children(d))
        return ids

    DIGCF_PRESENT = 0x02
    DIGCF_DEVICEINTERFACE = 0x10
    hdev = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(guid), None, None,
        DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if not hdev or hdev == wintypes.HANDLE(-1).value:
        return None
    out = {}
    try:
        idx = 0
        while True:
            fd = IFACE()
            fd.cbSize = ctypes.sizeof(IFACE)
            if setupapi.SetupDiEnumDeviceInterfaces(
                    hdev, None, ctypes.byref(guid), idx,
                    ctypes.byref(fd)) == 0:
                break   # ERROR_NO_MORE_ITEMS — всё перечислили
            idx += 1
            # detail-буфер: SP_DEVICE_INTERFACE_DETAIL_DATA_W —
            # cbSize обязан быть 8 на x64; DevicePath — с offset 4
            buf = ctypes.create_string_buffer(2048)
            ctypes.memmove(buf, (8).to_bytes(4, "little"), 4)
            di = DEVINFO()
            di.cbSize = ctypes.sizeof(DEVINFO)
            n = wintypes.DWORD(0)
            if setupapi.SetupDiGetDeviceInterfaceDetailW(
                    hdev, ctypes.byref(fd), buf, 2048,
                    ctypes.byref(n), ctypes.byref(di)) == 0:
                continue
            # стабильный ключ: instance-id из пути
            # \\?\USB#VID_xxx&PID_xxx#INSTANCE#{guid}
            path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
            parts = path.split("#")
            iid = "\\".join(parts[:3]).upper() if len(parts) >= 3 else path
            # накопители (флешки, внешние SSD) — НЕ периферия: у них в
            # потомках всегда есть USBSTOR\… (BOT) или SCSI\…DISK… (UAS).
            # Их события отдаёт режим «Диск», здесь они только мешают
            if any(i.startswith(("USBSTOR\\", "SCSI\\DISK"))
                   for i in descendants(di.devinst)):
                continue
            # человеческое имя: осмысленный родитель -> осмысленный
            # ребёнок -> любой родитель -> любой ребёнок. Чисто
            # вендорские/системные метки (GENERIC_NAMES) пропускаем,
            # скобочные классы устройств («(Универсальное аудио
            # устройство USB)») — полезны, оставляем.
            NAMES = ([devname(di.devinst)]
                     + [devname(c) for c in children(di.devinst)])

            def meaningful(n):
                return n and n not in GENERIC_NAMES

            name = next((n for n in NAMES if meaningful(n)), "")
            if not name:
                name = next((n for n in NAMES if n), "")
            out[iid] = name
    finally:
        try:
            setupapi.SetupDiDestroyDeviceInfoList(hdev)
        except Exception:
            pass
    return out


def _usb_disk_snapshot():
    """{ключ: имя} USB- и съёмных дисков для режима «Диск».

    Ключ — стабильный instance-id USBSTOR/USB-девнода диска (или буква
    для не-USB съёмных, напр. кардридера). Имя — буква диска, если
    назначена, иначе описание устройства. ВАЖНО: внешний SSD/флешка
    часто отдают DRIVE_FIXED и/или не имеют буквы — старая проверка
    `GetDriveTypeW == DRIVE_REMOVABLE` по буквам их не видела вовсе.
    Поэтому перечисляем дисковые интерфейсы (GUID_DEVINTERFACE_DISK) и
    берём те, чья цепочка девнод уходит в USBSTOR\\… или USB\\….
    None — API недоступен."""
    try:
        kernel32 = ctypes.windll.kernel32
        setupapi = ctypes.windll.setupapi
        cfgmgr = ctypes.windll.cfgmgr32
    except Exception:
        return None

    class GUID(ctypes.Structure):
        _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD),
                    ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]

    # GUID_DEVINTERFACE_DISK {53F56307-B6BF-11D0-94F2-00A0C91EFB8B}
    guid = GUID(0x53F56307, 0xB6BF, 0x11D0,
                (ctypes.c_ubyte * 8)(0x94, 0xF2, 0x00, 0xA0,
                                     0xC9, 0x1E, 0xFB, 0x8B))

    class IFACE(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD),
                    ("guid", GUID),
                    ("flags", wintypes.DWORD),
                    ("reserved", ctypes.c_size_t)]

    class DEVINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD),
                    ("cls", GUID),
                    ("devinst", wintypes.DWORD),
                    ("reserved", ctypes.c_size_t)]

    HANDLE = wintypes.HANDLE
    kernel32.CreateFileW.restype = HANDLE
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, HANDLE)
    kernel32.DeviceIoControl.restype = wintypes.BOOL
    kernel32.DeviceIoControl.argtypes = (
        HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID)
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (HANDLE,)
    setupapi.SetupDiGetClassDevsW.restype = HANDLE
    setupapi.SetupDiGetClassDevsW.argtypes = (
        ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD)
    setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
    setupapi.SetupDiEnumDeviceInterfaces.argtypes = (
        HANDLE, ctypes.c_void_p, ctypes.POINTER(GUID),
        wintypes.DWORD, ctypes.POINTER(IFACE))
    setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
    setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = (
        HANDLE, ctypes.POINTER(IFACE), ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(DEVINFO))
    setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = (HANDLE,)
    cfgmgr.CM_Get_Parent.restype = wintypes.DWORD
    cfgmgr.CM_Get_Parent.argtypes = (
        ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, wintypes.ULONG)
    cfgmgr.CM_Get_Device_IDW.restype = wintypes.DWORD
    cfgmgr.CM_Get_Device_IDW.argtypes = (
        wintypes.DWORD, wintypes.LPWSTR, wintypes.ULONG, wintypes.ULONG)
    cfgmgr.CM_Get_DevNode_Registry_PropertyW.restype = wintypes.DWORD
    cfgmgr.CM_Get_DevNode_Registry_PropertyW.argtypes = (
        wintypes.DWORD, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG),
        ctypes.c_void_p, ctypes.POINTER(wintypes.ULONG), wintypes.ULONG)

    IOCTL_STORAGE_GET_DEVICE_NUMBER = 0x2D1080
    INVALID = HANDLE(-1).value

    def disk_number(path):
        # доступ 0 = только запрос параметров: без прав администратора
        h = kernel32.CreateFileW(path, 0, 3, None, 3, 0, None)
        if not h or h == INVALID:
            return None
        sdn = (wintypes.DWORD * 3)()   # STORAGE_DEVICE_NUMBER
        ret = wintypes.DWORD(0)
        ok = kernel32.DeviceIoControl(
            h, IOCTL_STORAGE_GET_DEVICE_NUMBER, None, 0,
            sdn, ctypes.sizeof(sdn), ctypes.byref(ret), None)
        kernel32.CloseHandle(h)
        return sdn[1] if ok else None   # DeviceNumber

    def devname(devinst):
        # FriendlyName (12) -> DeviceDesc (1); у USBSTOR-девнода это,
        # например, «TOSHIBA External USB 3.0 USB Device»
        for prop in (12, 1):
            buf = ctypes.create_unicode_buffer(512)
            cb = wintypes.ULONG(1024)
            try:
                if cfgmgr.CM_Get_DevNode_Registry_PropertyW(
                        devinst, prop, None, buf,
                        ctypes.byref(cb), 0) == 0 and buf.value:
                    return buf.value
            except Exception:
                pass
        return ""

    def usbstor_name(node, iu):
        # реестр часто отдаёт только обобщённое «(Стандартные дисковые
        # накопители)» — тогда разбираем имя из instance-id:
        # USBSTOR\DISK&VEN_TOSHIBA&PROD_EXTERNAL_USB_3.0&REV_0\SERIAL&0
        n = devname(node)
        if n and not n.startswith("("):
            return n
        try:
            seg = iu.split("\\")[1]
            ven = prod = ""
            for part in seg.split("&"):
                u = part.upper()
                if u.startswith("VEN_"):
                    ven = part[4:].replace("_", " ").strip()
                elif u.startswith("PROD_"):
                    prod = part[5:].replace("_", " ").strip()
            if ven or prod:
                return " ".join(x for x in (ven, prod) if x)
        except Exception:
            pass
        return n

    # 1) буквы → номера физических дисков (съёмные И фиксированные)
    letter_by_num = {}   # номер физдиска → [буквы]
    bitmask = kernel32.GetLogicalDrives()
    for i in range(26):
        letter = "%s:\\" % chr(ord("A") + i)
        if not bitmask & (1 << i):
            continue
        if kernel32.GetDriveTypeW(letter) not in (2, 3):
            continue   # сетевые/CD и прочее — не наша забота
        n = disk_number("\\\\.\\%s" % letter)
        if n is not None:
            letter_by_num.setdefault(n, []).append(letter)

    # 2) дисковые интерфейсы: предок USBSTOR\ / USB\ → это USB-диск
    hdev = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(guid), None, None, 0x02 | 0x10)
    if not hdev or hdev == INVALID:
        return None
    usb_key_by_num = {}   # номер физдиска → [ключ USB-диска, имя]
    try:
        idx = 0
        while True:
            fd = IFACE()
            fd.cbSize = ctypes.sizeof(IFACE)
            if setupapi.SetupDiEnumDeviceInterfaces(
                    hdev, None, ctypes.byref(guid), idx,
                    ctypes.byref(fd)) == 0:
                break
            idx += 1
            buf = ctypes.create_string_buffer(2048)
            ctypes.memmove(buf, (8).to_bytes(4, "little"), 4)
            di = DEVINFO()
            di.cbSize = ctypes.sizeof(DEVINFO)
            n = wintypes.DWORD(0)
            if setupapi.SetupDiGetDeviceInterfaceDetailW(
                    hdev, ctypes.byref(fd), buf, 2048,
                    ctypes.byref(n), ctypes.byref(di)) == 0:
                continue
            dn = disk_number(ctypes.wstring_at(ctypes.addressof(buf) + 4))
            node = di.devinst
            for _ in range(8):
                idb = ctypes.create_unicode_buffer(512)
                if cfgmgr.CM_Get_Device_IDW(node, idb, 512, 0) != 0:
                    break
                iu = idb.value.upper()
                if iu.startswith(("USBSTOR\\", "USB\\")):
                    usb_key_by_num[dn] = (iu, usbstor_name(node, iu))
                    break
                if iu.startswith(("PCI\\", "ACPI\\", "IDE\\")):
                    break
                parent = wintypes.DWORD(0)
                if cfgmgr.CM_Get_Parent(
                        ctypes.byref(parent), node, 0) != 0:
                    break
                node = parent.value
    finally:
        try:
            setupapi.SetupDiDestroyDeviceInfoList(hdev)
        except Exception:
            pass

    # 3) сшиваем: USB-диск с буквой → имя-буква; без буквы → описание
    out = {}
    for dn, (key, name) in usb_key_by_num.items():
        ls = letter_by_num.get(dn)
        out[key] = ls[0] if ls else (name or key)
    # не-USB съёмные (кардридер и т.п.) — по буквам, как раньше
    for dn, ls in letter_by_num.items():
        if dn not in usb_key_by_num:
            h = kernel32.GetDriveTypeW(ls[0])
            if h == 2:
                for L in ls:
                    out[L] = L
    return out


def _usb_fire(h2, verb, name):
    """Событие USB: out и text несут одинаковый человекочитаемый текст."""
    txt = "%s: %s" % (verb, name)
    h2.engine.fire(h2.node_id, {"out": txt, "text": txt})


def _st_usb(handle, node):
    kind = _prop(node, "kind", "disk") or "disk"
    mode = _prop(node, "mode", "any") or "any"
    label = "Периферия" if kind == "periph" else "Диск"

    def check(state, h2):
        if kind == "periph":
            snap = _usb_devices()
            what = "периферия"
        else:
            snap = _usb_disk_snapshot()
            what = "диски"
        if snap is None:
            # API недоступен: не считаем всё отключённым, ругаемся ОДИН раз
            if not state.get("warned"):
                state["warned"] = True
                h2.engine.log(
                    "error",
                    "USB: SetupAPI недоступен — %s не отслеживаются" % what)
            return
        if "snap" not in state:
            # первый тик: фиксируем baseline и ПОКАЗЫВАЕМ в логе,
            # что нода живёт (иначе «не работает» неотличимо от «спит»)
            state["snap"] = snap
            h2.engine.log("info", "USB (%s): слежу за устройствами, "
                          "сейчас подключено: %d" % (label, len(snap)))
            return
        prev = state["snap"]
        state["snap"] = snap
        for k in sorted(set(snap) - set(prev)):
            if mode in ("any", "arrive"):
                _usb_fire(h2, "Подключено", snap[k] or k)
        for k in sorted(set(prev) - set(snap)):
            if mode in ("any", "remove"):
                _usb_fire(h2, "Отключено", prev[k] or k)

    return _poll(handle, 1.0, check)


def _st_battery(handle, node):
    mode = _prop(node, "mode", "ac") or "ac"
    thr = int(_fnum(_prop(node, "threshold", 20), 20))

    class SPS(ctypes.Structure):
        _fields_ = [("ac", ctypes.c_byte), ("flag", ctypes.c_byte),
                    ("percent", ctypes.c_byte), ("_r", ctypes.c_byte),
                    ("sec", ctypes.c_ulong), ("_r2", ctypes.c_ulong)]

    def read():
        st = SPS()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)) == 0:
            return None
        return st

    def check(state, h2):
        st = read()
        if st is None:
            return
        if mode == "ac":
            prev = state.setdefault("ac", st.ac)
            if st.ac in (0, 1) and prev != st.ac:
                h2.fire("on" if st.ac == 1 else "off")
            state["ac"] = st.ac
        else:
            pct = st.percent
            if pct == 255:
                return
            prev = state.setdefault("low", False)
            if pct <= thr and not prev:
                h2.fire(int(pct))
            state["low"] = pct <= thr

    return _poll(handle, 5.0, check)


def _st_pixel(handle, node):
    x = _fnum(_prop(node, "x", 100), 100)
    y = _fnum(_prop(node, "y", 100), 100)

    def check(state, h2):
        rgb = _pixel(x, y)
        prev = state.setdefault("rgb", rgb)
        if rgb != prev:
            h2.fire("%d %d %d" % rgb)
        state["rgb"] = rgb

    return _poll(handle, 0.1, check)


_OCR_WINRT_PKGS = ["winrt-Windows.Media.Ocr", "winrt-Windows.Graphics.Imaging",
                   "winrt-Windows.Storage.Streams", "winrt-Windows.Globalization",
                   "winrt-Windows.Foundation",
                   "winrt-Windows.Foundation.Collections"]


def _ocr_packages_ready():
    """Проверка всех winrt-пакетов OCR ПО ФАЙЛАМ, БЕЗ ИМПОРТА.

    Импорт winrt грузит нативную DLL (msvcp140.dll и др.) в процесс
    сервера — после этого pip не может заменить файлы (WinError 5),
    и установка циклится вечно. Проверка по файлам размыкает круг:
    пока чего-то нет -> NeedPackagesError -> pip --upgrade в чистом
    процессе -> только потом первый импорт.

    ВАЖНО: если неполон ХОТЬ ОДИН пакет — запрашиваем ВСЕ разом.
    Все winrt-* живут в общей папке winrt\\, а pip --target --upgrade
    сносит её ЦЕЛИКОМ и распаковывает только запрошенные пакеты:
    запрос подмножества стирает файлы остальных (пинг-понг
    «починили одно — сломали другое»)."""
    rels = ("media/ocr", "graphics/imaging", "storage/streams",
            "foundation/collections", "globalization", "foundation")
    for rel in rels:
        ok = False
        for sp in sys.path:
            if not sp:
                continue
            d = os.path.join(sp, "winrt", "windows", *rel.split("/"))
            # пакет цел, если есть и папка, и __init__.py: полый пакет
            # после неудачного rmtree не должен проходить проверку
            if os.path.isfile(os.path.join(d, "__init__.py")):
                ok = True
                break
        if not ok:
            return list(_OCR_WINRT_PKGS)
    # winrt-runtime: pywinrt 3.x делает winrt/ ПАКЕТОМ-ПРОСТРАНСТВОМ —
    # в корне winrt/ НЕТ __init__.py (и это нормально). Runtime узнаём
    # по подпакету winrt/runtime/__init__.py или нативному _winrt*.pyd.
    for sp in sys.path:
        if not sp:
            continue
        base = os.path.join(sp, "winrt")
        if os.path.isdir(base) and (
                os.path.isfile(os.path.join(base, "runtime", "__init__.py"))
                or any(f.startswith("_winrt") and f.endswith(".pyd")
                       for f in os.listdir(base))):
            return []   # всё на месте
    return list(_OCR_WINRT_PKGS)


def _ocr_ns(name):
    """Импорт WinRT-модуля. Вызывать ТОЛЬКО после _ocr_packages_ready() —
    иначе частичный импорт загрузит DLL и заблокирует доустановку."""
    import importlib
    try:
        return importlib.import_module("winrt.windows." + name)
    except ImportError:
        pass
    try:
        return importlib.import_module("winsdk.windows." + name)
    except ImportError:
        return None


def _st_ocr(handle, node):
    """OCR нашёл текст: ВСТРОЕННЫЙ Windows OCR (Windows.Media.Ocr) —
    офлайн, без внешних программ. Нужны только pip-пакеты winrt-* (ставятся
    сами в папку плагина) и языковой пакет Windows."""
    x = int(_fnum(_prop(node, "x", 0), 0))
    y = int(_fnum(_prop(node, "y", 0), 0))
    w = int(_fnum(_prop(node, "w", 300), 300))
    h = int(_fnum(_prop(node, "h", 100), 100))
    text = str(_prop(node, "text", "") or "").lower()
    lang = str(_prop(node, "lang", "auto") or "auto")

    missing = _ocr_packages_ready()
    if missing:
        raise NeedPackagesError(missing + ["pillow"])
    from PIL import ImageGrab
    import asyncio

    ocr_mod = _ocr_ns("media.ocr")
    imaging = _ocr_ns("graphics.imaging")
    streams = _ocr_ns("storage.streams")
    if not all((ocr_mod, imaging, streams)):
        raise RuntimeError(
            "Не удалось загрузить модули Windows OCR (winrt). "
            "Перезапустите run.bat и попробуйте снова.")

    def make_engine():
        # в pywinrt это свойство, в некоторых биндингах — метод: терпим оба
        av = ocr_mod.OcrEngine.available_recognizer_languages
        langs = list(av() if callable(av) else av)
        if lang and lang != "auto":
            for l in langs:
                if l.language_tag.lower().startswith(lang.lower()):
                    return ocr_mod.OcrEngine.try_create_from_language(l)
        for l in langs:
            if l.language_tag.lower().startswith("ru"):
                return ocr_mod.OcrEngine.try_create_from_language(l)
        try:
            return ocr_mod.OcrEngine.try_create_from_user_profile_languages()
        except Exception:
            return None

    engine = make_engine()
    if engine is None:
        raise RuntimeError(
            "Windows OCR недоступен — добавьте русский языковой пакет: "
            "Параметры → Время и язык → Язык и регион → русский "
            "(языковые компоненты, включая распознавание текста).")

    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()

    def recognize(img):
        """PIL-картинка -> распознанный текст (Windows OCR).

        ВАЖНО: всё — ОДНА coroutine. run_coroutine_threadsafe принимает
        только настоящие coroutine: pywinrt-объекты, возвращённые
        recognize_async/create_async, awaitable, но не coroutine —
        их можно только await-ить внутри async-функции (как в RuVoise)."""
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

    def check(state, h2):
        img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
        found = recognize(img)
        if not found:
            return
        if text and text not in found.lower():
            return
        if found != state.get("last"):
            state["last"] = found
            # как у «Голосовой команды»: out — событие, text — строка
            h2.engine.fire(h2.node_id, {"out": found, "text": found})

    stopper = _poll(handle, max(1.0, _fnum(_prop(node, "period", 2), 2)), check)

    def stop_all():
        try:
            loop.call_soon_threadsafe(loop.stop)
        except Exception:
            pass
        try:
            stopper()
        except Exception:
            pass
    return stop_all


def _vosk_model_ok(path):
    """В модели есть главные файлы Vosk. Граф бывает двух официальных видов:
    один HCLG.fst — ИЛИ пара HCLr.fst + Gr.fst (lookahead, как у
    vosk-model-small-ru-0.22). Оба варианта рабочие."""
    if not os.path.isfile(os.path.join(path, "am", "final.mdl")):
        return False
    g = os.path.join(path, "graph")
    if os.path.isfile(os.path.join(g, "HCLG.fst")):
        return True
    return (os.path.isfile(os.path.join(g, "HCLr.fst"))
            and os.path.isfile(os.path.join(g, "Gr.fst")))


def _find_vosk_model(root, lang):
    """Папка модели Vosk: своя языковая подпапка или модель прямо в _model.
    Чужую языковую модель НЕ подставляем — иначе автоскачивание нужной
    никогда не случится, а распознавание было бы неправильным языком."""
    try:
        subs = sorted(os.listdir(root))
    except Exception:
        return None
    # 1) подпапки с нужным языком (vosk-model-small-ru-...)
    for name in subs:
        sub = os.path.join(root, name)
        if lang in name.lower() and os.path.isdir(sub) and _vosk_model_ok(sub):
            return sub
    # 2) пользователь сам положил модель прямо в _model
    if os.path.isdir(root) and _vosk_model_ok(root):
        return root
    return None


VOSK_MODELS = {
    # lang: (файл zip, имя папки модели)
    "ru": ("vosk-model-small-ru-0.22.zip", "vosk-model-small-ru-0.22"),
    "en": ("vosk-model-small-en-us-0.15.zip", "vosk-model-small-en-us-0.15"),
}


def _ensure_vosk_model(handle, lang):
    """Модель Vosk: если нет — скачивается сама в plugins/triggers/_model."""
    model_root = os.path.join(os.path.dirname(__file__), "_model")
    model_path = _find_vosk_model(model_root, lang)
    if model_path is not None:
        return model_path
    zip_name, dir_name = VOSK_MODELS.get(lang, VOSK_MODELS["ru"])
    url = "https://alphacephei.com/vosk/models/" + zip_name
    import zipfile
    import urllib.request
    os.makedirs(model_root, exist_ok=True)
    zip_path = os.path.join(model_root, zip_name)
    try:
        handle.engine.log("info",
                          "Голос: скачиваю модель распознавания (%s, ~45 МБ, один раз)..."
                          % zip_name)
        # качаем с контролем: Content-Length vs фактический размер файла —
        # недокачанный zip и был причиной «Folder does not contain model files»
        req = urllib.request.Request(url, headers={"User-Agent": "KeyFlow/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(zip_path, "wb") as fh:
            total = resp.headers.get("Content-Length")
            got = 0
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                fh.write(chunk)
                got += len(chunk)
        if total and int(total) != got:
            raise RuntimeError("скачалось %d из %d байт — соединение оборвалось"
                               % (got, int(total)))
        handle.engine.log("info", "Голос: проверяю архив (%.1f МБ)..." % (got / 1048576.0))
        with zipfile.ZipFile(zip_path) as zf:   # упадёт здесь, если zip битый
            if zf.testzip() is not None:
                raise RuntimeError("архив повреждён")
            # старая битая папка (недокачанный прошлый раз) — долой, не смешивать
            import shutil
            stale = os.path.join(model_root, dir_name)
            if os.path.isdir(stale) and not _vosk_model_ok(stale):
                shutil.rmtree(stale, ignore_errors=True)
            handle.engine.log("info", "Голос: распаковываю модель...")
            zf.extractall(model_root)
        try:
            os.remove(zip_path)
        except Exception:
            pass
        model_path = _find_vosk_model(model_root, lang)
    except Exception as e:
        # битые следы убираем, чтобы следующий запуск скачал заново
        try:
            if os.path.isfile(zip_path):
                os.remove(zip_path)
        except Exception:
            pass
        try:
            import shutil
            bad = os.path.join(model_root, dir_name)
            if os.path.isdir(bad) and not _vosk_model_ok(bad):
                shutil.rmtree(bad, ignore_errors=True)
        except Exception:
            pass
        raise RuntimeError(
            "Не удалось скачать модель (%s). Скачайте %s с "
            "https://alphacephei.com/vosk/models и распакуйте в: %s. (%s)"
            % (e, zip_name, model_root, e))
    if model_path is None:
        # неполная модель не должна блокировать следующие запуски: убираем
        try:
            import shutil
            stale = os.path.join(model_root, dir_name)
            if os.path.isdir(stale) and not _vosk_model_ok(stale):
                shutil.rmtree(stale, ignore_errors=True)
        except Exception:
            pass
        raise RuntimeError("Модель скачана, но неполна — она удалена, "
                           "запустите воркфлоу снова (скачается заново).")
    handle.engine.log("info", "Голос: модель готова — распознавание офлайн.")
    return model_path


def _load_vosk_model(handle, model_path):
    """Model() с обходом главной ловушки Vosk на Windows: его C++-ядро не
    читает пути с кириллицей («does not contain model files» на валидной
    модели). Для не-ASCII пути модель один раз копируется в ASCII-папку
    %LOCALAPPDATA%\\KeyFlow\\vosk_models и грузится оттуда."""
    from vosk import Model
    try:
        model_path.encode("ascii")
        return Model(model_path)            # путь чистый — грузим напрямую
    except UnicodeEncodeError:
        pass
    base = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                        "KeyFlow", "vosk_models")
    os.makedirs(base, exist_ok=True)
    shadow = os.path.join(base, os.path.basename(model_path.rstrip("\\/")))
    if not _vosk_model_ok(shadow):
        import shutil
        handle.engine.log("info",
                          "Голос: путь с кириллицей — копирую модель в %s (один раз)..."
                          % base)
        if os.path.isdir(shadow):
            shutil.rmtree(shadow, ignore_errors=True)
        shutil.copytree(model_path, shadow)
    return Model(shadow)


def _st_voice(handle, node):
    """Голосовая команда: ЛОКАЛЬНОЕ распознавание (Vosk, офлайн, без сети)."""
    phrase = str(_prop(node, "phrase", "") or "").lower()
    if not phrase:
        raise RuntimeError("Введите фразу, по которой сработает нода")
    lang = str(_prop(node, "lang", "ru") or "ru")
    dev_query = str(_prop(node, "device", "") or "").lower()
    try:
        import pyaudio
        from vosk import KaldiRecognizer
    except Exception:
        raise NeedPackagesError(["vosk", "pyaudio"])
    ev = threading.Event()

    def loop():
        stream = None
        pa = None
        resampler = None
        try:
            model_path = _ensure_vosk_model(handle, lang)
            model = _load_vosk_model(handle, model_path)
            rec = KaldiRecognizer(model, 16000)
            pa = pyaudio.PyAudio()
            # выбор входного устройства: по умолчанию Windows может подставить
            # «не тот» вход — тогда в ноду приходит тишина. Значение из списка:
            # "#24 — Имя" (или просто "#24"/"24"/часть имени — старые сохранения).
            dev_idx = None
            qs = dev_query.strip()
            if qs and "— по умолчанию —" not in qs:
                m = ""
                if " — " in qs:
                    head, tail = qs.split(" — ", 1)
                    m = head.strip().lstrip("#").strip()
                    name_part = tail.strip()
                else:
                    m = qs.lstrip("#").strip()
                    name_part = qs
                if m.isdigit():
                    try:
                        if pa.get_device_info_by_index(int(m)).get(
                                "maxInputChannels", 0) > 0:
                            dev_idx = int(m)
                    except Exception:
                        dev_idx = None
                    if dev_idx is None:
                        handle.engine.log(
                            "err", "Голос: входного устройства №%s нет — "
                                   "использую устройство по умолчанию." % m)
                elif name_part:
                    low = name_part.lower()
                    for i in range(pa.get_device_count()):
                        try:
                            info = pa.get_device_info_by_index(i)
                        except Exception:
                            continue
                        if (info.get("maxInputChannels", 0) > 0
                                and low in str(info.get("name", "")).lower()):
                            dev_idx = i
                            break
                    if dev_idx is None:
                        handle.engine.log(
                            "err", "Голос: микрофон с именем «%s» не найден — "
                                   "использую устройство по умолчанию." % name_part)
            try:
                def_idx = pa.get_default_input_device_info().get("index")
            except Exception:
                def_idx = None
            for i in range(pa.get_device_count()):
                try:
                    info = pa.get_device_info_by_index(i)
                except Exception:
                    continue
                if info.get("maxInputChannels", 0) > 0:
                    mark = "  <-- по умолчанию" if i == def_idx else ""
                    handle.engine.log("info", "Голос: вход #%d: %s%s"
                                      % (i, info.get("name", "?"), mark))
            handle.engine.log("info", "Голос: открыт вход: %s"
                              % ("по умолчанию" if dev_idx is None else "#%d" % dev_idx))
            kw = {"input_device_index": dev_idx} if dev_idx is not None else {}
            # устройство может не уметь 16 кГц или быть «мертвым»: пробуем его
            # родную частоту (Vosk ждёт 16 кГц — ресемплим сами), затем моно/стерео
            rate = 16000
            try:
                _cap = pa.get_device_info_by_index(
                    dev_idx if dev_idx is not None
                    else pa.get_default_input_device_info()["index"])
                rate = int(_cap.get("defaultSampleRate") or 0) or 16000
            except Exception:
                rate = 16000
            if rate != 16000:
                try:
                    import audioop

                    def resampler(data, _r=[rate]):
                        converted, _st = audioop.ratecv(data, 2, 1, _r[0], 16000, None)
                        return converted
                except Exception:
                    rate = 16000
                    resampler = None
            last_err = None
            for ch in (1, 2):
                try:
                    stream = pa.open(format=pyaudio.paInt16, channels=ch,
                                     rate=rate, input=True,
                                     frames_per_buffer=4000, **kw)
                    if ch == 2:
                        import audioop

                        def _mono(data, _f=resampler):
                            m = audioop.tomono(data, 2, 0.5, 0.5)
                            return _f(m) if _f else m
                        resampler = _mono
                    break
                except Exception as e2:
                    last_err = e2
                    stream = None
            if stream is None and dev_idx is not None:
                handle.engine.log("err", "Голос: вход #%d не открылся (%s) — "
                                         "пробую устройство по умолчанию."
                                  % (dev_idx, last_err))
                try:
                    stream = pa.open(format=pyaudio.paInt16, channels=1,
                                     rate=rate, input=True,
                                     frames_per_buffer=4000)
                except Exception:
                    stream = None
                    rate = 16000
                    resampler = None
                    try:
                        stream = pa.open(format=pyaudio.paInt16, channels=1,
                                         rate=16000, input=True,
                                         frames_per_buffer=4000)
                    except Exception:
                        stream = None
            if stream is None:
                raise RuntimeError("Не удалось открыть микрофон: %s" % last_err)
            if rate != 16000 and resampler is None:
                raise RuntimeError("Устройство на %d Гц, ресемплинг недоступен" % rate)
            handle.engine.log("info", "Голос: поток открыт (%d Гц)" % rate)
        except Exception as e:
            _err_once(handle, {"_err_logged": False}, e)
            return

        def heard(s, key):
            import json as _json
            try:
                return (_json.loads(s or "{}").get(key) or "")
            except Exception:
                return ""

        last_fire = 0.0
        last_text = ""
        last_fired = ""
        silence = {"t": time.time(), "max": 0.0}
        try:
            while not ev.is_set() and not handle.engine.stopping:
                try:
                    data = stream.read(4000, exception_on_overflow=False)
                except Exception:
                    time.sleep(0.2)
                    continue
                if resampler is not None:
                    try:
                        data = resampler(data)
                    except Exception:
                        continue
                # детектор тишины: раз в 5 секунд — максимальный уровень
                # сигнала за период; 0.000 значит микрофон молчит —
                # предупреждение в лог один раз, без повторов
                try:
                    import audioop
                    lvl = audioop.rms(data, 2) / 32768.0
                    silence["max"] = max(silence["max"], lvl)
                except Exception:
                    pass
                if time.time() - silence["t"] > 5.0:
                    if silence["max"] < 0.001 and not silence.get("warned"):
                        silence["warned"] = True
                        handle.engine.log(
                            "err", "Голос: микрофон молчит (уровень 0.000) — "
                                   "выберите другой микрофон в поле «Микрофон».")
                    silence["t"] = time.time()
                    silence["max"] = 0.0
                if not rec.AcceptWaveform(data):
                    # partial-результат приходит ПО ХОДУ речи (мгновенно),
                    # текст в нём лежит под ключом "partial" (не "text")
                    text = heard(rec.PartialResult(), "partial")
                    if text and phrase in text.lower() and time.time() - last_fire > 1.5:
                        last_fire = time.time()
                        last_fired = text
                        handle.engine.fire(handle.node_id,
                                           {"out": None, "text": text})
                else:
                    # конец фразы: текст под ключом "text"
                    text = heard(rec.Result(), "text")
                    if text and text != last_text:
                        last_text = text
                    if (text and phrase in text.lower()
                            and text != last_fired):
                        last_fired = text
                        last_fire = time.time()
                        handle.engine.fire(handle.node_id,
                                           {"out": None, "text": text})
        except Exception as e:
            _err_once(handle, {"_err_logged": False}, e)
        finally:
            try:
                stream.stop_stream(); stream.close()
            except Exception:
                pass
            try:
                pa.terminate()
            except Exception:
                pass

    threading.Thread(target=loop, daemon=True).start()
    return ev.set


def _st_audio_loud(handle, node):
    try:
        import pyaudio
    except Exception:
        raise NeedPackagesError(["pyaudio"])
    thr = _fnum(_prop(node, "threshold", 0.3), 0.3)
    ev = threading.Event()

    def loop():
        state = {"t": 0}
        pa = None
        stream = None
        try:
            pa = pyaudio.PyAudio()
            # прямой поток микрофона: уровень считается каждые ~64 мс,
            # без «речевого детектора», который мог вообще не пускать звук
            stream = pa.open(format=pyaudio.paInt16, channels=1, rate=16000,
                             input=True, frames_per_buffer=1024)
            while not ev.is_set() and not handle.engine.stopping:
                try:
                    data = stream.read(1024, exception_on_overflow=False)
                except Exception:
                    time.sleep(0.2)
                    continue
                try:
                    import audioop
                    level = audioop.rms(data, 2) / 32768.0
                except Exception:
                    continue
                if level >= thr and time.time() - state["t"] > 1.5:
                    state["t"] = time.time()
                    handle.fire(round(level, 3))
        except Exception as e:
            _err_once(handle, {"_err_logged": False}, e)
        finally:
            try:
                if stream:
                    stream.stop_stream()
                    stream.close()
            except Exception:
                pass
            try:
                if pa:
                    pa.terminate()
            except Exception:
                pass

    threading.Thread(target=loop, daemon=True).start()
    return ev.set


def _st_uia(handle, node):
    try:
        import comtypes
        import comtypes.client
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as UIA
        uia = comtypes.client.CreateObject(UIA.CUIAutomation,
                                           interface=UIA.IUIAutomation,
                                           clsctx=comtypes.CLSCTX_INPROC_SERVER)
    except Exception:
        raise NeedPackagesError(["comtypes"])
    name = str(_prop(node, "element", "") or "").lower()

    def check(state, h2):
        el = uia.GetFocusedElement()
        cur = el.CurrentName or ""
        prev = state.setdefault("name", cur)
        state["name"] = cur
        if cur == prev:
            return
        if name and name not in cur.lower():
            return
        h2.fire(cur)

    return _poll(handle, 0.4, check)


# ------------------------------------------------------------------- ноды

def T(nid, en, ru, desc_en, desc_ru, params):
    return {
        "id": nid,
        "title": {"en": en, "ru": ru},
        "desc": {"en": desc_en, "ru": desc_ru},
        "category": "triggers",
        "inputs": [],
        "outputs": [{"name": "out", "type": "event"}],
        "params": list(params),
        "trigger": True,
    }


STARTS = {}


def _d(defn, start_fn):
    defn["start"] = start_fn
    STARTS[defn["id"]] = start_fn
    return defn


# отдельная нода с двумя выходами: «out» (событие) + «path» (полный путь)
FILE_NODE = T("triggers/file_change", "File/folder changed", "Изменение файла/папки",
              "Fires on file create/change/delete/rename in a folder. Toggle enables extension filter. Output 'path' = full file path.",
              "Создание/изменение/удаление/переименование файлов в папке. Тумблер включает фильтр расширений. Выход «path» — полный путь к файлу.",
              [P("path", "string", "", "Folder", "Папка"),
               P("recursive", "bool", False, "Subfolders", "Вложенные папки"),
               P("filter_ext", "bool", False, "Filter by extension", "Фильтр по расширению"),
               P("ext", "string", "txt", "Extensions: txt, png", "Расширения: txt, png"),
               P("kind", "combo", "all", "What", "Что",
                 ["all", "created", "changed", "deleted", "renamed"])])
FILE_NODE["outputs"] = [{"name": "out", "type": "event"},
                        {"name": "path", "type": "string"}]
NODES = [_d(FILE_NODE, _st_file_change)]

NODES += [
    _d(T("triggers/mouse_area", "Mouse enters area", "Мышь в области",
         "Fires when the cursor enters the rectangle X,Y,W,H.",
         "Срабатывает, когда курсор входит в прямоугольник X,Y,Ш,В.",
         [P("x", "number", 0, "X", "X"),
          P("y", "number", 0, "Y", "Y"),
          P("w", "number", 200, "Width", "Ширина"),
          P("h", "number", 200, "Height", "Высота")]),
       _st_mouse_area),

    _d(T("triggers/schedule", "Schedule", "Расписание",
         "Specific time with weekdays, or a cron expression.",
         "Конкретное время с днями недели или cron-выражение.",
         [P("mode", "combo", "time", "Mode", "Режим", ["time", "cron"]),
          P("time", "string", "12:00", "Time HH:MM", "Время ЧЧ:ММ"),
          P("days", "string", "", "Days: пн,вт (empty = all)", "Дни: пн,вт (пусто = все)"),
          P("cron", "string", "*/30 * * * *", "Cron (min hour dom mon dow)", "Cron (мин час день мес день_нед)")]),
       _st_schedule),

    _d(T("triggers/proc_start", "Program started", "Запуск программы",
         "Fires when a matching process appears (name.exe, PID or path part).",
         "Срабатывает при появлении процесса (имя.exe, PID или кусок пути).",
         [P("match", "string", "notepad.exe", "Process", "Процесс")]),
       _st_proc_start),

    _d(T("triggers/proc_exit", "Program closed", "Закрытие программы",
         "Fires when a matching process exits.",
         "Срабатывает при завершении процесса.",
         [P("match", "string", "notepad.exe", "Process", "Процесс")]),
       _st_proc_exit),

    _d(T("triggers/window_active", "Window activated", "Смена активного окна",
         "Fires when a window with the title/class becomes active (empty = any change).",
         "Окно с заголовком/классом стало активным (пусто = любая смена).",
         [P("title", "string", "", "Title contains", "Заголовок содержит"),
          P("cls", "string", "", "Window class", "Класс окна")]),
       _st_window_active),

    _d(T("triggers/clipboard", "Clipboard changed", "Буфер обмена",
         "Fires when new text/files/image appear in the clipboard. Optional: only when text contains the given substring.",
         "Срабатывает при новом тексте/файлах/картинке в буфере. Фильтр «текст содержит» — срабатывает только если в тексте есть эта часть слова.",
         [P("mode", "combo", "any", "Content", "Содержимое",
            ["any", "text", "files", "image"]),
          P("text_contains", "string", "", "Text contains", "Текст содержит")]),
       _st_clipboard),

    _d(T("triggers/network", "Network", "Сеть",
         "Internet up/down (any adapter: Ethernet or Wi-Fi), "
         "or a host:port became reachable. ⚠ Work in progress.",
         "Появление/пропадание интернета (любой адаптер: кабель или "
         "Wi-Fi) или доступность host:порт. ⚠ В разработке.",
         [P("mode", "combo", "inet_up", "Event", "Событие",
            ["inet_up", "inet_down", "port_open"]),
          P("host", "string", "127.0.0.1", "Host (for port mode)",
            "Хост (для режима «Порт»)"),
          P("port", "string", "53", "Port (for port mode)",
            "Порт (для режима «Порт»)")]),
       _st_network),

    _d(T("triggers/usb", "USB devices", "USB устройства",
         "Fires when a USB device connects/disconnects (disk or peripheral).",
         "Срабатывает при подключении/отключении USB-устройства (диск или периферия).",
         [P("kind", "combo", "disk", "Device", "Устройство",
            ["disk", "periph"]),
          P("mode", "combo", "any", "Event", "Событие",
            ["any", "arrive", "remove"])]),
       _st_usb),

    _d(T("triggers/battery", "Battery", "Батарея",
         "AC plugged/unplugged or charge below threshold (laptops).",
         "Питание подключили/отключили или заряд ниже порога (ноутбуки).",
         [P("mode", "combo", "ac", "Event", "Событие", ["ac", "low"]),
          P("threshold", "number", 20, "Percent, only for 'low'", "Процент, только для «Низкий заряд»")]),
       _st_battery),

    _d(T("triggers/pixel", "Pixel changed", "Пиксель изменился",
         "Fires when the screen pixel color at X,Y changes.",
         "Срабатывает при смене цвета пикселя экрана в X,Y.",
         [P("x", "number", 100, "X", "X"),
          P("y", "number", 100, "Y", "Y")]),
       _st_pixel),

    _d(T("triggers/ocr", "OCR found text", "OCR нашёл текст",
         "Fires when text appears in the screen area. Built-in Windows OCR (offline): needs the Windows language pack only.",
         "Срабатывает, когда в области экрана появляется текст. Встроенный Windows OCR (офлайн): нужен только языковой пакет Windows.",
         [P("x", "number", 0, "X", "X"),
          P("y", "number", 0, "Y", "Y"),
          P("w", "number", 400, "Width", "Ширина"),
          P("h", "number", 120, "Height", "Высота"),
          P("text", "string", "", "Text (empty = any)", "Текст (пусто = любой)"),
          P("lang", "string", "auto", "Lang (auto/ru/en)", "Язык (auto/ru/en)"),
          P("period", "number", 2, "Check every, sec", "Проверять каждые, сек")]),
       _st_ocr),

    _d(T("triggers/voice", "Voice command", "Голосовая команда",
         "Fires when the microphone hears the phrase. Offline (Vosk): model auto-downloaded once to plugins/triggers/_model. ⚠ Work in progress.",
         "Срабатывает, когда микрофон слышит фразу. Офлайн (Vosk): модель один раз скачивается в plugins/triggers/_model. ⚠ В разработке.",
         [P("phrase", "string", "компьютер", "Phrase", "Фраза"),
          P("device", "dynamic", "", "Microphone", "Микрофон", options_endpoint="audio_devices"),
          P("lang", "combo", "ru", "Model language", "Язык модели", ["ru", "en"])]),
       _st_voice),

    _d(T("triggers/audio_loud", "Loudness above threshold", "Громкость выше порога",
         "Fires when the microphone level exceeds the threshold (0-1).",
         "Срабатывает, когда уровень микрофона выше порога (0-1).",
         [P("threshold", "number", 0.3, "Threshold 0-1", "Порог 0-1")]),
       _st_audio_loud),

    _d(T("triggers/uia", "UI element focused", "Элемент UI активен",
         "Fires when the focused UI Automation element name matches (needs comtypes). ⚠ Work in progress.",
         "Срабатывает, когда имя активного элемента UI Automation совпало (нужен comtypes). ⚠ В разработке.",
         [P("element", "string", "", "Element name", "Имя элемента")]),
       _st_uia),
]

# у нод голоса, OCR и USB второй выход: «text» — строка для «Превью»
# (голос/OCR — распознанный текст, USB — «Подключено: …» / «Отключено: …»)
for _vd in NODES:
    if _vd["id"] in ("triggers/voice", "triggers/ocr", "triggers/usb"):
        _vd["outputs"].append({"name": "text", "type": "string"})

# ноды «в разработке»: детект интернета у «Сети», «Голосовая команда» и
# «Элемент UI активен» ещё ненадёжны — веб покажет тег в палитре и бейдж
# на канвасе (плюс пометку в описании ниже)
for _nd in NODES:
    if _nd["id"] in ("triggers/network", "triggers/voice", "triggers/uia"):
        _nd["wip"] = True


# --------------------------------------------------------------- регистрация

def register(api):
    for d in NODES:
        api.add_node(d)
