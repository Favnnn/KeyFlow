# -*- coding: utf-8 -*-
"""KeyFlow web plugin — ноды для работы с сайтами."""
import re
import webbrowser

import requests


def N(nid, en, ru, category, inputs=(), outputs=(("out", "event"),), params=(), desc=None):
    return {
        "id": nid,
        "title": {"en": en, "ru": ru},
        "desc": desc or {},
        "category": category,
        "inputs": [{"name": n, "type": t} for (n, t) in inputs],
        "outputs": [{"name": n, "type": t} for (n, t) in outputs],
        "params": list(params),
    }


def P(name, ptype, default, en, ru, options=None):
    p = {"name": name, "type": ptype, "default": default,
         "label": {"en": en, "ru": ru}}
    if options is not None:
        p["options"] = list(options)
    return p


EVENT_IN = ("in", "event")


def exec_open_url(api, p, inputs, emit):
    api.check_stop()
    url = inputs.get("url") or p["url"]
    url = str(url or "").strip()
    if not url:
        raise ValueError("Укажите ссылку")
    if "://" not in url:
        url = "https://" + url
    new_tab = bool(p.get("new_tab", True))
    if new_tab:
        webbrowser.open_new_tab(url)
    else:
        webbrowser.open(url, new=0)
    api.log("Открыт сайт: %s" % url)
    emit("out", None)


OPEN_URL_NODE = N(
    "web/open_url", "Open URL", "Открыть сайт", "web",
    inputs=(EVENT_IN, ("url", "string")), outputs=(("out", "event"),),
    params=[
        P("url", "string", "https://example.com", "URL", "Ссылка"),
        P("new_tab", "bool", True, "In new tab", "В новой вкладке"),
    ],
    desc={"en": "Opens a website in the default browser. The 'url' wire overrides the parameter.",
          "ru": "Открывает сайт в браузере по умолчанию. Провод «url» подменяет параметр."},
)
OPEN_URL_NODE["exec"] = exec_open_url


def exec_http_request(api, p, inputs, emit):
    api.check_stop()
    url = inputs.get("url") or p["url"]
    url = str(url or "").strip()
    if not url:
        raise ValueError("Укажите URL")
    method = str(p["method"] or "GET").upper()
    body = str(p.get("body") or "")
    r = requests.request(method, url, data=(body if body else None), timeout=15)
    api.log("HTTP %s %s -> %s" % (method, url, r.status_code))
    emit("status", r.status_code)
    emit("body", r.text)
    emit("out", None)


HTTP_NODE = N(
    "web/http_request", "HTTP request", "HTTP-запрос", "web",
    inputs=(EVENT_IN, ("url", "string")),
    outputs=(("out", "event"), ("status", "number"), ("body", "string")),
    params=[
        P("method", "combo", "GET", "Method", "Метод", ["GET", "POST", "PUT", "DELETE"]),
        P("url", "string", "https://api.github.com", "URL", "URL"),
        P("body", "string", "", "Body (POST)", "Тело (POST)"),
    ],
    desc={"en": "Performs an HTTP request and outputs status + response body.",
          "ru": "Выполняет HTTP-запрос, на выходе код статуса и тело ответа."},
)
HTTP_NODE["exec"] = exec_http_request


def exec_extract_regex(api, p, inputs, emit):
    api.check_stop()
    text = inputs.get("text")
    if text is None:
        text = p["text"]
    text = "" if text is None else str(text)
    try:
        m = re.search(str(p["pattern"] or ""), text)
    except re.error as e:
        raise ValueError("Некорректное регулярное выражение: %s" % e)
    if m:
        g = int(p.get("group", 0) or 0)
        try:
            val = m.group(g)
        except IndexError:
            val = m.group(0)
        emit("match", val)
        emit("found", True)
    else:
        emit("match", "")
        emit("found", False)
    emit("out", None)


EXTRACT_NODE = N(
    "web/extract_regex", "Extract by regex", "Извлечь по регулярке", "web",
    inputs=(EVENT_IN, ("text", "string")),
    outputs=(("out", "event"), ("match", "string"), ("found", "bool")),
    params=[
        P("pattern", "string", r"<title>(.*?)</title>", "Regex pattern", "Регулярка"),
        P("group", "number", 1, "Group", "Группа"),
        P("text", "string", "", "Text", "Текст"),
    ],
    desc={"en": "Searches text (e.g. HTTP response body) with a regular expression.",
          "ru": "Ищет в тексте (например, в теле HTTP-ответа) по регулярному выражению."},
)
EXTRACT_NODE["exec"] = exec_extract_regex


def register(api):
    api.add_node(OPEN_URL_NODE)
    api.add_node(HTTP_NODE)
    api.add_node(EXTRACT_NODE)
