import ctypes
script_path = "fix_network.bat"
with open(script_path, "w") as f:
    f.write("PowerShell -Command \"Set-NetConnectionProfile -NetworkCategory Private\"\n")
    f.write("PowerShell -Command \"New-NetFirewallRule -DisplayName 'FastAPI' -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow\"\n")
ctypes.windll.shell32.ShellExecuteW(None, "runas", script_path, "", "", 1)
