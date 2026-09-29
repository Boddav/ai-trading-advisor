@echo off
echo ========================================
echo  Jev Server (port 5003)
echo ========================================
echo.

echo [1/2] Killing old server on port 5003...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5003 ^| findstr LISTENING') do (
    taskkill /F /PID %%a 2>nul
)
timeout /t 2 /nobreak >nul

echo [2/2] Starting Jev server...
start "JevServer" python "%~dp0JevServer.py"
timeout /t 3 /nobreak >nul

echo.
echo Done! Jev server running on localhost:5003
echo   POST /decide  -> open_long / open_short / close / hold
echo   GET  /health  -> status
echo.
pause
