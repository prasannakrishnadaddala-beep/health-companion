@echo off
title Health Companion - Local Server
color 0A

echo.
echo ============================================================
echo    💊  HEALTH COMPANION - Personal AI Health Assistant
echo ============================================================
echo.

REM ── Check Python ─────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH.
    echo.
    echo Please install Python 3.8+ from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [OK] Python %PYVER% found.

REM ── Set API Key ───────────────────────────────────────────────
if "%ANTHROPIC_API_KEY%"=="" (
    if exist ".env" (
        for /f "tokens=1,2 delims==" %%a in (.env) do (
            if "%%a"=="ANTHROPIC_API_KEY" set ANTHROPIC_API_KEY=%%b
        )
    )
)

if "%ANTHROPIC_API_KEY%"=="" (
    echo.
    echo [SETUP] Anthropic API Key not found.
    echo.
    echo You need an API key from https://console.anthropic.com/
    echo.
    set /p ANTHROPIC_API_KEY="Paste your API key here: "
    echo.
    echo ANTHROPIC_API_KEY=%ANTHROPIC_API_KEY%> .env
    echo [OK] API key saved to .env file for future use.
)

echo [OK] API Key loaded.

REM ── Install dependencies ──────────────────────────────────────
echo.
echo [SETUP] Installing required packages...
pip install flask anthropic werkzeug --quiet --upgrade
if errorlevel 1 (
    echo [ERROR] Failed to install packages. Check your internet connection.
    pause
    exit /b 1
)
echo [OK] All packages installed successfully.

REM ── Create uploads folder ─────────────────────────────────────
if not exist "uploads" mkdir uploads

REM ── Launch app ────────────────────────────────────────────────
echo.
echo ============================================================
echo    Starting server... Opening browser in 2 seconds...
echo ============================================================
echo.
echo    URL:  http://127.0.0.1:5000
echo    Stop: Press Ctrl+C in this window
echo.

REM Open browser after 2 second delay
start "" cmd /c "timeout /t 2 >nul && start http://127.0.0.1:5000"

REM Run Flask app
python app.py

echo.
echo [INFO] Server stopped.
pause
