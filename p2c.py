#!/usr/bin/env python3
"""P2C bot: merchant-ловушка (WS -> take) + client-оплата чужих QR (createQR -> confirmQR).

Python-порт Go-версии (stdlib-логика сохранена 1:1):
очередь только по WS, без поллинга.
Лимиты API: p2c методы 20/мин, upload/refund/swap 10/мин,
getMe 30/мин, WS connect 20/мин + max 5 коннектов. 429 уважаем через retry_after.

Запуск: ./.venv/bin/python p2c.py [команда]
"""
import json
import os
import queue
import signal
import socket
import sys
import threading
import time
import warnings
import concurrent.futures
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

warnings.filterwarnings("ignore")

# Frozen exe/app: работаем из папки рядом с exe (там .env/active.id/spent.json).
if getattr(sys, "frozen", False):
    try:
        _d = os.path.dirname(sys.executable)
        if _d.endswith("Contents/MacOS"):
            _d = os.path.normpath(os.path.join(_d, "..", "..", ".."))
        os.chdir(_d)
    except Exception:
        pass

try:
    import orjson
    def json_loads(s: Any) -> Any:
        return orjson.loads(s)
    def json_dumps(v: Any) -> str:
        return orjson.dumps(v).decode("utf-8")
except ImportError:
    try:
        import ujson
        def json_loads(s: Any) -> Any:
            return ujson.loads(s)
        def json_dumps(v: Any) -> str:
            return ujson.dumps(v)
    except ImportError:
        def json_loads(s: Any) -> Any:
            return json.loads(s)
        def json_dumps(v: Any) -> str:
            return json.dumps(v)

try:
    import requests
    import websocket
except ImportError as e:
    sys.stderr.write(
        "Нет зависимостей: запусти через .venv: ./.venv/bin/python p2c.py ...\n"
        f"({e}; установка: .venv/bin/pip install -r requirements.txt)\n"
    )
    sys.exit(2)

# ---------- красивый терминал (только ANSI, без зависимостей) ----------

C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_RED = "\033[31m"
C_GREEN = "\033[32m"
C_YEL = "\033[33m"
C_BLUE = "\033[34m"
C_MAG = "\033[35m"
C_CYAN = "\033[36m"
C_WHITE = "\033[37m"

NO_COLOR = bool(os.environ.get("NO_COLOR")) or os.environ.get("TERM") == "dumb"


def paint(col: str, s: str) -> str:
    if NO_COLOR:
        return s
    return col + s + C_RESET


def clear_screen() -> None:
    if NO_COLOR:
        return
    sys.stdout.write("\033[H\033[2J")


def cut(s: Any, n: int) -> str:
    s = str(s)
    r = list(s)
    if len(r) <= n:
        return s
    if n <= 1:
        return "…"
    return "".join(r[: n - 1]) + "…"


def blog_plain(fmt: str, *a: Any) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    msg = fmt % a if a else fmt
    print(f"{ts} {msg}", flush=True)


# ---------- режимы ----------

MODE_TEST = 1  # ловит очередь, показывает совпадения, take НЕ делает
MODE_SEMI = 2  # ловит + take, complete вручную (кнопка C)
MODE_AUTO = 3  # всё сам: take + auto-complete через паузу (риск NotPaid!)

MODE_NAMES = {MODE_TEST: "ТЕСТ", MODE_SEMI: "ЛОВЛЯ", MODE_AUTO: "АВТОМАТ"}
MODE_COLORS = {MODE_TEST: C_CYAN, MODE_SEMI: C_YEL, MODE_AUTO: C_RED}


def mode_name(m: int) -> str:
    return MODE_NAMES.get(m, "?")


def mode_color(m: int) -> str:
    return MODE_COLORS.get(m, C_WHITE)


# ---------- config ----------


class Config:
    def __init__(self) -> None:
        self.api_key = ""
        self.base = "https://api.cr.bot/v1"
        self.min_rub = 0.0
        self.max_rub = 0.0  # 0 = без лимита
        self.max_total_rub = 0.0  # 0 = без лимита общего оборота
        self.min_reward = 0.0  # USDT
        self.brand_ban: Dict[str, bool] = {}
        self.mcc_ban: Dict[str, bool] = {}
        self.dry_run = False
        self.ws_token_mod = False
        self.ws_conns = 1  # параллельных WS-коннектов (1..5, лимит ключа)
        self.account_id = ""
        self.auto_delay = 20.0  # секунд, для АВТОМАТА: пауза take -> complete
        self.autopay = False  # автобанк (браузер+Альфа), только АВТО
        self.alfa_url = "https://web.alfabank.ru/dashboard/"
        self.autopay_timeout = 120.0


def getenv(k: str, default: str) -> str:
    v = os.environ.get(k)
    if v != "" and v is not None:
        return v  # type: ignore[return-value]
    return default


def load_dotenv() -> None:
    """Читает .env в каталоге запуска, export в шелле имеет приоритет."""
    try:
        f = open(".env", "r", encoding="utf-8")
    except OSError:
        return
    with f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            line = line.strip()
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            eq = line.find("=")
            if eq < 1:
                continue
            k = line[:eq].strip()
            v = line[eq + 1 :].strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
                v = v[1:-1]
            else:
                v = v.strip("'\"")
                i = v.find(" #")
                if i >= 0:
                    v = v[:i].strip()
            if not k or not v:
                continue
            if os.environ.get(k) == "" or os.environ.get(k) is None:
                os.environ[k] = v


def parse_float_env(keys: List[str], default: float) -> float:
    for k in keys:
        v = os.environ.get(k)
        if v:
            try:
                return float(v.strip())
            except ValueError:
                pass
    return default


def parse_set(s: str) -> Dict[str, bool]:
    m: Dict[str, bool] = {}
    for p in s.split(","):
        p = p.lower().strip()
        if p:
            m[p] = True
    return m


def load_config() -> Config:
    load_dotenv()
    cfg = Config()
    key = os.environ.get("CRBOT_API_KEY") or os.environ.get("API_KEY") or ""
    cfg.base = getenv("CRBOT_BASE", "https://api.cr.bot/v1").rstrip("/")
    ws_conns = 1  # один коннект по умолчанию: WS: ● ONLINE x1
    v = os.environ.get("WS_CONNS")
    if v:
        try:
            ws_conns = int(v.strip())
        except ValueError:
            pass
    ws_conns = max(1, min(5, ws_conns))  # лимит ключа: TooManyConnections
    delay_sec = parse_float_env(["AUTO_DELAY_SEC", "AUTO_DELAY"], 20)
    if delay_sec < 5:
        delay_sec = 5  # минимум 5с: complete раньше почти всегда даст NotPaid
    cfg.api_key = key.strip()
    cfg.min_rub = parse_float_env(["MIN_RUB", "MIN_AMOUNT"], 0)
    cfg.max_rub = parse_float_env(["MAX_RUB", "MAX_AMOUNT"], 0)
    cfg.max_total_rub = parse_float_env(["MAX_TOTAL_RUB", "TOTAL_LIMIT_RUB", "LIMIT_RUB"], 0)
    cfg.min_reward = parse_float_env(["MIN_REWARD", "MIN_REWARD_USDT"], 0)
    cfg.brand_ban = parse_set(getenv("BRAND_BAN", ""))
    cfg.mcc_ban = parse_set(getenv("MCC_BAN", ""))
    dry = getenv("DRY_RUN", "")
    cfg.dry_run = dry == "1" or dry.lower() == "true"
    cfg.ws_token_mod = getenv("WS_TOKEN_MODE", "") == "1"
    cfg.ws_conns = ws_conns
    cfg.account_id = getenv("ACCOUNT_ID", "")
    cfg.auto_delay = delay_sec
    v = (os.environ.get("AUTOPAY_ENABLED") or os.environ.get("AUTOBANK", "") or "").strip().lower()
    cfg.autopay = v in ("1", "true", "yes", "on")
    cfg.alfa_url = getenv("ALFA_URL", "https://web.alfabank.ru/dashboard/").strip() or "https://web.alfabank.ru/dashboard/"
    cfg.autopay_timeout = parse_float_env(["AUTOPAY_TIMEOUT_SEC", "AUTOBANK_TIMEOUT"], 120)
    return cfg


