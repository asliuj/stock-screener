"""
Stock Screener - Flask Backend
Screens constituent stocks across a configurable set of ETFs for:
  - P/E Ratio < threshold (default 30)
  - Volume spike > threshold vs 20-day average (default 2x)
  - RSI in range (default 50–70)
Ranks results by RSI (descending), returns all passing stocks.
Runs on port 8080.
"""

import os
import re
import json
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO

import numpy as np
import pandas as pd
import yfinance as yf
import requests
from flask import Flask, jsonify, render_template, request
from ta.momentum import RSIIndicator

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True

# ── Constants ─────────────────────────────────────────────────────────────────
PORT             = 8080
PE_MAX           = 25.0
VOLUME_RATIO_MIN = 2.0
RSI_MIN          = 1.0
RSI_MAX          = 30.0
RSI_PERIOD       = 14
VOL_WINDOW       = 20
BATCH_SIZE       = 25
BATCH_DELAY      = 2.0
PERF_CACHE_TTL   = 300

ALL_ETFS = [
    # Stock Market Overview
    "dia", "oef", "spy", "qqq",
    # State Street — SPDR
    "xlv", "xlk", "xlf", "xle", "xar", "xli", "xly", "xlp", "xlu", "xlre", "xlb", "xlc",
    # Defense
    "nato", "ita", "ppa", "shld", "war", "idef", "gcad",
    # Blackrock — iShares
    "ihak", "igv", "iyh", "iye", "iyf", "iyr", "ivv", "iwf", "iwm", "efa",
    # Vanguard
    "vgt", "vfh", "vht", "vde", "vis", "vcr", "vdc", "vpu", "vnq", "vaw", "vox",
    # RAG (AI/Tech)
    "chat", "igpt", "arty", "aiq",
]
HOLDINGS_CACHE_FILE = os.path.join(os.path.dirname(__file__), "holdings_cache.json")
_ALL_ETFS_SET = frozenset(ALL_ETFS)   # O(1) membership test

# ── State ─────────────────────────────────────────────────────────────────────
_state = {
    "status": "idle", "progress": 0, "message": "", "last_run": None,
    "results": [], "universe_size": 0, "screened": 0, "passed": 0,
    "error": None, "params": None,
}
_lock = threading.Lock()

_perf_state: dict = {"cache": {}, "ts": 0.0}
_perf_lock = threading.Lock()

_holdings: dict[str, list[str]] = {}
_weights:  dict[str, dict[str, float]] = {}
_names:    dict[str, str] = {}
_holdings_meta = {
    "status":     "unloaded",
    "updated":    None,
    "message":    "Holdings not yet loaded.",
    "fresh_fetch": False,
}
_holdings_lock = threading.Lock()

# ── Helpers ───────────────────────────────────────────────────────────────────

# Exchange suffixes: tickers ending with these (e.g. ASML.AS, 7203.T) keep their dot.
# Anything else (e.g. BRK.B) is a US share class — dot becomes a dash.
_EXCHANGE_SUFFIXES = {
    "L", "T", "V",
    "AS", "SW", "PA", "MC", "DE", "AX", "KS", "KQ", "HK", "TW",
    "NS", "BO", "TO", "SZ", "SS", "MI", "BR", "LS", "OL", "ST",
    "HE", "CO", "IR", "AT", "VI", "WA", "PR", "BK", "JK", "NZ",
}

def _normalize_ticker(raw: str) -> str:
    t = raw.strip()
    if "." in t:
        base, suffix = t.rsplit(".", 1)
        if suffix.upper() not in _EXCHANGE_SUFFIXES:
            return f"{base}-{suffix}"   # BRK.B → BRK-B
    return t

_VANGUARD_ETFS = frozenset(e for e in ALL_ETFS if e.startswith("v"))

_ISHARES_PRODUCTS: dict[str, tuple[str, str]] = {
    "oef":  ("239723", "ishares-sp-100-etf"),
    "ivv":  ("239726", "ishares-core-sp-500-etf"),
    "iwm":  ("239710", "ishares-russell-2000-etf"),
    "iwf":  ("239708", "ishares-russell-1000-growth-etf"),
    "efa":  ("239623", "ishares-msci-eafe-etf"),
    "ita":  ("239502", "ishares-us-aerospace-defense-etf"),
    "iyr":  ("239520", "ishares-us-real-estate-etf"),
    "iyh":  ("239511", "ishares-us-healthcare-etf"),
    "iye":  ("239507", "ishares-us-energy-etf"),
    "iyf":  ("239508", "ishares-us-financials-etf"),
    "igv":  ("239771", "ishares-north-american-techsoftware-etf"),
    "ihak": ("307352", "ishares-cybersecurity-and-tech-etf"),
}


def _norm_weights(raw: dict[str, float]) -> dict[str, float]:
    """Normalise to percent form (0–100); multiply by 100 if all values ≤ 1."""
    clean = {k: v for k, v in raw.items() if np.isfinite(v)}
    if not clean:
        return {}
    scale = 100 if max(clean.values()) <= 1.0 else 1
    return {k: round(v * scale, 4) for k, v in clean.items()}

def _safe_name(val) -> str:
    s = str(val).strip()
    return s if s not in ("", "nan", "-", "None") else ""

