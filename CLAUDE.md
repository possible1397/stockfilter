# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## ⚠ 先讀這一段:現行策略的真相來源

**`docs/策略規格_凍結版_2026-07-11.md` 是唯一有效的策略規格。** 本檔以下的架構說明
描述的是「程式怎麼運作」,不是「該用哪些規則交易」。兩者衝突時以凍結規格為準。

五年資料重驗 (`docs/參數優化報告_2026-07-09.md` 後半) 的關鍵結論:

- **綜合評分系統沒有預測力** —— score ≥ 45 的訊號 20 日超額 +0.01%,反而低於全部訊號的
  +0.48%。sector / density / chip 這套加權上層建築在可交易的意義上是無效的。
  評分仍然每天照算並輸出,但**只作為診斷資訊,不是進場條件** (`score_threshold: 0`)。
- **唯一的 alpha 來源是 Breakout 訊號**,60 日超額 +2.92% (p=0.0006),而且需要寬出場。
- **分批停利是負優勢結構** —— 右偏分布下截斷長尾贏家,倉位加權後每筆 -0.97%。已停用。

**已否決,不要重新提議:** 綜合評分門檻、密度 A/B/C 分級、RS 相對強度濾網、分批停利、
兩段式 K 線出場、族群動能濾網。這些都經過訓練/保留期驗證後被否決,細節見凍結規格文件。

修改 `config.yaml` 的策略參數前,先跑 `python scripts/check_freeze.py` —— 它把凍結規格的
20 項參數編碼成常數,不一致會直接報錯。向前驗證期 (2026-04-01 ~ 2026-10-01) 內不得調參。

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

# ── 每日例行 (等同雙擊 run_update.bat) ──
python main.py                          # 更新資料 (一定執行) + 掃描 (受大盤濾網控制)
python main.py --data-only              # 只更新資料，不掃描
python scripts/forward_validate.py      # 補算缺漏訊號日 + 重放凍結規格 -> output/forward_validation.md

# ── 維運 ──
python scripts/check_freeze.py          # config.yaml 是否偏離凍結規格
python scripts/db_health.py             # 資料健檢:覆蓋率、缺漏日、欄位空值率
python scripts/db_health.py --detail --csv
python scripts/backup_db.py --dest D:\stockfilter_backup   # SQLite 線上備份
python scripts/migrate_scan_results.py  # 一次性:加唯一性約束與參數戳記
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
Step 6: Composite scoring: Market 10% + Sector 20% + Density 20% + Pattern 15% + Breakout 10% + Chain 5% + Chip 20% (core/scorer.py 是計算的唯一實作 —— 但**評分只是診斷資訊，不是進場條件**，見開頭的真相來源段)
Step 7: Output → output/scanner_YYYYMMDD.csv + SQLite scan_results + PNG charts
```

### Key Design Decisions

**SQLite uses `INSERT OR IGNORE` by default, plus adjustment detection** (`data/fetcher.py:_incremental_update()`). yfinance retroactively adjusts prices after each dividend/split. Incremental fetches re-download a 14-day overlap window and compare closes against the cache; if they diverge > 0.5%, the full history is re-fetched with `save_dataframe(replace=True)`. Without this, pre- and post-adjustment bars mix and create phantom gaps that corrupt pivots/breakouts. `_drop_incomplete_today()` also prevents saving today's incomplete bar before 14:00 Taipei time (INSERT OR IGNORE would otherwise freeze the intraday snapshot forever).

**Market regime scoring has a single implementation** in `utils/regime.py`, shared by `MarketRegimeDetector`, `BacktestEngine`, and `precompute_backtest.py`. Do not re-implement it.

**Recognizer slope thresholds are normalized (%/day)** via `max_slope_pct` (config `pattern.max_slope_pct`), not absolute NT$ per day, so selection is price-scale independent.

**Backtest fidelity rules** (`backtest/engine.py` + `scripts/precompute_backtest.py`):
- Precompute passes the per-day `top_sectors_df` so sector scores and A-grades match the live scanner.
- Liquidity is checked per-signal-date inside the engine (`min_vol_lots`), never by filtering the universe with latest data (look-ahead).
- Metrics weight each trade by `position_size × position_ratio` and use the engine's daily equity curve for MDD/Sharpe.

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
| `backtest/optimizer.py` | Walk-forward optimization via Optuna TPE — **已停用**，五年重驗顯示網格/TPE 搜尋出的參數無法通過保留期 |
| `utils/regime.py` | 市場分數的單一實作，scanner / engine / precompute 共用 |
| `utils/provenance.py` | config_hash + git_commit，寫入 scan_results 的來源戳記 |
| `core/paper_trader.py` | 紙上交易，結果寫入 `paper_trades`（目前 0 筆，未實際驗證過） |
| `scripts/forward_validate.py` | 向前驗證：補算缺漏訊號日 + 以凍結參數重放 |
| `scripts/check_freeze.py` | 凍結規格一致性檢查 |
| `scripts/db_health.py` | 資料健檢 |

### Configuration

All parameters live in `config.yaml`. Current values (= 凍結規格 2026-07-11):
- `market_score_threshold: 40` — safety gate，只擋新單，不擋資料更新
- `convergence_threshold: 0.3` — max compression ratio to qualify
- `cleaner.min_vol_lots: 500` — minimum 5-day avg volume
- `backtest.score_threshold: 0` — 評分經五年驗證無預測力，不作為進場條件
- `backtest.require_breakout: true` — 只進突破確認訊號（唯一有 alpha 的類型）
- `backtest.stop_loss: 0.10`, `take_profit: 0`（停用）, `max_hold_days: 60`
- `backtest.atr_stop_mult: 4.0`, `atr_activate_pct: 15.0` — 浮盈 15% 後才啟動追蹤
- `backtest.partial_profit_threshold: 0` — 分批停利已停用（負優勢結構）

這些值由 `scripts/check_freeze.py` 把關。改動前請確認是刻意變更，並同步更新凍結規格
文件與該腳本的 `FROZEN` 常數，且向前驗證計時歸零。

### Output Files

| File | Description |
|------|-------------|
| `output/scanner_YYYYMMDD.csv` | Daily ranked opportunity list |
| `output/*.png` | Candlestick charts with trendlines for converging stocks |
| `output/forward_validation.md` | 向前驗證報告（持倉、已平倉、累積績效） |
| `output/daily_run.log` | run_update.bat 的執行紀錄 |
| `stock_data.db` | SQLite: `daily_prices` / `chip_data` / `scan_results` / `scan_runs` / `paper_trades` (gitignored) |
| `logs/scanner.log` | Rotating log, 10MB max, 5 backups |

### Entry Conditions (recognizer.py)

A stock qualifies as **converging** when all pass:
1. Trend filter: ≥200 bars → Close > SMA50 > SMA200; 50–199 bars → Close > SMA50 and SMA50 rising; <50 bars → rejected
2. 5d avg volume < 50d avg volume × 75% (volume dry-up / VCP)
3. Linear regression on highs converges downward, lows converges upward (slopes normalized to %/day, `max_slope_pct`)
4. `convergence_ratio` (final_width / initial_width) < 0.3
5. Pattern spans ≥ 20 bars, ≥ 3 peaks and 3 valleys

**Breakout** additionally requires: Close > resistance AND today's volume > 5d avg × 1.5×. A close above resistance on insufficient volume is *downgraded to converging* (not rejected).
