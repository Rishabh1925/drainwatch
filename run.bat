@echo off
REM ──────────────────────────────────────────────────────────────
REM  DrainWatch — One-click local launcher (Windows)
REM  Double-click this file to start DrainWatch locally.
REM ──────────────────────────────────────────────────────────────
title DrainWatch - Local Development
cd /d "%~dp0"

echo ═══════════════════════════════════════════
echo   DrainWatch — Local Development Launcher
echo ═══════════════════════════════════════════
echo.

REM ── Check Python ──
python --version >nul 2>&1
if errorlevel 1 (
    python3 --version >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Python 3 is not installed. Install Python 3.11+ first.
        pause
        exit /b 1
    )
    set PY=python3
) else (
    set PY=python
)
echo [OK] Python found

REM ── Check Node ──
set HAS_NODE=false
node --version >nul 2>&1
if not errorlevel 1 (
    echo [OK] Node.js found
    set HAS_NODE=true
) else (
    echo [WARN] Node.js not found. Frontend dev server will not start.
)

REM ── Virtual environment ──
if not exist ".venv" (
    echo [INFO] Creating virtual environment...
    %PY% -m venv .venv
)
call .venv\Scripts\activate.bat
echo [OK] Virtual environment activated

REM ── Install Python dependencies ──
%PY% -c "import fastapi" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing Python dependencies...
    pip install -e ".[api]" -q
)
echo [OK] Python dependencies ready

REM ── .env file ──
if not exist ".env" (
    if exist ".env.example" (
        copy .env.example .env >nul
        echo [INFO] Created .env from .env.example. Edit it with your Supabase credentials.
    ) else (
        echo [WARN] No .env file found. The app will use local storage.
    )
)

REM ── Frontend build ──
if "%HAS_NODE%"=="true" (
    if not exist "frontend\dist" (
        echo [INFO] Building frontend...
        cd frontend
        call npm install --silent
        call npm run build --silent
        cd ..
    )
)
if exist "frontend\dist" (
    echo [OK] Frontend built
) else (
    echo [WARN] Frontend not built. The API will work but no dashboard UI.
)

REM ── Checkpoint check ──
if exist "checkpoints\best\model.safetensors" (
    echo [OK] Model checkpoint found
) else (
    echo [WARN] No model checkpoint at checkpoints\best\. Inference will return 503.
    echo   See README - Model Checkpoint Setup
)

echo.
echo Starting DrainWatch API server...
echo   Dashboard: http://localhost:8000
echo   Health:    http://localhost:8000/health
echo   Press Ctrl+C to stop.
echo.

set PYTHONPATH=src
uvicorn drainwatch.api.main:app --host 0.0.0.0 --port 8000 --reload
pause