def _safe_float(val) -> float:
    try: v = float(val); return v if np.isfinite(v) else 0.0
    except (TypeError, ValueError): return 0.0

def _safe_val(v) -> float | None:
    try: x = float(v); return round(x, 4) if np.isfinite(x) and x else None
    except (TypeError, ValueError): return None


_INVALID_TICKER = frozenset(("", "-", "nan", "<NA>", "None"))

def _parse_df_holdings(df: pd.DataFrame, tcol: str) -> tuple[list[str], dict, dict]:
    """Extract (tickers, weights, names) from a DataFrame given the ticker column name."""
    # Filter to equity rows only when an Asset Class column is present
    ac_col = next((c for c in df.columns if str(c).lower().replace(" ", "") == "assetclass"), None)
    if ac_col:
        df = df[df[ac_col].astype(str).str.strip().str.lower() == "equity"]
    wcol = next((c for c in df.columns if "weight" in str(c).lower()), None)
    ncol = next((c for c in df.columns if str(c).lower().strip() in ("name", "security name", "issuer")), None)
    # Use str(v) via list comprehension — pandas .astype(str) on StringDtype columns
    # leaves pd.NA as a float-like NaN object instead of converting to the string "nan".
    ticker_vals = [str(v).strip() for v in df[tcol]]
    wcol_vals   = df[wcol].tolist() if wcol else None
    ncol_vals   = df[ncol].tolist() if ncol else None
    tickers, weights, names = [], {}, {}
    for i, t in enumerate(ticker_vals):
        if t not in _INVALID_TICKER and not t.startswith("Cash"):
            nt = _normalize_ticker(t)
            tickers.append(nt)
            if wcol_vals:
                weights[nt] = _safe_float(wcol_vals[i])
            if ncol_vals:
                n = _safe_name(ncol_vals[i])
                if n:
                    names[nt] = n
    return tickers, weights, names


