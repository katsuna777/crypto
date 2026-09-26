#!/usr/bin/env python3
"""Точка входа для сборки (PyInstaller: exe/dmg)."""
import os
import sys

# Замороженный exe/app: все относительные файлы (.env, active.id, ...)
# ищем рядом с exe, а не в cwd/_MEIPASS.
if getattr(sys, "frozen", False):
    try:
        _d = os.path.dirname(sys.executable)
        # macOS .app: Contents/MacOS/P2C -> рядом с .app ничего нет,
        # конфиг кладём рядом с .app (4 уровня вверх от exe = dir с .app).
        if _d.endswith("Contents/MacOS"):
            _d = os.path.normpath(os.path.join(_d, "..", "..", ".."))
        os.chdir(_d)
    except Exception:
        pass

import gui  # noqa: E402

if __name__ == "__main__":
    gui.main()
