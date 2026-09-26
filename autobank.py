#!/usr/bin/env python3
"""Автобанк: живой Chrome + Альфа-Банк, только для режима АВТО.

Идея:
- Человек ОДИН раз нажимает «Открыть браузер» и логинится в Альфу.
  Профиль хранится в ./chrome-profile — сессия живёт между перезапусками,
  бот работает 24/7 без повторного логина.
- Chrome запускается как ОБЫЧНЫЙ браузер (remote-debugging + attach),
  без флага webdriver и плашки «управляется автоматическим ПО» —
  иначе антибот Альфы режет запросы («Запрос отклонен», upstream error).
- В режиме АВТО после take ордера p2c вызывает pay_order(payment):
  скрипт в уже открытом браузере пытается оплатить сам. Тест/Ловля
  автобанк НЕ трогают.
- Кнопки ищутся ПО НАЗВАНИЯМ (видимый текст), а не по жёстким селекторам:
  скрипт парсит все кликабельные элементы страницы и жмёт подходящий.
  Что видит — пишет в лог («вижу кнопки: [...]»), по нему правим словари.
- Если у ордера есть ссылка — открывает её прямо в браузере и жмёт
  «Оплатить». Если только QR-payload — превращает его в ссылку
  (https://qr.nspk.ru/<payload>) и идёт тем же путём; параллельно
  пробует вставить payload/реквизиты в форму Альфы.
- Если оплатить не вышло — скриншот autopay_<id>.png + (False, причина),
  ордер отменяется (reason=bank), чтобы не схватить NotPaid.

AUTOBANK_DRY=1 — сухой прогон: всё ищет и заполняет, но финальную кнопку
«Оплатить» НЕ жмёт (для тестов на твоём QR/ссылке).
"""
import json as _json
import os
import re
import subprocess
import threading
import time
import urllib.request
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

ALFA_URL = os.environ.get("ALFA_URL", "https://web.alfabank.ru/dashboard/").strip() or "https://web.alfabank.ru/dashboard/"
PROFILE_DIR = os.environ.get("CHROME_PROFILE", os.path.join(os.getcwd(), "chrome-profile"))
TIMEOUT = float(os.environ.get("AUTOPAY_TIMEOUT_SEC", "120") or 120)
DEBUG_PORT = int(os.environ.get("AUTOBANK_DEBUG_PORT", "9222") or 9222)
HEADLESS = (os.environ.get("AUTOBANK_HEADLESS", "") or "").strip().lower() in ("1", "true", "yes")
# По требованию оператора: cert-ошибки (нет Russian Trusted CA) игнорируются,
# иначе web.alfabank.ru не открывается. Риск MITM — на стороне оператора.
IGNORE_CERT = (os.environ.get("AUTOBANK_IGNORE_CERT", "1") or "").strip().lower() in ("1", "true", "yes")
DRY = (os.environ.get("AUTOBANK_DRY", "") or "").strip().lower() in ("1", "true", "yes")

# --- словари названий кнопок (порядок = приоритет). Дописывай по логам. ---
BTN_TRANSFERS = [
    ["переводы"], ["платежи"], ["сбп"], ["оплата услуг"],
]
BTN_QR = [
    ["по qr"], ["qr-код"], ["qr код"], ["qr"], ["скан"],
]
BTN_PAY = [
    ["оплатить"], ["перевести"], ["подтвердить"], ["отправить"],
    ["далее"], ["продолжить"], ["заплатить"],
]
BTN_BACK = [["назад"], ["отмена"], ["закрыть"]]

INPUT_AMOUNT = ["сумм", "amount"]
INPUT_RECIP = ["карт", "телефон", "phone", "получател", "счёт", "счет", "card", "qr", "ссылк", "реквизит"]

# Маркер успеха после клика (текст на странице).
OK_WORDS = ("успеш", "выполнен", "отправлен", "оплачен", "готово", "принят",
            "success", "done", "completed")

# Маркеры того, что банк/WAF не отдал страницу (а не грузится медленно).
BLOCK_MARKERS = (
    "запрос был заблокирован",
    "запрос отклонен",
    "запрос отклонён",
    "upstream connect error",
    "connection termination",
    "attention required",
    "just a moment",
    "капча",
    "captcha",
)

