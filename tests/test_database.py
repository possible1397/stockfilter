"""tests/test_database.py — SQLite 讀寫單元測試"""
import os
import tempfile
import pandas as pd
import numpy as np
import pytest
import data.database as db


def _make_df(bars: int = 10) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=bars, freq="B")
    return pd.DataFrame({
        "Open":   np.ones(bars) * 100,
        "High":   np.ones(bars) * 105,
        "Low":    np.ones(bars) * 95,
        "Close":  np.linspace(100, 110, bars),
        "Volume": np.ones(bars, dtype=int) * 500_000,
    }, index=dates)


@pytest.fixture
def tmp_db(tmp_path):
    """提供一個臨時 SQLite 資料庫路徑"""
    path = str(tmp_path / "test.db")
    db.init_db(path)
    return path


class TestDatabase:
    def test_init_db_creates_tables(self, tmp_db):
        with db.get_connection(tmp_db) as conn:
            tables = {row[0] for row in
                      conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "daily_prices" in tables
        assert "scan_results" in tables

    def test_save_and_load_dataframe(self, tmp_db):
        df = _make_df(bars=5)
        df.index.name = "Date"
        db.save_dataframe("2330.TW", df, tmp_db)
        loaded = db.load_dataframe("2330.TW", tmp_db)
        assert not loaded.empty
        assert len(loaded) == 5
        assert "Close" in loaded.columns

    def test_get_latest_date(self, tmp_db):
        df = _make_df(bars=5)
        df.index.name = "Date"
        db.save_dataframe("2330.TW", df, tmp_db)
        latest = db.get_latest_date("2330.TW", tmp_db)
        assert latest is not None
        assert latest == "2024-01-05"  # 5th business day from 2024-01-01 (Mon–Fri)

    def test_get_latest_date_missing_ticker(self, tmp_db):
        result = db.get_latest_date("NONEXISTENT.TW", tmp_db)
        assert result is None

    def test_save_dataframe_empty_skipped(self, tmp_db):
        db.save_dataframe("2330.TW", pd.DataFrame(), tmp_db)
        loaded = db.load_dataframe("2330.TW", tmp_db)
        assert loaded.empty

    def test_upsert_does_not_duplicate(self, tmp_db):
        df = _make_df(bars=5)
        df.index.name = "Date"
        db.save_dataframe("2330.TW", df, tmp_db)
        db.save_dataframe("2330.TW", df, tmp_db)  # 再存一次
        loaded = db.load_dataframe("2330.TW", tmp_db)
        assert len(loaded) == 5  # 不應重複

    def test_multiple_tickers(self, tmp_db):
        df = _make_df(bars=3)
        df.index.name = "Date"
        db.save_dataframe("2330.TW", df, tmp_db)
        db.save_dataframe("2454.TW", df, tmp_db)
        assert len(db.load_dataframe("2330.TW", tmp_db)) == 3
        assert len(db.load_dataframe("2454.TW", tmp_db)) == 3