def load_spent_rub() -> float:
    try:
        if os.path.exists("spent.json"):
            with open("spent.json", "r", encoding="utf-8") as f:
                d = json.load(f)
                return float(d.get("spent_rub", 0.0))
    except Exception:
        pass
    return 0.0


def save_spent_rub(amt: float) -> None:
    try:
        with open("spent.json", "w", encoding="utf-8") as f:
            json.dump({"spent_rub": amt, "updated_at": datetime.now().isoformat()}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---------- состояние дашборда ----------


class UiLog:
    def __init__(self, at: str, text: str) -> None:
        self.at = at
        self.text = text


class UiState:
    def __init__(self, mode: int) -> None:
        self.mu = threading.Lock()
        self.mode = mode
        self.ws_ok = False
        self.ws_n = 0  # сколько WS-коннектов сейчас слушают
        self.ws_note = ""
        self.queue: List[dict] = []
        self.active: dict = {}
        self.has_active = False
        self.auto_count = 0  # секунд до auto-complete
        self.seen = 0
        self.matched = 0
        self.taken = 0
        self.done = 0
        self.errs = 0
        self.r429 = 0
        self.spent_rub = 0.0
        self.max_total_rub = 0.0
        self.last_take_ms = 0
        self.last_take_at = ""
        self.skipped_rate = 0  # НЕ брать: темп 3.05с/лимит
        self.skipped_busy = 0  # НЕ брать: предыдущий take в полёте
        self.skipped_filter = 0  # НЕ брать: фильтры/фейл-кэш/пауза/актив
        self.take_min_ms = 0  # статистика ПО ВСЕМ попыткам take
        self.take_max_ms = 0
        self.take_sum_ms = 0
        self.take_n = 0
        self.logs: List[UiLog] = []
        self.account_label = ""
        self.paused_note = ""
        self.bank_status = "закрыт"  # автобанк: закрыт|открыт|плачу…|ошибка
        self.bank_busy = False

    def record_take(self, ms: int) -> None:
        with self.mu:
            self.last_take_ms = ms
            self.last_take_at = datetime.now().strftime("%H:%M:%S")
            if self.take_n == 0:
                self.take_min_ms, self.take_max_ms = ms, ms
            else:
                self.take_min_ms = min(self.take_min_ms, ms)
                self.take_max_ms = max(self.take_max_ms, ms)
            self.take_sum_ms += ms
            self.take_n += 1

    def take_avg_ms(self) -> int:
        if self.take_n == 0:
            return 0
        return self.take_sum_ms // self.take_n

    def add_log(self, fmt: str, *a: Any) -> None:
        msg = fmt % a if a else fmt
        with self.mu:
            self.logs.append(UiLog(datetime.now().strftime("%H:%M:%S"), msg))
            if len(self.logs) > 12:
                self.logs = self.logs[-12:]

    def set_queue(self, m: Dict[str, dict]) -> None:
        arr = list(m.values())
        arr.sort(key=lambda o: fnum(o.get("reward_amount")) + fnum(o.get("boost_amount")), reverse=True)
        arr = arr[:8]
        with self.mu:
            self.queue = arr


# ---------- API client (одна keep-alive сессия = минимум latency) ----------


class ApiError(Exception):
    def __init__(self, code: int = 0, name: str = "", description: str = "",
                 request_id: str = "", retry_after: int = 0, min_amount: str = "",
                 max_amount: str = "", penalty_type: str = "", penalty_until: str = "",
                 raw: str = "") -> None:
        super().__init__(f"{name} ({code}): {description} [req {request_id}]")
        self.code = code
        self.name = name
        self.description = description
        self.request_id = request_id
        self.retry_after = retry_after
        self.min_amount = min_amount
        self.max_amount = max_amount
        self.penalty_type = penalty_type
        self.penalty_until = penalty_until
        self.raw = raw


def trunc(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[:n] + "…"


def fnum(s: Any) -> float:
    try:
        return float(str(s).strip())
    except (ValueError, TypeError, AttributeError):
        return 0.0


def cfg_passes(cfg: Config, o: dict) -> Tuple[bool, str]:
    amt = fnum(o.get("in_amount"))
    if cfg.min_rub > 0 and amt < cfg.min_rub:
        return False, f"amt {amt:.2f} < min {cfg.min_rub:.2f}"
    if cfg.max_rub > 0 and amt > cfg.max_rub:
        return False, f"amt {amt:.2f} > max {cfg.max_rub:.2f}"
    if cfg.min_reward > 0 and fnum(o.get("reward_amount")) + fnum(o.get("boost_amount")) < cfg.min_reward:
        return False, "reward low"
    if cfg.brand_ban.get(str(o.get("brand_name") or "").lower().strip()):
        return False, "brand ban"
    mcc = str(o.get("mcc") or "")
    if mcc and cfg.mcc_ban.get(mcc.lower()):
        return False, "mcc ban"
    if not o.get("qr_id"):
        return False, "no qr_id"
    return True, ""


class FastHTTPAdapter(requests.adapters.HTTPAdapter):
    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        kwargs["socket_options"] = [
            (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),
            (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
        ]
        super().init_poolmanager(*args, **kwargs)


class Client:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.s = requests.Session()
        adapter = FastHTTPAdapter(pool_connections=16, pool_maxsize=32)
        self.s.mount("https://", adapter)
        self.s.mount("http://", adapter)
        self.s.headers["X-API-Key"] = self.cfg.api_key
        self.s.headers["User-Agent"] = "p2cbot/1.0 (python, fast)"
        self.mu = threading.Lock()
        self.until: Dict[str, float] = {}  # method -> time.time(), до которого пауза после 429/penalty

    def wait_if_limited(self, method: str) -> None:
        with self.mu:
            t = self.until.get(method, 0)
        d = t - time.time()
        if d > 0:
            time.sleep(d)

    def set_limited(self, method: str, d_sec: float) -> None:
        if d_sec <= 0:
            return
        with self.mu:
            self.until[method] = time.time() + d_sec

    def do(self, method: str, path: str, body: Any = None) -> Tuple[Any, str]:
        self.wait_if_limited(method)
        try:
            if body is not None:
                if isinstance(body, (bytes, bytearray)):
                    headers = {"Content-Type": "application/json"}
                    resp = self.s.post(self.cfg.base + path, data=body, headers=headers, timeout=12)
                else:
                    resp = self.s.post(self.cfg.base + path, json=body, timeout=12)
            else:
                resp = self.s.get(self.cfg.base + path, timeout=12)
        except requests.RequestException as e:
            raise e
        try:
            e = resp.json()
        except ValueError:
            raise ApiError(code=resp.status_code, name="BadJSON",
                           description=f"http {resp.status_code} bad json: {trunc(resp.text, 300)}")
        if not isinstance(e, dict) or not e.get("ok"):
            e = e if isinstance(e, dict) else {}
            code = int(e.get("error_code") or 0) or resp.status_code
            ae = ApiError(
                code=code, name=str(e.get("error") or ""), description=str(e.get("description") or ""),
                request_id=str(e.get("request_id") or ""),
                retry_after=int(e.get("retry_after") or 0),
                min_amount=str(e.get("min_amount") or ""), max_amount=str(e.get("max_amount") or ""),
                penalty_type=str(e.get("penalty_type") or ""),
                penalty_until=str(e.get("penalty_end_at") or ""),
                raw=trunc(resp.text, 500),
            )
            if ae.code == 429:
                d = float(ae.retry_after) if ae.retry_after > 0 else 3.0
                if d < 1:
                    d = 3.0
                self.set_limited(method, d)  # уважаем retry_after — защита от бана
            raise ae
        return e.get("result"), str(e.get("request_id") or "")

    # ---------- методы API ----------
    def get_me(self) -> Any:
        r, _ = self.do("getMe", "/getMe", None)
        return r

    def get_config(self) -> Any:
        r, _ = self.do("getConfig", "/p2cMerchant/getConfig", None)
        return r

    def get_status(self) -> Any:
        r, _ = self.do("getStatus", "/p2cMerchant/getStatus", None)
        return r

    def get_accounts(self) -> List[dict]:
        r, _ = self.do("getAccounts", "/p2cMerchant/getAccounts", None)
        if isinstance(r, dict):
            accs = r.get("accounts") or []
            return accs if isinstance(accs, list) else []
        return []

    def get_ws_token(self) -> str:
        r, _ = self.do("getWsToken", "/p2cMerchant/getWsToken", None)
        if isinstance(r, dict):
            return str(r.get("ws_token") or "")
        return ""

    def take_payment(self, qr_id: str) -> Tuple[dict, str]:
        body_bytes = f'{{"qr_id":"{qr_id}"}}'.encode("utf-8")
        r, rid = self.do("takePayment", "/p2cMerchant/takePayment", body_bytes)
        return r if isinstance(r, dict) else {}, rid

    def complete_payment(self, payment_id: int, account_id: str) -> dict:
        r, _ = self.do("completePayment", "/p2cMerchant/completePayment",
                       {"payment_id": payment_id, "account_id": account_id})
        return r if isinstance(r, dict) else {}

    def cancel_payment(self, payment_id: int, reason: str) -> dict:
        """Отмена взятого в работу платежа. reason: bank|balance|qr|qr-paid."""
        r, _ = self.do("cancelPayment", "/p2cMerchant/cancelPayment",
                       {"payment_id": payment_id, "reason": reason})
        return r if isinstance(r, dict) else {}

    def get_pending_actions(self) -> List[dict]:
        """Платежи, требующие действия мерчанта (напр. upload_statement). До 100 штук."""
        r, _ = self.do("getPendingActions", "/p2cMerchant/getPendingActions", None)
        if isinstance(r, dict):
            acts = r.get("actions") or []
            return acts if isinstance(acts, list) else []
        return []

    def get_payment_status(self, payment_id: int) -> str:
        """1 редкий запрос для проверки активного ордера (лимит 20/мин — мы делаем 1/45с)."""
        r, _ = self.do("getPayments", "/p2cMerchant/getPayments",
                       {"payment_ids": [payment_id], "size": 1})
        pays = (r.get("payments") or []) if isinstance(r, dict) else []
        if not pays:
            raise ApiError(name="NotFound", description=f"payment {payment_id} not found")
        return str(pays[0].get("status") or "")

    def create_qr(self, payload: str) -> Tuple[dict, str]:
        r, rid = self.do("createQR", "/p2cClient/createQR", {"payload": payload})
        return r if isinstance(r, dict) else {}, rid

    def confirm_qr(self, qr_id: str) -> Tuple[dict, str]:
        r, rid = self.do("confirmQR", "/p2cClient/confirmQR", {"qr_id": qr_id})
        return r if isinstance(r, dict) else {}, rid


# ---------- merchant WS ----------

def ws_url(base: str, api_key: str, ws_token: str) -> str:
    from urllib.parse import quote
    u = base
    if u.startswith("https://"):
        u = "wss://" + u[len("https://"):]
    elif u.startswith("http://"):
        u = "ws://" + u[len("http://"):]
    if ws_token:
        return u + "/p2cMerchant/ws?ws_token=" + quote(ws_token)
    return u + "/p2cMerchant/ws?api_key=" + quote(api_key)


def resolve_account(api: Client, cfg: Config) -> Tuple[str, str]:
    try:
        accs = api.get_accounts()  # 1 запрос при старте, дальше кэш
    except Exception as e:
        return "", "счета: ошибка " + str(e)
    if cfg.account_id:
        for a in accs:
            if str(a.get("id")) == cfg.account_id:
                return str(a.get("id")), f"{a.get('id')} ({a.get('bank')}, {a.get('name')})"
        return "", "ACCOUNT_ID не найден среди счетов!"
    for a in accs:
        if a.get("can_complete"):
            cfg.account_id = str(a.get("id"))
            return str(a.get("id")), f"{a.get('id')} ({a.get('bank')}, {a.get('name')}) [авто]"
    return "", "нет счёта с can_complete=true!"


# ---------- баннер и выбор режима ----------

def banner() -> None:
    clear_screen()
    print(paint(C_CYAN, C_BOLD + "  ╔══════════════════════════════════════════════════╗"))
    print(paint(C_CYAN, C_BOLD + "  ║") + paint(C_WHITE, C_BOLD + "           P2C  BOT  •  Crypto Bot              ") + paint(C_CYAN, C_BOLD + "║"))
    print(paint(C_CYAN, C_BOLD + "  ╚══════════════════════════════════════════════════╝" + C_RESET))
    print(paint(C_DIM, "   Ловушка очереди (WS) + оплата чужих QR  •  быстро, без нагрузки, с уважением к лимитам"))
    print()


def menu() -> int:
    banner()
    print(f"   {paint(C_WHITE, C_BOLD + 'Выбери режим работы:')}")
    print()
    print(f"   {paint(C_CYAN, C_BOLD + '1) ТЕСТ')}  {paint(C_DIM, 'только смотрит очередь, показывает заказы,')}")
    print(f"      {paint(C_GREEN, 'ничего не берёт и не платит — безопасно')}")
    print()
    print(f"   {paint(C_YEL, C_BOLD + '2) ЛОВЛЯ')}  {paint(C_DIM, 'ловит заказ, показывает QR на оплату,')}")
    print(f"      {paint(C_DIM, 'ждёт твоего подтверждения: оплатил в банке → жми C')}")
    print()
    print(f"   {paint(C_RED, C_BOLD + '3) АВТОМАТ')}  {paint(C_DIM, 'ловит заказ, показывает QR и завершает сам')}")
    print(f"      {paint(C_RED, 'ВНИМАНИЕ: рубли по QR всё равно платишь ты/автобанк.')}")
    print(f"      {paint(C_DIM, 'Раннее завершение даст NotPaid и пенальти мерчанта!')}")
    print()
    print(f"   {paint(C_WHITE, 'Номер [1/2/3] (Enter = 2):')} ", end="", flush=True)
    try:
        line = sys.stdin.readline()
    except KeyboardInterrupt:
        print()
        sys.exit(0)
    line = (line or "").lower().strip()
    if line in ("1", "тест", "test"):
        return MODE_TEST
    if line in ("3", "автомат", "авто", "auto", "полный"):
        return MODE_AUTO
    return MODE_SEMI


def mode_help(m: int) -> str:
    if m == MODE_TEST:
        return "Q выход • только смотрит, ничего не берёт"
    if m == MODE_SEMI:
        return "C подтвердить оплату (после оплаты в банке) • X отмена • U сброс • Q выход"
    return "АВТО: завершает сам через паузу • X отмена • U сброс • Q выход"


# ---------- рендер дашборда ----------

RENDER_MU = threading.Lock()  # перерисовки из разных потоков не должны мешаться


def render(cfg: Config, st: UiState) -> None:
    with RENDER_MU:
        with st.mu:
            clear_screen()
            mc = mode_color(st.mode)
            print(paint(C_CYAN, " ┌─ P2C BOT ─────────────────────────────────────────────┐"))
            print(f" │ Режим: {paint(mc, C_BOLD + '%-12s' % mode_name(st.mode))}  "
                  f"{paint(C_DIM, 'base=%s' % cut(cfg.base, 34))}│")
            max_r = "∞"
            if cfg.max_rub > 0:
                max_r = f"{cfg.max_rub:.0f}"
            print(f" │ Фильтры: {paint(C_WHITE, 'RUB %g–%s  reward≥%g' % (cfg.min_rub, max_r, cfg.min_reward))}  "
                  f"{paint(C_DIM, 'dry=%s' % (st.mode == MODE_TEST))}│")
            if st.ws_ok:
                ws = paint(C_GREEN, f"● ONLINE x{st.ws_n}")
            else:
                ws = paint(C_RED, "● OFFLINE")
            note = (" " + cut(st.ws_note, 30)) if st.ws_note else ""
            print(f" │ WS: {ws}{paint(C_DIM, note)} "
                  f"{paint(C_WHITE, 'очередь:%d seen:%d match:%d' % (len(st.queue), st.seen, st.matched))}│")
            print(f" │ Взято:{paint(C_YEL, str(st.taken))}  Завершено:{paint(C_GREEN, str(st.done))}  "
                  f"Ошибок:{paint(C_RED, str(st.errs))}  429:{paint(C_MAG, str(st.r429))} "
                  f"{paint(C_DIM, 'take:%dms(%d..%d) n=%d' % (st.take_avg_ms(), st.take_min_ms, st.take_max_ms, st.take_n))}│")
            print(f" │ skip: темп:{st.skipped_rate} в-полёте:{st.skipped_busy} фильтры:{st.skipped_filter} │")
            print(paint(C_CYAN, " ├─ Очередь (топ по награде) ─────────────────────────────┤"))
            if not st.queue:
                print(paint(C_DIM, " │  …пусто, жду snapshot/add с биржи…"))
            else:
                print(paint(C_DIM, " │  СУММА     НАГРАДА   БРЕНД              QR_ID    TTL"))
                for o in st.queue:
                    print(f" │  {paint(C_WHITE, '%9s' % o.get('in_amount', ''))}  "
                          f"{paint(C_GREEN, '%7s' % o.get('reward_amount', ''))}  "
                          f"{paint(C_DIM, cut(o.get('brand_name', ''), 18)):18s} "
                          f"{paint(C_DIM, cut(o.get('qr_id', ''), 10))}")
            print(paint(C_CYAN, " ├─ Активный ордер ────────────────────────────────────────┤"))
            if not st.has_active:
                print(paint(C_DIM, " │  — нет, ловлю дальше…"))
            else:
                cd = ""
                if st.mode == MODE_AUTO and st.auto_count > 0:
                    cd = paint(C_RED, f"  сам завершу через {st.auto_count}с")
                elif st.mode == MODE_SEMI:
                    cd = paint(C_YEL, "  оплати в банке → жми C")
                print(f" │  {paint(C_YEL, C_BOLD + '#%s' % st.active.get('payment_id', '?'))} "
                      f"{st.active.get('in_amount', '')} RUB → {st.active.get('out_amount', '')} USDT "
                      f"{paint(C_DIM, cut(st.active.get('url', ''), 30))}{cd}")
            if st.account_label:
                print(f" │  Счёт: {paint(C_DIM, st.account_label)}")
            if st.paused_note:
                print(f" │  {paint(C_RED, st.paused_note)}")
            print(paint(C_CYAN, " ├─ Лог ───────────────────────────────────────────────────┤"))
            if not st.logs:
                print(paint(C_DIM, " │  стартую…"))
            for lg in st.logs:
                print(f" │ {paint(C_DIM, lg.at)} {cut(lg.text, 58)}")
            print(paint(C_CYAN, " └─────────────────────────────────────────────────────────┘"))
            print(f"   {paint(C_DIM, mode_help(st.mode) + ' • pay <url> — оплатить чужой QR')}")
            if st.mode == MODE_SEMI:
                print(f"   {paint(C_YEL, C_BOLD + 'БУРМАЛДА')}")
            print(f"   {paint(C_WHITE, C_BOLD + '›')} ", end="", flush=True)


def save_taken(p: dict) -> None:
    with open("taken.json", "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False, indent=2)
    with open("taken.log", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat()} payment={p.get('payment_id')} "
                f"{p.get('in_amount')} RUB brand={p.get('brand_name')!r} url={p.get('url')}\n")


# ---------- client pay ----------

def run_pay(cfg: Config, api: Client, payload: str) -> int:
    payload = payload.strip()
    if not payload:
        blog_plain("usage: p2c.py pay <qr_url>")
        return 2
    t0 = time.time()
    try:
        q, _ = api.create_qr(payload)
    except ApiError as ae:
        if ae.name == "LowAmount":
            blog_plain("LowAmount: минимум %s RUB", ae.min_amount)
        elif ae.name == "HighAmount":
            blog_plain("HighAmount: максимум %s RUB", ae.max_amount)
        elif ae.name == "ExistingPayload":
            blog_plain("ExistingPayload: этот QR уже занят другой операцией")
        elif ae.name == "UnsupportedQRCode":
            blog_plain("UnsupportedQRCode: QR не поддерживается")
        elif ae.name == "InvalidPayload":
            blog_plain("InvalidPayload: некорректный payload/URL")
        elif ae.name == "BrandBanned":
            blog_plain("BrandBanned: бренд в бан-листе")
        elif ae.name == "VerificationRequired":
            blog_plain("VerificationRequired: клиент не прошёл верификацию")
        elif ae.name in ("Disabled", "FetchFailed"):
            blog_plain("%s: %s", ae.name, ae.description or "P2C отключен / ошибка провайдера QR")
        else:
            blog_plain("createQR fail: %s", str(ae))
        return 1
    except Exception as e:
        blog_plain("createQR net-err: %s", e)
        return 1
    blog_plain("quote: %s RUB = %s USDT rate=%s brand=%r cashback=%.0f%% qr=%s ttl=%s (%.0fms)",
               q.get("in_amount"), q.get("total_amount"), q.get("exchange_rate"),
               q.get("brand_name"), fnum(q.get("cashback_percent")),
               q.get("qr_id"), q.get("price_expires_at"), (time.time() - t0) * 1000)
    # Котировка живёт ~10с — confirm сразу, без лишних запросов (лимит 20/мин, скорость критична).
    try:
        q2, _ = api.confirm_qr(str(q.get("qr_id")))
    except ApiError as ae:
        if ae.name == "RateExpired":
            el = time.time() - t0
            blog_plain("RateExpired (%.1fs) — пересоздаю котировку один раз…", el)
            try:
                q, _ = api.create_qr(payload)
                q2, _ = api.confirm_qr(str(q.get("qr_id")))
            except Exception as e2:
                blog_plain("re-confirm fail: %s", e2)
                return 1
        else:
            _hints = {
                "QrExpired": "QR больше невалиден у провайдера",
                "InsufficientFunds": "недостаточно USDT для блокировки",
                "InvalidStatus": "QR не в статусе pending",
                "NotFound": "QR не найден",
                "FetchFailed": "ошибка проверки QR у провайдера",
            }
            hint = _hints.get(ae.name)
            if hint:
                blog_plain("confirm fail %s: %s", ae.name, hint)
            else:
                blog_plain("confirm fail: %s", str(ae))
            return 1
    except Exception as e:
        blog_plain("confirm net-err: %s", e)
        return 1
    el = time.time() - t0
    blog_plain("CONFIRMED за %.1fs: qr=%s status=%s %s RUB -> USDT заблокированы, ждём мерчанта",
               el, q2.get("qr_id"), q2.get("status"), q2.get("in_amount"))
    if el > 8:
        blog_plain("WARN: confirm занял >8с — в следующий раз сеть/прокси медленная, котировка могла протухнуть")
    return 0


# ---------- merchant main loop ----------

BACKOFF = [3, 5, 10, 20, 30, 60]


def run_merchant(cfg: Config, api: Client, mode: int,
                 stop_event: Optional[threading.Event] = None,
                 ui_holder: Optional[dict] = None) -> int:
    owned_stop = stop_event is None
    if stop_event is None:
        stop_event = threading.Event()

    def _on_signal(signum: int, frame: Any) -> None:
        stop_event.set()

    if owned_stop:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _on_signal)
            except (ValueError, OSError):
                pass  # не главный поток — Ctrl+C всё равно прибьёт через stdin EOF

    st = UiState(mode)
    if ui_holder is not None:
        ui_holder["st"] = st
    quiet = os.environ.get("P2C_PLAIN") == "1"  # без дашборда: plain-логи

    def draw() -> None:
        if not quiet:
            render(cfg, st)

    def blog(fmt: str, *a: Any) -> None:
        if quiet:
            blog_plain(fmt, *a)
            return
        st.add_log(fmt, *a)
        draw()

    def hot_log(fmt: str, *a: Any) -> None:
        # БЕЗ перерисовки: используется внутри try_take,
        # чтобы рендер не стоял между WS-событием и take-запросом.
        if quiet:
            blog_plain(fmt, *a)
            return
        st.add_log(fmt, *a)

    # Счёт нужен в semi/auto для complete; в test не обязателен.
    account_id = ""
    if mode != MODE_TEST:
        acc_id, label = resolve_account(api, cfg)
        account_id = acc_id
        with st.mu:
            st.account_label = label
        if not acc_id and mode == MODE_AUTO:
            print(paint(C_RED, "АВТОМАТ без счёта невозможен: " + label))
            print(paint(C_DIM, "Укажи ACCOUNT_ID или выбери счёт с can_complete=true, либо режим 1/2."))
            return 1
    if mode == MODE_AUTO:
        blog("АВТОМАТ: ловлю → пауза %sс → завершаю сам (счёт %s). Рубли по QR платишь ты/автобанк, раннее завершение = NotPaid!",
             int(cfg.auto_delay), account_id)

    # Прогрев: один дешёвый запрос ДО подключения к WS (горячий keep-alive).
    # getMe: лимит 30/мин — тратим 1 запрос.
    try:
        api.get_me()
        blog("соединение прогрето — take пойдёт по горячему каналу")
    except Exception as e:
        blog("прогрев соединения: %s (работаю дальше)", e)

    has_active = threading.Event()
    active_id = {"v": 0}
    active_lock = threading.Lock()

    # Восстановление активного ордера из active.id при старте (защита от повторного лова)
    if os.path.exists("active.id"):
        try:
            with open("active.id", "r", encoding="utf-8") as f:
                raw_id = f.read().strip()
                if raw_id:
                    pid_restored = int(raw_id)
                    active_id["v"] = pid_restored
                    has_active.set()
                    with st.mu:
                        st.has_active = True
                        st.active = {"payment_id": pid_restored}
                    blog("[старт] обнаружен незавершённый заказ #%d (active.id) — ловля остановлена до выполнения", pid_restored)
        except Exception:
            pass
    paused_until = {"v": 0.0}
    queue_map: Dict[str, dict] = {}
    queue_lock = threading.Lock()
    failed: Dict[str, float] = {}  # qr_id -> time.time(): недавно не взялось — не долбим повторно
    failed_lock = threading.Lock()

    def get_active_id() -> int:
        with active_lock:
            return active_id["v"]

    def set_active_id(v: int) -> None:
        with active_lock:
            active_id["v"] = v

    # --- завершение активного ордера (общее для кнопки C, авто-таймера, CLI) ---
    def do_complete(src: str) -> None:
        pid = get_active_id()
        if pid == 0 or not has_active.is_set():
            blog("Нечего завершать: активного заказа нет")
            return
        if not account_id:
            blog("Нечем завершить: нет счёта — открой accounts")
            return
        try:
            p = api.complete_payment(pid, account_id)
        except ApiError as ae:
            if ae.name == "TooManyRequests":
                with st.mu:
                    st.r429 += 1
            with st.mu:
                st.errs += 1
            blog("Не завершил #%d [%s]: %s", pid, src, str(ae))
            return
        except Exception as e:
            with st.mu:
                st.errs += 1
            blog("Не завершил #%d [%s]: %s", pid, src, e)
            return
        with st.mu:
            st.done += 1
            st.has_active = False
        has_active.clear()
        set_active_id(0)
        try:
            os.remove("active.id")
        except OSError:
            pass
        save_taken(p)
        blog("COMPLETED #%s (%s) — ловлю дальше", p.get("payment_id"), p.get("status"))

    def do_cancel(reason: str) -> None:
        """Отмена активного ордера. Частые отмены = MerchantPenalized (excessive_cancellations)."""
        pid = get_active_id()
        if pid == 0 or not has_active.is_set():
            blog("Нечего отменять: активного заказа нет")
            return
        try:
            p = api.cancel_payment(pid, reason)
        except ApiError as ae:
            if ae.name == "TooManyRequests":
                with st.mu:
                    st.r429 += 1
            if ae.name == "NotPaid":
                blog("Отмена отклонена: провайдер подтвердил оплату — используй C (complete #%d)", pid)
                return
            with st.mu:
                st.errs += 1
            blog("Не отменил #%d: %s", pid, str(ae))
            return
        except Exception as e:
            with st.mu:
                st.errs += 1
            blog("Не отменил #%d: %s", pid, e)
            return
        has_active.clear()
        set_active_id(0)
        try:
            os.remove("active.id")
        except OSError:
            pass
        with st.mu:
            st.has_active = False
        blog("CANCELED #%s (%s) — QR вернулся в очередь, ловлю дальше", p.get("payment_id"), reason)

    def show_pending() -> None:
        try:
            acts = api.get_pending_actions()
        except Exception as e:
            blog("pending fail: %s", e)
            return
        if not acts:
            blog("pending: действий не требуется")
            return
        for a in acts[:10]:
            blog("pending: #%s %s (%s)", a.get("payment_id"), a.get("action"), a.get("status"))
    # Сторож: файл active.id (мгновенно, 0 запросов) + сверка с API раз в 45с (1.3/мин).
    def watchdog() -> None:
        next_api = time.time() + 45
        while not stop_event.wait(5):
            if get_active_id() != 0 and not os.path.exists("active.id"):
                blog("[заказ %d] завершение замечено (файл убран) — ловлю дальше", get_active_id())
                set_active_id(0)
                has_active.clear()
                with st.mu:
                    st.has_active = False
                draw()
            if time.time() >= next_api:
                next_api = time.time() + 45
                pid = get_active_id()
                if pid != 0:
                    try:
                        status = api.get_payment_status(pid)
                    except Exception:
                        continue
                    if status != "processing":
                        blog("[active %d] статус %s — ловлю дальше", pid, status)
                        set_active_id(0)
                        has_active.clear()
                        with st.mu:
                            st.has_active = False
                            st.done += 1
                        try:
                            os.remove("active.id")
                        except OSError:
                            pass
                        draw()

    threading.Thread(target=watchdog, daemon=True).start()

    # Ввод команд дашборда (строчный, без raw-режима — легко и переносимо).
    cmd_ch: "queue.Queue[str]" = queue.Queue(maxsize=8)

    def stdin_reader() -> None:
        try:
            for line in sys.stdin:
                try:
                    cmd_ch.put_nowait(line.strip())
                except queue.Full:
                    pass
                if stop_event.is_set():
                    return
        except Exception:
            pass

    threading.Thread(target=stdin_reader, daemon=True).start()

    # Тикер перерисовки: очередь дышит, auto-обратный отсчёт тикает. 1/с — нагрузки ноль.
    def ticker() -> None:
        while not stop_event.wait(1):
            if quiet:
                continue
            with queue_lock:
                m = dict(queue_map)
            st.set_queue(m)
            draw()

    threading.Thread(target=ticker, daemon=True).start()

    # Обработчик команд.
    def cmd_loop() -> None:
        while not stop_event.is_set():
            try:
                line = cmd_ch.get(timeout=0.5)
            except queue.Empty:
                continue
            low = line.lower()
            if low in ("q", "quit", "exit"):
                blog("выход по Q")
                stop_event.set()
                return
            elif low in ("c", "complete"):
                if mode == MODE_TEST:
                    blog("ТЕСТ: брать нечего — take выключен")
                else:
                    threading.Thread(target=do_complete, args=("key-C",), daemon=True).start()
            elif low in ("u", "unhold"):
                try:
                    os.remove("active.id")
                except OSError:
                    pass
                set_active_id(0)
                has_active.clear()
                with st.mu:
                    st.has_active = False
                    st.paused_note = ""
                with active_lock:
                    paused_until["v"] = 0.0
                blog("флаг сброшен (U) — ловлю дальше")
            elif low in ("r", "reset", "reset_spent"):
                spent_rub["v"] = 0.0
                save_spent_rub(0.0)
                with st.mu:
                    st.spent_rub = 0.0
                    st.paused_note = ""
                blog("Оборот сброшен (0 RUB) — продолжение ловли")
            elif low in ("x", "cancel"):
                if mode == MODE_TEST:
                    blog("ТЕСТ: отменять нечего — take выключен")
                else:
                    parts = line.split()
                    reason = parts[1].lower() if len(parts) > 1 else "bank"
                    if reason not in ("bank", "balance", "qr", "qr-paid"):
                        blog("причина: bank|balance|qr|qr-paid (напр. X qr)")
                    else:
                        threading.Thread(target=lambda r=reason: do_cancel(r), daemon=True).start()
            elif low == "pending":
                threading.Thread(target=show_pending, daemon=True).start()
            elif low.startswith("pay "):
                u = line[4:].strip()
                threading.Thread(target=lambda: blog("pay ok: %s", cut(u, 40))
                                 if run_pay(cfg, api, u) == 0 else blog("pay fail: %s", cut(u, 40)),
                                 daemon=True).start()
            elif low != "":
                blog("? команды: C подтвердить • X отмена • U сброс • R сброс оборота • pending • pay <url> • Q выход")
            draw()

    threading.Thread(target=cmd_loop, daemon=True).start()

    # Гонка = один заказ. НЕ превышаем лимит 20/мин (иначе 429 и задержки).
    # Одна попытка в полёте + интервал ~3.05с между take. Свежий add
    # стреляет сразу, если окно свободно; лишние заявки отбрасываем.
    # 20/мин = 3000мс; держим 3050мс (буфер 50мс на джиттер, ~19.6/мин).
    take_executor = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="take_worker")

    in_flight = threading.Lock()
    last_take_ms = {"v": 0}
    spent_rub = {"v": load_spent_rub()}
    with st.mu:
        st.spent_rub = spent_rub["v"]
        st.max_total_rub = cfg.max_total_rub

    def try_take(o: dict) -> None:
        if mode != MODE_TEST and has_active.is_set():
            with st.mu:
                st.skipped_filter += 1
            return
        now_ms = int(time.time() * 1000)
        # ТЕСТ ничего не берёт и лимит не тратит — показываем ВСЕ совпадения.
        if mode == MODE_TEST:
            with st.mu:
                st.seen += 1
            ok, _ = cfg_passes(cfg, o)
            if not ok:
                with st.mu:
                    st.skipped_filter += 1
                return
            with st.mu:
                st.matched += 1
            hot_log("[ТЕСТ] %s RUB %s brand=%r reward=%s (не беру — тест)",
                    o.get("in_amount"), o.get("qr_id"), o.get("brand_name"), o.get("reward_amount"))
            return
        if not in_flight.acquire(blocking=False):
            with st.mu:
                st.skipped_busy += 1
            return  # предыдущий take ещё в полёте
        try:
            now_ms = int(time.time() * 1000)
            if now_ms - last_take_ms["v"] < 3020:
                with st.mu:
                    st.skipped_rate += 1
                return  # окно 20/мин занято — пропускаем, не долбим в 429
            if time.time() < paused_until["v"]:
                with st.mu:
                    st.skipped_filter += 1
                return
            if has_active.is_set():
                with st.mu:
                    st.skipped_filter += 1
                return  # ActiveOrderExists — бережём лимит
            if cfg.max_total_rub > 0 and (spent_rub["v"] + fnum(o.get("in_amount"))) > cfg.max_total_rub:
                with st.mu:
                    st.skipped_filter += 1
                    st.paused_note = f"Лимит оборота {spent_rub['v']:.0f}/{cfg.max_total_rub:.0f} RUB — сбрось кнопкой 'Сброс оборота' (R)"
                return
            with st.mu:
                st.seen += 1
            ok, _ = cfg_passes(cfg, o)
            if not ok:
                with st.mu:
                    st.skipped_filter += 1
                return
            with st.mu:
                st.matched += 1
            with failed_lock:
                ts = failed.get(str(o.get("qr_id")))
                if ts is not None and time.time() - ts < 60:
                    with st.mu:
                        st.skipped_filter += 1
                    return  # уже пробовали <60с назад — повторный take бессмысленен
            last_take_ms["v"] = now_ms
            t0 = time.time()
            try:
                p, _ = api.take_payment(str(o.get("qr_id")))
            except ApiError as ae:
                dt = int((time.time() - t0) * 1000)
                if ae.name in ("ActiveOrderExists", "AlreadyTakenByMerchant"):
                    has_active.set()
                    with st.mu:
                        st.has_active = True
                elif ae.name == "MerchantPenalized":
                    d = 5 * 60
                    if ae.retry_after > 0:
                        d = ae.retry_after
                    paused_until["v"] = time.time() + d
                    with st.mu:
                        st.paused_note = f"Пенальти {ae.penalty_type} до {ae.penalty_until} — пауза"
                    st.record_take(dt)
                    hot_log("[penalty] %dms %s until %s — пауза takes", dt, ae.penalty_type, ae.penalty_until)
                elif ae.name == "TooManyRequests":
                    with st.mu:
                        st.r429 += 1
                    st.record_take(dt)
                    hot_log("[take 429] %dms retry_after=%d — жду, лимит берегу", dt, ae.retry_after)
                elif ae.name in ("NotFound", "InvalidStatus"):
                    with failed_lock:
                        failed[str(o.get("qr_id"))] = time.time()
                    with st.mu:
                        st.errs += 1
                    st.record_take(dt)
                    hot_log("[уже забрали %s] %dms — конкуренты быстрее", cut(o.get("qr_id"), 8), dt)
                elif ae.name in ("NoActiveAccounts", "NoCompatiblePaymentMethod"):
                    with st.mu:
                        st.errs += 1
                    st.record_take(dt)
                    hot_log("[take fail %s] %dms %s: нет подходящего счёта — проверь счета (accounts) и провайдера QR",
                            cut(o.get("qr_id"), 8), dt, ae.name)
                elif ae.name in ("InsufficientDeposit", "PaymentExceedsTierLimit"):
                    with st.mu:
                        st.errs += 1
                    st.record_take(dt)
                    hot_log("[take fail %s] %dms %s: %s — проверь залог/лимит тира (status)",
                            cut(o.get("qr_id"), 8), dt, ae.name, ae.description)
                elif ae.name in ("NoPermissions", "Forbidden"):
                    with st.mu:
                        st.errs += 1
                    st.record_take(dt)
                    hot_log("[take fail %s] %dms %s: нет scope payments:take — добавь пресет «P2C Мерчант (оператор)» в app.cr.bot/dev/api-keys",
                            cut(o.get("qr_id"), 8), dt, ae.name)
                elif ae.name in ("IpWhitelistRequired", "AccessDenied"):
                    with st.mu:
                        st.errs += 1
                    st.record_take(dt)
                    hot_log("[take fail %s] %dms %s: нет IP в whitelist ключа — добавь IP сервера в app.cr.bot/dev/api-keys (нужен и для take/complete, и для ws_token)",
                            cut(o.get("qr_id"), 8), dt, ae.name)
                else:
                    with st.mu:
                        st.errs += 1
                    st.record_take(dt)
                    hot_log("[take fail %s] %dms %s (%s)", cut(o.get("qr_id"), 8), dt, ae.name, ae.description)
                return
            except Exception as e:
                dt = int((time.time() - t0) * 1000)
                with st.mu:
                    st.errs += 1
                st.record_take(dt)
                hot_log("[take net-err %s] %dms %s", cut(o.get("qr_id"), 8), dt, e)
                return
            dt = int((time.time() - t0) * 1000)
            has_active.set()
            try:
                pid = int(p.get("payment_id"))
            except (TypeError, ValueError):
                pid = 0
            set_active_id(pid)
            st.record_take(dt)

            amt_took = fnum(p.get("in_amount"))
            spent_rub["v"] += amt_took
            save_spent_rub(spent_rub["v"])

            with st.mu:
                st.taken += 1
                st.active = p
                st.has_active = True
                st.spent_rub = spent_rub["v"]
            try:
                with open("active.id", "w") as f:
                    f.write(str(pid))
            except OSError:
                pass
            save_taken(p)
            if mode == MODE_SEMI:
                hot_log("[ВЗЯЛ %dms] #%s %s RUB → %s USDT reward=%s brand=%r",
                        dt, p.get("payment_id"), p.get("in_amount"), p.get("out_amount"),
                        p.get("reward_amount"), p.get("brand_name"))
                hot_log(">>> оплати рубли в банке, затем жми C (подтвердить #%s)", p.get("payment_id"))
            else:
                hot_log("[ВЗЯЛ %dms] #%s %s RUB → %s USDT — сам завершу через %sс",
                        dt, p.get("payment_id"), p.get("in_amount"), p.get("out_amount"), int(cfg.auto_delay))

                def _auto(pid2: int, pay: dict) -> None:
                    # Ветка 1: автобанк включён — платим в браузере, complete
                    # только по факту успеха (защита от NotPaid). Таймер ниже —
                    # лишь обратный отсчёт/страховка.
                    if cfg.autopay:
                        try:
                            import autobank
                        except ImportError as e:
                            hot_log("[автобанк] нет модуля: %s — жду %sс и завершаю вслепую",
                                    e, int(cfg.auto_delay))
                            autobank = None  # type: ignore
                        if autobank is not None:
                            with st.mu:
                                st.bank_status = f"плачу #{pid2}"
                                st.bank_busy = True
                            hot_log("[автобанк] плачу #%s %s RUB в браузере (Альфа)…",
                                    pid2, pay.get("in_amount"))
                            try:
                                ok, msg = autobank.pay_order(
                                    pay, log=lambda m: hot_log("[автобанк] %s", m),
                                    timeout=cfg.autopay_timeout)
                            except Exception as e:
                                ok, msg = False, f"исключение: {e}"
                            with st.mu:
                                st.bank_busy = False
                                st.bank_status = "открыт" if ok else f"ошибка: {cut(msg, 60)}"
                            if get_active_id() != pid2 or stop_event.is_set():
                                return
                            if ok:
                                hot_log("[автобанк] %s — завершаю #%s", msg, pid2)
                                with st.mu:
                                    st.auto_count = 0
                                do_complete("auto-bank")
                                return
                            hot_log("[автобанк] НЕ оплачено: %s — отменяю #%s (bank)",
                                    msg, pid2)
                            do_cancel("bank")
                            return
                    # Ветка 2: классика — пауза и complete (авто без банка)
                    n = int(cfg.auto_delay)
                    for i in range(n, 0, -1):
                        if get_active_id() != pid2 or stop_event.is_set():
                            return
                        with st.mu:
                            st.auto_count = i
                        time.sleep(1)
                    with st.mu:
                        st.auto_count = 0
                    if get_active_id() == pid2:
                        do_complete("auto")

                threading.Thread(target=_auto, args=(pid, dict(p)), daemon=True).start()
        finally:
            in_flight.release()

    # Кипер горячего соединения: лёгкий getMe каждые 20с (3/мин из 30/мин).
    def keeper() -> None:
        while not stop_event.wait(20):
            try:
                api.get_me()
            except Exception as e:
                blog("кипер соединения: %s", e)

    threading.Thread(target=keeper, daemon=True).start()
    blog("кипер соединения включён (getMe/20с) + WS-коннектов: %d — чьё add придёт раньше, тот и стреляет", cfg.ws_conns)

    # Несколько параллельных WS-коннектов (лимит ключа — 5).
    def ws_loop(idx: int) -> None:
        if idx > 0 and stop_event.wait(idx * 1.5):
            return
        bi = 0
        while not stop_event.is_set():
            token = ""
            if cfg.ws_token_mod:
                try:
                    token = api.get_ws_token()
                except Exception as e:
                    blog_plain("getWsToken fail: %s (retry через 5с)", e)
                    if stop_event.wait(5):
                        return
                    continue
            u = ws_url(cfg.base, cfg.api_key, token)
            blog("ws#%d connect…", idx)
            draw()
            try:
                ws = websocket.create_connection(u, timeout=12,
                                                 header={"User-Agent": "p2cbot/1.0 (python, light)"})
            except Exception as e:
                d = BACKOFF[min(bi, len(BACKOFF) - 1)]
                bi = min(bi + 1, len(BACKOFF) - 1)
                blog("ws#%d dial fail (retry %sс): %s", idx, d, e)
                if stop_event.wait(d):
                    return
                continue
            bi = 0
            blog("ws#%d connected — слушаю очередь", idx)
            with st.mu:
                st.ws_n += 1
                st.ws_ok = True
                st.ws_note = "слушаю"
            draw()

            stop_ping = threading.Event()

            def ping_loop() -> None:
                while not stop_ping.wait(25):
                    try:
                        ws.ping(b"")
                        ws.send(json.dumps({"event": "pong"}))
                    except Exception:
                        return

            threading.Thread(target=ping_loop, daemon=True).start()
            try:
                ws.settimeout(90)
                while not stop_event.is_set():
                    try:
                        msg = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        blog("ws#%d read fail: timeout 90с без данных", idx)
                        break
                    except Exception as e:
                        blog("ws#%d read fail: %s", idx, e)
                        break
                    if not msg:
                        continue
                    if isinstance(msg, bytes):
                        try:
                            msg = msg.decode("utf-8", "ignore")
                        except Exception:
                            continue
                    try:
                        m = json_loads(msg)
                    except Exception:
                        continue
                    if not isinstance(m, dict):
                        continue
                    ev = m.get("event")
                    data = m.get("data")
                    if ev == "ping":
                        try:
                            ws.send(json.dumps({"event": "pong"}))
                        except Exception:
                            pass
                    elif ev == "snapshot":
                        arr = data if isinstance(data, list) else []
                        blog("[snapshot] %d в очереди", len(arr))
                        # Сортируем по награде: самые вкусные пробуем первыми.
                        try:
                            arr = sorted(arr,
                                         key=lambda o: fnum(o.get("reward_amount")) + fnum(o.get("boost_amount")),
                                         reverse=True)
                        except Exception:
                            pass
                        for o in arr:
                            if isinstance(o, dict) and o.get("qr_id"):
                                with queue_lock:
                                    queue_map[str(o["qr_id"])] = o
                                take_executor.submit(try_take, o)
                        with queue_lock:
                            m2 = dict(queue_map)
                        st.set_queue(m2)
                        draw()
                    elif ev == "add":
                        o = data if isinstance(data, dict) else {}
                        if o.get("qr_id"):
                            take_executor.submit(try_take, o)  # мгновенно в гонку (до UI рендера)
                            with queue_lock:
                                queue_map[str(o["qr_id"])] = o
                        with queue_lock:
                            m2 = dict(queue_map)
                        st.set_queue(m2)
                        draw()
                    elif ev == "remove":
                        qrid = (data.get("qr_id") if isinstance(data, dict) else "") or ""
                        if qrid:
                            with queue_lock:
                                queue_map.pop(str(qrid), None)
                    else:
                        err = m.get("error")
                        if err:
                            retry = int(m.get("retry_after") or 0)
                            blog("ws#%d auth/limit: %s retry=%d — проверь ключ/scopes/IP whitelist", idx, err, retry)
                            with st.mu:
                                st.ws_note = str(err)
                            d = retry if retry > 0 else 10
                            stop_ping.set()
                            try:
                                ws.close()
                            except Exception:
                                pass
                            with st.mu:
                                if st.ws_n > 0:
                                    st.ws_n -= 1
                                st.ws_ok = st.ws_n > 0
                            if stop_event.wait(d):
                                return
                            break
            finally:
                stop_ping.set()
                try:
                    ws.close()
                except Exception:
                    pass
                with st.mu:
                    if st.ws_n > 0:
                        st.ws_n -= 1
                    st.ws_ok = st.ws_n > 0
                    if not stop_event.is_set():
                        st.ws_note = "reconnect…"
            if stop_event.is_set():
                return
            d = BACKOFF[min(bi, len(BACKOFF) - 1)]
            bi = min(bi + 1, len(BACKOFF) - 1)
            blog("ws#%d reconnect через %sс (лимит 20/мин — жду)", idx, d)
            if stop_event.wait(d):
                return

    threads = []
    for i in range(cfg.ws_conns):
        t = threading.Thread(target=ws_loop, args=(i,), daemon=True)
        t.start()
        threads.append(t)
    try:
        while not stop_event.is_set():
            time.sleep(0.2)
    except KeyboardInterrupt:
        stop_event.set()
    finally:
        take_executor.shutdown(wait=False)
    return 0


# ---------- main / CLI ----------

def usage() -> None:
    print(paint(C_CYAN, C_BOLD + "p2c — лёгкий P2C бот с красивым терминалом"))
    print("""  Без аргументов — меню с выбором режима (1/2/3).

  Режимы ловли очереди:
    p2c.py тест      1) ТЕСТ — только смотрит очередь, ничего не берёт
    p2c.py ловля      2) ЛОВЛЯ — ловит заказ, показывает QR, ждёт твоего подтверждения
    p2c.py автомат    3) АВТОМАТ — ловит, показывает QR и завершает сам (осторожно!)
    (по-английски тоже можно: test / semi / auto, merchant = ловля)

  Внутри дашборда команды (Enter после буквы):
    C — подтверждаю, оплатил (после оплаты рублей в банке)
    X [причина] — отменить активный (bank|balance|qr|qr-paid; осторожно, частые отмены = пенальти!)
    U — сбросить
    pending — платежи, требующие действия (напр. upload_statement)
    pay <url> — оплатить чужой QR прямо из дашборда
    Q — выход

  Разовое:
    p2c.py check|config|status|accounts|pending
    p2c.py pay <qr_url>
    p2c.py bankpay <ссылка|qr_payload> [сумма] — тест автобанка без биржи
      (с AUTOBANK_DRY=1 только ищет кнопки, «Оплатить» не жмёт)
    p2c.py complete <payment_id> [account_id]
    p2c.py cancel <payment_id> <reason>
    p2c.py unhold

  ENV/.env: CRBOT_API_KEY (обяз.), CRBOT_BASE, MIN_RUB, MAX_RUB, MIN_REWARD,
    BRAND_BAN, MCC_BAN, WS_TOKEN_MODE=1, WS_CONNS=1 (по умолчанию, 1..5, слушают очередь),
    ACCOUNT_ID, AUTO_DELAY_SEC=20, P2C_PLAIN=1 (без дашборда).
    Автобанк (только АВТО): AUTOPAY_ENABLED=1, ALFA_URL, AUTOPAY_TIMEOUT_SEC=120.
    Файл .env читается автоматически во всех режимах; export в шелле имеет приоритет.

  Запуск через venv: ./.venv/bin/python p2c.py ловля""")


def need_key(cfg: Config) -> bool:
    if not cfg.api_key:
        print("нет ключа: задай CRBOT_API_KEY")
        return False
    return True


def print_json(v: Any) -> None:
    print(json.dumps(v, ensure_ascii=False, indent=2))


def main() -> None:
    cfg = load_config()
    api = Client(cfg)
    if len(sys.argv) < 2:
        if not need_key(cfg):
            sys.exit(2)
        sys.exit(run_merchant(cfg, api, menu()))
        return
    cmd = sys.argv[1]
    if cmd in ("gui", "ui", "app", "гуи"):
        import gui
        gui.main()
        return
    elif cmd in ("-h", "--help", "help"):
        usage()
    elif cmd in ("menu", "меню"):
        if not need_key(cfg):
            sys.exit(2)
        sys.exit(run_merchant(cfg, api, menu()))
    elif cmd in ("test", "тест"):
        if not need_key(cfg):
            sys.exit(2)
        sys.exit(run_merchant(cfg, api, MODE_TEST))
    elif cmd in ("semi", "merchant", "ловля", "ручной"):
        if not need_key(cfg):
            sys.exit(2)
        sys.exit(run_merchant(cfg, api, MODE_SEMI))
    elif cmd in ("auto", "авто", "автомат", "полный"):
        if not need_key(cfg):
            sys.exit(2)
        if len(sys.argv) >= 3 and sys.argv[2] in ("-y", "--yes"):
            sys.exit(run_merchant(cfg, api, MODE_AUTO))
        print(paint(C_RED, C_BOLD + "АВТОМАТ завершает заказы сам через паузу."))
        print(paint(C_DIM, "Рубли по QR платишь ты/автобанк. Раннее завершение = NotPaid + пенальти мерчанта."))
        print("Продолжить в АВТОМАТЕ? [y/N]: ", end="", flush=True)
        try:
            line = sys.stdin.readline()
        except KeyboardInterrupt:
            print()
            sys.exit(2)
        if (line or "").strip().lower() not in ("y", "д"):
            print("Отмена. Используй p2c.py ловля.")
            sys.exit(2)
        sys.exit(run_merchant(cfg, api, MODE_AUTO))
    elif cmd == "check":
        if not need_key(cfg):
            sys.exit(2)
        try:
            print_json(api.get_me())
        except Exception as e:
            blog_plain("fail: %s", e)
            sys.exit(1)
    elif cmd == "config":
        if not need_key(cfg):
            sys.exit(2)
        try:
            print_json(api.get_config())
        except Exception as e:
            blog_plain("fail: %s", e)
            sys.exit(1)
    elif cmd == "status":
        if not need_key(cfg):
            sys.exit(2)
        try:
            print_json(api.get_status())
        except Exception as e:
            blog_plain("fail: %s", e)
            sys.exit(1)
    elif cmd == "accounts":
        if not need_key(cfg):
            sys.exit(2)
        try:
            accs = api.get_accounts()
        except Exception as e:
            blog_plain("fail: %s", e)
            sys.exit(1)
        for a in accs:
            print(f"{a.get('id')} bank={a.get('bank')} name={a.get('name')!r} "
                  f"status={a.get('status')} can_complete={a.get('can_complete')}")
    elif cmd == "pay":
        if not need_key(cfg):
            sys.exit(2)
        if len(sys.argv) < 3:
            print("usage: p2c.py pay <qr_url>")
            sys.exit(2)
        sys.exit(run_pay(cfg, api, sys.argv[2]))
    elif cmd in ("bankpay", "banktest", "тестбанка"):
        # Тест автобанка без биржи: открывает ссылку/QR в браузере и платит.
        # С AUTOBANK_DRY=1 — только ищет кнопки, «Оплатить» НЕ жмёт.
        if len(sys.argv) < 3:
            print("usage: p2c.py bankpay <ссылка|qr_payload> [сумма]")
            print("  AUTOBANK_DRY=1 ./.venv/bin/python p2c.py bankpay 'https://qr.nspk.ru/AS...' 10")
            sys.exit(2)
        try:
            import autobank
        except ImportError:
            print("нет selenium: ./.venv/bin/pip install -r requirements.txt")
            sys.exit(2)
        payload = sys.argv[2]
        amount = sys.argv[3] if len(sys.argv) >= 4 else ""
        payment = {"payment_id": f"test-{int(time.time())}", "in_amount": amount,
                   "url": payload, "qr_payload": payload}
        ok, msg = autobank.pay_order(
            payment, log=lambda m: blog_plain("bank: %s", m),
            timeout=cfg.autopay_timeout)
        blog_plain("BANKPAY %s: %s", "OK" if ok else "FAIL", msg)
        sys.exit(0 if ok else 1)
    elif cmd == "complete":
        if not need_key(cfg):
            sys.exit(2)
        if len(sys.argv) < 3:
            print("usage: p2c.py complete <payment_id> [account_id]")
            sys.exit(2)
        try:
            pid = int(sys.argv[2])
        except ValueError:
            print("payment_id должен быть числом")
            sys.exit(2)
        acc = cfg.account_id
        if len(sys.argv) >= 4:
            acc = sys.argv[3]
        if not acc:
            try:
                accs = api.get_accounts()  # 1 запрос, только в момент ручного complete
            except Exception as e:
                blog_plain("getAccounts fail: %s", e)
                sys.exit(1)
            for a in accs:
                if a.get("can_complete"):
                    acc = str(a.get("id"))
                    break
            if not acc:
                blog_plain("нет счёта с can_complete=true — выбери вручную в мини-аппе")
                sys.exit(1)
            blog_plain("account auto: %s", acc)
        try:
            p = api.complete_payment(pid, acc)
        except Exception as e:
            blog_plain("complete fail: %s", e)
            sys.exit(1)
        blog_plain("COMPLETED payment=%s status=%s at=%s",
                   p.get("payment_id"), p.get("status"), p.get("completed_at"))
        try:
            os.remove("active.id")  # сигнал для процесса merchant: можно ловить дальше
        except OSError:
            pass
        blog_plain("merchant увидит это за ~5с и продолжит ловлю")
    elif cmd == "pending":
        if not need_key(cfg):
            sys.exit(2)
        try:
            acts = api.get_pending_actions()
        except Exception as e:
            blog_plain("fail: %s", e)
            sys.exit(1)
        if not acts:
            print("pending: действий не требуется")
        for a in acts:
            print(f"payment={a.get('payment_id')} action={a.get('action')} status={a.get('status')}")
    elif cmd == "cancel":
        if not need_key(cfg):
            sys.exit(2)
        if len(sys.argv) < 4:
            print("usage: p2c.py cancel <payment_id> <bank|balance|qr|qr-paid>")
            sys.exit(2)
        try:
            pid = int(sys.argv[2])
        except ValueError:
            print("payment_id должен быть числом")
            sys.exit(2)
        reason = sys.argv[3].lower()
        if reason not in ("bank", "balance", "qr", "qr-paid"):
            print("reason: bank|balance|qr|qr-paid")
            sys.exit(2)
        try:
            p = api.cancel_payment(pid, reason)
        except Exception as e:
            blog_plain("cancel fail: %s", e)
            sys.exit(1)
        blog_plain("CANCELED payment=%s status=%s reason=%s",
                   p.get("payment_id"), p.get("status"), p.get("cancel_reason", reason))
        try:
            os.remove("active.id")  # сигнал для процесса merchant: можно ловить дальше
        except OSError:
            pass
    elif cmd == "unhold":
        try:
            os.remove("active.id")
        except OSError:
            pass
        print("ok: флаг активного ордера сброшен, merchant продолжит ловлю за ~5с")
    else:
        usage()
        sys.exit(2)


if __name__ == "__main__":
    main()