_mu = threading.Lock()
_driver = None
_chrome_proc: Optional[subprocess.Popen] = None


_state = {"status": "закрыт", "busy": False, "last": ""}


def status() -> dict:
    with _mu:
        return dict(_state)


def _set(status_txt: str, busy: Optional[bool] = None, last: str = "") -> None:
    with _mu:
        _state["status"] = status_txt
        if busy is not None:
            _state["busy"] = busy
        if last:
            _state["last"] = last


def normalize_bank_url(raw: str) -> str:
    """Чинит ссылку: схлопывает задвоенную схему, добавляет https://."""
    u = (raw or "").strip().strip("'\"")
    if not u:
        return ALFA_URL
    u = re.sub(r"^(?:https?://)+", "https://", u, flags=re.I)  # https://https://x -> https://x
    if re.match(r"^\w[\w+\-.]*:", u):  # file://, about:, data: и т.п. — не трогаем
        return u
    return "https://" + u


def payload_to_link(payload: str) -> str:
    """QR-payload -> ссылка, которую можно открыть в браузере."""
    p = (payload or "").strip()
    if not p:
        return ""
    if re.match(r"^https?://", p, flags=re.I):
        return re.sub(r"^(?:https?://)+", "https://", p, flags=re.I)
    # Короткий код СБП вида AS... — ссылка NSPK; остальное отдаём как есть
    # (EMVCo-строку или токен потом пробуем вставить в поле QR в Альфе).
    if re.match(r"^AS[\w\-]{2,64}$", p):
        return "https://qr.nspk.ru/" + p
    return p


def extract_targets(payment: Dict) -> Dict:
    """Из сырых полей take-ответа: ссылки, QR-payload, реквизиты, сумма."""
    t: Dict = {"links": [], "payload": "", "recipient": "",
               "amount": str(payment.get("in_amount", "") or "")}
    for k, v in payment.items():
        if not isinstance(v, str) or not v.strip():
            continue
        val, low = v.strip(), k.lower()
        if re.match(r"^\w[\w+\-.]*:", val, flags=re.I):
            t["links"].append((k, normalize_bank_url(val)))
        elif any(s in low for s in ("qr", "payload", "sbp", "nspk", "code")):
            if not t["payload"] and len(val) >= 4:
                t["payload"] = val
        if any(s in low for s in ("card", "pan", "phone", "recipient", "requisit", "account")):
            digits = re.sub(r"\D", "", val)
            if len(digits) >= 10 and not t["recipient"]:
                t["recipient"] = val
    if t["payload"]:
        link = payload_to_link(t["payload"])
        if link.startswith("http") and not any(link == u for _, u in t["links"]):
            t["links"].append(("qr-payload", link))
    # уникализируем ссылки с сохранением порядка
    seen, uniq = set(), []
    for k, u in t["links"]:
        if u not in seen:
            seen.add(u)
            uniq.append((k, u))
    t["links"] = uniq
    return t


def browser_alive() -> bool:
    global _driver
    if _driver is None:
        return False
    try:
        _ = _driver.current_url  # дешёвый пинг сессии
        return True
    except Exception:
        return False


def _find_chrome_bin() -> str:
    """Находит Chrome/Chromium на macOS, Linux и Windows."""
    import shutil
    # 1. Явный путь из env — высший приоритет.
    env_bin = (os.environ.get("CHROME_BIN", "") or "").strip().strip("'\"")
    if env_bin and os.path.exists(env_bin):
        return env_bin
    # 2. PATH: chrome / google-chrome / chromium / msedge (на Win ставит и Chrome, и Edge).
    for name in ("chrome", "google-chrome", "google-chrome-stable", "chromium",
                 "chromium-browser", "msedge"):
        w = shutil.which(name)
        if w and os.path.exists(w):
            return w
    # 3. Windows registry: App Paths\chrome.exe (HKLM + HKCU).
    try:
        import winreg  # type: ignore
        for hive in (getattr(winreg, "HKEY_LOCAL_MACHINE", None),
                     getattr(winreg, "HKEY_CURRENT_USER", None)):
            if hive is None:
                continue
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as k:
                    p, _ = winreg.QueryValueEx(k, "")
                    if p and os.path.exists(p):
                        return p
            except OSError:
                continue
    except ImportError:
        pass
    # 4. Фиксированные пути: macOS, Linux, Windows.
    cands = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/snap/bin/chromium",
    ]
    for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA", "PROGRAMW6432"):
        base = os.environ.get(v, "")
        if base:
            cands += [
                os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"),
                os.path.join(base, "Chromium", "Application", "chrome.exe"),
                os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
            ]
    # Локальный профиль пользователя напрямую (если env-блок выше пуст).
    home = os.path.expanduser("~")
    if home and home != "~":
        cands.append(os.path.join(home, "AppData", "Local", "Google", "Chrome",
                                  "Application", "chrome.exe"))
    for b in cands:
        if b and os.path.exists(b):
            return b
    return ""


