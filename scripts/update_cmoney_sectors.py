"""
CMoney 概念股族群自動爬蟲
從 https://www.cmoney.tw/forum/concept 抓取 150+ 個主題族群及其成分股
更新至 supply_chain_data.json

執行:
  python scripts/update_cmoney_sectors.py           # 完整更新
  python scripts/update_cmoney_sectors.py --dry-run # 只印出結果，不寫入

技術架構:
  - Python  負責: 族群清單解析 + 資料合併寫入 + .TW/.TWO 判斷
  - Node.js 負責: 執行 CMoney 頁面內嵌的 NUXT IIFE，解碼混淆後的 stockId 列表
"""

import os
import sys
import json
import time
import re
import subprocess
import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── 路徑設定 ────────────────────────────────────────────────────────────────
_ROOT      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILE  = os.path.join(_ROOT, 'data', 'supply_chain_data.json')
CACHE_FILE = os.path.join(_ROOT, 'sector_list_cache.json')
DECODER_JS = os.path.join(_ROOT, 'scripts', 'decode_cmoney.js')

# ── 爬蟲設定 ────────────────────────────────────────────────────────────────
CONCEPT_LIST_URL = "https://www.cmoney.tw/forum/concept"
SLEEP_SEC        = 2.0    # 每次請求間隔
REQUEST_TIMEOUT  = 20
NODE_TIMEOUT     = 30     # Node.js 子程序最長等待秒數

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-TW,zh;q=0.9',
    'Referer': 'https://www.cmoney.tw/',
}

SKIP_GROUPS   = {"其他", "未分類"}
MANUAL_LAYERS = {"上游", "中游", "下游", "製造"}


# ── 工具函式 ────────────────────────────────────────────────────────────────

def _get(url: str) -> requests.Response | None:
    """帶 SSL 回退的 GET，最多重試 3 次。"""
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=HEADERS,
                                timeout=REQUEST_TIMEOUT, verify=True)
            if resp.status_code == 200:
                resp.encoding = 'utf-8'
                return resp
            print(f"    [!] HTTP {resp.status_code}，第 {attempt+1} 次重試...")
        except requests.exceptions.SSLError:
            try:
                resp = requests.get(url, headers=HEADERS,
                                    timeout=REQUEST_TIMEOUT, verify=False)
                if resp.status_code == 200:
                    resp.encoding = 'utf-8'
                    return resp
            except requests.RequestException as e:
                print(f"    [!] SSL 回退失敗: {e}")
        except requests.RequestException as e:
            print(f"    [!] 連線失敗: {e}，第 {attempt+1} 次重試...")
        time.sleep(2 ** attempt)
    return None


