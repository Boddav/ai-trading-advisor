@echo off
echo ========================================
echo  Jev teszt - egy probahivas
echo ========================================
python "%~dp0JevServer.py" --selftest
echo.
echo Fut-e a szerver (start_jev.bat)?
curl -s http://127.0.0.1:5003/health
echo.
echo (ha itt "status": "ok" latszik, a Zorro eleri a szervert)
echo.
pause