def _debug_alive(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3) as r:
            d = _json.loads(r.read().decode("utf-8", "ignore"))
            return bool(d.get("webSocketDebuggerUrl"))
    except Exception:
        return False


def _launch_chrome(port: int) -> str:
    """Поднять обычный Chrome с remote-debugging. Возвращает '' или текст ошибки."""
    global _chrome_proc
    if _debug_alive(port):
        return ""  # уже висит наш (перезапуск бота) — просто аттачимся
    bin_path = _find_chrome_bin()
    if not bin_path:
        return ("не найден Chrome (ставь Google Chrome / задай путь в CHROME_BIN, "
                "на Win обычно C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe)")
    os.makedirs(PROFILE_DIR, exist_ok=True)
    cmd = [
        bin_path,
        f"--remote-debugging-port={port}",
        # Chrome 111+: без этого DevTools-команды/attach режутся (типично на Win).
        "--remote-allow-origins=*",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-dev-shm-usage",
        "about:blank",
    ]
    if HEADLESS:
        cmd += ["--headless=new", "--disable-gpu", "--no-sandbox"]
    if IGNORE_CERT:
        cmd += ["--ignore-certificate-errors", "--allow-insecure-localhost"]
    try:
        _chrome_proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        return f"не стартовал процесс Chrome: {e}"
    for _ in range(50):  # до ~25с ждём DevTools
        if _debug_alive(port):
            return ""
        if _chrome_proc.poll() is not None:
            return ("профиль занят старым окном Chrome от бота — "
                    "закрой его и нажми «Открыть браузер» ещё раз")
        time.sleep(0.5)
    return "Chrome не поднял DevTools за 25с"


def _attach(port: int):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    o = Options()
    o.add_experimental_option("debuggerAddress", f"127.0.0.1:{port}")
    return webdriver.Chrome(options=o)


_BLANK_URLS = ("", "about:blank", "chrome://newtab/", "chrome://new-tab-page/")


def _goto(drv, url: str, log: Callable, tag: str, wait_s: float = 12) -> Tuple[bool, str]:
    """Открыть url и ПРОВЕРИТЬ, что вкладка реально туда ушла.

    Возвращает (moved, current_url). Редиректы банка/СБП — норма,
    успехом считаем любой уход с пустой вкладки.
    """
    try:
        drv.get(url)
    except Exception as e:
        # Тяжёлая страница (Альфа SPA) часто даёт TimeoutException —
        # стопаем загрузку и смотрим, что всё же приехало.
        log(f"autobank [{tag}]: get: {type(e).__name__}: {e} — смотрю что загрузилось")
        try:
            drv.execute_script("window.stop();")
        except Exception:
            pass
    cur = ""
    t0 = time.time()
    while time.time() - t0 < wait_s:
        try:
            cur = drv.current_url or ""
        except Exception as e:
            log(f"autobank [{tag}]: current_url: {e}")
            time.sleep(1)
            continue
        if cur not in _BLANK_URLS:
            break
        time.sleep(1)
    try:
        cur = drv.current_url or cur
    except Exception:
        pass
    moved = cur not in _BLANK_URLS
    log(f"autobank [{tag}]: вкладка: {(cur or 'пусто')[:120]}")
    return moved, cur


