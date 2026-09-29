@echo off
rem Jev szerver Kronos-szal. KRONOS_DIR = ahova a Kronos repot klonoztad.
rem MODE: context (Jev latja a Kronos elorejelzest) / agree (csak ha mindketto egyetert) / kronos_only
set KRONOS_DIR=C:\Kronos
set MODE=agree

echo [1/2] Killing old server on port 5003...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5003 ^| findstr LISTENING') do (
    taskkill /F /PID %%a 2>nul
)
timeout /t 2 /nobreak >nul

echo [2/2] Starting Jev + Kronos server (mode: %MODE%)...
start "JevServer+Kronos" python "%~dp0JevServer.py" --kronos "%KRONOS_DIR%" --kronos-mode %MODE%
echo Az elso inditas letolti a Kronos modellt (~100 MB), ez eltarthat par percig.
pause
