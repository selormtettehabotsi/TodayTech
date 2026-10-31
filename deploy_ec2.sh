#!/bin/bash
set -e

echo "====================================================="
echo "   TodayTech AWS EC2 Automated Deployment Setup"
echo "====================================================="

# 1. Install Docker & prerequisites if missing
if ! command -v docker &> /dev/null; then
    echo "📦 Installing Docker and Docker Compose..."
    sudo apt-get update -y
    sudo apt-get install -y ca-certificates curl gnupg lsb-release git
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
      $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo apt-get update -y
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    sudo usermod -aG docker $USER || true
    echo "✅ Docker installed."
fi

# 2. Check profile.yaml
if [ ! -f profile.yaml ]; then
    echo "📄 Creating profile.yaml from template..."
    cp profile.example.yaml profile.yaml
fi

# 3. Check .env
if [ ! -f .env ]; then
    echo "⚠️ .env file not found! Copying from .env.example..."
    cp .env.example .env
    echo ""
    echo "👉 Please edit .env with your real API keys: nano .env"
    echo "   Then re-run: bash deploy_ec2.sh"
    exit 1
fi

# 4. Create data folder
mkdir -p data data/drafts

# 5. Build and run containers
echo "🔨 Building and launching TodayTech containers in background..."
docker compose up -d --build

# 6. Display IP and status
PUBLIC_IP=$(curl -s https://ifconfig.me || echo "your-ec2-ip")

echo ""
echo "====================================================="
echo "   TodayTech is LIVE on your AWS EC2 Instance!"
echo "====================================================="
echo ""
echo "🌐 Web Dashboard: http://${PUBLIC_IP}:8000"
echo "🤖 Telegram Bot: Active & listening 24/7"
echo ""
echo "Helpful commands:"
echo "  • View live logs:    docker compose logs -f"
echo "  • Restart service:  docker compose restart"
echo "  • Stop service:     docker compose down"
echo "====================================================="
