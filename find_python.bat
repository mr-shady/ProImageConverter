@echo off
rem Finds a normal desktop Python installation.
rem Python environments belonging to Hermes/ChatGPT are intentionally ignored.

set "PYTHON_CMD="
set "HERMES_PYTHON="

rem 1) Prefer the official Windows Python Launcher.
where py >nul 2>nul
if not errorlevel 1 (
    for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do (
        echo %%P | findstr /i /c:"\hermes\hermes-agent\" >nul
        if errorlevel 1 (
            set "PYTHON_CMD=py -3"
            exit /b 0
        ) else (
            set "HERMES_PYTHON=%%P"
        )
    )
)

rem 2) Search regular per-user installations before PATH.
for /d %%D in ("%LocalAppData%\Programs\Python\Python*") do (
    if exist "%%~fD\python.exe" (
        echo %%~fD | findstr /i /c:"\hermes\hermes-agent\" >nul
        if errorlevel 1 set "PYTHON_CMD=%%~fD\python.exe"
    )
)
if defined PYTHON_CMD exit /b 0

rem 3) Search regular all-users installations.
for /d %%D in ("%ProgramFiles%\Python*") do (
    if exist "%%~fD\python.exe" set "PYTHON_CMD=%%~fD\python.exe"
)
if defined PYTHON_CMD exit /b 0

for /d %%D in ("%ProgramFiles(x86)%\Python*") do (
    if exist "%%~fD\python.exe" set "PYTHON_CMD=%%~fD\python.exe"
)
if defined PYTHON_CMD exit /b 0

rem 4) Check every python.exe visible in PATH, but reject Hermes.
for /f "delims=" %%P in ('where python 2^>nul') do (
    echo %%P | findstr /i /c:"\hermes\hermes-agent\" >nul
    if errorlevel 1 (
        if not defined PYTHON_CMD set "PYTHON_CMD=%%P"
    ) else (
        set "HERMES_PYTHON=%%P"
    )
)
if defined PYTHON_CMD exit /b 0

for /f "delims=" %%P in ('where python3 2^>nul') do (
    echo %%P | findstr /i /c:"\hermes\hermes-agent\" >nul
    if errorlevel 1 (
        if not defined PYTHON_CMD set "PYTHON_CMD=%%P"
    ) else (
        set "HERMES_PYTHON=%%P"
    )
)
if defined PYTHON_CMD exit /b 0

if defined HERMES_PYTHON exit /b 2
exit /b 1
