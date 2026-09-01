"""
research/probe_revenue.py — 月營收資料來源探測（階段 2 前置）

不抓資料，只回答一個問題:免費而且允許程式取用的月營收來源，
歷史深度到哪裡?

背景（2026-09-01 查證）:
  - 公開資訊觀測站的月營收彙總表網頁，robots.txt 禁止自動擷取 —— 不走這條
  - 政府資料開放平臺的資料集頁同樣擋自動存取
  - 剩下兩條官方/半官方路徑，深度未知，所以實測:
      A. TWSE OpenAPI（官方、為程式取用而設計）
      B. FinMind API（免費層有呼叫限制，且部分資料集限付費會員）

本檔只做唯讀的探測請求，各一到兩次，不會大量抓取。

用法: python research/probe_revenue.py
"""
import json
import sys
import urllib.request

TIMEOUT = 20
UA = {"User-Agent": "stockfilter-research/1.0 (personal research)"}


def get(url, params=None):
    if params:
        import urllib.parse
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.status, r.read()


def probe_twse():
    print("\n" + "=" * 70)
    print("  A. TWSE OpenAPI（官方）")
    print("=" * 70)
    for name, url in [
        ("上市 月營收 t187ap05_L", "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"),
        ("上市 基本資料 t187ap03_L", "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"),
    ]:
        try:
            status, body = get(url)
            data = json.loads(body)
            print(f"  {name}: HTTP {status}, {len(data)} 筆")
            if data:
                keys = list(data[0].keys())
                print(f"    欄位: {keys[:12]}")
                for k in keys:
                    if "年" in k or "月" in k or "資料" in k:
                        print(f"    期別欄位 {k} = {data[0][k]!r}")
        except Exception as e:
            print(f"  {name}: 失敗 —— {type(e).__name__}: {e}")

    print("\n  上櫃（TPEx）月營收:")
    for url in ["https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"]:
        try:
            status, body = get(url)
            data = json.loads(body)
            print(f"    HTTP {status}, {len(data)} 筆, 欄位 {list(data[0].keys())[:10]}")
        except Exception as e:
            print(f"    失敗 —— {type(e).__name__}: {e}")

    print("\n  ⚠ 重點:TWSE OpenAPI 一般只提供「最新一期」。")
    print("     若上面回傳的期別只有一個月份，代表五年歷史要靠每月自己累積，")
    print("     無法回補 —— 那就不能用在探索期（2021-09~2024-12）的研究上。")


def probe_finmind():
    print("\n" + "=" * 70)
    print("  B. FinMind API（免費層）")
    print("=" * 70)
    url = "https://api.finmindtrade.com/api/v4/data"
    params = {"dataset": "TaiwanStockMonthRevenue", "data_id": "2330",
              "start_date": "2020-01-01"}
    try:
        status, body = get(url, params)
        j = json.loads(body)
        msg = j.get("msg", "")
        data = j.get("data", [])
        print(f"  HTTP {status}  msg={msg!r}  筆數={len(data)}")
        if data:
            dates = sorted({d.get("date") or d.get("revenue_month") for d in data})
            print(f"    欄位: {list(data[0].keys())}")
            print(f"    期間: {dates[0]} ~ {dates[-1]}  共 {len(dates)} 期")
            print("    → 免費層可取得歷史，五年研究可行")
        else:
            print("    → 沒有資料。可能是需要 token、或此資料集限付費會員")
    except Exception as e:
        print(f"  失敗 —— {type(e).__name__}: {e}")
        print("    （若是 402/403，代表需要付費會員）")


def main():
    print("月營收資料來源探測  ——  只做少量唯讀請求")
    probe_twse()
    probe_finmind()
    print("\n" + "=" * 70)
    print("  判讀")
    print("=" * 70)
    print("  想用在探索期（2021-09 起）的研究，來源必須有五年歷史。")
    print("  若兩條路都只有最新一期或需付費，階段 2 的月營收就要改成:")
    print("    (a) 從現在開始每月累積，只用在未來的向前驗證，或")
    print("    (b) 付費取得歷史，或")
    print("    (c) 跳過月營收，階段 2 改測別的免費維度")


if __name__ == "__main__":
    sys.exit(main())
