# Stock Screener Project

## Overview
A real-time ETF-based stock screener that screens constituent stocks across 39 ETFs/indices (Stock Market Overview, State Street SPDR, Defense, Blackrock iShares, and Vanguard) for:
- **P/E Ratio** < threshold (default 25)
- **Volume Spike** > threshold vs 20-day average (default 2×)
- **RSI** in range (default 1–30)

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

### Defense (8 ETFs)
| Ticker | Name |
|--------|------|
| XAR | SPDR S&P Aerospace & Defense ETF |
| NATO | Tema NATO & Defense ETF |
| ITA | iShares U.S. Aerospace & Defense ETF |
| PPA | Invesco Aerospace & Defense ETF |
| SHLD | Global X Defense Tech ETF |
| WAR | VanEck Defense ETF |
| IDEF | iShares MSCI Global Defense Industry ETF |
| GCAD | Global X Defence ETF |

### Blackrock — iShares (10 ETFs)
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
| OEF, IHAK, IYH, IYE, IYF, IYR, IGV, IVV, IWF, IWM, EFA | iShares CSV | 43–1930 |
| ITA | iShares CSV | 43–1930 (moved to Defense group) |
| VGT, VFH, VHT, VDE, VIS, VCR, VDC, VPU, VNQ, VAW, VOX | Vanguard JSON | 67–415 |
| QQQ | Wikipedia (Nasdaq-100) | ~102 |
| NATO, PPA, SHLD, WAR, IDEF, GCAD | stockanalysis.com | ~16–25 (free tier cap) |

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
| P/E Ratio | 25 | Market cap / TTM net income |
| Volume Ratio | 2× | Latest volume / 20-day average volume |
| RSI Min | 1 | 14-period RSI |
| RSI Max | 30 | 14-period RSI |

Screening is two-pass:
1. **Pass 1** — RSI + volume filter using a batch `yf.download()` (**25 tickers per batch**, **2s delay** between batches)
2. **Pass 2** — P/E lookup for survivors only, parallelised via `ThreadPoolExecutor(max_workers=5)`

**Important — yfinance batch download behaviour (v0.2.40+):**
`yf.download()` with `group_by="ticker"` always returns a MultiIndex DataFrame, even for a single-ticker batch. `screen_batch()` builds a `ticker_data: dict[str, DataFrame]` from the download result and looks up each ticker via `ticker_data.get(ticker)` — never `data.copy()` — to avoid MultiIndex leaking into downstream column lookups.

**Fallback data sources (rate-limit resilience):**
- **Price history**: if `_yf_download()` raises after all retries, `screen_batch()` falls back to fetching each ticker individually from **Stooq** (`stooq.com/q/d/l/?s={sym}.us`) — same OHLCV CSV, no key required
- **P/E ratio**: `fetch_pe()` tries yfinance first; if that fails, falls back to the **CNBC free quote API** (`quote.cnbc.com/quote-html-webservice/quote.htm`) which returns PE directly in JSON
- `_yf_download()` has exponential-backoff retry (10s / 20s / 40s) before raising

**On quiet market days (low overall volume):**
The 2× volume ratio default is calibrated for genuine volume spikes. On low-activity days the screener legitimately returns 0 results — this is not a bug. Users should lower the Volume Ratio slider to 1.2–1.5× if they want results on quiet days.

---

## Color Theme (Barchart-inspired, applied 2026-05-08)
Light theme with dark navy header, matching Barchart's brand aesthetic.

| CSS Variable | Value | Usage |
|---|---|---|
| `--bg` | `#f0f3f7` | Page background (light blue-gray) |
| `--surface` | `#ffffff` | Cards, panels, modals |
| `--surface2` | `#f5f7fb` | Alternating rows, inputs |
| `--border` | `#d0d7e2` | All borders |
| `--accent` / `--blue` | `#1a5fb4` | Barchart blue — buttons, links, accents |
| `--text` | `#1a2535` | Dark navy body text |
| `--muted` | `#6b7b90` | Secondary/dim text |
| `--green` | `#1a8a2e` | Positive price changes |
| `--danger` | `#c41a1a` | Negative price changes |
| `--warn` | `#b06800` | Volume ratio warnings |
| `--purple` | `#6040b0` | RSI meter gradient end |

