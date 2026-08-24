"""
scripts/check_freeze.py — 凍結規格一致性檢查

docs/策略規格_凍結版_2026-07-11.md 規定向前驗證期間 (2026-04-01 ~ 2026-10-01)
不得修改任何策略參數。此腳本把該規格編碼成常數，比對 config.yaml 是否漂移。

背景:2026-08-05 config.yaml 的 partial_profit_threshold 曾被改回 0.15，
導致回測、向前驗證與紙上交易全部套用了五年驗證已否決的分批停利規則
(2026-08-05 的回測報告出現 53 筆 partial_profit 出場)。此檢查就是為了防止再發生。

用法:
    python scripts/check_freeze.py          # 不符時 exit code 1
forward_validate.py 於啟動時會自動呼叫。
"""
import os
import sys

import yaml

FREEZE_DOC = "docs/策略規格_凍結版_2026-07-11.md"
FREEZE_START = "2026-04-01"
FREEZE_REVIEW = "2026-10-01"   # 6 個月向前驗證期滿，屆時才可重新調參

# (yaml 區段, 參數, 應有值) — 來源:凍結版規格
FROZEN = [
    ("backtest", "score_threshold", 0),          # 綜合評分經五年驗證無預測力
    ("backtest", "require_breakout", True),      # 唯一有 alpha 的訊號類型
    ("backtest", "stop_loss", 0.10),
    ("backtest", "atr_stop_mult", 4.0),
    ("backtest", "atr_activate_pct", 15.0),
    ("backtest", "max_hold_days", 60),
    ("backtest", "take_profit", 0),
    ("backtest", "partial_profit_threshold", 0), # 0 = 停用分批停利
    ("backtest", "k_line_trail", True),
    ("backtest", "max_positions", 5),
    ("backtest", "position_size", 0.2),
    ("scanner", "market_score_threshold", 40),
    ("cleaner", "min_vol_lots", 500),
    ("cleaner", "min_bars", 60),
    ("pattern", "convergence_threshold", 0.3),
    ("pattern", "min_bars", 20),
    ("pattern", "min_points", 3),
    ("pattern", "max_slope_pct", 0.1),
    ("pattern", "trend_filter", True),
    ("pattern", "volume_dryup", True),
]


def check(config_path: str = "config.yaml") -> list:
    """回傳漂移清單 [(區段.參數, 實際值, 應有值)]，空 list 表示一致。"""
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    drift = []
    for section, key, expected in FROZEN:
        actual = cfg.get(section, {}).get(key)
        if isinstance(expected, bool):
            same = bool(actual) == expected
        elif isinstance(expected, (int, float)):
            same = actual is not None and abs(float(actual) - float(expected)) < 1e-9
        else:
            same = actual == expected
        if not same:
            drift.append((f"{section}.{key}", actual, expected))
    return drift


def assert_frozen(config_path: str = "config.yaml", hard: bool = True) -> list:
    """給其他腳本呼叫:不一致時印出警告，hard=True 則直接中止。"""
    drift = check(config_path)
    if not drift:
        return drift

    print("=" * 70)
    print(f"[凍結規格檢查] config.yaml 與 {FREEZE_DOC} 不一致:")
    for name, actual, expected in drift:
        print(f"  - {name}: 目前 {actual!r}，凍結規格為 {expected!r}")
    print()
    print(f"  向前驗證期 {FREEZE_START} ~ {FREEZE_REVIEW} 內不得調參。")
    print("  若為刻意變更,請同步更新凍結規格文件與本檔 FROZEN 常數,")
    print("  並將向前驗證計時歸零 (重新起算 6 個月)。")
    print("=" * 70)
    if hard:
        sys.exit(1)
    return drift


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    drift = check()
    if drift:
        assert_frozen()
    print(f"[凍結規格檢查] OK — config.yaml 與 {FREEZE_DOC} 一致 ({len(FROZEN)} 項參數)。")
