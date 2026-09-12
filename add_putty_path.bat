@echo off
setlocal enabledelayedexpansion
for /f "usebackq tokens=2,*" %%A in (`reg query HKCU\Environment /v Path 2^>nul`) do set "CURPATH=%%B"
echo CURRENT=!CURPATH!
echo !CURPATH! | find /i "PuTTY" >nul
if !errorlevel! == 0 (
    echo RESULT=ALREADY_PRESENT
    goto :eof
)
if defined CURPATH (
    set "NEWPATH=!CURPATH!;C:\Program Files\PuTTY"
) else (
    set "NEWPATH=C:\Program Files\PuTTY"
)
setx Path "!NEWPATH!" >nul
echo RESULT=DONE
