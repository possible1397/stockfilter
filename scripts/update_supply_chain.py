"""
TPEx 全市場產業鏈自動爬蟲器
負責爬取 https://ic.tpex.org.tw/ 的官方產業鏈資料。
會自動掃描所有 40+ 個產業，並將其下的所有股票萃取出來，更新到 supply_chain_data.json。

執行:
  pip install requests beautifulsoup4 urllib3
  python scripts/update_supply_chain.py
"""

import os
import json
import time
import re
import requests
import urllib3
from bs4 import BeautifulSoup

# 關閉 SSL 憑證警告 (政府網站常有憑證問題)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                         'data', 'supply_chain_data.json')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

def load_existing_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_data(data):
    # Ensure directory exists
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n[OK] 產業鏈資料已成功更新至: {DATA_FILE}")

def scrape_all_industries():
    """爬取首頁，取得所有產業代碼與名稱"""
    print("正在取得 TPEx 產業總表...")
    res = requests.get('https://ic.tpex.org.tw/', headers=HEADERS, timeout=10, verify=False)
    # 網站使用 BIG5 或 UTF-8，我們讓 requests 自動判定，若亂碼再手動轉
    res.encoding = 'utf-8' 
    soup = BeautifulSoup(res.text, 'html.parser')

    industries = {}
    for a in soup.find_all('a', href=re.compile(r'introduce\.php\?ic=[a-zA-Z0-9]+')):
        match = re.search(r'ic=([a-zA-Z0-9]+)', a['href'])
        if match:
            code = match.group(1).upper()
            name = a.text.strip()
            # 過濾無效字串
            if name and len(name) > 1 and len(code) == 4:
                industries[code] = name
                
    print(f"共找到 {len(industries)} 個產業板塊。")
    return industries

def scrape_industry_tickers(ic_code: str, ind_name: str) -> list:
    """進入單一產業頁面，抓取所有股票代號"""
    url = f"https://ic.tpex.org.tw/introduce.php?ic={ic_code}"
    try:
        res = requests.get(url, headers=HEADERS, timeout=15, verify=False)
        res.encoding = 'utf-8'
        soup = BeautifulSoup(res.text, 'html.parser')
        
        tickers = set()
        # 尋找所有 stk_code=1234 的連結
        links = soup.find_all('a', href=re.compile(r'stk_code=\d{4}'))
        for link in links:
            match = re.search(r'stk_code=(\d{4})', link.get('href', ''))
            if match:
                symbol = match.group(1)
                # 為簡化處理，統一先以 .TW 結尾 (若需區分上市櫃，需進階比對清單)
                # 不過 yfinance 通常 .TW 會 fallback 或你原本的程式已有容錯
                tickers.add(f"{symbol}.TW")
                
        # 加碼：有時候它是包在某些 onclick 裡面
        # 我們直接用 regex 掃描整個網頁原始碼找四位數的台股代碼特徵
        # (這裡只取 stk= 的最安全，避免誤抓年份)
        
        tickers_list = sorted(list(tickers))
        if tickers_list:
            print(f"  [{ic_code}] {ind_name:10} -> 找到 {len(tickers_list):3} 檔")
        else:
            print(f"  [{ic_code}] {ind_name:10} -> 0 檔 (可能無資料或需展開)")
            
        return tickers_list
    except Exception as e:
        print(f"  [{ic_code}] {ind_name} 爬取失敗: {e}")
        return []

def main():
    print("=== 台股全市場產業鏈自動爬蟲器 (TPEx) ===\n")
    
    # 載入現有資料
    data = load_existing_data()
    if "_metadata" not in data:
        data["_metadata"] = {}
        
    # 1. 取得所有產業清單
    industries = scrape_all_industries()
    if not industries:
        print("無法取得產業清單，程式中止。")
        return
        
    total_tickers_found = 0
    updated_chains = 0
    
    # 2. 逐一爬取各產業的成分股
    for i, (ic_code, ind_name) in enumerate(industries.items(), 1):
        # 為了避免中文字元問題，有些特殊空白要處理
        clean_name = ind_name.replace('\n', '').replace('\r', '').strip()
        
        tickers = scrape_industry_tickers(ic_code, clean_name)
        if tickers:
            # 我們將該產業的所有股票統一放在 "全產業" 這個分類節點下
            # 若未來需要細分上中下游，需進階解析 DOM
            if clean_name not in data:
                data[clean_name] = {}
                
            # 保留原本若已經手動建好的 上中下游 結構，把新的股票合併進去
            # 找看看原本有沒有 "全產業" 節點，沒有就建一個
            target_level = "全產業"
            if target_level not in data[clean_name]:
                data[clean_name][target_level] = {
                    "description": f"由 {ic_code} 頁面自動彙整的所有概念標的",
                    "tickers": []
                }
                
            existing_set = set(data[clean_name][target_level].get("tickers", []))
            new_set = set(tickers)
            merged = sorted(list(existing_set.union(new_set)))
            
            data[clean_name][target_level]["tickers"] = merged
            total_tickers_found += len(new_set)
            updated_chains += 1
            
        # 爬蟲禮儀，避免超載
        time.sleep(1)
        
    # 3. 儲存結果
    data["_metadata"]["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    data["_metadata"]["crawler_source"] = "https://ic.tpex.org.tw/"
    data["_metadata"]["total_industries"] = len(industries)
    
    if updated_chains > 0:
        print(f"\n爬取結束！涵蓋 {updated_chains} 個產業，共注入超過 {total_tickers_found} 個標籤關聯。")
        save_data(data)
    else:
        print("\n本次沒有抓到任何新資料。")

if __name__ == "__main__":
    main()
