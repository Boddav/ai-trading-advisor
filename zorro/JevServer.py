# =================================================================
# JEV SERVER — Zorro lite-C  <->  TypeSafe Jev bridge
#
# Önálló fájl: csak beépített Python modulok, Python 3.7+.
# Másold a Zorro Strategy mappájába a JevTrader.c mellé, együtt ezekkel:
#   start_jev.bat, test_jev.bat, jev_key.txt (a kulccsal)
#
# JevTrader.c POST-ol ide (http_transfer), a szerver megkérdezi a Jevet,
# és visszaadja a döntést: open_long / open_short / close / hold.
#
# Endpoints:
#   POST /decide   {"asset","tf","digits","pos","entry","bars":[[o,h,l,c],...]}
#                  -> {"action","p_long","p_short","p_intent","reason"}
#   GET  /health
#
# API kulcs: jev_key.txt a szerver mellett, vagy TYPESAFE_API_KEY környezeti változó.
# Port: 5003 (nem ütközik: MLDRIVEN 5001, UltOsc 5002)
#
# Cache: minden Jev választ elment a jev_cache.jsonl-be (kulcs: a kérés tartalma).
# Ugyanarra a kérésre (pl. backtest újrafuttatás) nem hívja újra a Jevet.
#
# Usage: python JevServer.py [--port 5003] [--selftest]
#        python JevServer.py --prefetch [..\Data\JevExport.jsonl] [--threads 8]
# =================================================================
import argparse
import hashlib
import json
import logging
import math
import os
import sys
import threading
import time
import urllib.error
import urllib.request

try:
    from http.server import ThreadingHTTPServer as _Server
except ImportError:  # Python < 3.7
    from http.server import HTTPServer as _Server
from http.server import BaseHTTPRequestHandler

# ============ BEÁLLÍTÁSOK ============
JEV_MODEL = "jev-latest"
# Kérdés mód:
#   "tp_first" = "Hamarabb éri el az ár a take profitot, mint a stop losst?" long és short
#                irányra külön (igen/nem). Nyit, ha a jobb irány esélye >= MIN_TP_FIRST.
#   "classic"  = "long vagy short?" + "nyissak most vagy várjak?" (MIN_CONFIDENCE / MIN_BIAS)
QUESTION_MODE = "tp_first"
SL_ATR = 1.5            # egyezzen a JevTrader.c SL_ATR / TP_ATR értékével!
TP_ATR = 3.0
# Nullszaldó esély SL/(SL+TP) = 1.5/4.5 = 0.33 (költségek nélkül). Ennél kicsit több kell.
MIN_TP_FIRST = 0.40
# A két irány kizárja egymást (long TP-first => short SL-first). Ha a Jev mindkettőre közel
# ugyanazt mondja, nincs irány-információ -> nem nyit. Ennyivel kell jobbnak lennie a jobbiknak.
MIN_DIR_EDGE = 0.05
MIN_CONFIDENCE = 0.60   # classic: ennyi kell a Jev "open" / "close" válaszára
MIN_BIAS = 0.55         # classic: ennyi kell az irányra (long/short) nyitáskor
PLACEHOLDER_KEY = "IDE_IRD_A_JEV_API_KULCSOT"
TYPESAFE_URL = os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai").rstrip("/") + "/v1/systemone"
# =====================================

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(HERE, "jev_cache.jsonl")
DEFAULT_EXPORT = os.path.join(HERE, "..", "Data", "JevExport.jsonl")
log = logging.getLogger("jevserver")


class JevCache:
    """Jev válaszok (answers) a kérés tartalma szerint; fájlban megmarad."""

    def __init__(self, path=CACHE_FILE):
        self.path = path
        self.lock = threading.Lock()
        self.data = {}
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                        self.data[row["k"]] = row["a"]
                    except (ValueError, KeyError):
                        continue  # félbemaradt sor

    @staticmethod
    def key(req):
        canon = json.dumps([JEV_MODEL, QUESTION_MODE, req], sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(canon.encode()).hexdigest()

    def get(self, k):
        with self.lock:
            return self.data.get(k)

    def put(self, k, answers):
        with self.lock:
            self.data[k] = answers
            if self.path:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"k": k, "a": answers}) + "\n")

    def __len__(self):
        return len(self.data)


