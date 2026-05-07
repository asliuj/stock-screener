# Stock Screener Project

## Overview
A real-time ETF-based stock screener that screens constituent stocks across 39 ETFs/indices (Stock Market Overview, State Street SPDR, Defense, Blackrock iShares, and Vanguard) for:
- **P/E Ratio** < threshold (default 30)
- **Volume Spike** > threshold vs 20-day average (default 2×)
- **RSI** in range (default 50–70)

Results are ranked by RSI (highest first). Each result is tagged with the ETF(s) it belongs to.

---

## Tech Stack
- **Backend**: Python 3, Flask (port 8080)
- **Data**: yfinance (Yahoo Finance API), requests (SSGA/iShares/Vanguard holdings)
- **Technical Analysis**: ta (RSI via `ta.momentum.RSIIndicator`)
- **Frontend**: Vanilla HTML/CSS/JS, served by Flask

---

## Project Structure
```
stock-screener/
├── CLAUDE.md               # This file — project context
├── requirements.txt        # Python dependencies
├── holdings_cache.json     # Cached ETF constituent tickers, weights, names (refreshed monthly)
├── app.py                  # Flask backend + screener logic
└── templates/
    └── index.html          # Frontend UI
```

---

## ETF Universe (39 ETFs/indices across 5 groups)

### Stock Market Overview (4 indices)
| Ticker | Name | Holdings Source |
|--------|------|-----------------|
| DIA | Dow Jones Industrial Average | SSGA XLSX |
| OEF | S&P 100 | iShares CSV |
| SPY | S&P 500 | SSGA XLSX |
| QQQ | Nasdaq 100 | Wikipedia (Nasdaq-100 page) |

These appear at the **top** of the ETF Holdings Report modal as the "Stock Market Overview" group.

### State Street — SPDR (12 ETFs)
| Ticker | Name |
|--------|------|
| XLV | Health Care Select Sector SPDR Fund |
| XLK | Technology Select Sector SPDR Fund |
| XLF | Financial Select Sector SPDR Fund |
| XLE | Energy Select Sector SPDR Fund |
| XLI | Industrial Select Sector SPDR Fund |
| XLY | Consumer Discretionary Select Sector SPDR Fund |
| XLP | Consumer Staples Select Sector SPDR Fund |
| XLU | Utilities Select Sector SPDR Fund |
| XLRE | Real Estate Select Sector SPDR Fund |
| XLB | Materials Select Sector SPDR Fund |
| XLC | Communication Services Select Sector SPDR Fund |

### Defense (2 ETFs)
| Ticker | Name |
|--------|------|
| XAR | SPDR S&P Aerospace & Defense ETF |
| NATO | Tema NATO & Defense ETF |

### Blackrock — iShares (11 ETFs)
| Ticker | Name |
|--------|------|
| IHAK | iShares Cybersecurity and Tech ETF |
| IYH | iShares U.S. Healthcare ETF |
| IYE | iShares U.S. Energy ETF |
| IYF | iShares U.S. Financials ETF |
| IYR | iShares U.S. Real Estate ETF |
| IGV | iShares Expanded Tech-Software Sector ETF |
| IVV | iShares Core S&P 500 ETF |
| IWF | iShares Russell 1000 Growth ETF |
| IWM | iShares Russell 2000 ETF |
| ITA | iShares U.S. Aerospace & Defense ETF |
| EFA | iShares MSCI EAFE ETF |

### Vanguard (11 ETFs)
| Ticker | Name |
|--------|------|
| VGT | Vanguard Information Technology ETF |
| VFH | Vanguard Financials ETF |
| VHT | Vanguard Health Care ETF |
| VDE | Vanguard Energy ETF |
| VIS | Vanguard Industrials ETF |
| VCR | Vanguard Consumer Discretionary ETF |
| VDC | Vanguard Consumer Staples ETF |
| VPU | Vanguard Utilities ETF |
| VNQ | Vanguard Real Estate ETF |
| VAW | Vanguard Materials ETF |
| VOX | Vanguard Communication Services ETF |

---

## Startup Flow

Every time `python app.py` is run the user is **always** prompted:

```
Update ETF stock list before starting? (y/n):
```

This prompt must never be skipped. The user decides on every launch whether to refresh.

**Answer `y` — Full holdings refresh (NO shortcuts):**
1. Iterates all 39 ETFs/indices in order and fetches each one's **complete** constituent ticker list using the 6-source priority pipeline below — every source is tried in sequence; the first one that returns valid data wins
2. Saves the result to `holdings_cache.json` with today's date
3. Prints the full ETF Holdings Summary table to the terminal (ETF name, count, and all tickers)
4. Starts Flask on port 8080
5. **The ETF Holdings Report modal opens automatically** when the browser first loads after a fresh refresh — this is non-negotiable; do not skip this step