def _tidy_tabs(drv, log: Callable, tag: str) -> None:
    """Закрыть лишние пустые вкладки, встать на последнюю — ту, что видит человек.

    Без этого навигация может уехать в фоновую вкладку, а на экране висит пустая.
    """
    try:
        hs = list(drv.window_handles or [])
    except Exception as e:
        log(f"autobank [{tag}]: вкладки не читаются: {e}")
        return
    for h in hs[:-1]:
        try:
            drv.switch_to.window(h)
            if (drv.current_url or "") in _BLANK_URLS:
                drv.close()
        except Exception:
            pass
    try:
        left = list(drv.window_handles or [])
        if left:
            drv.switch_to.window(left[-1])
    except Exception as e:
        log(f"autobank [{tag}]: не встал на вкладку: {e}")


def _find_bank_tab(drv) -> bool:
    """Встать на вкладку с Альфой. True — нашёл, False — её нет."""
    try:
        hs = list(drv.window_handles or [])
    except Exception:
        return False
    for h in hs:
        try:
            drv.switch_to.window(h)
            if "alfabank" in (drv.current_url or ""):
                return True
        except Exception:
            continue
    try:
        if hs:
            drv.switch_to.window(hs[0])
    except Exception:
        pass
    return False


def page_block_reason(drv) -> str:
    """Пусто — страница живая; иначе маркер блокировки/ошибки края сети."""
    try:
        title = (drv.title or "")
    except Exception:
        title = ""
    try:
        body = (drv.find_element("tag name", "body").text or "")
    except Exception:
        body = ""
    cert_markers = ("нарушения конфиденциальности", "your connection is not private",
                    "privacy error", "err_cert")
    if any(m in title.lower() or m in body.lower() for m in cert_markers):
        return ("cert-error: нет российского корневого сертификата. "
                "Поставь «Russian Trusted Root CA» (Госуслуги) в систему "
                "или открой ссылку в обычном Chrome — если там так же, дело не в боте")
    low = body.lower()
    for m in BLOCK_MARKERS:
        if m in low:
            cut = body.strip().replace("\n", " ")[:160]
            return f"{m} ({cut})"
    return ""


def ensure_browser(log: Callable = print, alfa_url: str = "") -> Tuple[bool, str]:
    """Запустить обычный Chrome (если убит), приаттачиться и открыть Альфу.

    Идемпотентно, 24/7. Возвращает (ok, сообщение); ok=True + пометка
    «блокировка», если Альфа отдала WAF-заглушку, — окно при этом открыто
    и человек может проверить руками.
    """
    global _driver
    url = normalize_bank_url(alfa_url or os.environ.get("ALFA_URL", "") or ALFA_URL)
    if _driver is not None and browser_alive():
        try:
            _tidy_tabs(_driver, log, "alfa")
            if "alfabank" not in (_driver.current_url or ""):
                _goto(_driver, url, log, "alfa")
            block = page_block_reason(_driver)
            if block:
                msg = (f"браузер уже открыт, но Альфа режет запросы: {block}. "
                       "Проверь: открой эту же ссылку в ОБЫЧНОМ Chrome. "
                       "Если там так же — дело в IP/сети (нужен российский IP, выключи VPN дата-центра), не в боте.")
                _set("открыт (блок?)", busy=False, last=block)
                log(f"autobank: {msg}")
                return True, msg
            _set("открыт", busy=False)
            return True, "браузер уже открыт"
        except Exception as e:
            log(f"autobank: переоткрываю вкладку: {e}")
    try:
        import selenium  # noqa: F401 — проверяем наличие заранее
    except ImportError:
        return False, "нет selenium: ./.venv/bin/pip install -r requirements.txt"
    _set("запуск…", busy=False)
    err = _launch_chrome(DEBUG_PORT)
    if err:
        _set("ошибка запуска", busy=False, last=err)
        return False, err
    try:
        drv = _attach(DEBUG_PORT)
        drv.set_page_load_timeout(30)
        drv.implicitly_wait(3)
        with _mu:
            _driver = drv
    except Exception as e:
        _set("ошибка запуска", busy=False, last=str(e))
        return False, f"не приаттачился: {e}"
    _tidy_tabs(drv, log, "alfa")  # целимся в видимую вкладку, а не в фоновую
    moved, cur = _goto(drv, url, log, "alfa")
    if not moved:
        # Первая навигация сразу после attach иногда падает в пустоту — ретрай.
        time.sleep(1)
        moved, cur = _goto(drv, url, log, "alfa-retry")
    if not moved:
        msg = (f"Chrome открыт, но ссылка не открылась (вкладка пустая). "
               f"Открой {url} вручную в этом окне, залогинься — дальше бот подхватит сам. "
               f"Если висит about:blank дольше минуты — проверь сеть: нужен российский IP, выключи VPN. "
               f"Строка «вкладка:» в журнале покажет, где встало")
        _set("открыт (нет навигации)", busy=False, last=cur)
        log(f"autobank: {msg}")
        return False, msg
    block = page_block_reason(drv)
    if block:
        msg = (f"Chrome открыт ({url}) — залогинься один раз. "
               f"ВНИМАНИЕ: Альфа сейчас режет запросы: {block}. "
               "Открой ссылку в обычном Chrome: тот же блок = нужен российский IP / выключить VPN.")
        _set("открыт (блок?)", busy=False, last=block)
        log(f"autobank: {msg}")
        return True, msg
    _set("открыт", busy=False)
    log(f"autobank: Chrome открыт, Альфа: {url} — залогинься один раз, дальше сам")
    return True, "открыт"


