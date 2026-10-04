# -*- coding: utf-8 -*-
"""Загрузчик плагинов.

Плагин = папка в plugins/ с файлами:
    plugin.json  — манифест: { "id", "name", "version", "description", "enabled" }
    plugin.py    — код. Достаточно определить функцию register(api) и в ней
                   вызвать api.add_node({...}) для каждой ноды.

Изоляция: любой сбой (битый manifest, синтаксическая ошибка, падение
register, дубликат id) локализуется внутри плагина — программа продолжает
работать, плагин помечается как сломанный и виден во вкладке «Плагины».
"""
import importlib.util
import json
import os
import sys
import traceback


class PluginAPI:
    """То, что плагин получает в register(api)."""

    def __init__(self, plugin):
        self.plugin = plugin

    def add_node(self, node_def):
        if not isinstance(node_def, dict):
            raise ValueError("add_node: ожидается dict")
        nid = node_def.get("id")
        if not nid or "/" not in str(nid):
            raise ValueError("add_node: нода должна иметь id вида 'plugin/name'")
        if not callable(node_def.get("exec")) and not node_def.get("trigger"):
            raise ValueError("add_node(%s): нет exec-функции" % nid)
        node_def["plugin"] = self.plugin.id
        if nid in self.plugin.node_defs:
            raise ValueError("Дубликат id ноды: %s" % nid)
        self.plugin.node_defs[nid] = node_def

    def packages_dir(self):
        """Папка зависимостей ЭТОГО плагина (plugins/<имя>/_packages).

        Движок ставит сюда пакеты (pip install --target) и добавляет папку
        в sys.path: плагин самодостаточен, его папку можно копировать
        целиком вместе с зависимостями.
        """
        d = os.path.join(self.plugin.path, "_packages")
        os.makedirs(d, exist_ok=True)
        return d


class Plugin:
    def __init__(self, dirpath):
        self.path = dirpath
        self.id = os.path.basename(dirpath)
        self.name = self.id
        self.version = "0.0.0"
        self.description = ""
        self.enabled = True
        self.status = "loaded"      # loaded | error | disabled
        self.error = None
        self.node_defs = {}
        self.module = None
        self._manifest = {}

    # ------------------------------------------------------------------
    def read_manifest(self):
        mpath = os.path.join(self.path, "plugin.json")
        with open(mpath, "r", encoding="utf-8") as f:
            self._manifest = json.load(f)
        self.id = self._manifest.get("id", self.id)
        self.name = self._manifest.get("name", self.id)
        self.version = str(self._manifest.get("version", "0.0.0"))
        self.description = self._manifest.get("description", "")
        self.enabled = bool(self._manifest.get("enabled", True))

    def write_manifest_enabled(self, enabled):
        self._manifest["enabled"] = bool(enabled)
        mpath = os.path.join(self.path, "plugin.json")
        with open(mpath, "w", encoding="utf-8") as f:
            json.dump(self._manifest, f, ensure_ascii=False, indent=2)
        self.enabled = bool(enabled)

    # ------------------------------------------------------------------
    def load(self, log=None):
        """(Пере)загрузить код плагина. Никогда не бросает исключений."""
        self.node_defs = {}
        self.module = None
        self.error = None
        try:
            self.read_manifest()
        except Exception as e:
            self.status = "error"
            self.error = "plugin.json: %s" % e
            if log:
                log("error", "Плагин «%s»: %s" % (self.id, self.error))
            return

        if not self.enabled:
            self.status = "disabled"
            return

        ppath = os.path.join(self.path, "plugin.py")
        if not os.path.isfile(ppath):
            self.status = "error"
            self.error = "plugin.py не найден"
            if log:
                log("error", "Плагин «%s»: %s" % (self.id, self.error))
            return

        modname = "kflow_plugin_%s" % self.id.replace("-", "_").replace(".", "_")
        try:
            # папка зависимостей плагина сразу видна его коду (импорты в
            # start()/exec() находятся даже если пакет поставился позже)
            pkg_dir = os.path.join(self.path, "_packages")
            if os.path.isdir(pkg_dir) and pkg_dir not in sys.path:
                sys.path.insert(0, pkg_dir)
            if self.module is not None and modname in sys.modules:
                # reload: повторная регистрация поверх старой
                self.node_defs = {}
                importlib.reload(self.module)
            else:
                spec = importlib.util.spec_from_file_location(modname, ppath)
                module = importlib.util.module_from_spec(spec)
                sys.modules[modname] = module
                spec.loader.exec_module(module)
                self.module = module
            api = PluginAPI(self)
            if hasattr(self.module, "register") and callable(self.module.register):
                self.module.register(api)
            else:
                for nd in getattr(self.module, "NODES", []) or []:
                    api.add_node(nd)
            self.status = "loaded"
            if log:
                log("info", "Плагин «%s» v%s загружен (нод: %d)"
                    % (self.name, self.version, len(self.node_defs)))
        except Exception:
            self.status = "error"
            self.error = traceback.format_exc(limit=6)
            self.node_defs = {}
            if log:
                log("error", "Плагин «%s» сломан и отключён:\n%s" % (self.name, self.error))


class PluginManager:
    def __init__(self, plugins_dir, log=None):
        self.plugins_dir = plugins_dir
        self.log = log or (lambda level, msg: None)
        self.plugins = {}

    def discover(self):
        self.plugins = {}
        if not os.path.isdir(self.plugins_dir):
            return
        for entry in sorted(os.listdir(self.plugins_dir)):
            dirpath = os.path.join(self.plugins_dir, entry)
            if not os.path.isdir(dirpath):
                continue
            if not os.path.isfile(os.path.join(dirpath, "plugin.json")):
                continue
            p = Plugin(dirpath)
            p.load(self.log)
            self.plugins[p.id] = p

    def collect_defs(self):
        """Все ноды включённых плагинов: {node_type: def}."""
        defs = {}
        for p in self.plugins.values():
            if p.status == "loaded":
                for nid, nd in p.node_defs.items():
                    if nid in defs:
                        self.log("error", "Дубликат ноды «%s» (плагины %s / %s) — оставлен первый"
                                 % (nid, defs[nid].get("plugin"), p.id))
                        continue
                    defs[nid] = nd
        return defs

    def infos(self):
        out = []
        for p in self.plugins.values():
            out.append({
                "id": p.id,
                "name": p.name,
                "version": p.version,
                "description": p.description,
                "enabled": p.enabled,
                "status": p.status,
                "error": p.error,
                "nodes": sorted(p.node_defs.keys()) if p.status == "loaded" else [],
            })
        return out

    def set_enabled(self, plugin_id, enabled):
        p = self.plugins.get(plugin_id)
        if not p:
            return False, "Плагин не найден"
        try:
            p.write_manifest_enabled(enabled)
        except Exception as e:
            return False, str(e)
        p.load(self.log)
        return True, "ok"

    def reload(self, plugin_id):
        p = self.plugins.get(plugin_id)
        if not p:
            return False, "Плагин не найден"
        p.load(self.log)
        return True, "ok"
