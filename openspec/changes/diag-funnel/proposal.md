# 診斷模式：篩選漏斗 + 每日診斷 CSV

## Context
掃描結果為 0 時完全不知道原因。
目標：
1. 終端機顯示篩選漏斗（各條件淘汰幾檔）
2. 每次掃描都產出 `output/diag_YYYYMMDD.csv`，包含所有通過 DataCleaner 的標的，記錄每檔在哪個關卡被淘汰及關鍵指標數值

---

## 修改 1：`core/recognizer.py` — TriangleResult 加診斷欄位

```python
@dataclass
class TriangleResult:
    # 現有欄位不動...

    # 新增：診斷欄位
    reason: str = ""          # 人類可讀原因
    failure_stage: str = ""   # TREND / VOLUME / PIVOTS / SLOPE / RATIO / SPAN / BREAKOUT / ""(通過)

    # 中間指標（供 CSV 使用，不影響現有邏輯）
    diag_close: float = 0.0
    diag_sma50: float = 0.0
    diag_sma200: float = 0.0
    diag_vol_ratio: float = 0.0   # 5日均量 / 50日均量
    diag_peak_count: int = 0
    diag_valley_count: int = 0
```

在 `analyze()` 中，**每個 early return 之前**設定 reason + failure_stage，並在適當時機填入 diag_* 欄位：

| 關卡 | failure_stage | reason 範例 | 填入的 diag 欄位 |
|------|--------------|------------|----------------|
| 趨勢 (行97) | `"TREND"` | `f"close {c:.2f} ≤ SMA50 {s50:.2f}"` | close, sma50, sma200 |
| 量縮 (行104) | `"VOLUME"` | `f"vol_ratio {ratio:.2f} > 0.75"` | close, sma50, sma200, vol_ratio |
| pivot (行112) | `"PIVOTS"` | `f"峰{pk}谷{vl}，需各≥3"` | + peak_count, valley_count |
| 斜率 (行125) | `"SLOPE"` | `f"slope_dn {sd:.5f} slope_up {su:.5f}"` | + peak_count, valley_count |
| 比值 (行148) | `"RATIO"` | `f"ratio {r:.3f} ≥ 0.3"` | + convergence_ratio |
| 週期 (行152) | `"SPAN"` | `f"span {span}根 < 20"` | + bar_span |
| 突破量 (行162) | `"BREAKOUT"` | `f"量不足 {vt:.0f} < {v5:.0f}×1.5"` | 全部 |

計算每個指標**在各自關卡確認前就填入 result**，這樣即使 early return 也有數據。

---

## 修改 2：`main.py` — 收集全部結果 + 漏斗 log + 診斷 CSV

### 2a. Step 3 改為收集全部結果
```python
all_scan_results = []   # 新增：收集所有結果
converging_results = []
for ticker, df in valid_data.items():
    result = recognizer.analyze(ticker, df)
    all_scan_results.append(result)
    if result.is_converging:
        converging_results.append(result)
```

### 2b. Step 3 結束後呼叫漏斗報告
```python
_print_funnel_report(all_scan_results)
```

### 2c. Step 7 結束後輸出診斷 CSV
```python
DIAG_CSV = os.path.join(OUTPUT_DIR, f"diag_{datetime.today().strftime('%Y%m%d')}.csv")
_save_diag_csv(all_scan_results, supply_eng, DIAG_CSV)
```

### 2d. CSV 欄位與排序

檔名：`output/diag_YYYYMMDD.csv`

欄位：
```
failure_stage, ticker, name, reason,
current_close, sma50, sma200,
vol_ratio, peak_count, valley_count,
convergence_ratio, bar_span
```

排序邏輯（從最接近通過到最早被淘汰）：
```
"" (通過) → BREAKOUT → SPAN → RATIO → SLOPE → PIVOTS → VOLUME → TREND
```

### 2e. 新增兩個 helper 函式（加在 `run_scanner` 外）

