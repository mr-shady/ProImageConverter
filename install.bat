@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title Install Pro Image Converter

call "%~dp0find_python.bat"
if errorlevel 2 goto hermes_only
if errorlevel 1 goto no_python

echo Python found:
"%PYTHON_CMD%" --version
echo Executable:
"%PYTHON_CMD%" -c "import sys; print(sys.executable)"
echo.
echo Installing the required packages...
"%PYTHON_CMD%" -m ensurepip --upgrade >nul 2>nul
"%PYTHON_CMD%" -m pip install --upgrade pip
if errorlevel 1 goto install_error

"%PYTHON_CMD%" -m pip install -r requirements.txt
if errorlevel 1 goto install_error

echo.
echo ============================================================
echo Core installation completed successfully.
echo ============================================================
echo.
echo You can now run run.bat.
echo.
echo HEIC, SVG and camera RAW support are optional.
echo Use install_optional_formats.bat only if you need them.
echo.
pause
exit /b 0

:hermes_only
echo.
echo ============================================================
echo Only the internal Hermes Python was found.
echo ============================================================
echo.
echo Found:
echo %HERMES_PYTHON%
echo.
echo This Python belongs to another application and should not be used
echo for installing or running Pro Image Converter.
echo.
echo Install the official 64-bit Python 3.11 or newer from python.org.
echo During installation enable:
echo.
echo     Add python.exe to PATH
echo.
echo Then close this window and run install.bat again.
echo.
pause
exit /b 2

:no_python
echo.
echo Python was not found.
echo.
echo Install the official 64-bit Python 3.11 or newer from python.org.
echo During installation enable:
echo.
echo     Add python.exe to PATH
echo.
echo Then run install.bat again.
echo.
pause
exit /b 1

:install_error
echo.
echo Core package installation failed.
echo Review the error messages above.
echo.
pause
exit /b 1
