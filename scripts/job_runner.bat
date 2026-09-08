@echo off
REM ── stockfilter 工作執行器 ────────────────────────────────────
REM 由 Windows 工作排程器每 2 分鐘呼叫一次。
REM 檢查 output\jobs\ 裡有沒有 .bat，有就執行，執行完移到 done\。
REM 這樣就不必每次都手動點兩下。
REM
REM 要停用:schtasks /delete /tn "stockfilter job runner" /f
setlocal
cd /d D:\Project\stockfilter
if not exist output\jobs mkdir output\jobs
if not exist output\jobs\done mkdir output\jobs\done

REM 上一個工作還在跑就跳過（長時間的抓取工作會佔用很久）
if exist output\jobs\.lock goto :eof
echo %date% %time% > output\jobs\.lock

REM 每日例行的補跑檢查（錯過 15:30 也不會漏掉）
venv\Scripts\python.exe scripts\daily_guard.py >> output\jobs\runner.log 2>&1

for %%F in (output\jobs\*.bat) do (
    echo. >> output\jobs\runner.log
    echo ==== %date% %time%  執行 %%~nxF ==== >> output\jobs\runner.log
    call "%%F" >> output\jobs\runner.log 2>&1
    move /y "%%F" output\jobs\done\ >nul 2>&1
    echo ==== %date% %time%  完成 %%~nxF ==== >> output\jobs\runner.log
)

del /f /q output\jobs\.lock >nul 2>&1
