#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
#  DrainWatch — One-click local launcher (macOS / Linux)
#  Double-click this file or run: bash run.sh
# ──────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
echo -e "${CYAN}═══════════════════════════════════════════${NC}"
echo -e "${CYAN}  DrainWatch — Local Development Launcher  ${NC}"
echo -e "${CYAN}═══════════════════════════════════════════${NC}"
echo ""

# ── Check Python ──
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}✗ Python 3 is not installed. Install Python 3.11+ first.${NC}"
    exit 1
fi
PY=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo -e "${GREEN}✓${NC} Python ${PY}"

# ── Check Node ──
if ! command -v node &>/dev/null; then
    echo -e "${YELLOW}⚠ Node.js not found. Frontend dev server will not start.${NC}"
    HAS_NODE=false
else
    echo -e "${GREEN}✓${NC} Node $(node --version)"
    HAS_NODE=true
fi

# ── Virtual environment ──
if [ ! -d ".venv" ]; then
    echo -e "${YELLOW}→ Creating virtual environment...${NC}"
    python3 -m venv .venv
fi
source .venv/bin/activate
echo -e "${GREEN}✓${NC} Virtual environment activated"

# ── Install Python dependencies ──
if ! python -c "import fastapi" &>/dev/null 2>&1; then
    echo -e "${YELLOW}→ Installing Python dependencies...${NC}"
    pip install -e ".[api]" -q 2>/dev/null || pip install -e ".[api]"
fi
echo -e "${GREEN}✓${NC} Python dependencies ready"

# ── .env file ──
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo -e "${YELLOW}→ Created .env from .env.example. Edit it with your Supabase credentials.${NC}"
    else
        echo -e "${YELLOW}⚠ No .env file found. The app will use local storage.${NC}"
    fi
fi

# ── Frontend build ──
if [ "$HAS_NODE" = true ] && [ ! -d "frontend/dist" ]; then
    echo -e "${YELLOW}→ Building frontend...${NC}"
    (cd frontend && npm install --silent && npm run build --silent)
fi
if [ -d "frontend/dist" ]; then
    echo -e "${GREEN}✓${NC} Frontend built"
else
    echo -e "${YELLOW}⚠ Frontend not built. The API will work but no dashboard UI.${NC}"
fi

# ── Checkpoint check ──
if [ -f "checkpoints/best/model.safetensors" ]; then
    echo -e "${GREEN}✓${NC} Model checkpoint found"
else
    echo -e "${YELLOW}⚠ No model checkpoint at checkpoints/best/. Inference will return 503.${NC}"
    echo -e "  See README → Model Checkpoint Setup"
fi

echo ""
echo -e "${CYAN}Starting DrainWatch API server...${NC}"
echo -e "  Dashboard: ${GREEN}http://localhost:8000${NC}"
echo -e "  Health:    ${GREEN}http://localhost:8000/health${NC}"
echo -e "  Press Ctrl+C to stop."
echo ""

PYTHONPATH=src exec uvicorn drainwatch.api.main:app --host 0.0.0.0 --port 8000 --reload
