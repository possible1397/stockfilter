from FinMind.data import DataLoader

def test_finmind():
    dl = DataLoader()
    
    # 1. 測試法人買賣超
    print("Fetching institutional investors for 2330...")
    df_inst = dl.taiwan_stock_institutional_investors(stock_id="2330", start_date="2024-03-01")
    if not df_inst.empty:
        print(f"Inst Data Shape: {df_inst.shape}")
        print(f"Columns: {df_inst.columns.tolist()}")
        print(df_inst.head(3))
        
    # 2. 測試大戶持股比例
    print("\nFetching holding shares per for 2330...")
    df_hold = dl.taiwan_stock_holding_shares_per(stock_id="2330", start_date="2024-03-01")
    if not df_hold.empty:
        print(f"Hold Data Shape: {df_hold.shape}")
        print(f"Columns: {df_hold.columns.tolist()}")
        print(df_hold.head(3))

if __name__ == "__main__":
    test_finmind()