def load_json(path: str) -> dict:
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_json(data: dict, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def build_exchange_map(cache: dict) -> dict:
    """
    從 sector_list_cache.json 建立 {股票代號: '.TW' | '.TWO'} 對照表。
    .TWO 優先 (上市/上櫃同代號時取上櫃)。
    """
    exmap: dict[str, str] = {}
    for key in cache:
        if key.endswith('.TWO'):
            exmap[key[:-4]] = '.TWO'
        elif key.endswith('.TW'):
            code = key[:-3]
            if code not in exmap:
                exmap[code] = '.TW'
    return exmap


def to_ticker(code: str, exmap: dict) -> str:
    return f"{code}{exmap.get(code, '.TW')}"


# ── 核心爬蟲 ────────────────────────────────────────────────────────────────

def fetch_concept_groups() -> dict[str, str]:
    """抓取 CMoney 概念股總覽 → {族群名稱: CMoney代碼 C50xxx}"""
    print(f"[1/3] 取得概念股族群清單: {CONCEPT_LIST_URL}")
    resp = _get(CONCEPT_LIST_URL)
    if resp is None:
        return {}

    soup = BeautifulSoup(resp.text, 'html.parser')
    groups: dict[str, str] = {}
    seen_codes: set[str] = set()
    pattern = re.compile(r'^/forum/concept/(C\d{5,6})$')

    for a in soup.find_all('a', href=True):
        m = pattern.match(a['href'])
        if not m:
            continue
        code = m.group(1)
        if code in seen_codes:
            continue
        seen_codes.add(code)
        name = a.get_text(strip=True)
        if name and len(name) >= 2 and not name.isdigit() and name not in SKIP_GROUPS:
            groups[name] = code

    print(f"    找到 {len(groups)} 個概念股族群。")
    return groups


def fetch_group_members(group_code: str, exmap: dict) -> list[str]:
    """
    呼叫 Node.js 解碼 CMoney NUXT IIFE，取得成分股代號列表。
    回傳 yfinance 格式的 ticker 列表 (含 .TW/.TWO 後綴)。
    """
    time.sleep(SLEEP_SEC)
    try:
        result = subprocess.run(
            ['node', DECODER_JS, group_code],
            capture_output=True, text=True,
            timeout=NODE_TIMEOUT, encoding='utf-8'
        )
        if result.returncode != 0:
            return []
        codes: list[str] = json.loads(result.stdout.strip())
        return sorted({to_ticker(c, exmap) for c in codes if c.isdigit()})
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
        return []


# ── 主流程 ──────────────────────────────────────────────────────────────────

def main():
    dry_run = '--dry-run' in sys.argv
    print("=== CMoney 概念股族群自動爬蟲 ===")
    if dry_run:
        print("    [DRY-RUN 模式：只印出，不寫檔]\n")

    existing = load_json(DATA_FILE)
    cache    = load_json(CACHE_FILE)
    exmap    = build_exchange_map(cache)
    print(f"    交易所對照表: {len(exmap):,} 筆 | 現有族群: {len(existing)} 個\n")

    # Step 1: 族群清單
    groups = fetch_concept_groups()
    if not groups:
        print("[!] 無法取得族群清單，結束。")
        return

    # Step 2: 成分股 (Node.js 解碼)
    print(f"\n[2/3] 逐一抓取成分股 (共 {len(groups)} 個族群，Node.js 解碼)...")
    results: dict[str, list[str]] = {}
    skipped_manual = 0
    empty_groups   = 0

    for i, (name, code) in enumerate(groups.items(), 1):
        existing_layers = set(existing.get(name, {}).keys())
        if existing_layers & MANUAL_LAYERS:
            print(f"  [{i:3d}/{len(groups)}] {name:<20} 已有手動結構，跳過")
            skipped_manual += 1
            continue

        tickers = fetch_group_members(code, exmap)
        if tickers:
            results[name] = tickers
            print(f"  [{i:3d}/{len(groups)}] [{code}] {name:<20} {len(tickers):3d} 檔")
        else:
            empty_groups += 1
            print(f"  [{i:3d}/{len(groups)}] [{code}] {name:<20}   0 檔")

    # Step 3: 合併寫入
    print(f"\n[3/3] 合併寫入...")
    updated = 0
    for name, tickers in results.items():
        if name not in existing:
            existing[name] = {}
        prev_set = set(existing[name].get("全產業", {}).get("tickers", []))
        merged   = sorted(prev_set | set(tickers))
        new_cnt  = len(set(tickers) - prev_set)
        existing[name]["全產業"] = {
            "description": f"CMoney 概念股 {groups[name]} 自動彙整",
            "tickers": merged,
        }
        updated += 1
        if new_cnt:
            print(f"  {name:<22} +{new_cnt} 新增 (共 {len(merged)} 檔)")

    if "_metadata" not in existing:
        existing["_metadata"] = {}
    existing["_metadata"]["cmoney_last_updated"]  = time.strftime("%Y-%m-%d %H:%M:%S")
    existing["_metadata"]["cmoney_source"]         = CONCEPT_LIST_URL
    existing["_metadata"]["cmoney_groups_found"]   = len(groups)
    existing["_metadata"]["cmoney_groups_updated"] = updated

    print(f"\n  結果: 更新 {updated} 個族群 | 跳過手動 {skipped_manual} 個 | "
          f"空族群 {empty_groups} 個")

    if not dry_run:
        save_json(existing, DATA_FILE)
        print(f"  [OK] 已寫入: {DATA_FILE}")
    else:
        print("  [DRY-RUN] 不寫入。")


if __name__ == "__main__":
    main()
