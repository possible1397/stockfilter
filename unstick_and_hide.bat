@echo off
chcp 65001 >nul
REM ===================================================================
REM  unstick_and_hide.bat
REM
REM  用途兩個:
REM   1. 解開卡住的 job runner。2026-09-12 我用 schtasks /change 改工作，
REM      它跳出來要 Windows 密碼，在背景執行時等不到輸入就一直掛著，
REM      連帶把 runner 鎖住了。（我不該用 /change，改用 /create /f 就不用密碼。）
REM   2. 讓排程改用隱藏視窗執行，不要再每 2 分鐘閃一次 cmd 黑框。
REM
REM  請右鍵 -> 以系統管理員身分執行。全程不會要你輸入密碼。
REM ===================================================================
cd /d D:\Project\stockfilter

echo [1/4] 結束卡住的工作...
schtasks /end /tn "stockfilter job runner" >nul 2>&1
taskkill /f /im schtasks.exe >nul 2>&1
del /f /q output\jobs\.lock >nul 2>&1

echo [2/4] 改用隱藏視窗重建工作（/create /f 不需要密碼）...
schtasks /create /tn "stockfilter job runner" /tr "wscript.exe D:\Project\stockfilter\scripts\run_hidden.vbs D:\Project\stockfilter\scripts\job_runner.bat" /sc minute /mo 2 /rl highest /f
schtasks /create /tn "stockfilter daily update" /tr "wscript.exe D:\Project\stockfilter\scripts\run_hidden.vbs D:\Project\stockfilter\run_update.bat" /sc weekly /d MON,TUE,WED,THU,FRI /st 15:30 /rl highest /f

echo [3/4] 重新套用電池與重複設定...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; foreach($n in @('stockfilter job runner','stockfilter daily update')){ $t=Get-ScheduledTask -TaskName $n; $s=$t.Settings; $s.DisallowStartIfOnBatteries=$false; $s.StopIfGoingOnBatteries=$false; $s.ExecutionTimeLimit='PT2H'; $s.StartWhenAvailable=$true; $s.MultipleInstances=2; Set-ScheduledTask -TaskName $n -Settings $s | Out-Null; Write-Host ('  ok: '+$n) }; $t=Get-ScheduledTask -TaskName 'stockfilter job runner'; $tr=$t.Triggers[0]; $tr.Repetition.Interval='PT2M'; $tr.Repetition.Duration=''; Set-ScheduledTask -TaskName 'stockfilter job runner' -Trigger $tr | Out-Null; Write-Host '  ok: 每 2 分鐘、無期限'"

echo [4/4] 試跑一次（這次應該完全看不到視窗）...
schtasks /run /tn "stockfilter job runner" >nul 2>&1

echo.
echo 完成。從現在起不會再跳 cmd 視窗了。
echo 注意:執行時間上限已改成 2 小時，萬一再卡住會自己被結束掉。
echo.
pause
