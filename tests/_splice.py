import io
p = r"C:\Users\Vad\Desktop\ТТесты2\Hotkey\plugins\triggers\plugin.py"
src = io.open(p, encoding="utf-8").read()
block = io.open(r"C:\Users\Vad\Desktop\ТТесты2\Hotkey\tests\_voice_block.py", encoding="utf-8").read()
a = src.index("def _st_voice(handle, node):")
b = src.index("def _st_uia(handle, node):")
src = src[:a] + block + "\n\n" + src[b:]
io.open(p, "w", encoding="utf-8", newline="\n").write(src)
print("replaced", b - a, "chars with", len(block))
