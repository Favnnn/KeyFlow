import os, sys, time
sys.path.insert(0, os.getcwd())
from core.plugin_manager import PluginManager
from core.engine import Engine

pm = PluginManager(os.path.join(os.getcwd(), "plugins"))
pm.discover()
events = []
eng = Engine(os.getcwd(), on_event=events.append)
eng.defs.update(pm.collect_defs())

# нода с несуществующим пакетом -> NeedPackagesError -> очередь -> pip -> retry
graph = {"nodes": [{"id": 1, "type": "triggers/ocr", "title": "OCR",
                    "properties": {}, "inputs": [], "outputs": [{"name": "out"}]}],
         "links": []}
ok, msg = eng.start("t_autoinstall", graph)
print("started:", ok)
deadline = time.time() + 8
while time.time() < deadline:
    kinds = [e.get("msg", "") for e in events if e.get("type") == "log"]
    if any("Устанавливаю недостающие" in m for m in kinds):
        print("LOG:", [m for m in kinds if "Устанавливаю" in m][0][:80])
        break
    time.sleep(0.2)
else:
    print("FAIL: установщик не стартовал")
eng.stop()
print("DONE")
