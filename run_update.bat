@echo off
REM ── stockfilter 每日例行 ──────────────────────────────────────────────
REM   1. main.py            更新價格/籌碼 (一定執行)，大盤分數 >= 40 才繼續掃描
REM                         只想更新資料不掃描: main.py --data-only
REM                         弱勢盤仍要看掃描結果: main.py --force
REM   2. forward_validate   補算缺少的訊號日並重放凍結規格
REM                         -> output/forward_validation.md
REM   凍結規格檢查會在 forward_validate 啟動時自動執行，參數被改動就會中止。
REM   紀錄: output/daily_run.log
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d D:\Project\stockfilter
set "PY=venv\Scripts\python.exe"
set "LOG=output\daily_run.log"

echo === START %date% %time% === > "%LOG%"

echo. >> "%LOG%"
echo ############ [1/2] main.py ############ >> "%LOG%"
"%PY%" main.py >> "%LOG%" 2>&1

echo. >> "%LOG%"
echo ############ [2/2] forward_validate.py ############ >> "%LOG%"
"%PY%" scripts\forward_validate.py >> "%LOG%" 2>&1

echo. >> "%LOG%"
echo === DONE %date% %time% === >> "%LOG%"
echo.
echo 完成。報告: output\forward_validation.md ^| 紀錄: output\daily_run.log
timeout /t 20
