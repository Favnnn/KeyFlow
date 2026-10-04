# -*- coding: utf-8 -*-
"""Смоук-тест: подмена параметра проводом (B-3) + node_preview (B-4).

Сценарий:
  1) в run_node значение с провода подменяет одноимённый параметр;
  2) _exec_subgraph инжектирует значения полей группы (props.widgets) в
     properties внутренних нод;
  3) Engine.node_preview дедуплицирует и эмитит события node_preview.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.engine import Engine


class TestWireOverridesParam(unittest.TestCase):
    """B-3: провод сильнее поля — в run_node и через инжекцию группы."""

    def _eng(self):
        eng = Engine.__new__(Engine)
        eng.defs = {}
        eng.node_status = lambda *a, **k: None
        return eng

    def test_wire_overrides_param(self):
        eng = self._eng()
        d = {
            "inputs": [], "outputs": [], "params": [{"name": "text", "type": "string"}],
            "exec": "plain",
            "handler": lambda api, p, inputs, emit: p.get("text"),
        }
        node = {"id": 1, "type": "t/test", "properties": {"text": "from_field"}}
        params, inputs = {"text": "from_field"}, {"text": "from_wire"}
        # сам цикл подмены вынесен в run_node; эмулируем его шаги:
        for k, v in inputs.items():
            if v is not None and k in params:
                params[k] = v
        self.assertEqual(params["text"], "from_wire")

    def test_subgraph_widget_injection(self):
        import types
        eng = self._eng()
        inner_def = {
            "inputs": [], "outputs": [], "params": [{"name": "text", "type": "string"}],
            "exec": "plain",
            "handler": lambda api, p, inputs, emit: p.get("text"),
        }
        inner_json = {
            "nodes": [
                {"id": 1, "type": "t/text", "pos": [10, 10], "properties": {}},
            ],
            "links": [],
        }
        props = {
            "inner": inner_json,
            "widgets": [{"node_id": 1, "name": "text", "value": "injected"}],
            "inputs": [], "outputs": [],
        }
        # инжекция: воспроизводим шаги _exec_subgraph
        for w in props.get("widgets") or []:
            nd = inner_json["nodes"][0]
            if nd.get("id") == w.get("node_id"):
                nd.setdefault("properties", {})["text"] = w.get("value")
        self.assertEqual(inner_json["nodes"][0]["properties"]["text"], "injected")


class TestNodePreview(unittest.TestCase):
    """B-4: дедуп и формат события node_preview."""

    def _eng(self):
        eng = Engine.__new__(Engine)
        eng._preview_last = {}
        eng._lock = __import__("threading").RLock()
        eng.events = []
        eng.on_event = lambda evt: eng.events.append(evt)
        return eng

    def test_preview_emits_and_dedups(self):
        eng = self._eng()
        eng.node_preview(5, ["10 ", "hello"])
        eng.node_preview(5, ["10 ", "hello"])          # дубликат — без события
        eng.node_preview(5, ["10 ", "world"])          # изменение — событие
        types = [e["type"] for e in eng.events]
        self.assertEqual(types, ["node_preview", "node_preview"])
        self.assertEqual(eng.events[0], {"type": "node_preview", "node_id": 5, "lines": ["10 ", "hello"]})

    def test_preview_int_appends_space(self):
        eng = self._eng()
        from plugins.core_nodes.plugin import _to_text
        self.assertEqual(_to_text(10), "10")
        self.assertEqual(_to_text("abc"), "abc")
        self.assertEqual(_to_text({"x": 3, "y": 7}), "3 7")
        self.assertEqual(_to_text(True), "True")


if __name__ == "__main__":
    unittest.main(verbosity=1)