**Answer `n` — Load from cache:**
- Cache exists and was fetched this calendar month → loads from disk immediately, Flask starts
- Cache is missing or stale → triggers a full synchronous refresh using the same 6-source pipeline (no shortcuts), then **auto-opens the ETF Holdings Report modal** in the browser when the refresh completes

---

## Holdings Fetch Pipeline (Priority Order — NO shortcuts)

Each ETF is fetched using the first source that returns valid data. Always go through the full pipeline in order — never skip to yfinance directly.

### 1. SSGA XLSX (SPDR ETFs — State Street)
- URL: `https://www.ssga.com/library-content/products/fund-data/etfs/us/holdings-daily-us-en-{ticker}.xlsx`
- Returns: full holdings (23–500+ stocks) with weights and names
- Applies to: DIA, SPY, XLV, XLK, XLF, XLE, XLI, XLY, XLP, XLU, XLRE, XLB, XLC, XAR
- Non-SPDR ETFs return 404 and fall through immediately — this is expected

### 2. iShares CSV API (Blackrock ETFs)
- URL: `https://www.ishares.com/us/products/{product_id}/{slug}/1467271812596.ajax?fileType=csv`
- Returns: full holdings (43–1930 stocks) with weights and names
- Applies to: OEF, IHAK, IYH, IYE, IYF, IYR, IGV, IVV, IWF, IWM, ITA, EFA
- Product ID mapping is hardcoded in `_ISHARES_PRODUCTS` in app.py; do not guess IDs — look up the product page URL on ishares.com if an ID needs to be added or corrected

### 3. Vanguard JSON API (Vanguard ETFs)
- URL: `https://investor.vanguard.com/investment-products/etfs/profile/api/{ticker}/portfolio-holding/stock`
- Returns: full holdings from `response.fund.entity[].ticker` (67–415 stocks) with weights and names
- Applies to: VGT, VFH, VHT, VDE, VIS, VCR, VDC, VPU, VNQ, VAW, VOX

### 4. Wikipedia (Nasdaq-100 / QQQ only)
- URL: `https://en.wikipedia.org/wiki/Nasdaq-100`
- Returns: ~102 Nasdaq-100 constituents parsed from the HTML table with a "Ticker" or "Symbol" column (must have ≥ 50 rows to qualify); names captured, no weights
- Applies to: QQQ only
- Uses `pd.read_html()` — requires `lxml` or `html5lib` (both in requirements.txt)

### 5. stockanalysis.com SvelteKit API (any ETF — top ~25 US holdings)
- URL: `https://stockanalysis.com/etf/{ticker}/holdings/__data.json`
- Returns: top 25 US-listed holdings extracted from the SvelteKit `__data.json` payload via regex `"\$([A-Z]{1,5}(?:-[A-Z])?)"` (strips the `$` prefix); no weights or names
- Applies to: any ETF that falls through sources 1–3 (currently NATO)
- Full list requires a paid subscription; top 25 is the free ceiling

### 6. yfinance fallback (last resort only)
- Returns: top 10 holdings only via `yf.Ticker(symbol).funds_data.top_holdings`; no weights or names
- Used only when all five sources above fail for a given ETF
- A result from this source is a warning sign that the provider API may be broken

---

## Per-ETF Source Reference (as of 2026-05)
| ETF | Source | Holdings |
|-----|--------|----------|
| DIA, SPY, XLV, XLK, XLF, XLE, XLI, XLY, XLP, XLU, XLRE, XLB, XLC, XAR | SSGA XLSX | 23–500+ |
| OEF, IHAK, IYH, IYE, IYF, IYR, IGV, IVV, IWF, IWM, ITA, EFA | iShares CSV | 43–1930 |
| VGT, VFH, VHT, VDE, VIS, VCR, VDC, VPU, VNQ, VAW, VOX | Vanguard JSON | 67–415 |
| QQQ | Wikipedia (Nasdaq-100) | ~102 |
| NATO | stockanalysis.com | ~16–25 (free tier cap) |

---

## Holdings Cache
- File: `holdings_cache.json`
- Format: `{"updated": "YYYY-MM-DD", "holdings": {"xlv": [...]}, "weights": {"xlv": {"TICKER": 7.2}}, "names": {"TICKER": "Company Name"}}`
- Validity: current calendar month
- Validated at startup: checks date freshness AND presence of all 39 ETF keys
- If any ETF is missing from cache, a full refresh is triggered

