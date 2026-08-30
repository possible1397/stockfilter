"""
utils/provenance.py — 訊號的來源戳記

每一筆寫進 scan_results 的訊號都要能回答:「這是用哪一組參數、哪一版程式算出來的?」
沒有這個,參數一改,新舊訊號就在同一張表裡混在一起而且事後無法分辨 ——
對一個核心紀律是「凍結參數、保留期驗證」的專案來說,這是研究可複現的前提。

config_hash 只雜湊真正影響訊號的設定區段 (scanner/pattern/cleaner/backtest),
所以改註解或調 logging 不會讓雜湊值變動。
"""
import hashlib
import json
import os
import subprocess

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SECTIONS = ("scanner", "pattern", "cleaner", "backtest")

_cache = {}


def config_hash(config_path: str = None) -> str:
    """回傳影響訊號的設定區段的 SHA1 前 10 碼。"""
    path = config_path or os.path.join(_ROOT, "config.yaml")
    key = ("cfg", path)
    if key in _cache:
        return _cache[key]
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        subset = {k: cfg.get(k, {}) for k in _SECTIONS}
        blob = json.dumps(subset, sort_keys=True, ensure_ascii=False, default=str)
        val = hashlib.sha1(blob.encode("utf-8")).hexdigest()[:10]
    except Exception:
        val = ""
    _cache[key] = val
    return val


def git_commit() -> str:
    """回傳目前 HEAD 的短 SHA;取不到就回空字串 (工作目錄有未提交變更時加 +)。"""
    if "git" in _cache:
        return _cache["git"]
    val = ""
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             cwd=_ROOT, capture_output=True, text=True, timeout=10)
        if sha.returncode == 0:
            val = sha.stdout.strip()
            dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                                   cwd=_ROOT, capture_output=True, text=True, timeout=15)
            if dirty.returncode == 0 and dirty.stdout.strip():
                val += "+"
    except Exception:
        val = ""
    _cache["git"] = val
    return val


def stamp() -> tuple:
    """(config_hash, git_commit) —— 寫入 scan_results 時一併帶上。"""
    return config_hash(), git_commit()
