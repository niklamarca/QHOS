Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "cmd /c cd /d C:\Users\usuario\Claude\Projects\trader && python remote_monitor.py", 0, False
