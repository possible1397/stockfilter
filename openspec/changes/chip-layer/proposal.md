# Proposal: Chip Layer Analysis (籌碼面強化)

## Problem
Currently, the stock scanner relies entirely on technical patterns (VCP) and sector rotation. However, in the Taiwan stock market, "institutional accumulation" (三大法人佈局) is a critical leading indicator for successful breakouts. Technical breakouts without institutional support often result in "fake breakouts" (假突破).

## Proposed Solution
Integrate "Chip Layer" data to supplement the technical score. We will:
1. Fetch daily institutional Net Buy/Sell data (Foreign, Investment Trust, Dealer).
2. Fetch weekly Big Player Holdings (400+ shares / 1000+ shares) data.
3. Calculate a "Chip Score" based on recent accumulation trends (e.g., Investment Trust buying for 3+ consecutive days).
4. Integrate the Chip Score (20% weight) into the final ranking.

## Target Impact
- Reduce "Fake Breakout" rate.
- Identify "High Conviction" setups where both VCP and Big Money align.
- Provide a "Chip Grade" (A/B/C) similar to existing sector grades.

## Components to be Added/Modified
- `core/chip_analysis.py`: (NEW) Logic for fetching and scoring chip data.
- `data/fetcher.py`: (MODIFY) Add routines for TWSE/TPEX institutional data.
- `core/scorer.py`: (MODIFY) Integrate Chip Score into the composite ranking.
- `config.yaml`: (MODIFY) Add chip-specific thresholds (e.g., `min_trust_buy_days`).

## Verification Plan
1. **Unit Test**: Test `chip_analysis.py` with mock institutional data.
2. **Integration Test**: Run `main.py --sample` and verify the "Chip Score" appears in the output.
3. **Backtest**: Compare performance with and without the Chip Layer filter.
