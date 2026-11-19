@echo off
setlocal
cd /d "%~dp0"
echo.
echo  Liquid Glass Sensors - installation
echo  -----------------------------------
echo  Python environment, drivers and autostart will be set up.
echo  Windows will ask for administrator rights.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 (
    echo.
    echo Installation failed. Read the messages above.
    pause
)
endlocal
