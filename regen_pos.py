import os
import pandas as pd
import yaml
from core.position_manager import PositionManager
from data.database import load_dataframe

with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

pm = PositionManager(config=config)
pos_stock_data = {}
for t in pm.get_tickers():
    df = load_dataframe(t, config['scanner']['db_path'])
    if not df.empty:
        pos_stock_data[t] = df

advices = pm.monitor(pos_stock_data)

adv_data = []
for a in advices:
    adv_data.append({
        "股票代號": a.ticker,
        "買進均價": a.entry_price,
        "現價": a.current_price,
        "損益%": a.profit_pct,
        "剩餘比例": f"{a.remaining_ratio * 100:.0f}%",
        "硬停損": a.hard_stop,
        "ATR追蹤停損": a.atr_trail_stop,
        "K線追蹤停損": a.kline_trail_stop,
        "建議停損": a.recommended_stop,
        "操作建議": a.action,
        "是否建議減碼": a.should_partial_exit,
        "持倉最高價": a.highest_since_entry
    })

out_path = os.path.join(config['scanner']['output_dir'], 'positions_2026-04-15.csv')
pd.DataFrame(adv_data).to_csv(out_path, index=False, encoding='utf-8-sig')
print("Successfully regenerated format for positions_2026-04-15.csv")
