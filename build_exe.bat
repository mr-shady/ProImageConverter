@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title Build Pro Image Converter EXE

call "%~dp0find_python.bat"
if errorlevel 2 goto hermes_only
if errorlevel 1 goto no_python

"%PYTHON_CMD%" -m pip install --upgrade pyinstaller
if errorlevel 1 goto build_error

"%PYTHON_CMD%" -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "ProImageConverter" ^
  --icon "assets\app_icon.ico" ^
  --add-data "assets;assets" ^
  --collect-all customtkinter ^
  app.py

if errorlevel 1 goto build_error

echo.
echo Build finished successfully.
echo The EXE file is inside the dist folder.
echo.
pause
exit /b 0

:hermes_only
echo.
echo Only the internal Hermes Python was found.
echo Install the official Python before building the EXE.
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

:build_error
echo.
echo EXE build failed. Review the messages above.
echo.
pause
exit /b 1
