# 台股三角收斂自動篩選器

> **策略核心：反轉邏輯** — 用型態收斂密度預測族群輪動，而非用輪動過濾型態

## 目錄

- [專案簡介](#專案簡介)
- [策略邏輯](#策略邏輯)
- [專案結構](#專案結構)
- [安裝與環境設定](#安裝與環境設定)
- [快速開始](#快速開始)
- [模組說明](#模組說明)
- [設定檔](#設定檔)
- [回測框架](#回測框架)
- [單元測試](#單元測試)
- [輸出格式](#輸出格式)

---

## 專案簡介

本專案是針對**台灣股市 (TWSE 上市 + TPEx 上櫃)** 設計的全自動量化篩選系統，透過每日批次掃描全台約 1,600+ 支個股，識別處於「三角收斂 (Triangle Convergence)」型態的標的，並結合族群輪動分析、供應鏈共振偵測與多維度量化評分，篩選出最佳進場時機。

**主要特色：**

- 全市場掃描，每日自動執行，無須人工選股
- 反轉邏輯：以收斂密度預測即將輪動的族群，提前佈局
- 多維度評分系統，綜合大盤、族群、型態、供應鏈四個面向
- 完整的 A/B/C 機會分級，協助優先排序
- 支援 SQLite 歷史回測與參數優化

---

## 策略邏輯

### 傳統邏輯 vs 反轉邏輯

| | 傳統方法 | 本系統 (反轉邏輯) |
|--|---------|----------------|
| **步驟 1** | 用族群輪動篩選強勢族群 | 全市場掃描三角收斂型態 |
| **步驟 2** | 在強勢族群內找型態 | 統計各族群的收斂密度 |
| **步驟 3** | 等待突破 | 密度高的族群 → **預測即將輪動** |
| **優點** | 順勢操作 | 提前佈局，避免追高 |
| **缺點** | 進場時往往已漲一段 | 需要耐心等待催化劑 |

### 執行流程 (7 步驟)

```
Step 0: 大盤環境偵測 (市場分數 < 40 停止執行)
   ↓
Step 1: 批次下載 K 線資料 (8 執行緒 + SQLite 快取 + 增量更新)
   ↓
Step 2: 資料清洗 (5日均量 ≥ 500 張，歷史 K 線 ≥ 60 根)
   ↓
Step 3: 全市場三角收斂掃描 (VCP 濾網：趨勢 + 量縮 + 收斂比 < 30%)
   ↓
Step 4: 收斂密度聚合 (按族群/供應鏈統計密度，密度 ≥ 15% = 高密度)
   ↓
Step 5: A/B/C 機會分級 + Leader-Laggard 偵測
   ↓
Step 6: 綜合量化評分 (大盤 15% + 密度 25% + 型態 30% + 供應鏈 20% + 突破 10%)
   ↓
Step 7: 輸出 CSV 報表 + SQLite 歷史記錄
```

### 三角收斂 (VCP) 篩選條件

1. **趨勢濾網**：收盤價 > SMA50 > SMA200（大趨勢偏多）
2. **量縮濾網**：近 5 日均量 < 近 50 日均量 × 75%（籌碼沉澱）
3. **斜率條件**：壓力線微下傾、支撐線微上傾（對稱或上升三角）
4. **收斂比 ≤ 0.3**：末端振幅 / 初始振幅 < 30%（高度壓縮）
5. **橫跨 ≥ 20 根 K 線**（型態需有足夠醞釀期）
6. **真突破確認**：突破壓力線 + 當日成交量 > 5 日均量 × 1.5 倍

### 機會分級

| 等級 | 條件 | 意義 |
|-----|------|------|
| **A 級** | 族群密度 ≥ 15% **且** 族群/供應鏈已有領頭股突破 | 最佳進場時機 |
| **B 級** | 族群密度 ≥ 15% **但** 尚未出現突破確認 | 持續監控，等待確認 |
| **C 級** | 族群密度 < 15%（單獨收斂）| 風險較高，謹慎操作 |

---

## 專案結構

```
Stock/
├── main.py                      # 主程式入口，7 步驟掃描流程
├── config.yaml                  # 全域設定檔（所有可調參數）
├── requirements.txt             # Python 依賴套件
├── pyrightconfig.json           # Pyright/Pylance 型別檢查設定
│
├── core/                        # 核心策略模組
│   ├── recognizer.py            # 三角收斂型態識別器 (VCP)
│   ├── market_regime.py         # 大盤環境偵測 (^TWII + ^TWOII)
│   ├── sector_rotation.py       # 族群輪動分析引擎
│   ├── convergence_scanner.py   # 收斂密度聚合 + A/B/C 分級
│   ├── scorer.py                # 綜合評分引擎
│   └── supply_chain.py          # 供應鏈關係模組
│
├── data/                        # 資料存取層
│   ├── fetcher.py               # K 線下載器 (yfinance + 增量更新)
│   ├── cleaner.py               # 資料清洗過濾器
│   ├── database.py              # SQLite 操作層 (WAL + Context Manager)
│   ├── stock_list.py            # 台股代號清單 (TWSE + TPEx)
│   └── supply_chain_data.json   # 供應鏈資料庫 (40 條產業鏈)
│
├── backtest/                    # 回測框架
│   ├── engine.py                # 逐日模擬回測引擎
│   ├── metrics.py               # 績效指標計算
│   ├── optimizer.py             # Walk-Forward 參數優化 (Optuna)
│   └── report.py                # 回測報告產生器
│
├── utils/                       # 工具模組
│   ├── logger.py                # 統一 logging 設定
│   └── visualizer.py            # K 線圖表產生器 (mplfinance)
│
├── scripts/                     # 輔助腳本
│   ├── update_supply_chain.py   # 更新供應鏈資料
│   ├── scrape_tpex_chain.py     # 爬取 TPEx 供應鏈資訊
│   └── explore_tpex.py          # 上櫃股票探索工具
│
├── tests/                       # 單元測試
│   ├── test_cleaner.py          # DataCleaner 測試 (8 cases)
│   ├── test_database.py         # SQLite 讀寫測試 (7 cases)
│   ├── test_scorer.py           # 評分引擎測試 (8 cases)
│   ├── test_recognizer.py       # 型態識別器測試 (8 cases)
│   └── test_market_regime.py    # 大盤偵測器測試 (6 cases)
│
└── output/                      # 輸出目錄（自動建立）
    ├── scanner_YYYYMMDD.csv     # 每日掃描報表
    └── *.png                    # K 線圖表
```

---

## 安裝與環境設定

### 系統需求

- Python 3.11+
- Windows / macOS / Linux

### 安裝步驟

```bash
# 1. Clone 專案
git clone https://github.com/possible1397/stockfilter.git
cd stockfilter

# 2. 建立虛擬環境
python -m venv venv

# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

# 3. 安裝依賴
pip install -r requirements.txt
```

### 依賴套件

| 套件 | 版本 | 用途 |
|------|------|------|
| pandas | 3.0.1 | 資料處理 |
| numpy | 2.4.3 | 數值計算 |
| scipy | 1.17.1 | 極值偵測 (argrelextrema) |
| yfinance | 1.2.0 | K 線資料下載 |
| mplfinance | 0.12.10b0 | K 線圖表繪製 |
| requests | 2.32.5 | API 呼叫 |
| certifi | 2026.2.25 | SSL 憑證驗證 |
| optuna | 4.8.0 | 參數優化 |
| PyYAML | 6.0.3 | 設定檔讀取 |
| pytest | 9.0.2 | 單元測試 |

---

## 快速開始

```bash
# 樣本模式 (5 檔測試，開發時使用)
python main.py --sample

# 全台股掃描模式 (~1,600 檔，約 10-20 分鐘)
python main.py
```

執行後會：
1. 在 `output/` 目錄產生 `scanner_YYYYMMDD.csv` 報表
2. 將掃描結果寫入 `stock_data.db` 的 `scan_results` 表
3. 為每個收斂標的產生 K 線圖表 (.png)
4. 在 `logs/scanner.log` 記錄完整執行 log

---

## 模組說明

### `core/recognizer.py` — 型態識別器

識別三角收斂 (VCP, Volatility Contraction Pattern) 的核心模組。

**效能優化**：只對最近 `min_bars × 2` 根 K 線執行 `argrelextrema`，避免全歷史計算。

```python
from core.recognizer import PatternRecognizer

recognizer = PatternRecognizer(
    order=5,                    # 極值偵測範圍
    min_points=3,               # 最少高低點數量
    min_bars=20,                # 型態最短 K 線數
    convergence_threshold=0.3,  # 收斂比門檻
)
result = recognizer.analyze("2330.TW", df)
print(result.is_converging, result.convergence_ratio, result.pattern_score)
```

**TriangleResult 欄位：**

| 欄位 | 說明 |
|------|------|
| `is_converging` | 是否符合收斂條件 |
| `convergence_ratio` | 收斂比 (越小越壓縮) |
| `bar_span` | 型態橫跨 K 線數 |
| `resistance_price` | 壓力線目前價格 |
| `support_price` | 支撐線目前價格 |
| `is_breakout` | 是否已帶量突破 |
| `pattern_score` | 型態品質分 (0–100) |
| `breakout_score` | 突破強度分 (0–100) |

---

### `core/market_regime.py` — 大盤環境偵測

抓取加權指數 (^TWII) 與櫃買指數 (^TWOII)，計算市場環境分數 (0–100)。

**TTL 快取**：同一小時內多次呼叫不重複下載，節省 API 請求。

**評分維度：**

| 維度 | 滿分 | 說明 |
|------|------|------|
| MA 位置分 | 40 | 收盤距 MA20/MA60 的距離 |
| MA20 斜率分 | 30 | MA20 近 5 日是否上揚 |
| 短期動能分 | 30 | 5 日報酬率 |

```python
from core.market_regime import MarketRegimeDetector

detector = MarketRegimeDetector()
score = detector.get_market_score()  # 0–100，加權: TSE 60% + OTC 40%
```

---

### `core/sector_rotation.py` — 族群輪動分析

從 TWSE OpenAPI 取得上市股票產業分類，並計算各族群三維度動能。

**三維度評分：**

| 維度 | 權重 | 說明 |
|------|------|------|
| 動能 (Momentum) | 40% | 5 日平均漲幅 |
| 廣度 (Breadth) | 30% | 族群內上漲股數比例 |
| 加速度 (Acceleration) | 30% | 近 5 日成交值 vs 20 日均值比率 |

---

### `core/convergence_scanner.py` — 密度聚合與分級

**核心演算法：**

```
收斂密度 = 族群內收斂股數 / 族群總股數

高密度門檻 = 15% (同時需 ≥ 2 檔收斂)
```

---

### `core/supply_chain.py` — 供應鏈模組

載入 `data/supply_chain_data.json`（40 條產業鏈、上/中/下游分類），偵測供應鏈共振效應。

**Leader-Laggard 偵測**：若同供應鏈上游已有股票 5 日漲幅 > 3%，下游收斂標的升為 A 級。

---

### `core/scorer.py` — 綜合評分引擎

**評分權重：**

| 維度 | 權重 |
|------|------|
| 大盤環境 (market) | 15% |
| 收斂密度 (density) | 25% |
| 型態品質 (pattern) | 30% |
| 突破強度 (breakout) | 10% |
| 供應鏈動能 (chain) | 20% |

機會等級加分：A +5、B +2、C +0

---

### `data/fetcher.py` — 增量下載器

**智慧快取策略：**

```
DB 有今日資料     → 直接使用快取
非交易日且近 3 天 → 使用快取
有舊快取          → 只下載缺少的日期（增量更新）
無快取            → 下載完整 1 年
```

---

### `data/database.py` — SQLite 資料層

使用 **Context Manager** + **WAL 模式** 確保多執行緒安全與效能。

**資料表：**

| 表名 | 說明 |
|------|------|
| `daily_prices` | 個股日 K 線資料 |
| `scan_results` | 每日掃描結果歷史 (可查詢) |

```python
# 查詢歷史掃描結果
import sqlite3
conn = sqlite3.connect("stock_data.db")
df = pd.read_sql("SELECT * FROM scan_results WHERE scan_date='2026-03-21' ORDER BY total_score DESC", conn)
```

---

## 設定檔

所有參數集中在 `config.yaml`，修改後立即生效，無需改動程式碼。

```yaml
scanner:
  force_safe_trade: true        # 大盤分數低於門檻時停止執行
  market_score_threshold: 40    # 大盤安全門檻 (0-100)
  download_workers: 8           # 下載執行緒數
  db_path: stock_data.db

pattern:
  convergence_threshold: 0.3    # 收斂比門檻
  trend_filter: true            # 趨勢濾網 (收盤 > SMA50 > SMA200)
  volume_dryup: true            # 量縮濾網

cleaner:
  min_vol_lots: 500             # 5 日均量門檻 (張)
  min_bars: 60                  # 最少 K 線筆數

logging:
  level: INFO                   # DEBUG / INFO / WARNING / ERROR
  log_dir: logs
```

---

## 回測框架

```bash
# 執行回測 (需先有 SQLite 資料)
python -c "
from backtest.engine import BacktestEngine, BacktestConfig
from backtest.report import generate_report

config = BacktestConfig(
    start_date='2025-01-01',
    end_date='2026-03-20',
    score_threshold=40.0,
    stop_loss_pct=-5.0,
    take_profit_pct=15.0,
)
engine = BacktestEngine(config)
trades, metrics = engine.run()
generate_report(trades, metrics, config)
"
```

**績效指標：** 勝率、盈虧比、期望值、最大回撤、Sharpe Ratio、最大連敗

**Walk-Forward 優化** (`backtest/optimizer.py`)：使用 Optuna TPE 演算法，滾動視窗 (訓練 6 個月 / 驗證 2 個月) 進行參數搜索，避免過度擬合。

---

## 單元測試

```bash
# 執行全部測試
pytest tests/ -v

# 執行特定模組
pytest tests/test_scorer.py -v
```

**測試覆蓋：**

| 測試檔案 | Cases | 覆蓋範圍 |
|---------|-------|---------|
| `test_cleaner.py` | 8 | 資料過濾邏輯 |
| `test_database.py` | 7 | SQLite 讀寫、Upsert |
| `test_scorer.py` | 8 | 評分計算、等級加分、排序 |
| `test_recognizer.py` | 8 | 型態識別、邊界條件 |
| `test_market_regime.py` | 6 | 評分維度、TTL 快取、mock |
| **合計** | **37** | **全部通過 ✅** |

---

## 輸出格式

### CSV 報表欄位

| 欄位 | 說明 |
|------|------|
| `grade` | 機會等級 (A/B/C) |
| `ticker` | 股票代號 |
| `sector` | 產業別 |
| `main_product` | 主要產品描述 |
| `chain_position` | 供應鏈位置 (如 半導體/中游) |
| `status` | Breakout / Converging |
| `total_score` | 綜合評分 (0–100) |
| `convergence_ratio` | 收斂比 |
| `resistance_price` | 壓力線價格 |
| `support_price` | 支撐線價格 |
| `converging_peers` | 同族群收斂標的 |

### Log 輸出

執行 log 同時輸出至：
- **Console**：即時顯示
- **`logs/scanner.log`**：Rotating file (最大 10MB，保留 5 份)

---

## 注意事項

- 本系統僅供研究與學習用途，不構成任何投資建議
- 台股有 T+2 交割制度，回測參數已納入手續費 (0.1425%) + 交易稅 (0.3%) + 滑價 (0.1%)
- 供應鏈資料 (`data/supply_chain_data.json`) 需定期更新，可執行 `scripts/update_supply_chain.py`

---

*由 Python 量化分析工具自動生成報表，搭配人工判斷使用*