def _fetch_stockanalysis(sym_lower: str) -> tuple[list[str], dict[str, float]]:
    """Fetch tickers + weights from stockanalysis.com (top ~25 free tier)."""
    try:
        resp = requests.get(
            f"https://stockanalysis.com/etf/{sym_lower}/holdings/__data.json",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if resp.status_code == 200:
            raw   = resp.text
            pairs = re.findall(r'"\$([A-Z]{1,5}(?:-[A-Z])?)",\s*"(\d+\.\d+)%"', raw)
            if pairs:
                tickers = list(dict.fromkeys(_normalize_ticker(t) for t, _ in pairs))
                weights = {_normalize_ticker(t): _safe_float(w) for t, w in pairs}
                return tickers, _norm_weights(weights)
            tickers = list(dict.fromkeys(
                _normalize_ticker(m) for m in re.findall(r'"\$([A-Z]{1,5}(?:-[A-Z])?)"', raw)
            ))
            return tickers, {}
    except Exception:
        pass
    return [], {}


def fetch_etf_holdings(symbol: str) -> tuple[list[str], dict[str, float], dict[str, str]]:
    """Fetch ETF constituent tickers, weights, and names via priority pipeline.
    Returns (tickers, {ticker: weight_pct}, {ticker: name})."""
    sym_lower = symbol.lower()

    # ── SSGA/SPDR ────────────────────────────────────────────────────
    try:
        url  = f"https://www.ssga.com/library-content/products/fund-data/etfs/us/holdings-daily-us-en-{sym_lower}.xlsx"
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        if resp.status_code == 200:
            xl        = pd.read_excel(BytesIO(resp.content), header=None)
            ticker_row = next((i for i, row in xl.iterrows() if "Ticker" in row.values), None)
            if ticker_row is not None:
                df = pd.read_excel(BytesIO(resp.content), header=ticker_row)
                tickers, weights, names = _parse_df_holdings(df, "Ticker")
                if tickers:
                    if not weights:
                        weights = _fetch_stockanalysis(sym_lower)[1]
                    log.info(f"Fetched {len(tickers)} holdings from {symbol} via SSGA")
                    return tickers, _norm_weights(weights), names
    except Exception as e:
        log.debug(f"{symbol} SSGA fetch failed: {e}")

    # ── iShares/BlackRock ─────────────────────────────────────────────
    if sym_lower in _ISHARES_PRODUCTS:
        try:
            pid, slug = _ISHARES_PRODUCTS[sym_lower]
            url  = (f"https://www.ishares.com/us/products/{pid}/{slug}"
                    f"/1467271812596.ajax?fileType=csv&fileName={symbol.upper()}_holdings&dataType=fund")
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
            if resp.status_code == 200:
                lines   = resp.content.decode("utf-8-sig").splitlines()
                hdr_idx = next((i for i, l in enumerate(lines) if "Ticker" in l), None)
                if hdr_idx is not None:
                    df   = pd.read_csv(StringIO("\n".join(lines[hdr_idx:])))
                    tcol = next((c for c in df.columns if "icker" in c), None)
                    if tcol:
                        tickers, weights, names = _parse_df_holdings(df, tcol)
                        if tickers:
                            if not weights:
                                weights = _fetch_stockanalysis(sym_lower)[1]
                            log.info(f"Fetched {len(tickers)} holdings from {symbol} via iShares")
                            return tickers, _norm_weights(weights), names
        except Exception as e:
            log.debug(f"{symbol} iShares fetch failed: {e}")

    # ── Vanguard ──────────────────────────────────────────────────────
    if sym_lower in _VANGUARD_ETFS:
        try:
            url  = (f"https://investor.vanguard.com/investment-products/etfs"
                    f"/profile/api/{sym_lower}/portfolio-holding/stock")
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=20)
            if resp.status_code == 200:
                tickers, weights, names = [], {}, {}
                for e in resp.json().get("fund", {}).get("entity", []):
                    t = str(e.get("ticker", "")).strip()
                    if t and t not in ("", "-", "nan"):
                        nt = _normalize_ticker(t)
                        tickers.append(nt)
                        weights[nt] = _safe_float(
                            e.get("percentWeight") or e.get("holdingPercent") or e.get("weighting") or 0)
                        n = _safe_name(e.get("longName") or e.get("name") or e.get("fundName") or "")
                        if n:
                            names[nt] = n
                if tickers:
                    if not weights:
                        weights = _fetch_stockanalysis(sym_lower)[1]
                    log.info(f"Fetched {len(tickers)} holdings from {symbol} via Vanguard")
                    return tickers, _norm_weights(weights), names
        except Exception as e:
            log.debug(f"{symbol} Vanguard fetch failed: {e}")

    # ── Wikipedia (QQQ only) ──────────────────────────────────────────
    if sym_lower == "qqq":
        try:
            resp = requests.get("https://en.wikipedia.org/wiki/Nasdaq-100",
                                headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            if resp.status_code == 200:
                for tbl in pd.read_html(StringIO(resp.text), header=0):
                    tcol = next((c for c in tbl.columns if "ticker" in str(c).lower() or "symbol" in str(c).lower()), None)
                    ncol = next((c for c in tbl.columns if "company" in str(c).lower() or "security" in str(c).lower()), None)
                    if tcol and len(tbl) >= 50:
                        ticker_vals = [str(v).strip() for v in tbl[tcol]]
                        name_vals   = tbl[ncol].tolist() if ncol else None
                        tickers, names = [], {}
                        for i, t in enumerate(ticker_vals):
                            if t not in _INVALID_TICKER:
                                nt = _normalize_ticker(t)
                                tickers.append(nt)
                                if name_vals:
                                    n = _safe_name(name_vals[i])
                                    if n:
                                        names[nt] = n
                        if len(tickers) >= 50:
                            # Supplement with weights from stockanalysis.com (top ~25 free)
                            _, weights = _fetch_stockanalysis("qqq")
                            log.info(f"Fetched {len(tickers)} holdings from QQQ via Wikipedia"
                                     + (f" + {len(weights)} weights from stockanalysis.com" if weights else ""))
                            return tickers, weights, names
        except Exception as e:
            log.debug(f"QQQ Wikipedia fetch failed: {e}")

    # ── stockanalysis.com (top ~25 with weights, free tier) ──────────
    try:
        tickers, weights = _fetch_stockanalysis(sym_lower)
        if tickers:
            log.info(f"Fetched {len(tickers)} holdings from {symbol} via stockanalysis.com")
            return tickers, weights, {}
    except Exception as e:
        log.debug(f"{symbol} stockanalysis.com fetch failed: {e}")

    # ── yfinance fallback ─────────────────────────────────────────────
    try:
        tickers = [_normalize_ticker(str(t)) for t in yf.Ticker(symbol).funds_data.top_holdings.index if t]
        log.info(f"Fetched {len(tickers)} top holdings from {symbol} via yfinance (fallback)")
        return tickers, {}, {}
    except Exception as e:
        log.warning(f"Could not fetch holdings for {symbol}: {e}")
        return [], {}, {}


def load_holdings_cache() -> bool:
    """Load holdings from disk. Returns True if cache exists and is current month."""
    try:
        with open(HOLDINGS_CACHE_FILE) as f:
            data = json.load(f)
        updated = data.get("updated")
        loaded  = data.get("holdings", {})
        if not updated or not loaded:
            return False
        missing = [e for e in ALL_ETFS if e not in loaded]
        if missing:
            log.info(f"Cache missing ETFs {missing} — will refresh")
            return False
        with _holdings_lock:
            _holdings.clear(); _holdings.update(loaded)
            _weights.clear();  _weights.update(data.get("weights", {}))
            _names.clear();    _names.update(data.get("names", {}))
            _holdings_meta["updated"] = updated
        return updated[:7] == datetime.now().strftime("%Y-%m")
    except Exception as e:
        log.warning(f"Could not load holdings cache: {e}")
        return False


def save_holdings_cache():
    """Persist current holdings to disk."""
    try:
        with _holdings_lock:
            data = {"updated": _holdings_meta["updated"],
                    "holdings": dict(_holdings), "weights": dict(_weights), "names": dict(_names)}
        with open(HOLDINGS_CACHE_FILE, "w") as f:
            json.dump(data, f, indent=2)
        log.info(f"Holdings cache saved → {HOLDINGS_CACHE_FILE}")
    except Exception as e:
        log.error(f"Could not save holdings cache: {e}")


def refresh_holdings():
    """Fetch all ETF holdings and cache to disk. Safe to run in a background thread."""
    with _holdings_lock:
        _holdings_meta.update(status="loading", message="Fetching ETF holdings — please wait…")

    new_h, new_w, new_n = {}, {}, {}
    for etf in ALL_ETFS:
        with _holdings_lock:
            _holdings_meta["message"] = f"Fetching {etf.upper()} holdings…"
        tickers, weights, names = fetch_etf_holdings(etf.upper())
        new_h[etf] = tickers
        new_w[etf] = weights
        for t, n in names.items():
            new_n.setdefault(t, n)
        log.info(f"  {etf.upper()}: {len(tickers)} holdings")

    today = datetime.now().strftime("%Y-%m-%d")
    with _holdings_lock:
        _holdings.clear(); _holdings.update(new_h)
        _weights.clear();  _weights.update(new_w)
        _names.clear();    _names.update(new_n)
        _holdings_meta.update(updated=today, status="ready",
                              message=f"Holdings ready — last updated {today}",
                              fresh_fetch=True)
    save_holdings_cache()


def get_universe(universes: list[str]) -> list[str]:
    with _holdings_lock:
        combined = list(dict.fromkeys(t for etf in universes for t in _holdings.get(etf.lower(), [])))
    log.info(f"Universe: {len(combined)} tickers from {', '.join(universes)}")
    return combined


def compute_rsi(closes: pd.Series, period: int = RSI_PERIOD) -> float:
    try:
        if len(closes) < period + 1: return np.nan
        v = RSIIndicator(close=closes, window=period).rsi().iloc[-1]
        return float(v) if not np.isnan(v) else np.nan
    except Exception:
        return np.nan


def _yf_download(tickers, max_retries: int = 3, **kwargs):
    """yf.download with exponential-backoff retry on rate-limit errors."""
    kwargs = {"group_by": "ticker", "auto_adjust": True, "progress": False, "threads": True} | kwargs
    for attempt in range(max_retries):
        try:
            return yf.download(tickers=tickers, **kwargs)
        except Exception as e:
            is_rate_limit = any(k in str(e).lower() for k in ("rate", "429", "too many"))
            if is_rate_limit and attempt < max_retries - 1:
                wait = 10 * (2 ** attempt)   # 10s, 20s, 40s
                log.warning(f"Rate limited — retrying in {wait}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(wait)
            else:
                raise


def _fetch_history_stooq(ticker: str, days: int = 90) -> pd.DataFrame | None:
    """Fetch daily OHLCV from Stooq as a fallback when yfinance is rate limited."""
    try:
        now  = datetime.now()
        sym  = ticker.replace("-", ".").lower()
        url  = (f"https://stooq.com/q/d/l/?s={sym}.us"
                f"&d1={(now - timedelta(days=days)).strftime('%Y%m%d')}"
                f"&d2={now.strftime('%Y%m%d')}&i=d")
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200 or "No data" in resp.text[:50]:
            return None
        df = pd.read_csv(StringIO(resp.text), parse_dates=["Date"]).sort_values("Date")
        if "Close" not in df.columns or "Volume" not in df.columns or len(df) < 30:
            return None
        return df.set_index("Date")
    except Exception:
        return None


def _fetch_pe_cnbc(ticker: str) -> float | None:
    """Fetch trailing P/E ratio from CNBC's free quote API."""
    try:
        url  = (f"https://quote.cnbc.com/quote-html-webservice/quote.htm"
                f"?symbols={ticker}&requestMethod=itv&noform=1&partnerId=2"
                f"&fund=1&exthrs=1&outputFormat=json&events=0")
        resp = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            return None
        data = resp.json()
        quote = (data.get("QuickQuoteResult", {})
                     .get("QuickQuote", [None])[0])
        if not quote:
            return None
        pe = quote.get("pe") or quote.get("PERatio")
        return float(pe) if pe not in (None, "", "N/A") else None
    except Exception:
        return None


def screen_batch(tickers: list[str], pe_max: float, rsi_min: float, rsi_max: float, vol_ratio_min: float) -> list[dict]:
    """Download and screen a batch of tickers. Returns passing stocks."""
    # Try yfinance batch download; fall back to per-ticker Stooq on rate limit
    try:
        data = _yf_download(tickers, period="3mo", interval="1d")
        lvl0 = set(data.columns.get_level_values(0))
        ticker_data: dict[str, pd.DataFrame] = {t: data[t] for t in tickers if t in lvl0}
    except Exception as e:
        log.warning(f"yfinance batch failed ({e}); falling back to Stooq per-ticker")
        ticker_data = {t: df for t in tickers if (df := _fetch_history_stooq(t)) is not None}

    # Pass 1: RSI + volume filter
    survivors: list[tuple[str, float, float, float, float, float]] = []
    for ticker in tickers:
        try:
            df = ticker_data.get(ticker)
            if df is None:
                continue
            df = df.dropna(subset=["Close", "Volume"])
            if len(df) < VOL_WINDOW + RSI_PERIOD:
                continue

            closes, volumes = df["Close"], df["Volume"]
            rsi = compute_rsi(closes)
            if np.isnan(rsi) or rsi <= rsi_min or rsi > rsi_max:
                continue

            avg_vol    = volumes.iloc[-VOL_WINDOW - 1:-1].mean()
            latest_vol = volumes.iloc[-1]
            if avg_vol == 0 or latest_vol / avg_vol < vol_ratio_min:
                continue

            survivors.append((ticker, float(rsi), float(latest_vol / avg_vol),
                               float(latest_vol), float(avg_vol), float(closes.dropna().iloc[-1])))
        except Exception as e:
            log.debug(f"  ✗ {ticker}: {e}")

    if not survivors:
        return []

    # Pass 2: P/E for survivors in parallel
    def fetch_pe(ticker: str, last_close: float) -> dict | None:
        pe, price = None, last_close
        try:
            tkr        = yf.Ticker(ticker)
            fi         = tkr.fast_info
            market_cap = getattr(fi, "market_cap", None)
            price      = float(getattr(fi, "last_price", last_close))
            if market_cap and market_cap > 0:
                fin = tkr.financials
                if fin is not None and not fin.empty and "Net Income" in fin.index:
                    net_income = float(fin.loc["Net Income"].iloc[0])
                    if net_income > 0:
                        pe = market_cap / net_income
        except Exception:
            pass

        if pe is None:
            pe = _fetch_pe_cnbc(ticker)

        if pe is None or pe <= 0 or pe >= pe_max:
            return None
        return {"price": round(price, 2), "pe": round(pe, 2)}

    results = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        future_map = {
            pool.submit(fetch_pe, ticker, last_close): (ticker, rsi, vol_ratio, latest_vol, avg_vol)
            for ticker, rsi, vol_ratio, latest_vol, avg_vol, last_close in survivors
        }
        for future in as_completed(future_map):
            ticker, rsi, vol_ratio, latest_vol, avg_vol = future_map[future]
            pe_data = future.result()
            if pe_data is None:
                continue
            results.append({
                "ticker": ticker, "price": pe_data["price"], "pe_ratio": pe_data["pe"],
                "volume_ratio": round(vol_ratio, 2), "rsi": round(rsi, 2),
                "volume": int(latest_vol), "avg_volume": int(avg_vol),
            })
            log.info(f"  ✓ {ticker} | P/E={pe_data['pe']:.1f} | VolRatio={vol_ratio:.2f}x | RSI={rsi:.1f}")
    return results


def run_screener(params: dict):
    """Full screening run — called in a background thread."""
    universes     = params.get("universes", ALL_ETFS)
    pe_max        = float(params.get("pe_max", PE_MAX))
    rsi_min       = float(params.get("rsi_min", RSI_MIN))
    rsi_max       = float(params.get("rsi_max", RSI_MAX))
    vol_ratio_min = float(params.get("vol_ratio_min", VOLUME_RATIO_MIN))

    with _lock:
        _state.update(status="running", progress=0, results=[], error=None,
                      message="Fetching stock universe…", screened=0, passed=0, params=params)

    try:
        research = [_normalize_ticker(t.strip().upper()) for t in params.get("research_stocks", []) if t.strip()]
        universe = get_universe(universes)

        with _holdings_lock:
            ticker_etf_map: dict[str, list[str]] = {}
            for etf in universes:
                for t in _holdings.get(etf.lower(), []):
                    ticker_etf_map.setdefault(t, []).append(etf.upper())

        for t in research:
            ticker_etf_map.setdefault(t, []).append("Research")
            if t not in universe:
                universe.append(t)

        total = len(universe)
        with _lock:
            _state.update(universe_size=total, message=f"Screening {total} stocks in batches…")

        all_results = []
        batches = [universe[i:i + BATCH_SIZE] for i in range(0, total, BATCH_SIZE)]

        for idx, batch in enumerate(batches):
            with _lock:
                _state.update(
                    progress=int((idx / len(batches)) * 90),
                    message=f"Batch {idx + 1}/{len(batches)} | Screened {idx * BATCH_SIZE}/{total} | Passed {len(all_results)}",
                )
            batch_results = screen_batch(batch, pe_max, rsi_min, rsi_max, vol_ratio_min)
            for r in batch_results:
                r["etfs"] = ticker_etf_map.get(r["ticker"], [])
            all_results.extend(batch_results)
            with _lock:
                _state.update(screened=min((idx + 1) * BATCH_SIZE, total), passed=len(all_results))
            if idx < len(batches) - 1:
                time.sleep(BATCH_DELAY)

        all_results.sort(key=lambda x: x["rsi"], reverse=True)
        with _lock:
            _state.update(status="done", progress=100, results=all_results,
                          last_run=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                          message=f"Scan complete — {_state['screened']} screened, {len(all_results)} passed all filters")
        log.info(f"Screener done. {len(all_results)} passed.")

    except Exception as e:
        log.error(f"Screener error: {e}", exc_info=True)
        with _lock:
            _state.update(status="error", error=str(e), message=f"Error: {e}")


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/run", methods=["POST"])
def api_run():
    with _holdings_lock:
        if _holdings_meta["status"] != "ready":
            return jsonify({"ok": False, "message": _holdings_meta["message"]}), 409
    with _lock:
        if _state["status"] == "running":
            return jsonify({"ok": False, "message": "Screener already running"}), 409

    body = request.get_json(silent=True) or {}
    params = {
        "universes":       body.get("universes", ALL_ETFS),
        "research_stocks": body.get("research_stocks", []),
        "pe_max":          float(body.get("pe_max", PE_MAX)),
        "rsi_min":         float(body.get("rsi_min", RSI_MIN)),
        "rsi_max":         float(body.get("rsi_max", RSI_MAX)),
        "vol_ratio_min":   float(body.get("vol_ratio_min", VOLUME_RATIO_MIN)),
    }
    threading.Thread(target=run_screener, args=(params,), daemon=True).start()
    return jsonify({"ok": True, "message": "Screener started"})


@app.route("/api/status")
def api_status():
    with _lock:
        return jsonify(dict(_state))


@app.route("/api/holdings")
def api_holdings():
    """Lightweight status endpoint — safe to poll every 2s."""
    with _holdings_lock:
        fresh = _holdings_meta["fresh_fetch"]
        _holdings_meta["fresh_fetch"] = False
        return jsonify({
            "status":      _holdings_meta["status"],
            "updated":     _holdings_meta["updated"],
            "message":     _holdings_meta["message"],
            "counts":      {k: len(v) for k, v in _holdings.items()},
            "fresh_fetch": fresh,
        })


@app.route("/api/holdings/refresh", methods=["POST"])
def api_holdings_refresh():
    """Trigger a full holdings refresh in the background."""
    with _holdings_lock:
        if _holdings_meta["status"] == "loading":
            return jsonify({"ok": False, "message": "Refresh already in progress"}), 409
        _holdings_meta["status"] = "loading"
        _holdings_meta["message"] = "Refreshing ETF holdings…"
    threading.Thread(target=refresh_holdings, daemon=True).start()
    return jsonify({"ok": True, "message": "Refresh started"})


@app.route("/api/holdings/data")
def api_holdings_data():
    """Full tickers/weights/names — call once on demand, not on a poll loop."""
    with _holdings_lock:
        return jsonify({"tickers": dict(_holdings), "weights": dict(_weights), "names": dict(_names)})


def _fetch_etf_performance() -> dict:
    """Download YTD price data for all ETFs; return ytd%, daily%, price, and expense_ratio."""
    symbols = [e.upper() for e in ALL_ETFS]
    result  = {e: {"ytd": None, "daily": None, "price": None, "expense_ratio": None} for e in ALL_ETFS}
    data = _yf_download(symbols, start=f"{datetime.now().year}-01-01")
    for etf in ALL_ETFS:
        try:
            closes = data[etf.upper()]["Close"].dropna()
            if len(closes) >= 2:
                first, prev, last = float(closes.iloc[0]), float(closes.iloc[-2]), float(closes.iloc[-1])
                result[etf].update({
                    "ytd":   round((last - first) / first * 100, 2),
                    "daily": round((last - prev)  / prev  * 100, 2),
                    "price": round(last, 2),
                })
        except Exception:
            pass

    def _get_expense_ratio(sym_lower: str) -> tuple[str, float | None]:
        try:
            info = yf.Ticker(sym_lower.upper()).info
            er = info.get("netExpenseRatio") or info.get("annualReportExpenseRatio") or info.get("expenseRatio")
            return sym_lower, round(float(er), 4) if er else None
        except Exception:
            return sym_lower, None

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_get_expense_ratio, e): e for e in ALL_ETFS}
        for fut in as_completed(futures):
            etf_key, er = fut.result()
            result[etf_key]["expense_ratio"] = er

    return result


