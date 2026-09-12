@echo off
REM ── stockfilter 工作執行器 ────────────────────────────────────
REM 由 Windows 工作排程器每 2 分鐘呼叫一次。
REM   1. 清掉過期的鎖（行程被睡眠/關機殺掉時會留下）
REM   2. 每日例行的補跑檢查
REM   3. 月營收補抓的守門員（每次一輪，配額冷卻由它自己管）
REM   4. 執行 output\jobs\ 裡的待辦 .bat
REM
REM 要停用:schtasks /delete /tn "stockfilter job runner" /f
setlocal
cd /d D:\Project\stockfilter
if not exist output\jobs mkdir output\jobs
if not exist output\jobs\done mkdir output\jobs\done

REM 過期鎖偵測:超過 45 分鐘的鎖視為死行程留下的，直接清掉。
REM （正常工作最久約 20 分鐘:main.py 約 10 分、營收一輪約 7 分。）
REM （2026-09-08 就是因為沒有這一步，一個死掉的鎖讓自動化停擺 24 小時。）
venv\Scripts\python.exe -c "import os,time,sys;p=r'output\jobs\.lock';sys.exit(0) if not os.path.exists(p) else (os.remove(p) if time.time()-os.path.getmtime(p)>2700 else None)" >nul 2>&1

if exist output\jobs\.lock goto :eof
echo %date% %time% > output\jobs\.lock

REM 每日例行的補跑檢查（錯過 15:30 也不會漏掉）
venv\Scripts\python.exe scripts\daily_guard.py >> output\jobs\runner.log 2>&1

REM 月營收補抓（每次一輪，不在行程裡長等）
venv\Scripts\python.exe scripts\revenue_guard.py >> output\jobs\runner.log 2>&1

REM R1 模擬單（每日一次，已處理過的價格日會自己略過）
venv\Scripts\python.exe scripts\r1_paper.py >> output\jobs\runner.log 2>&1

for %%F in (output\jobs\*.bat) do (
    echo. >> output\jobs\runner.log
    echo ==== %date% %time%  執行 %%~nxF ==== >> output\jobs\runner.log
    call "%%F" >> output\jobs\runner.log 2>&1
    move /y "%%F" output\jobs\done\ >nul 2>&1
    echo ==== %date% %time%  完成 %%~nxF ==== >> output\jobs\runner.log
)

del /f /q output\jobs\.lock >nul 2>&1
