@echo off
REM Сборка Windows: один автономный P2C.exe (все внутри, ничего рядом не нужно).
REM Запуск: build_win.bat
cd /d "%~dp0"
python -m venv .venv
call .venv\Scripts\pip install -r requirements.txt pyinstaller
rmdir /s /q build dist 2>nul
.venv\Scripts\pyinstaller --noconfirm --clean ^
  --name P2C --onefile --windowed ^
  --exclude-module tkinter ^
  main.py
echo OK: dist\P2C.exe
