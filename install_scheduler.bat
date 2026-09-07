@echo off
chcp 65001 >nul
REM ── 一次性安裝:兩個 Windows 排程工作 ──────────────────────────
REM   1. stockfilter job runner   每 2 分鐘檢查 output\jobs\ 有沒有待辦工作
REM   2. stockfilter daily update 週一~週五 15:30 跑每日例行
REM 使用者層級工作，不需要管理員權限。
REM
REM 2026-09-07 修正:上一版 /tr 用了 \" 想包住路徑，但 cmd 不吃反斜線跳脫，
REM 結果存進排程的執行路徑帶著多餘的字元而找不到檔案 ——
REM 兩個工作都顯示 Ready 卻從來沒真正執行。路徑沒有空白，直接用一層引號即可。
cd /d D:\Project\stockfilter
set "LOG=output\install_scheduler.log"

echo === %date% %time% === > "%LOG%"

echo [0/3] 移除舊的（若存在）... >> "%LOG%"
schtasks /delete /tn "stockfilter job runner" /f >> "%LOG%" 2>&1
schtasks /delete /tn "stockfilter daily update" /f >> "%LOG%" 2>&1

echo. >> "%LOG%"
echo [1/3] 安裝工作執行器（每 2 分鐘）... >> "%LOG%"
schtasks /create /tn "stockfilter job runner" /tr "D:\Project\stockfilter\scripts\job_runner.bat" /sc minute /mo 2 /f >> "%LOG%" 2>&1

echo [2/3] 安裝每日例行（週一~週五 15:30）... >> "%LOG%"
schtasks /create /tn "stockfilter daily update" /tr "D:\Project\stockfilter\run_update.bat" /sc weekly /d MON,TUE,WED,THU,FRI /st 15:30 /f >> "%LOG%" 2>&1

echo. >> "%LOG%"
echo [3/3] 立刻執行一次執行器，確認真的會動... >> "%LOG%"
schtasks /run /tn "stockfilter job runner" >> "%LOG%" 2>&1
ping -n 20 127.0.0.1 >nul

echo. >> "%LOG%"
echo --- 執行結果（Last Result 0 = 成功）--- >> "%LOG%"
schtasks /query /tn "stockfilter job runner" /fo list /v | findstr /i "TaskName Task_To_Run Last_Run_Time Last_Result Next_Run_Time Status Scheduled_Task_State" >> "%LOG%" 2>&1
echo. >> "%LOG%"
schtasks /query /tn "stockfilter daily update" /fo list /v | findstr /i "TaskName Task_To_Run Last_Run_Time Last_Result Next_Run_Time Status Scheduled_Task_State" >> "%LOG%" 2>&1

echo. >> "%LOG%"
echo --- jobs 資料夾 --- >> "%LOG%"
dir /b output\jobs\*.bat >> "%LOG%" 2>&1
echo --- done 資料夾 --- >> "%LOG%"
dir /b output\jobs\done\*.bat >> "%LOG%" 2>&1

echo.
echo 安裝完成。詳見 output\install_scheduler.log
echo 如果 Last Result 是 0，而且 01_phase2_revenue.bat 已經移到 done，代表成功。
echo.
echo 要停用:
echo   schtasks /delete /tn "stockfilter job runner" /f
echo   schtasks /delete /tn "stockfilter daily update" /f
pause
