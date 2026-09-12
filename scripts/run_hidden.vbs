' run_hidden.vbs <要執行的檔>
' 以隱藏視窗執行，不會跳出 cmd 黑框。
' 工作排程器每 2 分鐘呼叫一次 job_runner，直接呼叫 .bat 會一直閃視窗。
If WScript.Arguments.Count = 0 Then WScript.Quit 1
Set sh = CreateObject("WScript.Shell")
sh.Run "cmd /c """ & WScript.Arguments(0) & """", 0, True
