PowerShell -Command "Set-NetConnectionProfile -NetworkCategory Private"
PowerShell -Command "New-NetFirewallRule -DisplayName 'FastAPI' -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow"