@app.route("/api/etf-performance")
def api_etf_performance():
    """YTD%, daily%, and price for every ETF. Cached for 5 minutes.
    Lock released before blocking fetch — concurrent misses both fetch, second write is harmless."""
    with _perf_lock:
        if time.time() - _perf_state["ts"] < PERF_CACHE_TTL and _perf_state["cache"]:
            return jsonify(_perf_state["cache"])
    try:
        result = _fetch_etf_performance()
    except Exception as e:
        log.warning(f"ETF performance fetch failed: {e}")
        with _perf_lock:
            return jsonify(_perf_state["cache"] or {etf: {"ytd": None, "daily": None, "price": None} for etf in ALL_ETFS})
    with _perf_lock:
        _perf_state["cache"].clear()
        _perf_state["cache"].update(result)
        _perf_state["ts"] = time.time()
    return jsonify(result)


@app.route("/api/prices", methods=["POST"])
def api_prices():
    """Latest price, daily change, and MA20/MA50 for a list of tickers."""
    tickers = (request.get_json(silent=True) or {}).get("tickers", [])
    if not tickers:
        return jsonify({})

    prices = {}
    try:
        data = _yf_download(tickers, period="3mo", interval="1d")
        for ticker in tickers:
            try:
                if ticker not in data.columns.get_level_values(0):
                    continue
                closes = data[ticker]["Close"].dropna()
                if len(closes) < 2:
                    continue
                last, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
                prices[ticker] = {
                    "price":  round(last, 2),
                    "change": round(last - prev, 2),
                    "pct":    round((last - prev) / prev * 100, 2) if prev else 0,
                    "ma20":   round(float(closes.iloc[-20:].mean()), 2) if len(closes) >= 20 else None,
                    "ma50":   round(float(closes.iloc[-50:].mean()), 2) if len(closes) >= 50 else None,
                }
            except Exception:
                pass
    except Exception as e:
        log.warning(f"Price fetch failed: {e}")
    return jsonify(prices)