```python
STAGE_LABEL = {
    "TREND":    "趨勢濾網",
    "VOLUME":   "量縮濾網VCP",
    "PIVOTS":   "樞紐點不足",
    "SLOPE":    "斜率條件",
    "RATIO":    "收斂比>0.3",
    "SPAN":     "週期<20根",
    "BREAKOUT": "突破量不足",
    "":         "通過",
}
STAGE_SORT = {"": 0, "BREAKOUT": 1, "SPAN": 2, "RATIO": 3,
              "SLOPE": 4, "PIVOTS": 5, "VOLUME": 6, "TREND": 7}

def _print_funnel_report(all_results):
    from collections import Counter
    counts = Counter(r.failure_stage for r in all_results)
    passed = counts.get("", 0)
    logger.info("─" * 55)
    logger.info("  [診斷] 篩選漏斗報告")
    logger.info("─" * 55)
    for stage in ["TREND","VOLUME","PIVOTS","SLOPE","RATIO","SPAN","BREAKOUT"]:
        n = counts.get(stage, 0)
        if n:
            logger.info(f"  ✗ {STAGE_LABEL[stage]:<20} 淘汰 {n:4d} 檔")
    logger.info(f"  ✓ 通過全部條件          {passed:4d} 檔")
    logger.info("─" * 55)
    # 當無標的通過時，顯示最接近通過的前 10 檔
    if passed == 0:
        late = [r for r in all_results
                if r.failure_stage in ("RATIO", "SPAN", "BREAKOUT")]
        late.sort(key=lambda r: r.convergence_ratio if r.convergence_ratio < 1 else 99)
        if late:
            logger.info("  [接近通過] 最末關才被淘汰的前 10 檔：")
            for r in late[:10]:
                logger.info(f"    {r.ticker:10} | {STAGE_LABEL[r.failure_stage]:12} | {r.reason}")

def _save_diag_csv(all_results, supply_eng, path):
    import csv, os
    from data.stock_list import get_stock_name
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rows = sorted(all_results, key=lambda r: STAGE_SORT.get(r.failure_stage, 9))
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        fieldnames = [
            'failure_stage', 'ticker', 'name', 'reason',
            'current_close', 'sma50', 'sma200',
            'vol_ratio', 'peak_count', 'valley_count',
            'convergence_ratio', 'bar_span'
        ]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({
                'failure_stage':     STAGE_LABEL.get(r.failure_stage, r.failure_stage),
                'ticker':            r.ticker,
                'name':              get_stock_name(r.ticker),
                'reason':            r.reason,
                'current_close':     r.diag_close,
                'sma50':             round(r.diag_sma50, 2),
                'sma200':            round(r.diag_sma200, 2),
                'vol_ratio':         round(r.diag_vol_ratio, 3),
                'peak_count':        r.diag_peak_count,
                'valley_count':      r.diag_valley_count,
                'convergence_ratio': round(r.convergence_ratio, 4) if r.convergence_ratio < 1 else '',
                'bar_span':          r.bar_span or '',
            })
    logger.info(f"  診斷 CSV 已輸出至: {path}")
```

---

## 關鍵檔案

| 檔案 | 修改內容 |
|------|---------|
| `core/recognizer.py` | TriangleResult 加 7 個診斷欄位；analyze() 各 return 前設定 reason / failure_stage |
| `main.py` | Step 3 收集全部結果；漏斗報告函式；diag CSV 輸出 |

---

## 驗證

```bash
# 跑樣本模式確認產出
python main.py --sample

# 確認診斷 CSV 存在且欄位正確
python -c "
import csv, datetime
path = f'output/diag_{datetime.date.today().strftime(\"%Y%m%d\")}.csv'
with open(path, encoding='utf-8-sig') as f:
    rows = list(csv.DictReader(f))
print(f'總行數: {len(rows)}')
from collections import Counter
print(Counter(r['failure_stage'] for r in rows))
"

# 確認各 failure_stage 欄位正確設定
python -c "
import sys; sys.path.insert(0, '.')
import pandas as pd, numpy as np
from core.recognizer import PatternRecognizer
rec = PatternRecognizer()
idx = pd.date_range('2025-01-01', periods=250)
df = pd.DataFrame({
    'Close': np.linspace(20, 18, 250), 'High': np.linspace(21, 19, 250),
    'Low': np.linspace(19, 17, 250), 'Volume': [1000]*250
}, index=idx)
r = rec.analyze('TEST', df)
print('failure_stage:', r.failure_stage)   # 應為 TREND
print('reason:', r.reason)
print('diag_close:', r.diag_close)
"
```
