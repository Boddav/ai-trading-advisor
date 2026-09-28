@echo off
echo ========================================
echo  Jev elotoltes backtesthez
echo ========================================
echo Elotte: Zorro Test JEV_TEST_MODE 2-vel (kerdesek exportja)
echo.
python "%~dp0JevServer.py" --prefetch "%~dp0..\Data\JevExport.jsonl" --threads 8
echo.
echo Utana: JevTrader.c-ben JEV_TEST_MODE 1, start_jev.bat, Zorro Test.
pause