@app.route("/api/news/<path:ticker>")
def api_news(ticker):
    ticker = ticker.upper()
    articles = []
    try:
        for a in (yf.Ticker(ticker).news or [])[:20]:
            c         = a.get("content") or {}
            title     = c.get("title")     or a.get("title", "")
            url       = (c.get("canonicalUrl") or {}).get("url") or a.get("link", "")
            publisher = (c.get("provider")    or {}).get("displayName") or a.get("publisher", "")
            ts        = a.get("providerPublishTime")
            pub_date  = c.get("pubDate") or (datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else "")
            if title and url:
                articles.append({"title": title, "url": url, "publisher": publisher, "published": pub_date})
    except Exception as e:
        log.debug(f"News fetch failed for {ticker}: {e}")
    is_etf = ticker.lower() in _ALL_ETFS_SET
    sym    = ticker.lower().replace(".", "-")
    mw_path = "fund" if is_etf else "stock"
    links = {
        "yf_url":  f"https://finance.yahoo.com/quote/{ticker}/news/",
        "sa_url":  f"https://stockanalysis.com/{'etf' if is_etf else 'stocks'}/{sym}/",
        "sea_url": f"https://seekingalpha.com/symbol/{ticker}/news",
        "mw_url":  f"https://www.marketwatch.com/investing/{mw_path}/{sym}",
        "cnbc_url": f"https://www.cnbc.com/quotes/{ticker}",
    }
    return jsonify({"news": articles, **links})


