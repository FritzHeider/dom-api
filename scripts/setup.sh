#!/usr/bin/env bash
###############################################################################
# ChatGPT DOM Agent Bridge - Setup Script
#
# Full setup script that:
# - Checks Python version (require 3.11+)
# - Checks if Redis is running
# - Creates virtual environment
# - Installs dependencies
# - Sets up Playwright
# - Creates necessary directories
# - Prints success message with next steps
###############################################################################

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}ChatGPT DOM Agent Bridge Setup${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo -e "${BLUE}Project directory: ${PROJECT_DIR}${NC}"
echo ""

# Check Python version
echo -e "${BLUE}Checking Python version...${NC}"
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: Python3 is not installed${NC}"
    exit 1
fi

PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)

if [ "$PYTHON_MAJOR" -lt 3 ] || [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 11 ]; then
    echo -e "${RED}Error: Python 3.11+ is required, but found $PYTHON_VERSION${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Python $PYTHON_VERSION found${NC}"
echo ""

# Check Redis connection
echo -e "${BLUE}Checking Redis connection...${NC}"
if command -v redis-cli &> /dev/null; then
    if redis-cli ping > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Redis is running${NC}"
    else
        echo -e "${YELLOW}⚠ Redis is not running${NC}"
        echo -e "${YELLOW}Start Redis with: redis-server${NC}"
    fi
else
    echo -e "${YELLOW}⚠ redis-cli not found${NC}"
    echo -e "${YELLOW}Install Redis or ensure it's in PATH${NC}"
fi
echo ""

# Create virtual environment if it doesn't exist
echo -e "${BLUE}Setting up Python virtual environment...${NC}"
if [ ! -d "$PROJECT_DIR/venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$PROJECT_DIR/venv"
    echo -e "${GREEN}✓ Virtual environment created${NC}"
else
    echo -e "${GREEN}✓ Virtual environment already exists${NC}"
fi
echo ""

# Activate virtual environment
echo -e "${BLUE}Activating virtual environment...${NC}"
source "$PROJECT_DIR/venv/bin/activate"
echo -e "${GREEN}✓ Virtual environment activated${NC}"
echo ""

# Install dependencies
echo -e "${BLUE}Installing Python dependencies...${NC}"
if [ -f "$PROJECT_DIR/requirements.txt" ]; then
    pip install --upgrade pip setuptools wheel > /dev/null 2>&1
    pip install -r "$PROJECT_DIR/requirements.txt"
    echo -e "${GREEN}✓ Dependencies installed${NC}"
else
    echo -e "${YELLOW}⚠ requirements.txt not found at $PROJECT_DIR/requirements.txt${NC}"
    echo -e "${YELLOW}Creating minimal requirements.txt...${NC}"
    cat > "$PROJECT_DIR/requirements.txt" << 'EOF'
fastapi==0.104.1
uvicorn==0.24.0
playwright==1.40.0
redis==5.0.1
pytest==7.4.3
pytest-asyncio==0.21.1
pytest-mock==3.12.0
httpx==0.25.2
fakeredis==2.20.0
pydantic==2.5.0
websockets==12.0
aiofiles==23.2.1
EOF
    pip install --upgrade pip setuptools wheel > /dev/null 2>&1
    pip install -r "$PROJECT_DIR/requirements.txt"
    echo -e "${GREEN}✓ Dependencies installed${NC}"
fi
echo ""

# Install Playwright browsers
echo -e "${BLUE}Installing Playwright browsers...${NC}"
playwright install chromium
echo -e "${GREEN}✓ Chromium browser installed${NC}"
echo ""

# Install Playwright system dependencies
echo -e "${BLUE}Installing Playwright system dependencies...${NC}"
playwright install-deps chromium
echo -e "${GREEN}✓ System dependencies installed${NC}"
echo ""

# Create .env file if it doesn't exist
echo -e "${BLUE}Setting up environment configuration...${NC}"
if [ ! -f "$PROJECT_DIR/.env" ]; then
    if [ -f "$PROJECT_DIR/.env.example" ]; then
        cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
        echo -e "${GREEN}✓ Created .env from .env.example${NC}"
    else
        cat > "$PROJECT_DIR/.env" << 'EOF'
# ChatGPT DOM Agent Bridge Configuration

# API Settings
API_HOST=0.0.0.0
API_PORT=8000
API_LOG_LEVEL=INFO

# Browser Settings
BROWSER_HEADLESS=true
BROWSER_SLOW_MO_MS=0
BROWSER_TIMEOUT_MS=30000

# Session Management
SESSION_POOL_MAX=5
SESSION_IDLE_TIMEOUT_S=300
SESSION_RESTART_ON_ERROR=true

# Response Processing
RESPONSE_WAIT_TIMEOUT_S=120
MAX_RETRIES=3

# Redis Configuration
REDIS_URL=redis://localhost:6379/0

# ChatGPT Credentials
CHATGPT_EMAIL=
CHATGPT_PASSWORD=

# Logging
LOG_LEVEL=INFO
LOG_FILE=logs/bridge.log

# Debug
DEBUG_MODE=false
EOF
        echo -e "${GREEN}✓ Created new .env file${NC}"
        echo -e "${YELLOW}⚠ Please edit .env and add your ChatGPT credentials${NC}"
    fi
else
    echo -e "${GREEN}✓ .env already exists${NC}"
fi
echo ""

# Create directories
echo -e "${BLUE}Creating necessary directories...${NC}"

for dir in browser_profiles logs screenshots; do
    if [ ! -d "$PROJECT_DIR/$dir" ]; then
        mkdir -p "$PROJECT_DIR/$dir"
        echo -e "${GREEN}✓ Created $PROJECT_DIR/$dir${NC}"
    else
        echo -e "${GREEN}✓ $PROJECT_DIR/$dir already exists${NC}"
    fi
done
echo ""

# Create __init__.py files for bridge package
echo -e "${BLUE}Setting up Python package structure...${NC}"
if [ ! -f "$PROJECT_DIR/bridge/__init__.py" ]; then
    touch "$PROJECT_DIR/bridge/__init__.py"
fi
if [ ! -f "$PROJECT_DIR/tests/__init__.py" ]; then
    touch "$PROJECT_DIR/tests/__init__.py"
fi
echo -e "${GREEN}✓ Package structure ready${NC}"
echo ""

# Final summary
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}Setup completed successfully!${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "${BLUE}Next steps:${NC}"
echo ""
echo "1. ${YELLOW}Edit configuration:${NC}"
echo "   ${BLUE}vim $PROJECT_DIR/.env${NC}"
echo "   Add your ChatGPT email and password"
echo ""
echo "2. ${YELLOW}Ensure Redis is running:${NC}"
echo "   ${BLUE}redis-server${NC}"
echo "   Or with Docker:"
echo "   ${BLUE}docker run -d -p 6379:6379 redis:7-alpine${NC}"
echo ""
echo "3. ${YELLOW}Run the server:${NC}"
echo "   ${BLUE}$SCRIPT_DIR/start.sh${NC}"
echo "   Or manually:"
echo "   ${BLUE}source $PROJECT_DIR/venv/bin/activate${NC}"
echo "   ${BLUE}python -m uvicorn bridge.main:app --host 0.0.0.0 --port 8000${NC}"
echo ""
echo "4. ${YELLOW}Run tests:${NC}"
echo "   ${BLUE}pytest tests/ -v${NC}"
echo ""
echo "5. ${YELLOW}View API documentation:${NC}"
echo "   ${BLUE}http://localhost:8000/docs${NC}"
echo ""
echo -e "${BLUE}========================================${NC}"
