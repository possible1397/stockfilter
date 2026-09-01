"""
research/probe_revenue.py — FinMind 月營收取用能力探測（階段 2 前置）

2026-09-01 第一輪已確認:FinMind 免費層的 TaiwanStockMonthRevenue
可用、免 token，2330 取得 2020-01 ~ 2026-08 共 80 期 —— 月營收進得了探索期。

本輪要回答的是「1,900 檔要怎麼抓才不會被擋」:
  A. 有沒有一次取全市場的批次能力（省下 1,900 次請求）
  B. 逐檔抓的實際速率與配額上限在哪裡
  C. 配額用完時 API 怎麼回應（才能寫對重試邏輯）

只做少量唯讀請求，測完即停。

用法: python research/probe_revenue.py
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.finmindtrade.com/api/v4/data"
DATASET = "TaiwanStockMonthRevenue"
TIMEOUT = 25
UA = {"User-Agent": "stockfilter-research/1.0 (personal research)"}


def call(params):
    """回傳 (http_status, json_or_None, 錯誤字串)。"""
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, json.loads(r.read()), ""
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        return e.code, None, body
    except Exception as e:
        return -1, None, f"{type(e).__name__}: {e}"


def probe_bulk():
    print("\n" + "=" * 70)
    print("  A. 批次能力:不指定 data_id，能不能一次拿多檔?")
    print("=" * 70)
    for label, params in [
        ("不給 data_id + 單月區間",
         {"dataset": DATASET, "start_date": "2024-01-01", "end_date": "2024-01-31"}),
        ("data_id 留空",
         {"dataset": DATASET, "data_id": "", "start_date": "2024-01-01",
          "end_date": "2024-01-31"}),
    ]:
        status, j, err = call(params)
        if j is None:
            print(f"  {label}: HTTP {status}  {err[:160]}")
            continue
        data = j.get("data", [])
        ids = {d.get("stock_id") for d in data}
        print(f"  {label}: HTTP {status} msg={j.get('msg','')!r} "
              f"筆數={len(data)} 不同股票={len(ids)}")
        if len(ids) > 5:
            print(f"    → 支援批次!一次拿到 {len(ids)} 檔，1,900 檔只需要少數幾次請求")
        elif data:
            print("    → 只回單一標的，得逐檔抓")


def probe_rate():
    print("\n" + "=" * 70)
    print("  B. 逐檔抓的速率:連續 12 次請求")
    print("=" * 70)
    tickers = ["2330", "2317", "2454", "2308", "2382", "2412",
               "3008", "2881", "1301", "1216", "2603", "2609"]
    t0 = time.time()
    ok = 0
    for i, tk in enumerate(tickers, 1):
        t1 = time.time()
        status, j, err = call({"dataset": DATASET, "data_id": tk,
                               "start_date": "2021-01-01"})
        dt = time.time() - t1
        if j is None:
            print(f"  {i:>2}. {tk}  HTTP {status}  {dt:.2f}s  {err[:120]}")
            print("      → 配額或速率上限，停止測試")
            break
        n = len(j.get("data", []))
        ok += 1
        print(f"  {i:>2}. {tk}  HTTP {status}  {dt:.2f}s  {n} 期")
        time.sleep(0.3)
    total = time.time() - t0
    print(f"\n  成功 {ok}/{len(tickers)}，總耗時 {total:.1f}s"
          f"（平均 {total/max(ok,1):.2f}s/檔）")
    if ok:
        est = 1900 * total / ok / 60
        print(f"  → 以此速率，1,900 檔約需 {est:.0f} 分鐘（未計配額中斷）")


def main():
    print("FinMind 月營收取用能力探測")
    probe_bulk()
    probe_rate()
    print("\n" + "=" * 70)
    print("  判讀")
    print("=" * 70)
    print("  - 若 A 支援批次:階段 2 直接用批次抓，幾分鐘完成")
    print("  - 若只能逐檔且速率可接受:寫成可續跑的抓取腳本，中斷後能接續")
    print("  - 若很快撞到配額:考慮註冊免費帳號拿 token 提高上限，")
    print("    或改用其他免費維度")


if __name__ == "__main__":
    main()
