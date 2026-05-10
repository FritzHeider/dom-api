#!/usr/bin/env bash
###############################################################################
# ChatGPT DOM Agent Bridge - Start Script
#
# Starts the FastAPI server with proper:
# - Environment variable loading
# - Virtual environment activation
# - Redis connection verification
# - Graceful shutdown handling
###############################################################################

set -e

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}ChatGPT DOM Agent Bridge${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Check if .env exists
if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo -e "${RED}Error: .env file not found${NC}"
    echo -e "${YELLOW}Run $SCRIPT_DIR/setup.sh first${NC}"
    exit 1
fi

# Source .env file
echo -e "${BLUE}Loading environment configuration...${NC}"
source "$PROJECT_DIR/.env"
echo -e "${GREEN}✓ Environment loaded${NC}"
echo ""

# Check if virtual environment exists
if [ ! -d "$PROJECT_DIR/venv" ]; then
    echo -e "${RED}Error: Virtual environment not found${NC}"
    echo -e "${YELLOW}Run $SCRIPT_DIR/setup.sh first${NC}"
    exit 1
fi

# Activate virtual environment
echo -e "${BLUE}Activating virtual environment...${NC}"
source "$PROJECT_DIR/venv/bin/activate"
echo -e "${GREEN}✓ Virtual environment activated${NC}"
echo ""

# Check Redis connection
echo -e "${BLUE}Checking Redis connection...${NC}"
REDIS_HOST=$(echo "$REDIS_URL" | sed -E 's|redis://([^:]+).*|\1|')
REDIS_PORT=$(echo "$REDIS_URL" | sed -E 's|.*:([0-9]+).*|\1|')

if command -v redis-cli &> /dev/null; then
    if redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" ping > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Redis connection successful${NC}"
    else
        echo -e "${RED}Error: Cannot connect to Redis at $REDIS_HOST:$REDIS_PORT${NC}"
        echo -e "${YELLOW}Make sure Redis is running:${NC}"
        echo -e "${YELLOW}  redis-server${NC}"
        echo -e "${YELLOW}  or: docker run -d -p 6379:6379 redis:7-alpine${NC}"
        exit 1
    fi
else
    echo -e "${YELLOW}⚠ redis-cli not found, skipping Redis check${NC}"
fi
echo ""

# Create logs directory if it doesn't exist
if [ ! -d "$PROJECT_DIR/logs" ]; then
    mkdir -p "$PROJECT_DIR/logs"
fi

# Log file
LOG_FILE="${LOG_FILE:-$PROJECT_DIR/logs/bridge.log}"
echo -e "${BLUE}Logging to: $LOG_FILE${NC}"
echo ""

# Trap signals for graceful shutdown
trap 'echo -e "\n${YELLOW}Shutting down gracefully...${NC}"; exit 0' SIGINT SIGTERM

# Print startup information
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}Starting ChatGPT DOM Agent Bridge${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "${BLUE}Configuration:${NC}"
echo "  API Host: ${API_HOST:-0.0.0.0}"
echo "  API Port: ${API_PORT:-8000}"
echo "  Log Level: ${LOG_LEVEL:-INFO}"
echo "  Debug Mode: ${DEBUG_MODE:-false}"
echo "  Headless: ${BROWSER_HEADLESS:-true}"
echo "  Redis URL: $REDIS_URL"
echo ""

echo -e "${BLUE}Starting server...${NC}"
echo -e "${BLUE}----------------------------------------${NC}"
echo ""

# Start the server
export PYTHONUNBUFFERED=1
export LOG_FILE="$LOG_FILE"
export REDIS_URL="$REDIS_URL"
export API_HOST="${API_HOST:-0.0.0.0}"
export API_PORT="${API_PORT:-8000}"
export API_LOG_LEVEL="${API_LOG_LEVEL:-info}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"
export DEBUG_MODE="${DEBUG_MODE:-false}"
export BROWSER_HEADLESS="${BROWSER_HEADLESS:-true}"
export SESSION_POOL_MAX="${SESSION_POOL_MAX:-5}"
export RESPONSE_WAIT_TIMEOUT_S="${RESPONSE_WAIT_TIMEOUT_S:-120}"

# Validate API_PORT is numeric
if ! [[ "$API_PORT" =~ ^[0-9]+$ ]]; then
    echo -e "${RED}Error: API_PORT must be numeric, got: $API_PORT${NC}"
    exit 1
fi

# Change to project directory
cd "$PROJECT_DIR"

# Use the venv interpreter explicitly
VENV_PYTHON="$PROJECT_DIR/venv/bin/python3.11"
if [ ! -x "$VENV_PYTHON" ]; then
    VENV_PYTHON="$PROJECT_DIR/venv/bin/python3"
fi
if [ ! -x "$VENV_PYTHON" ]; then
    echo -e "${RED}Error: venv python not found${NC}"
    exit 1
fi

"$VENV_PYTHON" -m uvicorn bridge.main:app \
    --host "$API_HOST" \
    --port "$API_PORT" \
    --log-level "$API_LOG_LEVEL" \
    --reload-delay 1.0 \
    2>&1 | tee -a "$LOG_FILE"