---

## Ticker Normalization
International tickers require special handling for Yahoo Finance:
- Exchange suffix dots are **preserved**: `ASML.AS`, `AZN.L`, `NOVN.SW`
- US share-class dots are **converted to dashes**: `BRK.B` → `BRK-B`
- Logic lives in `_normalize_ticker()` using the `_EXCHANGE_SUFFIXES` set in app.py

---

## Screening Criteria (all configurable in the UI)
| Filter | Default | Condition |
|--------|---------|-----------|
| P/E Ratio | 30 | Market cap / TTM net income |
| Volume Ratio | 2× | Latest volume / 20-day average volume |
| RSI Min | 50 | 14-period RSI |
| RSI Max | 70 | 14-period RSI |

Screening is two-pass:
1. **Pass 1** — RSI + volume filter using a batch `yf.download()` (50 tickers per batch, 1s delay between batches)
2. **Pass 2** — P/E lookup for survivors only, parallelised via `ThreadPoolExecutor(max_workers=10)`

**Important — yfinance batch download behaviour (v0.2.40+):**
`yf.download()` with `group_by="ticker"` always returns a MultiIndex DataFrame, even for a single-ticker batch. The `_get_df()` helper therefore always slices via `data[ticker].copy()` regardless of batch size — the old `len(tickers) == 1` shortcut was removed because it left the MultiIndex intact and caused `dropna()` to silently fail.

**On quiet market days (low overall volume):**
The 2× volume ratio default is calibrated for genuine volume spikes. On low-activity days the screener legitimately returns 0 results — this is not a bug. Users should lower the Volume Ratio slider to 1.2–1.5× if they want results on quiet days.

---

## Key Design Decisions
- `ALL_ETFS` in app.py is the single source of truth — add/remove ETFs here only
- Holdings cache uses `.clear()/.update()` (in-place mutation) to avoid `global` reassignment
- ETF performance cache (`_perf_state`) uses a 5-minute TTL; lock released before the blocking fetch
- `TEMPLATES_AUTO_RELOAD = True` so Flask serves updated HTML without restart during development
- The frontend collects selected ETFs via `querySelectorAll('input.etf-check:checked')` — adding a new ETF only requires one line of HTML
- Each result row shows ETF badge(s) built from a reverse ticker→ETF map constructed at scan time
- `_get_df()` is a closure inside `screen_batch()` that slices a single ticker out of the batch download result; it must always use `data[ticker]` — never `data.copy()` — to avoid MultiIndex leaking into downstream column lookups

### Holdings API split
`GET /api/holdings` returns lightweight status only (`{status, updated, message, counts, fresh_fetch}`) — safe to poll every 2s.
`GET /api/holdings/data` returns full payload (`{tickers, weights, names}`) — call once on demand, not on a poll loop.

### `_parse_df_holdings(df, tcol)` helper
Shared parser for SSGA and iShares DataFrames. Extracts tickers, weights, and names.
- **Critical**: ticker values are extracted via `[str(v).strip() for v in df[tcol]]` (Python list comprehension), NOT `df[tcol].astype(str)`. Newer pandas StringDtype columns leave `pd.NA` as a float-like NaN object rather than the string `"nan"`, which causes `AttributeError: 'float' has no attribute 'startswith'` on the Cash-filter check. Python's `str()` always returns a real string.
- Invalid tickers are filtered via `_INVALID_TICKER = frozenset(("", "-", "nan", "<NA>", "None"))`.

### Browse All Holdings modal
Opened via the "Browse All Holdings" button in the header. Shows all constituent stocks from selected ETFs with:
- ETF badge(s) with fund weight % (e.g. "VGT 18.53%") — sorted by highest fund weight first
- Current price + ▲/▼ daily change % (fetched async via `POST /api/prices`)
- MA20 and MA50 (same async fetch, requires `period="3mo"` to have enough data)
- Company name

### ETF Holdings Report modal
Opened via "View ETF Holdings" button. Shows each ETF row with:
- ETF ticker (links to `finance.yahoo.com/quote/<TICKER>` — opens in new tab)
- Full name, YTD %, daily %, current price, holdings count
- All constituent tickers as Yahoo Finance hyperlinks (hover turns blue)

---

## Running the App
```bash
cd "c:\Users\asliu\Stock Screener"
pip install -r requirements.txt
python app.py
# Visit: http://localhost:8080
```

## Killing the App (Windows)
```powershell
netstat -ano | findstr ":8080" | ForEach-Object { ($_ -split '\s+')[-1] } | Sort-Object -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
```
