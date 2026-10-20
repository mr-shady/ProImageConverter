@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title Pro Image Converter

call "%~dp0find_python.bat"
if errorlevel 2 goto hermes_only
if errorlevel 1 goto no_python

"%PYTHON_CMD%" app.py
if errorlevel 1 (
    echo.
    echo The application could not start.
    echo First run install.bat.
    echo.
    pause
)
exit /b 0

:hermes_only
echo.
echo Only the internal Hermes Python was found.
echo Install the official Python 3.11 or newer and run install.bat.
echo.
pause
exit /b 2

:no_python
echo.
echo Python was not found.
echo Install Python 3.11 or newer and enable "Add python.exe to PATH".
echo Then run install.bat.
echo.
pause
exit /b 1
