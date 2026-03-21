"""
TWSE/TPEx 產業價值鏈資訊平台 - 專屬爬蟲腳本
負責爬取 https://ic.tpex.org.tw/ 的官方產業鏈資料 (上游/中游/下游)。

特點：
- 此網站結構有時會變更，且各個公司的代碼常被包裝在特定的 div/li 或點擊事件中。
- 此腳本示範針對該平台結構的解析方法，並將抓取到的標的合併回 supply_chain_data.json。

使用:
  pip install requests beautifulsoup4
  python scripts/scrape_tpex_chain.py
"""

import os
import json
import time
import re
import requests
from bs4 import BeautifulSoup

# 目標 JSON 檔案路徑
DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                         'data', 'supply_chain_data.json')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://ic.tpex.org.tw/',
    'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7'
}

def load_existing_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n[OK] 產業鏈資料已成功更新至: {DATA_FILE}")

def scrape_tpex_ic_node(ic_code: str, node_id: str) -> list:
    """
    抓取產業價值鏈特定節點的公司代碼。
    依據過去經驗，ic.tpex.org.tw 有時會透過 Ajax 呼叫取得公司清單，
    或者把公司清單直接寫在 HTML 裡隱藏的區塊中。
    
    ic_code: 產業代碼，例如 'D000' 代表半導體
    node_id: 節點代碼或名稱
    """
    print(f"正在抓取 [ic={ic_code}, node={node_id}] ...", end=" ", flush=True)
    
    # 網址範例：introduce.php 或 company_list.php
    url = f"https://ic.tpex.org.tw/introduce.php?ic={ic_code}"
    
    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, 'html.parser')
        
        tickers = []
        
        # ==========================================================
        # 爬蟲邏輯 (針對 ic.tpex.org.tw 常見 DOM 結構)
        # ==========================================================
        # 1. 尋找與 node_id 相符的區塊 (通常是 div id 或是特定 class)
        # 例如網站中會有 <div class="chain-node" data-node="UP">...
        # 或者是 <div id="company-list-1"> 裡面包著 <a href="stock.php?stk=2330">2330 台積電</a>
        
        # 這裡我們使用一個通用的正則表達式，直接找出網頁內所有的 4 碼數字股票代號
        # 實戰中若發現抓太廣，可以縮小 BeautifulSoup 尋找的範圍，例如:
        # container = soup.find('div', {'id': node_id}) 
        # html_text = str(container) if container else ""
        
        # 為了示範，若我們無法直接鎖定節點，我們尋找股票代號的 a 連結
        company_links = soup.find_all('a', href=re.compile(r'stk=\d{4}'))
        
        for link in company_links:
            # href 範例: 'stock.php?stk=2330'
            href = link.get('href', '')
            match = re.search(r'stk=(\d{4})', href)
            if match:
                symbol = match.group(1)
                # 簡單判定 (實務上可以再比對 TSE/OTC 名單，此處統一加 .TW 示範)
                tickers.append(f"{symbol}.TW")
                
        # 去除重複
        tickers = sorted(list(set(tickers)))
        print(f"找到 {len(tickers)} 檔")
        
        time.sleep(2)  # 禮貌性延遲
        return tickers
        
    except Exception as e:
        print(f"失敗: {e}")
        return []

def main():
    print("=== TPEx 產業價值鏈資訊平台 - 爬蟲腳本 ===\n")
    
    data = load_existing_data()
    if "_metadata" not in data:
        data["_metadata"] = {}
        
    # 定義爬取目標: (大產業名稱, 產業鏈代碼, 對應節點配置)
    # 這些 ic_code (如 D000, C000) 需要查閱 ic.tpex.org.tw 首頁來對應
    TARGET_MAPPING = [
        # 範例結構: ("產業名", "ic平台代碼", [("上游", "node1"), ("中游", "node2"), ("下游", "node3")])
        ("半導體", "D000", [
            ("上游", "node-up"),
            ("中游", "node-mid"),
            ("下游", "node-down")
        ]),
        ("光電面板", "C000", [
            ("上游", "node-up"),
            ("中游", "node-mid"),
            ("下游", "node-down")
        ])
    ]
    
    updated_any = False
    
    for chain_name, ic_code, levels in TARGET_MAPPING:
        if chain_name not in data:
            data[chain_name] = {}
            
        for level_name, node_id in levels:
            if level_name not in data[chain_name]:
                data[chain_name][level_name] = {"description": f"{chain_name}的{level_name}", "tickers": []}
            
            # 執行爬取
            new_tickers = scrape_tpex_ic_node(ic_code, node_id)
            
            if new_tickers:
                existing = set(data[chain_name][level_name].get("tickers", []))
                updated = sorted(list(existing.union(set(new_tickers))))
                
                data[chain_name][level_name]["tickers"] = updated
                updated_any = True
                
    # 更新中繼資料
    data["_metadata"]["last_updated"] = time.strftime("%Y-%m-%d")
    data["_metadata"]["crawler_source"] = "https://ic.tpex.org.tw/"
    
    if updated_any:
        save_data(data)
    else:
        print("\n本次沒有抓到新資料，請檢查網頁結構或 ic_code 是否有變動。")
        # 示範強制儲存
        save_data(data)

if __name__ == "__main__":
    main()