class JevError(RuntimeError):
    pass


# ---------------- Jev API ----------------
def jev_call(api_key, state, questions, timeout=10.0, retries=2):
    body = json.dumps({"model": JEV_MODEL, "state": state, "questions": questions}).encode()
    req = urllib.request.Request(TYPESAFE_URL, data=body, method="POST", headers={
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return json.loads(res.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            if (e.code in (408, 429) or e.code >= 500) and attempt < retries:
                time.sleep(float(e.headers.get("Retry-After") or 2 ** attempt))
                continue
            raise JevError("HTTP %d: %s" % (e.code, detail))
        except (urllib.error.URLError, OSError) as e:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise JevError(str(e))
    raise JevError("unreachable")


# ---------------- indikátorok (bars = [[o,h,l,c], ...], legrégebbi elöl) ----------------
def sma(v, n):
    return sum(v[-n:]) / n if len(v) >= n else None


def ema(v, n):
    if len(v) < n:
        return None
    k = 2.0 / (n + 1)
    e = sum(v[:n]) / n
    for x in v[n:]:
        e = x * k + e * (1 - k)
    return e


def rsi(v, n=14):
    if len(v) <= n:
        return None
    g = l = 0.0
    for a, b in zip(v[:n], v[1:n + 1]):
        g += max(b - a, 0.0)
        l += max(a - b, 0.0)
    ag, al = g / n, l / n
    for a, b in zip(v[n:], v[n + 1:]):
        ag = (ag * (n - 1) + max(b - a, 0.0)) / n
        al = (al * (n - 1) + max(a - b, 0.0)) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def atr(bars, n=14):
    if len(bars) <= n:
        return None
    trs = [max(b[1] - b[2], abs(b[1] - p[3]), abs(b[2] - p[3])) for p, b in zip(bars, bars[1:])]
    a = sum(trs[:n]) / n
    for t in trs[n:]:
        a = (a * (n - 1) + t) / n
    return a


def ret_bps(v, lb):
    if len(v) <= lb or v[-1 - lb] == 0:
        return None
    return (v[-1] / v[-1 - lb] - 1) * 10000


def _r(x, nd=5):
    return None if x is None else round(x, nd)


# ---------------- állapot, kérdések, döntés ----------------
def build_state(asset, tf, bars, side, entry, digits):
    closes = [b[3] for b in bars]
    a14 = atr(bars, 14)
    high20 = max(b[1] for b in bars[-20:])
    low20 = min(b[2] for b in bars[-20:])
    last = closes[-1]
    state = {
        "symbol": asset,
        "timeframe": tf,
        "last_close": round(last, digits),
        "returns_bps": {"last1": _r(ret_bps(closes, 1), 1), "last5": _r(ret_bps(closes, 5), 1),
                        "last20": _r(ret_bps(closes, 20), 1), "last100": _r(ret_bps(closes, 100), 1)},
        "indicators": {
            "sma20": _r(sma(closes, 20), digits), "sma50": _r(sma(closes, 50), digits),
            "sma200": _r(sma(closes, 200), digits), "ema20": _r(ema(closes, 20), digits),
            "rsi14": _r(rsi(closes, 14), 1), "atr14": _r(a14, digits),
            "high20": round(high20, digits), "low20": round(low20, digits),
            "range_pos20": _r((last - low20) / (high20 - low20), 2) if high20 > low20 else None,
        },
        "recent_bars": [[round(x, digits) for x in b] for b in bars[-12:]],
        "position": {"side": "flat"},
    }
    if side in ("long", "short"):
        state["position"] = {
            "side": side,
            "entry": round(entry, digits),
            "move_vs_entry_atr": _r((last - entry) / a14 * (1 if side == "long" else -1), 2) if a14 else None,
        }
    return state


def build_questions(asset, tf, side, mode=None):
    mode = mode or QUESTION_MODE
    ctx = ("%s %s closed bars. recent_bars are [open, high, low, close], oldest first. "
           "A trade uses a stop %sx ATR and a target %sx ATR away from entry." % (asset, tf, SL_ATR, TP_ATR))
    bias = {"type": "choice",
            "instructions": {"question": "Over the next few %s bars, is %s more likely to move up or down?" % (tf, asset),
                             "inputs": ctx},
            "criteria": {"long": "%s rises" % asset, "short": "%s falls" % asset}}
    if mode == "tp_first" and side not in ("long", "short"):
        def tp_first(d):
            up = d == "long"
            return {"type": "noul",
                    "instructions": {
                        "question": "If a %s %s trade is opened at last_close now, will price reach the take profit "
                                    "(%sx atr14 %s) before the stop loss (%sx atr14 %s)?"
                                    % (d, asset, TP_ATR, "above" if up else "below",
                                       SL_ATR, "below" if up else "above"),
                        "inputs": ctx},
                    "criteria": {"true": "take profit is hit first", "false": "stop loss is hit first"}}
        return {"long_tp_first": tp_first("long"), "short_tp_first": tp_first("short")}
    if side not in ("long", "short"):
        intent = {"type": "choice",
                  "instructions": {"question": "Open a new %s trade now, or wait?" % asset,
                                   "goal": "position is flat", "inputs": ctx},
                  "criteria": {"open": "open a trade in the expected direction now", "hold": "stay flat"}}
    else:
        intent = {"type": "choice",
                  "instructions": {"question": "Close the open %s %s position now, or keep it?" % (side, asset),
                                   "goal": "position is %s" % side, "inputs": ctx},
                  "criteria": {"close": "close the position now", "hold": "keep the position"}}
    return {"bias": bias, "intent": intent}


def _probs(answer, keys):
    answer = answer or {}
    picked = str(answer.get("choice", "")).strip().lower()
    raw = answer.get("probabilities") or {}
    vals = [max(0.0, float(raw.get(k, 1.0 if picked == k else 0.0))) for k in keys]
    total = sum(vals)
    if total <= 0:
        return {k: 1.0 / len(keys) for k in keys}
    return {k: v / total for k, v in zip(keys, vals)}


def _noul(answer):
    try:
        return min(1.0, max(0.0, float((answer or {}).get("noul", 0.0))))
    except (TypeError, ValueError):
        return 0.0


def decide(answers, side, mode=None):
    """-> (action, reason, p_long, p_short, p_intent)"""
    mode = mode or QUESTION_MODE
    if mode == "tp_first" and side not in ("long", "short"):
        pl, ps = _noul(answers.get("long_tp_first")), _noul(answers.get("short_tp_first"))
        d, p = ("long", pl) if pl >= ps else ("short", ps)
        if p < MIN_TP_FIRST:
            return "hold", "TP-first long=%.2f short=%.2f < %s" % (pl, ps, MIN_TP_FIRST), pl, ps, p
        if abs(pl - ps) < MIN_DIR_EDGE:
            return "hold", "no direction edge (long=%.2f short=%.2f, diff < %s)" % (pl, ps, MIN_DIR_EDGE), pl, ps, p
        return "open_" + d, "TP-first %s p=%.2f (long=%.2f short=%.2f)" % (d, p, pl, ps), pl, ps, p
    bias = _probs(answers.get("bias"), ("long", "short"))
    if side not in ("long", "short"):
        intent = _probs(answers.get("intent"), ("open", "hold"))
        d = "long" if bias["long"] >= bias["short"] else "short"
        if intent["open"] < MIN_CONFIDENCE:
            return "hold", "open p=%.2f < %s" % (intent["open"], MIN_CONFIDENCE), bias["long"], bias["short"], intent["open"]
        if bias[d] < MIN_BIAS:
            return "hold", "direction unclear (%s p=%.2f < %s)" % (d, bias[d], MIN_BIAS), bias["long"], bias["short"], intent["open"]
        return "open_" + d, "open p=%.2f, %s p=%.2f" % (intent["open"], d, bias[d]), bias["long"], bias["short"], intent["open"]
    intent = _probs(answers.get("intent"), ("close", "hold"))
    if intent["close"] >= MIN_CONFIDENCE:
        return "close", "close p=%.2f" % intent["close"], bias["long"], bias["short"], intent["close"]
    return "hold", "keep %s (close p=%.2f)" % (side, intent["close"]), bias["long"], bias["short"], intent["close"]


def handle_decide(req, api_key, call=jev_call, cache=None):
    asset = str(req["asset"])
    tf = str(req.get("tf", "H1"))
    digits = int(req.get("digits", 5))
    bars = [[float(x) for x in b[:4]] for b in req["bars"]]
    if len(bars) < 50:
        raise ValueError("legalább 50 bar kell, jött: %d" % len(bars))
    bad = [i for i, b in enumerate(bars) if min(b) <= 0 or b[1] < b[2]]
    if bad:
        raise ValueError("hibás gyertya (0 ár vagy high<low) %d helyen, pl. #%d: %s -> nincs Jev kérdés"
                         % (len(bad), bad[0], bars[bad[0]]))
    side = str(req.get("pos", "flat")).lower()
    entry = float(req.get("entry", 0) or 0)
    k = JevCache.key(req) if cache is not None else None
    answers = cache.get(k) if k else None
    cached = answers is not None
    if not cached:
        state = build_state(asset, tf, bars, side, entry, digits)
        answers = call(api_key, state, build_questions(asset, tf, side)).get("answers", {})
        if k:
            cache.put(k, answers)
    action, reason, pl, ps, pi = decide(answers, side)
    return {"action": action, "p_long": round(pl, 4), "p_short": round(ps, 4),
            "p_intent": round(pi, 4), "reason": reason, "cached": cached}


# ---------------- HTTP szerver ----------------
def make_handler(api_key, call=jev_call, cache=None):
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/health":
                self._reply(200, {"status": "ok", "model": JEV_MODEL})
            else:
                self._reply(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/decide":
                return self._reply(404, {"error": "not found"})
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                out = handle_decide(req, api_key, call, cache)
                log.info("%s pos=%s -> %s (%s)%s", req.get("asset"), req.get("pos"), out["action"],
                         out["reason"], " [cache]" if out["cached"] else "")
                self._reply(200, out)
            except (JevError, ValueError, KeyError, TypeError, IndexError) as e:
                log.error("decide failed: %s", e)
                # a Zorro oldal minden nem-akciót hold-nak vesz
                self._reply(200, {"action": "error", "reason": str(e)[:200]})

        def log_message(self, *args):
            pass

    return Handler


def api_key():
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    path = os.path.join(HERE, "jev_key.txt")
    if not key and os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            key = f.read().strip()
    if not key or key == PLACEHOLDER_KEY:
        sys.exit("Jev API kulcs hiányzik: a JevServer.py mellett legyen egy jev_key.txt, "
                 "benne egy sorban a kulcs (vagy TYPESAFE_API_KEY környezeti változó).\n"
                 "Keresett hely: " + path)
    return key


def selftest(key, call=jev_call):
    """Egy valódi Jev hívás minta EUR/USD adattal: kiírja a kérdéseket és a választ."""
    bars = []
    for i in range(200):  # mintaadat: enyhe emelkedés hullámzással
        c = 1.0800 + i * 0.00005 + 0.0008 * math.sin(i / 7.0)
        bars.append([round(c - 0.0002, 5), round(c + 0.0006, 5), round(c - 0.0006, 5), round(c, 5)])
    req = {"asset": "EUR/USD", "tf": "H1", "digits": 5, "pos": "flat", "entry": 0, "bars": bars}
    print("\n=== KÉRDÉSEK A JEVNEK ===")
    for name, q in build_questions("EUR/USD", "H1", "flat").items():
        print("  %s: %s" % (name, q["instructions"]["question"]))
        print("      lehetséges válaszok: %s" % ", ".join(str(k) for k in q["criteria"]))
    print("\n... Jev hívása (mintaadat: EUR/USD H1, 200 bar) ...")
    t0 = time.time()
    try:
        out = handle_decide(req, key, call)
    except JevError as e:
        print("\n*** HIBA: %s" % e)
        print("    401/403 = rossz API kulcs; egyéb = hálózat vagy TypeSafe oldali hiba")
        return
    print("\n=== VÁLASZ (%.0f ms) ===" % ((time.time() - t0) * 1000))
    if QUESTION_MODE == "tp_first":
        print("  long: TP előbb, mint SL:  %.2f" % out["p_long"])
        print("  short: TP előbb, mint SL: %.2f" % out["p_short"])
        print("  (nyit, ha a jobbik >= %.2f és legalább %.2f-dal jobb a másiknál)" % (MIN_TP_FIRST, MIN_DIR_EDGE))
    else:
        print("  long valószínűség:    %.2f" % out["p_long"])
        print("  short valószínűség:   %.2f" % out["p_short"])
        print("  nyitás valószínűsége: %.2f" % out["p_intent"])
    print("  DÖNTÉS: %s  (%s)" % (out["action"], out["reason"]))
    print("\nOK - a Jev kulcs működik. (A mintaadat nem valós piac, a döntés csak próba.)")


def prefetch(path, key, threads=8, call=jev_call, cache=None):
    """A Zorro export (JEV_TEST_MODE 2) kérdéseit párhuzamosan lekérdezi és cache-eli."""
    from concurrent.futures import ThreadPoolExecutor
    cache = cache if cache is not None else JevCache()
    if not os.path.exists(path):
        print("Nincs export fájl: %s\nElőbb futtasd a Zorro Test-et JEV_TEST_MODE 2-vel." % path)
        return
    reqs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    reqs.append(json.loads(line))
                except ValueError:
                    pass
    todo = [r for r in reqs if cache.get(JevCache.key(r)) is None]
    print("Export: %d kérdés, ebből már cache-ben: %d, lekérdezendő: %d" % (len(reqs), len(reqs) - len(todo), len(todo)))
    done = [0, 0]
    lock = threading.Lock()
    t0 = time.time()

    def one(r):
        try:
            handle_decide(r, key, call, cache)
            ok = True
        except (JevError, ValueError, KeyError, TypeError, IndexError) as e:
            log.error("%s: %s", r.get("asset"), e)
            ok = False
        with lock:
            done[0 if ok else 1] += 1
            n = done[0] + done[1]
            if n % 100 == 0 or n == len(todo):
                print("  %d / %d  (%.0f mp)" % (n, len(todo), time.time() - t0))

    with ThreadPoolExecutor(max_workers=max(1, threads)) as ex:
        list(ex.map(one, todo))
    print("Kész: %d sikeres, %d hibás. Cache: %d válasz. Most futtasd a Test-et JEV_TEST_MODE 1-gyel."
          % (done[0], done[1], len(cache)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5003)
    ap.add_argument("--selftest", action="store_true", help="egy próba Jev hívás, szerver nélkül")
    ap.add_argument("--prefetch", nargs="?", const=DEFAULT_EXPORT, help="Zorro export fájl előtöltése")
    ap.add_argument("--threads", type=int, default=8)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    key = api_key()
    if args.selftest:
        selftest(key)
        return
    if args.prefetch:
        prefetch(os.path.abspath(args.prefetch), key, args.threads)
        return
    cache = JevCache()
    log.info("Jev server on 127.0.0.1:%d (model=%s, mode=%s, min_tp_first=%s, min_dir_edge=%s, cache=%d)",
             args.port, JEV_MODEL, QUESTION_MODE, MIN_TP_FIRST, MIN_DIR_EDGE, len(cache))
    _Server(("127.0.0.1", args.port), make_handler(key, cache=cache)).serve_forever()


if __name__ == "__main__":
    main()