def close_browser() -> None:
    global _driver
    with _mu:
        drv = _driver
        _driver = None
    if drv is not None:
        try:
            drv.quit()  # detach: окно Chrome живёт дальше, сессия сохраняется
        except Exception:
            pass
    _set("закрыт", busy=False)


# ================= поиск кнопок по названиям =================

def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def discover_buttons(drv, limit: int = 40) -> List[Tuple[object, str]]:
    """Все видимые кликабельные с текстом: [(элемент, текст)]."""
    try:
        els = drv.find_elements(
            "xpath",
            "//button | //a | //*[@role='button'] | "
            "//input[@type='submit' or @type='button'] | //div[@onclick]",
        )
    except Exception:
        return []
    out = []
    for el in els[:400]:
        try:
            if not el.is_displayed():
                continue
            t = _clean(el.text or el.get_attribute("value") or
                       el.get_attribute("aria-label") or "")
            if t:
                out.append((el, t))
        except Exception:
            continue
        if len(out) >= limit:
            break
    return out


def button_names(drv, limit: int = 12) -> List[str]:
    return [t for _, t in discover_buttons(drv, limit)]


def click_by_text(drv, groups: List[List[str]], log: Callable,
                  timeout: float = 10, exclude: Tuple[str, ...] = ("назад", "отмена", "закрыть")
                  ) -> Tuple[Optional[object], str]:
    """Жмёт кнопку по названию. groups — варианты по приоритету.
    Возвращает (элемент, текст) или (None, диагностика со списком кнопок)."""
    end = time.time() + timeout
    seen: List[str] = []
    while time.time() < end:
        items = discover_buttons(drv)
        seen = [t for _, t in items]
        for group in groups:
            cands = []
            for el, t in items:
                tl = t.lower()
                if any(x in tl for x in exclude):
                    continue
                for phrase in group:
                    if phrase.lower() in tl:
                        cands.append((len(t), el, t))
                        break
            if cands:
                cands.sort(key=lambda x: x[0])  # короче текст = точнее
                el, t = cands[0][1], cands[0][2]
                try:
                    drv.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                    time.sleep(0.3)
                    try:
                        el.click()
                    except Exception:
                        drv.execute_script("arguments[0].click();", el)
                    log(f"autobank: нажал «{t}»")
                    return el, t
                except Exception as e:
                    return None, f"кнопка «{t}» не кликнулась: {e}. Вижу: {seen[:12]}"
        time.sleep(0.7)
    want = [g[0] for g in groups]
    return None, f"не нашёл {want}. Вижу кнопки: {seen[:12] if seen else '— пусто —'}"


