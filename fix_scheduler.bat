@echo off
chcp 65001 >nul
REM ===================================================================
REM  fix_scheduler.bat  —  診斷並修好 stockfilter 的 Windows 排程
REM
REM  背景:2026-09-12 發現 job runner 只是偶爾執行，不是每 2 分鐘。
REM  最常見的三個原因（本檔全部一次處理掉）:
REM    1. 筆電用電池時，工作排程器預設「不啟動」而且「切到電池就停止」
REM    2. /sc minute /mo 2 建立的重複排程有預設期限，到期就不再重複
REM    3. 前一次執行卡住，而「不允許同時執行第二個實例」導致之後全部跳過
REM
REM  請用滑鼠右鍵 -> 以系統管理員身分執行。
REM ===================================================================
cd /d D:\Project\stockfilter
set DIAG=output\scheduler_diag.txt
echo ==== %date% %time% ==== > %DIAG%

echo [1/5] 先記錄目前的設定（修改前）...
echo ---- BEFORE ---- >> %DIAG%
schtasks /query /tn "stockfilter job runner" /fo list /v >> %DIAG% 2>&1
schtasks /query /tn "stockfilter daily update" /fo list /v >> %DIAG% 2>&1

echo [2/5] 重建工作...
schtasks /delete /tn "stockfilter job runner" /f >nul 2>&1
schtasks /delete /tn "stockfilter daily update" /f >nul 2>&1
schtasks /create /tn "stockfilter job runner" /tr "D:\Project\stockfilter\scripts\job_runner.bat" /sc minute /mo 2 /rl highest /f
schtasks /create /tn "stockfilter daily update" /tr "D:\Project\stockfilter\run_update.bat" /sc weekly /d MON,TUE,WED,THU,FRI /st 15:30 /rl highest /f

echo [3/5] 用 PowerShell 修掉電池限制與重複期限...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; foreach($n in @('stockfilter job runner','stockfilter daily update')){ $t=Get-ScheduledTask -TaskName $n; $s=$t.Settings; $s.DisallowStartIfOnBatteries=$false; $s.StopIfGoingOnBatteries=$false; $s.ExecutionTimeLimit='PT0S'; $s.StartWhenAvailable=$true; $s.MultipleInstances=2; Set-ScheduledTask -TaskName $n -Settings $s | Out-Null; Write-Host ('  fixed: '+$n) }; $t=Get-ScheduledTask -TaskName 'stockfilter job runner'; $tr=$t.Triggers[0]; $tr.Repetition.Interval='PT2M'; $tr.Repetition.Duration=''; Set-ScheduledTask -TaskName 'stockfilter job runner' -Trigger $tr | Out-Null; Write-Host '  repetition: every 2 min, no expiry'"

echo [4/5] 立刻試跑一次...
schtasks /run /tn "stockfilter job runner"
timeout /t 15 /nobreak >nul

echo [5/5] 記錄修改後的設定...
echo ---- AFTER ---- >> %DIAG%
schtasks /query /tn "stockfilter job runner" /fo list /v >> %DIAG% 2>&1
schtasks /query /tn "stockfilter daily update" /fo list /v >> %DIAG% 2>&1

echo.
echo 完成。診斷檔已寫到 output\scheduler_diag.txt
echo 接下來 5 分鐘內如果 output\jobs\probe_result.txt 出現，就代表修好了。
echo.
pause
