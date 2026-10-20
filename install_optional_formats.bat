@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title Install Optional Image Formats

call "%~dp0find_python.bat"
if errorlevel 2 goto hermes_only
if errorlevel 1 goto no_python

echo.
echo Installing HEIC / HEIF support from the official PyPI index...
"%PYTHON_CMD%" -m pip install --index-url https://pypi.org/simple -r requirements-heic.txt
if errorlevel 1 (
    echo.
    echo HEIC installation was skipped or failed.
    echo The main application still works without HEIC support.
    echo.
) else (
    echo HEIC / HEIF support installed successfully.
)

echo.
echo Installing optional SVG and camera RAW support...
"%PYTHON_CMD%" -m pip install -r requirements-optional.txt
if errorlevel 1 (
    echo.
    echo One or more optional packages could not be installed.
    echo The main application still works normally.
) else (
    echo SVG and camera RAW support installed successfully.
)

echo.
pause
exit /b 0

:hermes_only
echo.
echo Only the internal Hermes Python was found.
echo Install the official Python first, then run install.bat.
echo.
pause
exit /b 2

:no_python
echo.
echo Python was not found.
echo Install Python 3.11 or newer and enable "Add python.exe to PATH".
echo.
pause
exit /b 1