def fill_input(drv, keywords: List[str], value: str, log: Callable,
               timeout: float = 8) -> Tuple[bool, str]:
    """Вводит value в видимый input, чей placeholder/name/id содержит ключевое слово."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            inputs = drv.find_elements("xpath", "//input[not(@type='hidden')] | //textarea")
        except Exception:
            inputs = []
        for el in inputs:
            try:
                if not el.is_displayed():
                    continue
                hay = " ".join([
                    el.get_attribute("placeholder") or "",
                    el.get_attribute("name") or "",
                    el.get_attribute("id") or "",
                    el.get_attribute("aria-label") or "",
                    el.get_attribute("inputmode") or "",
                ]).lower()
                if any(k.lower() in hay for k in keywords):
                    try:
                        el.clear()
                        el.send_keys(value)
                    except Exception:
                        drv.execute_script(
                            "arguments[0].focus(); arguments[0].value=arguments[1];"
                            "arguments[0].dispatchEvent(new Event('input',{bubbles:true}));"
                            "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));",
                            el, value)
                    log(f"autobank: ввёл «{value}» в поле ({_clean(hay)[:40]})")
                    return True, hay
            except Exception:
                continue
        time.sleep(0.7)
    return False, f"нет поля {keywords}"


def _shot(drv, payment_id: str) -> str:
    name = f"autopay_{payment_id}_{datetime.now().strftime('%H%M%S')}.png"
    try:
        drv.save_screenshot(name)
        return name
    except Exception:
        return ""


def _wait_success(drv, log: Callable, tmo: float, pid: str) -> Tuple[bool, str]:
    """Ждём текст успеха / уход формы оплаты."""
    t0 = time.time()
    while time.time() - t0 < tmo:
        time.sleep(2)
        try:
            body = (drv.find_element("tag name", "body").text or "").lower()
        except Exception:
            body = ""
        if any(w in body for w in OK_WORDS):
            return True, f"подтверждено банком #{pid} ({_shot(drv, pid)})"
        el, _ = click_by_text_probe(drv)
        if el is None and "ошибк" not in body and "не удалось" not in body:
            return True, f"форма ушла, ошибок нет #{pid}"
        if "недостаточно" in body or "ошибк" in body or "не удалось" in body:
            return False, f"банк отклонил (см. {_shot(drv, pid)}): {body[:150]}"
    return False, f"таймаут {int(tmo)}с без подтверждения ({_shot(drv, pid)})"


def click_by_text_probe(drv) -> Tuple[Optional[object], str]:
    """Есть ли ещё кнопка оплаты на странице (для _wait_success, без лога)."""
    for el, t in discover_buttons(drv, 40):
        tl = t.lower()
        if any(p in tl for p in ("оплатить", "перевести", "подтвердить", "отправить", "заплатить")):
            if not any(x in tl for x in ("назад", "отмена", "закрыть")):
                return el, t
    return None, ""


# ================= главный сценарий =================

def pay_order(payment: Dict, log: Callable = print, timeout: Optional[float] = None) -> Tuple[bool, str]:
    """Оплатить ордер в уже открытом браузере. Только из АВТО-режима.

    payment: dict из take. Возвращает (ok, message). Один платёж за раз.
    Ссылка → открыть и жать «Оплатить». QR → в ссылку и так же.
    """
    tmo = float(timeout or float(os.environ.get("AUTOPAY_TIMEOUT_SEC", "") or TIMEOUT))
    dry = DRY or (os.environ.get("AUTOBANK_DRY", "") or "").strip().lower() in ("1", "true", "yes")
    pid = str(payment.get("payment_id", "?"))

    with _mu:
        if _state["busy"]:
            return False, "автобанк занят другим платежом"
        _state["busy"] = True
        _state["status"] = f"плачу #{pid}"

    try:
        ok, msg = ensure_browser(log)
        if not ok:
            return False, msg
        drv = _driver
        assert drv is not None

        # Что вообще прилетело в ордере — в лог (по нему правим словари/поля).
        keys = sorted(str(k) for k in payment.keys())
        log(f"autobank #{pid}: поля ордера: {keys}")
        tgt = extract_targets(payment)
        log(f"autobank #{pid}: сумма={tgt['amount']} ссылок={len(tgt['links'])} "
            f"qr={'да' if tgt['payload'] else 'нет'} реквизиты={'да' if tgt['recipient'] else 'нет'}")

        block = page_block_reason(drv)
        if block:
            return False, f"Альфа блокирует (антибот): {block}. Скрин {_shot(drv, pid)}"

        # --- путь 1: есть ссылка (из ордера или QR→ссылка) — открыть и платить ---
        for src, link in tgt["links"]:
            try:
                drv.switch_to.new_window("tab")
            except Exception:
                try:  # старый драйвер без new_window — вкладка через JS
                    drv.execute_script("window.open('about:blank','_blank');")
                    drv.switch_to.window(drv.window_handles[-1])
                except Exception:
                    log(f"autobank #{pid}: новую вкладку не дал — открываю в текущей")
            moved, cur = _goto(drv, link, log, f"qr-{src}", wait_s=10)
            if not moved:
                log(f"autobank #{pid}: ссылка не открылась ({src}) — пробую дальше")
                continue
            log(f"autobank #{pid}: открыл ссылку ({src}), вижу кнопки: {button_names(drv)}")
            block = page_block_reason(drv)
            if block:
                log(f"autobank #{pid}: ссылка отдала блок: {block} — пробую дальше")
                continue
            # сумма, если поле есть прямо на этой странице
            if tgt["amount"]:
                fill_input(drv, INPUT_AMOUNT, tgt["amount"].replace(".", ","), log, timeout=4)
            el, found = click_by_text(drv, BTN_PAY, log, timeout=10)
            if el is None:
                log(f"autobank #{pid}: на странице ссылки: {found}")
                continue
            if dry:
                return True, f"DRY OK: нашёл «{found}», оплату НЕ жал. Скрин {_shot(drv, pid)}"
            good, why = _wait_success(drv, log, tmo, pid)
            if good:
                return True, why
            log(f"autobank #{pid}: после клика: {why} — пробую следующую ссылку")
        if tgt["links"]:
            return False, f"ссылки кончились, оплаты нет. Скрин {_shot(drv, pid)}"

        # --- путь 2: ссылок нет — платим через Альфу (переводы/СБП по тексту) ---
        if not _find_bank_tab(drv):
            log(f"autobank #{pid}: вкладки с Альфой нет — открываю заново")
            _goto(drv, normalize_bank_url(os.environ.get("ALFA_URL", "") or ALFA_URL),
                  log, f"alfa-back-{pid}", wait_s=10)
        el, found = click_by_text(drv, BTN_TRANSFERS, log, timeout=10)
        if el is None:
            return False, f"{found}. Скрин {_shot(drv, pid)} — допиши BTN_TRANSFERS"
        time.sleep(2)
        log(f"autobank #{pid}: в переводах, вижу кнопки: {button_names(drv)}")
        el_qr, _ = click_by_text(drv, BTN_QR, log, timeout=5, exclude=())
        if el_qr is not None:
            time.sleep(1.5)
            log(f"autobank #{pid}: в QR-режиме, вижу кнопки: {button_names(drv)}")
        if tgt["recipient"]:
            fill_input(drv, INPUT_RECIP, tgt["recipient"], log, timeout=6)
        elif tgt["payload"]:
            fill_input(drv, INPUT_RECIP, tgt["payload"], log, timeout=6)
        if tgt["amount"]:
            fill_input(drv, INPUT_AMOUNT, tgt["amount"].replace(".", ","), log, timeout=6)
        el, found = click_by_text(drv, BTN_PAY, log, timeout=10)
        if el is None:
            return False, f"{found}. Скрин {_shot(drv, pid)} — допиши BTN_PAY"
        if dry:
            return True, f"DRY OK: нашёл «{found}», оплату НЕ жал. Скрин {_shot(drv, pid)}"
        good, why = _wait_success(drv, log, tmo, pid)
        return (True, why) if good else (False, f"{why}")
    except Exception as e:
        try:
            shot = _shot(_driver, pid) if _driver else ""
        except Exception:
            shot = ""
        return False, f"исключение: {e} ({shot})"
    finally:
        with _mu:
            _state["busy"] = False
            _state["status"] = "открыт"
