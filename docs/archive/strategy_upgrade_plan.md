> ## ⚠ 已封存文件 · 封存於 2026-08-30
>
> **本文件描述的是 2026-07-11 凍結規格之前的想法，其中部分結論已被五年資料重驗否決。**
> 保留它是為了記錄當時的思路，不是作為現行做法。
>
> 現行策略請看 [`docs/策略規格_凍結版_2026-07-11.md`](../策略規格_凍結版_2026-07-11.md)；
> 否決的理由見 [`docs/參數優化報告_2026-07-09.md`](../參數優化報告_2026-07-09.md)。
>
> **具體被否決的部分：** 本文提議的「分批停利」(獲利 15% 減碼一半) 在五年回測中
> 被證實是負優勢結構 —— 右偏報酬分布下截斷長尾贏家，倉位加權後每筆期望值 -0.97%。
> 「動態 ATR」與「K 線追蹤」則被保留，但啟動門檻由浮盈 5% 改為 15%。

---

# 交易系統出場邏輯升級計畫 (動態 ATR + 階梯式減碼)

本計畫旨在升級現有的交易策略，將原有的「靜態 ATR 追蹤」優化為「動態適應性系統」，並新增「階梯式減碼」功能，以更好地處理如 1717 (長興) 這類強勢飆股的行情，在保護獲利的同時減少被盤中雜訊洗出場的機率。

## User Review Required

> [!IMPORTANT]
> **規則變動確認：**
> 1. **分批停利**：當獲利達到指定門檻（預設 15%）時，系統將自動平倉 **50%**。這會改變回測的獲利曲線分布（勝率可能上升，但單筆最大獲利可能下降）。
> 2. **動態 ATR**：止損位將隨每日波動調整，不再是進場時的固定數值。在波動擴大時，止損會自動拉開。

## Proposed Changes

### 1. 配置系統 (Configuration)
修改 `config.yaml` 以支援新參數。

#### [MODIFY] [config.yaml](file:///d:/PycharmProjects/Stock/config.yaml)
- 新增 `backtest.partial_profit_threshold`: 0.15 (15% 減碼)
- 新增 `backtest.partial_profit_ratio`: 0.5 (減碼一半)
- 新增 `backtest.dynamic_atr_update`: true (是否動態更新 ATR)
- 新增 `backtest.k_line_trail`: true (是否啟用前日 K 線動態跟蹤)

---

### 2. 回測引擎 (Backtest Engine)
核心邏輯重構。

#### [MODIFY] [backtest/engine.py](file:///d:/PycharmProjects/Stock/backtest/engine.py)
- **Trade 物件**：增加 `is_partially_closed` 標記與 `partial_exit_date/price` 欄位。
- **`_check_exit` 函數**：
    - 實作「階梯式減碼」：判斷獲利 > 門檻且未減碼時，記錄減碼行為。
    - 實作「動態 ATR 更新」：每根 K 線重新計算最新 ATR。
    - 實作「關鍵 K 線支撐」：若處於強勢狀態，將 `trailing_stop` 上調至 `prev_close` 或 `prev_low`。
- **績效計算**：更新 `_close_position` 與 `calculate_metrics` 以正確處理分批平倉後的損益。

---

### 3. 主掃描程式 (Scanner)
讓每日報表反映最新邏輯。

#### [MODIFY] [main.py](file:///d:/PycharmProjects/Stock/main.py)
- **`_build_action_plan`**：
    - 新增「減碼提示」：若目前持股已達減碼門檻，在 `操作建議` 欄位明確提示「明日開盤減碼」。
    - 更新「動態追蹤價」：顯示最新的動態 ATR 止損價位。

---

### 4. 工具類別 (Core Components)
#### [MODIFY] [core/recognizer.py](file:///d:/PycharmProjects/Stock/core/recognizer.py)
- 確保 `TriangleResult` 能攜帶足夠的 K 線基礎資訊（如 Open/High/Low）供出場邏輯使用。

## Open Questions

> [!WARNING]
> 1. **減碼後的剩餘部位**：當剩餘 1 張（或一半）時，我們是否要繼續使用原有的 3x ATR 追蹤，還是改用更嚴格的 1.5x ATR 以鎖定剩餘利潤？
> 2. **手續費計算**：分兩次賣出會產生兩次交易成本（雖然比例相同，但金額分開），我會確保回測引擎正確反應這一點。

## Verification Plan

### Automated Tests
- 執行 `python main.py --date 2026-04-08 --force` 驗證 1717 的初期操作建議。
- 執行 `python main.py --date 2026-04-12 --force` 驗證 1717 在目前的獲利狀態下，是否正確觸發「減碼建議」。

### Manual Verification
- 檢查 `output/scanner_20260412.csv` 中的 `操作建議` 內容是否包含：「獲利達標，建議明日開盤減碼 50%，剩餘部位以 73.8 元追蹤」。