Header (`<header>`): hardcoded `#0d1b2a` background + `2px solid #1a5fb4` bottom border (not CSS vars — intentionally fixed so theme changes don't break it).

Solid color replacements for semi-transparent backgrounds (work on white surfaces):
- ETF badge: `#ddeaff` / `#aac4f0`
- PE chip: `#d6f0da` / `#90cc98`
- AH up badge: `#d6f0da` / `#90cc98`
- AH down badge: `#fde0e0` / `#f0a0a0`
- Intel up/down actions: same as AH badges
- Rank medals: solid tints of gold/silver/bronze

---

## Key Design Decisions
- `ALL_ETFS` in app.py is the single source of truth — add/remove ETFs here only
- Holdings cache uses `.clear()/.update()` (in-place mutation) to avoid `global` reassignment
- ETF performance cache (`_perf_state`) uses a 5-minute TTL; lock released before the blocking fetch
- `TEMPLATES_AUTO_RELOAD = True` so Flask serves updated HTML without restart during development
- ETF checkboxes are **generated dynamically in JS** from the `ETF_GROUPS` array — adding a new ETF only requires adding it to `ETF_GROUPS` in `index.html`
- Each result row shows ETF badge(s) built from a reverse ticker→ETF map constructed at scan time
- `screen_batch()` stores per-ticker DataFrames in `ticker_data: dict[str, DataFrame]`; lookups use `ticker_data.get(ticker)` directly — no `_get_df` closure needed

### API endpoints
| Route | Purpose |
|-------|---------|
| `GET /api/holdings` | Lightweight status poll (`{status, updated, message, counts, fresh_fetch}`) — safe every 2s |
| `GET /api/holdings/data` | Full payload (`{tickers, weights, names}`) — call once on demand |
| `POST /api/prices` | Price + MA20/MA50 for a ticker list; uses `period="3mo"` for MA50 data |
| `GET /api/etf-performance` | YTD%, daily%, price for all ETFs; 5-min TTL cache |
| `GET /api/news/<ticker>` | yfinance news articles + 5 external link URLs |
| `GET /api/extended/<ticker>` | After-hours/pre-market quote, earnings, analyst consensus, upgrades/downgrades |
| `POST /api/afterhours` | Batch extended-hours price vs regular close; returns tickers with any AH movement (≥0.01%); skips bars inside 9:30am–4pm ET window |
| `POST /api/run` | Start screener; returns 409 if holdings not ready or already running |
| `GET /api/status` | Screener poll |

### Holdings API split
`GET /api/holdings` returns lightweight status only (`{status, updated, message, counts, fresh_fetch}`) — safe to poll every 2s.
`GET /api/holdings/data` returns full payload (`{tickers, weights, names}`) — call once on demand, not on a poll loop.

### `_parse_df_holdings(df, tcol)` helper
Shared parser for SSGA and iShares DataFrames. Extracts tickers, weights, and names.
- **Critical**: ticker values are extracted via `[str(v).strip() for v in df[tcol]]` (Python list comprehension), NOT `df[tcol].astype(str)`. Newer pandas StringDtype columns leave `pd.NA` as a float-like NaN object rather than the string `"nan"`, which causes `AttributeError: 'float' has no attribute 'startswith'` on the Cash-filter check. Python's `str()` always returns a real string.
- Invalid tickers are filtered via `_INVALID_TICKER = frozenset(("", "-", "nan", "<NA>", "None"))`.

### stockanalysis.com weight supplementation
`_fetch_stockanalysis(sym_lower)` returns `(tickers, weights)`. For SSGA, iShares, and Vanguard sources, if the primary source returns no weights, `_fetch_stockanalysis(sym_lower)[1]` is used as a fallback weight supplement (top ~25 free tier). QQQ (Wikipedia) and NATO always use stockanalysis.com for weights.

### Page layout (three panels)
The main page has three distinct control panels, each a `controls-panel` card:

1. **ETF Holdings panel** — "Browse All Holdings" button (top-right of panel) + Select All checkbox + ETF group checkboxes. Defense group is forced onto its own flex row (line break injected before `defense` and `blackrock` groups in `initGroupCheckboxes`).
2. **Research Stocks panel** — standalone panel containing only `#research-group-container`.
3. **Stock Momentum panel** — unified panel containing: filter inputs (P/E Max, RSI Min, RSI Max, Volume Ratio Min) + Run Scan button, divider, status bar, stat cards (Universe / Screened / Passed Filters / Showing), divider, Top Results table. All in one `controls-panel` with `flex-direction:column`.

Header contains only: logo (left), "View ETF Holdings" button (center), "Last run" timestamp (right).

### Browse All Holdings modal
Opened via the "Browse All Holdings" button inside the ETF Holdings panel. Shows all constituent stocks from selected ETFs with:
- ETF badge(s) with fund weight % (e.g. "VGT 18.53%") — sorted by highest fund weight first
- Current price + ▲/▼ daily change % (fetched async via `POST /api/prices`)
- MA20 and MA50 (same async fetch, requires `period="3mo"` to have enough data)
- Company name
- **% change filter row**: preset buttons (All, Gainers, >5%/>10%/>15%, Losers, >5%/>10%/>15%) plus custom Min/Max inputs
- **Stocks with no price data are hidden** once prices finish loading
- **⏰ After-hours alerts checkbox**: fetches `POST /api/afterhours` for all visible tickers; overlays a green/red pill badge (e.g. `▲ 7.3% AH`) on cards with ≥5% extended-hours movement; auto-filters grid to movers-only on enable; "Show all" button toggles back
- **📰 News button** on each card opens the News modal
- **📊 Intel button** on each card opens the Market Intel modal
- **+ Portfolio button** adds the stock to the Research Stocks group (localStorage-backed)

**After-hours filter logic**: When `_ahFilterOnly` is true, `applyPctFilter` is bypassed entirely — AH movers are shown regardless of whether regular-hours price data loaded. When `_ahFilterOnly` is false, the normal `applyPctFilter → applyAhFilter` pipeline runs.

**AH state helpers** (JS):
- `_resetAh()` — clears `_ahData`, `_ahFilterOnly`, status label, and filter button in one call
- `_setAhBtn(show, active)` — updates the "Show all / Show only movers" button state

### Research Stocks group
Stocks are saved to `localStorage` under key `screener_research_stocks`. They appear in the standalone **Research Stocks panel** — all research stocks are **always included** in every scan, passed to `/api/run` as `research_stocks`, and tagged "Research" in the results ETF badge column. They bypass the ETF holdings lookup.
- Each stock shows as a clickable blue ticker link (opens News+Price popup), a 📊 Intel button, and a ✕ remove button
- No checkboxes — `startScan()` uses `getResearch()` directly
- **Seeded defaults** (added once on first load): `DIA`, `^DJI`, `^IXIC`
- `RESEARCH_NAMES` map provides display names: `{ DIA: 'Dow Jones Industrial Average', '^DJI': '...Index', '^IXIC': 'NASDAQ Composite' }` — currently not shown in UI (ticker symbol shown instead), but available for future use
- `getResearch()` caches result in `_researchCache` (module-level); `saveResearch()` updates cache + localStorage together

### News + Price popup (modal)
Triggered by: clicking a ticker in Top Results, clicking a research stock ticker, or the 📰 News button on browse cards.
- Fetches `/api/news/<ticker>` and `POST /api/prices` **in parallel**
- **Price bar** at top (if price available): current price, ▲/▼ % change + dollar change (color-coded), MA20, MA50
- Article list below with publisher + date
- Footer: 5 external links — Yahoo Finance, Stock Analysis, Seeking Alpha, MarketWatch, CNBC
- Handles both old and new yfinance news response formats

### Market Intel modal (`📊 Intel`)
Triggered by: 📊 button in Top Results, Research Stocks, or browse cards. Fetches `GET /api/extended/<ticker>` and `POST /api/prices` **in parallel**.
- **Modal header**: title (`📊 TICKER · Company Name`) + **+ Portfolio button** (`#intel-add-btn`) side by side. Button shows `✓ Added` if ticker is already in Research Stocks; toggles via `toggleResearch(_intelTicker, this)`. Visible as soon as modal opens (state set synchronously before fetch).
- **Modal title**: `📊 TICKER · Company Name` — company name from `data.name` (`info["longName"]` or `info["shortName"]`); falls back to `📊 TICKER — Market Intel`. Set initially on open, updated after data loads.
- **Price bar** at top: current price, ▲/▼ % change + dollar change, MA20, MA50 (same bar as News+Price popup, rendered via shared `_renderPriceBar(barEl, d)` helper)
- **Extended Hours**: after-hours and pre-market price + ▲/▼ % change
- **Volume**: today's volume, 3-month average, ratio vs average (color-coded ≥2× = warn)
- **Earnings**: next date, EPS estimate, revenue estimate (from `tkr.calendar`)
- **Analyst Consensus**: recommendation, price target (mean/low/high), upside % (from `tkr.info`)
- **Recent Analyst Actions**: last 5 upgrades/downgrades from `tkr.upgrades_downgrades`
- `tkr.info` is fetched **once** at the top of `api_extended` and reused for name, extended-hours prices, and analyst data
- `api_extended` response includes `name` field: `info.get("longName") or info.get("shortName") or ""`

### Top Results table
Each ticker in the results table has three inline controls in the ticker cell: clickable ticker link (opens News+Price popup), 📊 Intel button (opens Market Intel modal), and **+ Portfolio button** (adds to Research Stocks; toggles to ✓ Added). No separate column — all three sit in the same `ticker-cell` div. Styled with `.ticker-sym:hover { text-decoration: underline }`.

### ETF Holdings Report modal
Opened via "View ETF Holdings" button. Shows each ETF row with:
- ETF ticker (links to `finance.yahoo.com/quote/<TICKER>` — opens in new tab)
- Full name, YTD %, daily %, current price, holdings count
- All constituent tickers as Yahoo Finance hyperlinks (hover turns blue)

### Performance optimisations (applied 2026-05-08)
- **File handles**: `load_holdings_cache` and `save_holdings_cache` use `with open(...)` — no unclosed handles
- **`api_afterhours` parallel downloads**: two `_yf_download` calls per batch (5m extended + 1d regular) now run concurrently via `ThreadPoolExecutor(max_workers=2)`
- **`_fetch_stockanalysis` regex**: runs on `resp.text` directly — no intermediate `json.dumps(resp.json())`
- **Browse search debounced**: `oninput` calls `_filterBrowseDebounced` (180ms) instead of `filterBrowse` directly — avoids thrashing `innerHTML` on every keystroke
- **`/api/holdings/data` cached**: `_holdingsCache` module-level variable; populated on first browse modal open, cleared on `fresh_fetch` so re-fetched holdings are picked up

### Shared JS helpers
```js
const _chg = ch => ({ arrow, color })        // ▲/▼ arrow + green/red color for a price change
const _ir  = (label, val, vs='') => ...      // renders one intel-row div (label + value)
const closeModal = id => ...                 // hides any modal by element ID
function _renderPriceBar(barEl, d)           // renders price bar HTML into barEl (shared by News + Intel modals)
function _resetAh()                          // clears all AH state (_ahData, _ahFilterOnly, UI)
function _setAhBtn(show, active)             // updates "Show all / Show only movers" button
```

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

## GitHub Backup
- Repo: `https://github.com/asliuj/stock-screener` (private)
- **Hourly auto-backup**: `backup.ps1` runs via Windows Task Scheduler task `StockScreenerGitBackup` every hour at :13 — commits and pushes any modified tracked files, logs to `backup.log`
- `holdings_cache.json` and `backup.log` are in `.gitignore` (not committed)
- To manage the task: `Get-ScheduledTask -TaskName StockScreenerGitBackup` / `Unregister-ScheduledTask -TaskName StockScreenerGitBackup`
