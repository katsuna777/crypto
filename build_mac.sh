#!/bin/sh
# Сборка Mac: P2C.app + P2C.dmg (один файл для установки).
# Запуск: ./build_mac.sh
set -e
cd "$(dirname "$0")"
VER=$(python3 -c "import app_info; print(app_info.__version__)" 2>/dev/null || echo "2.5.0")
./.venv/bin/pip install -q -r requirements.txt pyinstaller 2>&1 | tail -n 2
rm -rf build dist "P2C-${VER}-mac.dmg"
./.venv/bin/pyinstaller --noconfirm --clean \
  --name P2C --windowed --onedir \
  --exclude-module tkinter --collect-all selenium \
  main.py 2>&1 | tail -n 5
DMG="P2C-${VER}-mac.dmg"
hdiutil create -volname P2C -srcfolder dist/P2C.app -ov -format UDZO "$DMG"
echo "OK: dist/P2C.app + $DMG"