@app.route("/api/search")
def api_search():
    """Search for tickers by company name using yfinance Search."""
    q = request.args.get("q", "").strip()
    if not q or len(q) < 2:
        return jsonify([])
    try:
        results = yf.Search(q).quotes
        out = []
        for r in results:
            qt = r.get("quoteType", "")
            if qt not in ("EQUITY", "ETF", "INDEX"):
                continue
            symbol = r.get("symbol", "")
            if not symbol or len(symbol) > 10:
                continue
            name = r.get("shortname") or r.get("longname") or ""
            out.append({"symbol": symbol, "name": name, "type": qt})
            if len(out) >= 8:
                break
        return jsonify(out)
    except Exception as e:
        log.warning(f"Symbol search failed: {e}")
        return jsonify([])


@app.route("/api/extended/<path:ticker>")
def api_extended(ticker):
    """After-hours/pre-market quote, earnings calendar, analyst consensus, and recent upgrades."""
    ticker = ticker.upper()
    out = {"name": "", "post_market": None, "pre_market": None, "volume": None, "earnings": None, "dividend": None, "analyst": None, "upgrades": [], "insider": []}
    try:
        tkr  = yf.Ticker(ticker)
        fi   = tkr.fast_info

        # Fetch info once — used for both after-hours prices and analyst consensus
        try:
            info = tkr.info or {}
        except Exception as e:
            log.warning(f"tkr.info failed for {ticker}: {e}")
            info = {}

        out["name"] = info.get("longName") or info.get("shortName") or ""

        last = (_safe_val(getattr(fi, "last_price", None))
                or _safe_val(info.get("regularMarketPrice")) or 0)

        # After-hours / pre-market: fast_info attrs first, then info dict fallback
        for key, fi_attr, info_key in (
            ("post_market", "post_market_price", "postMarketPrice"),
            ("pre_market",  "pre_market_price",  "preMarketPrice"),
        ):
            try:
                price = (_safe_val(getattr(fi, fi_attr, None))
                         or _safe_val(info.get(info_key)))
                if price and last and abs(price - last) > 0.001:
                    change = round(price - last, 2)
                    out[key] = {"price": price, "change": change,
                                "pct": round(change / last * 100, 2)}
            except Exception:
                pass

        # Volume — info first, fast_info as fallback
        vol     = (info.get("regularMarketVolume") or info.get("volume")
                   or _safe_val(getattr(fi, "last_volume", None)))
        avg_vol = (info.get("averageVolume") or info.get("averageDailyVolume3Month")
                   or _safe_val(getattr(fi, "three_month_average_volume", None)))
        if vol:
            out["volume"] = {
                "today":   int(vol),
                "avg":     int(avg_vol) if avg_vol else None,
                "ratio":   round(vol / avg_vol, 2) if avg_vol else None,
            }

        # Earnings calendar
        try:
            cal = tkr.calendar
            if cal:
                d = cal if isinstance(cal, dict) else cal[cal.columns[0]].to_dict()
                dates = d.get("Earnings Date") or []
                if not isinstance(dates, (list, tuple)): dates = [dates]
                out["earnings"] = {
                    "next_date":        str(dates[0])[:10] if dates else None,
                    "eps_estimate":     _safe_val(d.get("Earnings Average") or d.get("EPS Estimate")),
                    "revenue_estimate": _safe_val(d.get("Revenue Average") or d.get("Revenue Estimate")),
                }
        except Exception:
            pass

        # Dividend + analyst consensus (info already fetched above)
        if info:
            rate  = _safe_val(info.get("dividendRate"))
            yld   = _safe_val(info.get("dividendYield"))
            ex_ts = info.get("exDividendDate")
            ex_date = None
            if ex_ts:
                try:
                    ex_date = datetime.fromtimestamp(ex_ts, tz=timezone.utc).strftime("%Y-%m-%d")
                except Exception:
                    pass
            if rate or yld:
                out["dividend"] = {
                    "rate":     round(rate, 4) if rate else None,
                    "yield":    round(yld * 100, 2) if yld else None,
                    "ex_date":  ex_date,
                }

            rec = (info.get("recommendationKey") or "").replace("_", " ").title() or None
            analyst = {
                "target_mean": _safe_val(info.get("targetMeanPrice")),
                "target_low":  _safe_val(info.get("targetLowPrice")),
                "target_high": _safe_val(info.get("targetHighPrice")),
                "rec":         rec,
                "n":           info.get("numberOfAnalystOpinions"),
                "current":     _safe_val(info.get("currentPrice")) or last or None,
            }
            if any(analyst.values()):
                out["analyst"] = analyst

        # Analyst upgrades / downgrades
        try:
            ud = tkr.upgrades_downgrades
            if ud is not None and not ud.empty:
                ud = ud.sort_index(ascending=False).head(5)
                out["upgrades"] = [
                    {"date":   str(idx.date()) if hasattr(idx, "date") else str(idx)[:10],
                     "firm":   str(row.get("Firm", "")),
                     "to":     str(row.get("ToGrade", "")),
                     "from":   str(row.get("FromGrade", "")),
                     "action": str(row.get("Action", ""))}
                    for idx, row in ud.iterrows()
                ]
        except Exception:
            pass

        # Insider transactions
        try:
            it = tkr.insider_transactions
            if it is not None and not it.empty:
                it = it.head(10)
                rows = []
                for _, row in it.iterrows():
                    text = str(row.get("Text", "") or "")
                    txn  = ("Sale" if "sale" in text.lower()
                            else "Purchase" if "purchase" in text.lower()
                            else "Other")
                    shares = row.get("Shares")
                    value  = row.get("Value")
                    rows.append({
                        "date":     str(row.get("Start Date", ""))[:10],
                        "insider":  str(row.get("Insider", "") or "").title(),
                        "position": str(row.get("Position", "") or ""),
                        "type":     txn,
                        "shares":   int(shares) if shares and not pd.isna(shares) else None,
                        "value":    int(value)  if value  and not pd.isna(value)  else None,
                        "ownership":str(row.get("Ownership", "") or ""),
                    })
                if rows:
                    out["insider"] = rows
        except Exception:
            pass

    except Exception as e:
        log.warning(f"Extended data failed for {ticker}: {e}")

    return jsonify(out)


