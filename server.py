#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KeyFlow — визуальная автоматизация Windows (ноды как в ComfyUI).

Запуск:  python server.py  (или run.bat)
Программа живёт в консоли, GUI открывается в браузере.
"""
import argparse
import io
import json
import os
import re
import shutil
import sys
import threading
import time
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Queue, Empty
from urllib.parse import urlparse, parse_qs, unquote

APP_DIR = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(APP_DIR, "vendor")
if os.path.isdir(VENDOR) and VENDOR not in sys.path:
    sys.path.insert(0, VENDOR)
sys.path.insert(0, APP_DIR)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from core import settings as cfg                     # noqa: E402
from core.engine import Engine                       # noqa: E402
from core import plugin_manager as pm                # noqa: E402

WEB_DIR = os.path.join(APP_DIR, "web")
WORKFLOWS_DIR = os.path.join(APP_DIR, "workflows")
OS_SCREENS_DIR = os.path.join(APP_DIR, "screenshots")
os.makedirs(WORKFLOWS_DIR, exist_ok=True)
os.makedirs(OS_SCREENS_DIR, exist_ok=True)

MIME = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".png": "image/png", ".jpg": "image/jpeg", ".svg": "image/svg+xml",
    ".ico": "image/x-icon", ".woff2": "font/woff2",
}


def audio_input_devices():
    """Входные аудиоустройства для выпадающего списка «Микрофон» в нодах.
    Возвращает [{"name": ..., "index": ...}]. PyAudio подтягивается нодой
    голоса лениво; пока его нет — пустой список (нода возьмёт устройство
    по умолчанию)."""
    try:
        import pyaudio
    except Exception:
        return []
    out = []
    try:
        pa = pyaudio.PyAudio()
        try:
            for i in range(pa.get_device_count()):
                try:
                    info = pa.get_device_info_by_index(i)
                except Exception:
                    continue
                if info.get("maxInputChannels", 0) > 0:
                    name = info.get("name", "?")
                    if isinstance(name, bytes):
                        try:
                            name = name.decode("utf-8", "replace")
                        except Exception:
                            name = "?"
                    out.append({"name": str(name), "index": i})
        finally:
            try:
                pa.terminate()
            except Exception:
                pass
    except Exception:
        pass
    return out


# --------------------------------------------------------------------------
# SSE hub: сервер -> браузер
# --------------------------------------------------------------------------
class Hub:
    def __init__(self):
        self.clients = []
        self.lock = threading.Lock()

    def register(self):
        q = Queue(maxsize=500)
        with self.lock:
            self.clients.append(q)
        return q

    def unregister(self, q):
        with self.lock:
            if q in self.clients:
                self.clients.remove(q)

    def publish(self, evt):
        data = json.dumps(evt, ensure_ascii=False)
        with self.lock:
            dead = []
            for q in self.clients:
                try:
                    q.put_nowait(data)
                except Exception:
                    dead.append(q)
            for q in dead:
                self.clients.remove(q)


HUB = Hub()


def log(level, msg):
    line = "[%s] %s: %s" % (time.strftime("%H:%M:%S"), level.upper(), msg)
    try:
        print(line, flush=True)
    except Exception:
        pass
    HUB.publish({"type": "log", "level": level, "msg": msg,
                 "ts": time.strftime("%H:%M:%S")})


PM = pm.PluginManager(os.path.join(APP_DIR, "plugins"), log)
PM.discover()
ENGINE = Engine(APP_DIR, lambda evt: HUB.publish(evt))
ENGINE.pm = PM                      # движку нужны пути плагинов для _packages
ENGINE.defs.update(PM.collect_defs())


# --------------------------------------------------------------------------
# Хранилище workflow
# --------------------------------------------------------------------------
def safe_name(name):
    name = str(name or "").strip()
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)[:60]
    return name or "main"


def wf_path(name):
    return os.path.join(WORKFLOWS_DIR, safe_name(name) + ".json")


def wf_list():
    out = []
    try:
        for f in sorted(os.listdir(WORKFLOWS_DIR)):
            if f.endswith(".json"):
                out.append(f[:-5])
    except Exception:
        pass
    if not out:
        out = ["main"]
    return out


def wf_load(name):
    p = wf_path(name)
    if not os.path.isfile(p):
        return {"name": safe_name(name), "graph": {"nodes": [], "links": [], "groups": []}}
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    if "graph" not in data:
        data = {"name": safe_name(name), "graph": data}
    return data


def wf_save(name, graph):
    data = {"name": safe_name(name), "graph": graph or {},
            "updated": time.strftime("%Y-%m-%d %H:%M:%S")}
    tmp = wf_path(name) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, wf_path(name))
    return True


def graph_of(name):
    data = wf_load(name)
    g = data.get("graph") or {"nodes": [], "links": [], "groups": []}
    # litegraph раньше не сохранял title нод (совпадал с дефолтом) — старые
    # воркфлоу грузятся без заголовков: дописываем из defs (id/type ноды).
    for n in g.get("nodes") or []:
        if n.get("title"):
            continue
        d = ENGINE.defs.get(n.get("type"))
        t = (d or {}).get("title") or {}
        n["title"] = t.get("ru") or t.get("en") or n.get("title")
    return g


def current_graph():
    s = cfg.load()
    return graph_of(s.get("active_workflow", "main"))


# --------------------------------------------------------------------------
# HTTP handler
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # -- helpers ---------------------------------------------------------
    def _plugin_import(self):
        """Принять multipart/form-data с .zip плагина и распаковать в plugins/."""
        ctype = self.headers.get("Content-Type", "")
        m = re.search(r'boundary="?([^";]+)"?', ctype)
        if not m:
            self._json({"ok": False, "message": "Ожидается multipart/form-data"}, 400)
            return
        boundary = m.group(1).encode("latin-1")
        n = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(n) if n else b""
        content = None
        for part in data.split(b"--" + boundary):
            part = part.strip(b"\r\n")
            if not part or part == b"--" or b"\r\n\r\n" not in part:
                continue
            head, _, payload = part.partition(b"\r\n\r\n")
            if payload.endswith(b"\r\n"):
                payload = payload[:-2]
            head_s = head.decode("utf-8", errors="replace")
            if 'filename="' in head_s and payload:
                content = payload
        if not content:
            self._json({"ok": False, "message": "Файл не найден в запросе"}, 400)
            return
        target_root = os.path.join(APP_DIR, "plugins")
        tmp = os.path.join(target_root, "__import_tmp__")
        if os.path.isdir(tmp):
            shutil.rmtree(tmp, ignore_errors=True)
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                names = [x for x in z.namelist() if x.strip()]
                common = None
                roots = {x.replace("\\", "/").split("/")[0] for x in names}
                if len(roots) == 1:
                    cand = next(iter(roots))
                    if all(x.replace("\\", "/") == cand + "/" or
                           x.replace("\\", "/").startswith(cand + "/") for x in names):
                        common = cand
                os.makedirs(tmp, exist_ok=True)
                for info in z.infolist():
                    name = info.filename.replace("\\", "/")
                    if name.endswith("/") or name.startswith("__MACOSX") or "/__MACOSX" in name:
                        continue
                    rel = name
                    if common and rel.startswith(common + "/"):
                        rel = rel[len(common) + 1:]
                    rel = rel.lstrip("/")
                    if not rel or ".." in rel.split("/"):
                        continue
                    dest = os.path.join(tmp, rel)
                    if not os.path.abspath(dest).startswith(os.path.abspath(tmp)):
                        continue
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with z.open(info) as src, open(dest, "wb") as out:
                        out.write(src.read())
            pid = None
            manifest = os.path.join(tmp, "plugin.json")
            if os.path.isfile(manifest):
                try:
                    with open(manifest, "r", encoding="utf-8") as f:
                        pid = safe_name(str(json.load(f).get("id") or "").replace("-", "_"))
                except Exception:
                    pid = None
            if not pid:
                raise ValueError("В архиве нет корректного plugin.json")
            final = os.path.join(target_root, pid)
            if os.path.isdir(final):
                shutil.rmtree(final)
            os.rename(tmp, final)
            PM.discover()
            ENGINE.defs = dict(PM.collect_defs())
            ENGINE.defs.setdefault("core/subgraph",
                {"id": "core/subgraph", "title": "Группа", "params": []})
            HUB.publish({"type": "plugins", "plugins": PM.infos()})
            self._json({"ok": True, "message": "Плагин «%s» импортирован" % pid,
                        "plugins": PM.infos()})
        except zipfile.BadZipFile:
            self._json({"ok": False, "message": "Это не ZIP-архив"}, 400)
        except Exception as e:
            self._json({"ok": False, "message": str(e)}, 400)
        finally:
            if os.path.isdir(tmp):
                shutil.rmtree(tmp, ignore_errors=True)

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _static(self, path):
        if path == "/":
            path = "/index.html"
        rel = unquote(path).lstrip("/")
        full = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not full.startswith(os.path.normpath(WEB_DIR)) or not os.path.isfile(full):
            self._json({"error": "not found"}, 404)
            return
        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = HUB.register()
        try:
            hello = json.dumps({"type": "hello", "ts": time.strftime("%H:%M:%S")})
            self.wfile.write(("data: %s\n\n" % hello).encode("utf-8"))
            self.wfile.flush()
            while True:
                try:
                    data = q.get(timeout=15)
                except Empty:
                    data = None
                if data is None:
                    self.wfile.write(b": ping\n\n")
                else:
                    self.wfile.write(("data: %s\n\n" % data).encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception:
            pass
        finally:
            HUB.unregister(q)

    def log_message(self, fmt, *args):
        pass  # шумные access-логи не нужны

    # -- GET --------------------------------------------------------------
    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        try:
            if path == "/api/events":
                self._sse()
            elif path == "/api/node_defs":
                defs = []
                for nid, d in sorted(ENGINE.defs.items()):
                    if nid == "core/subgraph":
                        continue
                    defs.append({k: v for k, v in d.items()
                                 if k not in ("exec", "start")})
                self._json(defs)
            elif path == "/api/plugins":
                self._json(PM.infos())
            elif path == "/api/plugins/export":
                pid = safe_name((parse_qs(url.query).get("id") or [""])[0].replace("-", "_"))
                p = PM.plugins.get(pid)
                if p is None or not os.path.isdir(p.path):
                    self._json({"error": "Плагин не найден"}, 404)
                    return
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                    for root, _dirs, files in os.walk(p.path):
                        for fn in files:
                            if fn == "__pycache__":
                                continue
                            full = os.path.join(root, fn)
                            rel = os.path.relpath(full, p.path)
                            z.write(full, os.path.join(pid, rel.replace(os.sep, "/")))
                data = buf.getvalue()
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition",
                                 'attachment; filename="%s.zip"' % pid)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif path == "/api/audio_devices":
                self._json(audio_input_devices())
            elif path == "/api/settings":
                self._json(cfg.load())
            elif path == "/api/workflows":
                self._json(wf_list())
            elif path == "/api/workflow":
                qs = parse_qs(url.query)
                name = (qs.get("name") or [cfg.load()["active_workflow"]])[0]
                self._json(wf_load(name))
            elif path == "/api/engine/status":
                st = ENGINE.status()
                st["active_workflow"] = cfg.load()["active_workflow"]
                self._json(st)
            else:
                self._static(path)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try:
                self._json({"error": str(e)}, 500)
            except Exception:
                pass

    # -- POST -------------------------------------------------------------
    def do_POST(self):
        url = urlparse(self.path)
        path = url.path
        try:
            if path == "/api/plugins/import":
                self._plugin_import()
                return
            body = self._body()
            if path == "/api/settings":
                s = cfg.update(body)
                HUB.publish({"type": "settings", "settings": s})
                self._json(s)
            elif path == "/api/workflow/save":
                name = body.get("name") or cfg.load()["active_workflow"]
                wf_save(name, body.get("graph"))
                self._json({"ok": True, "name": safe_name(name)})
            elif path == "/api/workflow/delete":
                name = safe_name(body.get("name"))
                p = wf_path(name)
                if os.path.isfile(p):
                    os.remove(p)
                self._json({"ok": True})
            elif path == "/api/engine/start":
                name = body.get("name") or cfg.load()["active_workflow"]
                name = safe_name(name)
                if ENGINE.running:
                    ENGINE.stop()
                ok, msg = ENGINE.start(name, graph_of(name))
                self._json({"ok": ok, "message": msg})
            elif path == "/api/engine/stop":
                ok, msg = ENGINE.stop()
                self._json({"ok": ok, "message": msg})
            elif path == "/api/engine/test_fire":
                node_id = body.get("node_id")
                ENGINE.ensure_runtime(cfg.load()["active_workflow"], current_graph())
                ENGINE.fire(node_id)
                self._json({"ok": True})
            elif path == "/api/hotkey_capture/start":
                hk = ENGINE.hotkeys
                if hk is None:
                    # слушателя нет (движок не запущен) — создадим временный
                    from core.hotkeys import HotkeyService
                    hk = HotkeyService(ENGINE)
                    hk.start()
                    ENGINE._cap_hk = hk   # держим ссылку, чистим при cancel
                if hk.capture_start():
                    self._json({"ok": True})
                else:
                    self._json({"ok": False, "message": "запись уже идёт"})
            elif path == "/api/hotkey_capture/poll":
                hk = ENGINE.hotkeys or ENGINE._cap_hk
                st = hk.capture_poll() if hk else None
                if st is None:
                    self._json({"ok": False, "message": "запись не идёт"})
                else:
                    tokens, held = st
                    self._json({"ok": True, "tokens": tokens, "held": held})
            elif path == "/api/hotkey_capture/cancel":
                hk = ENGINE.hotkeys or ENGINE._cap_hk
                if hk:
                    hk.capture_cancel()
                ENGINE._cap_hk = None   # временный слушатель больше не нужен
                self._json({"ok": True})
            elif path == "/api/plugins/toggle":
                ok, msg = PM.set_enabled(body.get("id"), bool(body.get("enabled")))
                if ok:
                    ENGINE.defs = dict(PM.collect_defs())
                    ENGINE.defs["core/subgraph"] = ENGINE.defs.get("core/subgraph",
                        {"id": "core/subgraph", "title": "Группа", "params": []})
                self._json({"ok": ok, "message": msg, "plugins": PM.infos()})
            elif path == "/api/plugins/reload":
                ok, msg = PM.reload(body.get("id"))
                if ok:
                    ENGINE.defs = dict(PM.collect_defs())
                    ENGINE.defs.setdefault("core/subgraph",
                        {"id": "core/subgraph", "title": "Группа", "params": []})
                self._json({"ok": ok, "message": msg, "plugins": PM.infos()})
            elif path == "/api/log":
                # диагностика из браузера: пишем в файл и в консоль/ленту
                lvl = str(body.get("level") or "info")[:8]
                msg = str(body.get("msg") or "")[:600].replace("\n", " ")
                try:
                    os.makedirs("logs", exist_ok=True)
                    with open(os.path.join("logs", "client.log"), "a",
                              encoding="utf-8") as f:
                        f.write("%s %s: %s\n" % (time.strftime("%H:%M:%S"), lvl, msg))
                except Exception:
                    pass
                HUB.publish({"type": "log", "level": lvl, "msg": msg,
                             "ts": time.strftime("%H:%M:%S")})
                self._json({"ok": True})
            else:
                self._json({"error": "unknown endpoint"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            log("error", "API %s: %s" % (path, e))
            try:
                self._json({"error": str(e)}, 500)
            except Exception:
                pass


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError)):
            return  # браузер/клиент просто отключился
        super().handle_error(request, client_address)


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="KeyFlow server")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    s = cfg.load()
    port = args.port or int(s.get("port", 8760))

    print("=" * 64, flush=True)
    print("  KeyFlow — визуальная автоматизация Windows", flush=True)
    print("  GUI:  http://127.0.0.1:%d" % port, flush=True)
    print("  Плагины: %s" % ", ".join(
        "%s(%s)" % (p.name, p.status) for p in PM.plugins.values()), flush=True)
    print("  Консоль держите открытой — это и есть процесс программы.", flush=True)
    print("=" * 64, flush=True)

    if s.get("open_browser", True) and not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open("http://127.0.0.1:%d" % port)).start()

    server = QuietServer(("127.0.0.1", port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановка…", flush=True)
        ENGINE.stop()
        server.server_close()


if __name__ == "__main__":
    main()
