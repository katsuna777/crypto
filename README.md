# P2C — merchant-бот Crypto Bot

GUI: ключ в «Настройки» → «Запустить». Режимы: Тест / Ловля / Авто.

## Запуск из исходника
```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python main.py   # GUI
```

## Скачать готовое
Releases → `P2C-win64.exe` (Windows) или `P2C-*.dmg` (Mac). Всё внутри, ставить ничего не надо.

## Сборка
```bash
./build_mac.sh    # Mac: dist/P2C.app + P2C-*.dmg
build_win.bat     # Windows: dist\P2C.exe
```

Релиз собирается сам: `git tag v2.5.0 && git push origin v2.5.0`.