@app.route("/api/afterhours", methods=["POST"])
def api_afterhours():
    """Batch after-hours price check vs regular close. Only returns tickers with extended-hours data."""
    tickers = (request.get_json(silent=True) or {}).get("tickers", [])
    if not tickers:
        return jsonify({})

    result = {}
    lvl0 = lambda df: df.columns.get_level_values(0)
    for i in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[i:i + BATCH_SIZE]
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                fut_ext = pool.submit(_yf_download, batch, period="1d", interval="5m", prepost=True)
                fut_reg = pool.submit(_yf_download, batch, period="5d", interval="1d")
                ext, reg = fut_ext.result(), fut_reg.result()
            for t in batch:
                try:
                    if t not in lvl0(ext) or t not in lvl0(reg): continue
                    ext_c = ext[t]["Close"].dropna()
                    reg_c = reg[t]["Close"].dropna()
                    if ext_c.empty or reg_c.empty: continue
                    last = ext_c.index[-1]
                    if getattr(last, "tz", None):
                        et = last.tz_convert("America/New_York")
                        if 930 <= et.hour * 100 + et.minute < 1600: continue
                    rc = float(reg_c.iloc[-1])
                    ah = float(ext_c.iloc[-1])
                    if not rc: continue
                    pct = round((ah - rc) / rc * 100, 2)
                    if abs(pct) >= 0.01:
                        result[t] = {"price": round(ah, 2), "pct": pct}
                except Exception:
                    pass
        except Exception as e:
            log.warning(f"After-hours batch failed: {e}")
        if i + BATCH_SIZE < len(tickers):
            time.sleep(1.0)
    return jsonify(result)


# ── Entry point ───────────────────────────────────────────────────────────────

def _auto_startup():
    """Load cache at startup; refresh in background if cache is missing/stale."""
    loaded = load_holdings_cache()
    if loaded:
        with _holdings_lock:
            _holdings_meta.update(status="ready",
                                  message=f"Holdings loaded from cache — {_holdings_meta['updated']}")
        log.info(f"Holdings loaded from cache ({_holdings_meta['updated']})")
    else:
        log.info("No current cache — refreshing holdings in background…")
        threading.Thread(target=refresh_holdings, daemon=True).start()

_auto_startup()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", PORT))
    log.info(f"Starting Stock Screener on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
