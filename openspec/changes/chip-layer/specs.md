# Technical Specification: Chip Layer Analysis

## 1. Data Requirements
### 1.1 Institutional Trading (三大法人)
- **Source**: TWSE/TPEX Daily Trade reports.
- **Fields**: 
  - `foreign_buy`: Foreign Investors net buy/sell (張).
  - `trust_buy`: Investment Trust net buy/sell (張).
  - `dealer_buy`: Dealers net buy/sell (張).
- **Frequency**: Daily (updated ~15:00-16:00 TST).

### 1.2 Big Player Holdings (大戶持股)
- **Source**: TDCC weekly data.
- **Fields**: 
  - `big_holder_ratio`: Combined percentage of holders with >400 or >1000 shares.
- **Frequency**: Weekly (usually Saturday).

## 2. Scoring Algorithm (Refined by Historical Data)
### 2.1 Institutional Buying Momentum (60%)
- **Streak Bonus**: 
    - 5~7 consecutive net buy days (Trust/Foreign): +30 pts (Early Acceleration Phase).
    - 10+ consecutive net buy days: +40 pts (Prolonged Stealth Accumulation).
- **Volume Absorption**: 
    - Consistent daily net buy of 500~1,500 lots (緩步吸籌) during VCP phase: +10 pts.
    - (Daily Net Buy / 5d Avg Vol) > 3.0%: +20 pts. (Significant "Adoption")

### 2.2 Big Player Concentration (40%)
- Weekly increase in 400+ share holder %: +20 pts.
- Weekly increase in 1000+ share holder %: +20 pts.
- Divergence check: -20 pts if price is consolidate but big players are selling.

## 3. Database Schema
```sql
CREATE TABLE IF NOT EXISTS chip_data (
    ticker      TEXT,
    date        TEXT,
    foreign_buy INTEGER,
    trust_buy   INTEGER,
    dealer_buy  INTEGER,
    total_buy   INTEGER,
    big_holder_ratio REAL,
    PRIMARY KEY (ticker, date)
);
```

## 4. Integration Points
- **Fetcher**: `data/fetcher.py` will include `fetch_institutional_data(ticker)`.
- **Scorer**: `core/scorer.py` will call `core/chip_analysis.py` to get the `chip_score`.
- **Output**: Update CSV columns and logs to show Chip Score and Grade.
