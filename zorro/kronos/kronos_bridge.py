# =================================================================
# Kronos híd — a Kronos (AAAI 2026, nyílt forrású gyertya-előrejelző modell) mintavételezett
# árpályáiból TP/SL valószínűségeket számol a JevServer és a kalibráció számára.
#
# Telepítés (egyszer, a Zorro gépen):
#   git clone https://github.com/shiyu-coder/Kronos C:\Kronos
#   pip install torch --index-url https://download.pytorch.org/whl/cpu
#   pip install numpy pandas einops huggingface_hub safetensors tqdm
# Az első futás letölti a modellt a Hugging Face-ről (Kronos-small ~100 MB).
#
# Használat:
#   kf = KronosForecaster(r"C:\Kronos")
#   s = kf.stats(bars)   # bars = [[o,h,l,c], ...] legrégebbi elöl (H1)
#   -> {"p_tp_first_long", "p_tp_first_short", "p_up", "exp_move_atr", "paths", "horizon"}
# =================================================================
import datetime as dt
import sys

SL_ATR = 1.5
TP_ATR = 3.0


def atr(bars, n=14):
    if len(bars) <= n:
        return None
    trs = [max(b[1] - b[2], abs(b[1] - p[3]), abs(b[2] - p[3])) for p, b in zip(bars, bars[1:])]
    a = sum(trs[:n]) / n
    for t in trs[n:]:
        a = (a * (n - 1) + t) / n
    return a


def outcome(entry, a, path, side, sl_atr=SL_ATR, tp_atr=TP_ATR):
    """1 = TP előbb, 0 = SL előbb (egy baron belül mindkettő: SL), None = nem dőlt el a horizonton."""
    up = side == "long"
    tp = entry + tp_atr * a if up else entry - tp_atr * a
    sl = entry - sl_atr * a if up else entry + sl_atr * a
    for _, h, l, _ in path:
        if (l <= sl) if up else (h >= sl):
            return 0
        if (h >= tp) if up else (l <= tp):
            return 1
    return None


def hourly_times(n, last=None):
    """n db órás időbélyeg (legrégebbi elöl), hétvége nélkül, az utolsó = last (vagy most)."""
    t = (last or dt.datetime.utcnow()).replace(minute=0, second=0, microsecond=0)
    out = []
    while len(out) < n:
        if t.weekday() < 5:
            out.append(t)
        t -= dt.timedelta(hours=1)
    return list(reversed(out))


def future_times(last, n):
    out, t = [], last
    while len(out) < n:
        t += dt.timedelta(hours=1)
        if t.weekday() < 5:
            out.append(t)
    return out


def path_stats(bars, paths, sl_atr=SL_ATR, tp_atr=TP_ATR):
    """Mintavételezett pályákból (lista a [o,h,l,c] listákból) valószínűségek."""
    a = atr(bars)
    entry = bars[-1][3]
    if not a or not paths:
        return None
    res = {}
    for side in ("long", "short"):
        outs = [outcome(entry, a, p, side, sl_atr, tp_atr) for p in paths]
        res["p_tp_first_" + side] = round(sum(1 for o in outs if o == 1) / len(outs), 3)
        res["p_sl_first_" + side] = round(sum(1 for o in outs if o == 0) / len(outs), 3)
    finals = [p[-1][3] for p in paths]
    res["p_up"] = round(sum(1 for f in finals if f > entry) / len(finals), 3)
    res["exp_move_atr"] = round((sum(finals) / len(finals) - entry) / a, 3)
    res["paths"] = len(paths)
    res["horizon"] = len(paths[0])
    return res


class KronosForecaster:
    def __init__(self, repo_dir, model="NeoQuasar/Kronos-small", tokenizer="NeoQuasar/Kronos-Tokenizer-base",
                 paths=20, horizon=24, device=None, max_context=512):
        sys.path.insert(0, repo_dir)
        from model import Kronos, KronosTokenizer, KronosPredictor  # noqa: E402  (Kronos repó)
        import pandas as pd
        self.pd = pd
        tok = KronosTokenizer.from_pretrained(tokenizer)
        mdl = Kronos.from_pretrained(model)
        self.predictor = KronosPredictor(mdl, tok, device=device, max_context=max_context)
        self.paths = paths
        self.horizon = horizon
        self.tag = "%s/%d/%d" % (model, paths, horizon)

    def sample_paths(self, bars, last_time=None):
        pd = self.pd
        xt = hourly_times(len(bars), last_time)
        yt = future_times(xt[-1], self.horizon)
        df = pd.DataFrame(bars, columns=["open", "high", "low", "close"])
        xs, ys = pd.Series(pd.to_datetime(xt)), pd.Series(pd.to_datetime(yt))
        preds = self.predictor.predict_batch([df] * self.paths, [xs] * self.paths, [ys] * self.paths,
                                             pred_len=self.horizon, T=1.0, top_p=0.9, sample_count=1,
                                             verbose=False)
        return [p[["open", "high", "low", "close"]].values.tolist() for p in preds]

    def stats(self, bars, last_time=None):
        return path_stats(bars, self.sample_paths(bars, last_time))
