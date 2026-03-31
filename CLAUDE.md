# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the full daily scanner (~1,600 stocks, 10-20 min)
python main.py

# Test mode (5 sample stocks, fast)
python main.py --sample

# Run backtest on SQLite cached data
python scripts/run_backtest.py

# Update supply chain data from ic.tpex.org.tw
python scripts/update_supply_chain.py

# Run all unit tests
pytest tests/ -v

# Run a single test file
pytest tests/test_recognizer.py -v
```

## Architecture Overview

**Strategy Core — "Reversal Logic":**
Full market scan → Find all converging patterns → Aggregate by sector → Predict incoming rotations *before* they happen. This is the opposite of traditional sector-rotation filtering.

### Execution Flow (main.py)

```
Step 0: Market Regime (^TWII + ^TWOII score < 40 → abort)
Step 1: Batch download OHLCV (8 threads, SQLite cache via data/fetcher.py)
Step 2: Data cleaning (volume ≥ 500 lots, bars ≥ 60)
Step 3: Full-market VCP scan (core/recognizer.py, scipy argrelextrema)
Step 4: Density aggregation per sector (core/convergence_scanner.py)
Step 5: A/B/C grading + leader-laggard detection
Step 6: Composite scoring: Market 15% + Density 25% + Pattern 30% + Breakout 10% + Chain 20%
Step 7: Output → output/scanner_YYYYMMDD.csv + SQLite scan_results + PNG charts
```

### Key Design Decisions

**SQLite uses `INSERT OR IGNORE`** (not REPLACE) in `data/database.py:save_dataframe()`. yfinance retroactively adjusts prices on each call; overwriting stored K-bars would shift `argrelextrema` pivot points and make the same data produce different signals on different days.

**Supply chain data** (`data/supply_chain_data.json`) has two kinds of entries:
- Manual chains (12 total): have `上游`/`中游`/`下游` keys with curated tickers
- Auto-crawled chains (38 total): have a `全產業` flat list from ic.tpex.org.tw

`scripts/update_supply_chain.py` skips any chain that already has manual `上/中/下游` structure to preserve hand-curated data.

**TTL cache** on `MarketRegimeDetector`: same-hour calls reuse cached index data to avoid redundant downloads.

**Incremental fetch** in `data/fetcher.py`: checks SQLite for the latest stored date per ticker, only downloads the missing tail (not the full year). Falls back to cache if yfinance fails.

### Module Map

| Module | Role |
|--------|------|
| `core/recognizer.py` | Triangle/VCP detection via linear regression on argrelextrema peaks/valleys |
| `core/market_regime.py` | 3-dim market score: MA position (40pt) + MA slope (30pt) + momentum (30pt) |
| `core/sector_rotation.py` | Sector score: momentum 40% + breadth 30% + volume acceleration 30% |
| `core/convergence_scanner.py` | Density aggregation, A/B/C grading, leader-laggard detection |
| `core/scorer.py` | Composite scorer; A/B/C bonus (+5/+2/+0), ranks by grade then score |
| `core/supply_chain.py` | JSON-based chain lookup: ticker → (chain, level), product descriptions |
| `data/fetcher.py` | Threaded incremental downloader with 3-day cache window for non-trading days |
| `data/database.py` | SQLite WAL-mode context manager; tables: `daily_prices`, `scan_results` |
| `backtest/engine.py` | Day-by-day simulation; exits: stop_loss / take_profit / ATR trailing / max_hold |
| `backtest/optimizer.py` | Walk-forward optimization via Optuna TPE (train 6mo, test 2mo) |

### Configuration

All parameters live in `config.yaml`. Key defaults:
- `market_score_threshold: 40` — safety gate
- `convergence_threshold: 0.3` — max compression ratio to qualify
- `cleaner.min_vol_lots: 500` — minimum 5-day avg volume
- `backtest.score_threshold: 60`, `stop_loss: 0.07`, `take_profit: 0.20`

### Output Files

| File | Description |
|------|-------------|
| `output/scanner_YYYYMMDD.csv` | Daily ranked opportunity list |
| `output/*.png` | Candlestick charts with trendlines for converging stocks |
| `stock_data.db` | SQLite: `daily_prices` + `scan_results` (gitignored) |
| `logs/scanner.log` | Rotating log, 10MB max, 5 backups |

### Entry Conditions (recognizer.py)

A stock qualifies as **converging** when all pass:
1. Close > SMA50 > SMA200 (uptrend)
2. 5d avg volume < 50d avg volume × 75% (volume dry-up / VCP)
3. Linear regression on highs converges downward, lows converges upward
4. `convergence_ratio` (final_width / initial_width) < 0.3
5. Pattern spans ≥ 20 bars, ≥ 3 peaks and 3 valleys

**Breakout** additionally requires: Close > resistance AND today's volume > 5d avg × 1.5×.
