#!/bin/bash

set -e

echo "🚀 Deploying Solana Arbitrage Bot"
echo "================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Check prerequisites
echo -e "${YELLOW}Checking prerequisites...${NC}"

command -v docker >/dev/null 2>&1 || { 
    echo -e "${RED}❌ Docker is required but not installed.${NC}" >&2
    exit 1
}

command -v docker-compose >/dev/null 2>&1 || { 
    echo -e "${RED}❌ Docker Compose is required but not installed.${NC}" >&2
    exit 1
}

# Check for environment file
if [ ! -f .env ]; then
    echo -e "${YELLOW}⚠️  .env file not found! Creating from template...${NC}"
    cp .env.example .env
    echo -e "${RED}Please edit .env and add your SOLANA_PRIVATE_KEY${NC}"
    exit 1
fi

# Validate environment variables
source .env
if [ -z "$SOLANA_PRIVATE_KEY" ]; then
    echo -e "${RED}❌ SOLANA_PRIVATE_KEY not set in .env file${NC}"
    exit 1
fi

# Create necessary directories
echo -e "${YELLOW}Creating directories...${NC}"
mkdir -p data logs monitoring/grafana/{dashboards,datasources}

# Build images
echo -e "${YELLOW}Building Docker images...${NC}"
docker-compose -f docker/docker-compose.yml build

# Start services
echo -e "${YELLOW}Starting services...${NC}"
docker-compose -f docker/docker-compose.yml up -d

# Wait for services to be healthy
echo -e "${YELLOW}Waiting for services to start...${NC}"
sleep 10

# Check service status
docker-compose -f docker/docker-compose.yml ps

# Display access information
echo -e "${GREEN}✅ Deployment complete!${NC}"
echo ""
echo "📊 Access points:"
echo "   - Bot Metrics: http://localhost:8000/metrics"
echo "   - Prometheus: http://localhost:9090"
echo "   - Grafana: http://localhost:3000 (admin/admin)"
echo ""
echo "📝 Useful commands:"
echo "   - View logs: docker-compose -f docker/docker-compose.yml logs -f arbitrage-bot"
echo "   - Stop bot: docker-compose -f docker/docker-compose.yml down"
echo "   - Restart bot: docker-compose -f docker/docker-compose.yml restart arbitrage-bot"
echo ""
echo -e "${YELLOW}⚠️  Remember to change the Grafana admin password!${NC}"