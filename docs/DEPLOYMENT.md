# Standard Operating Procedure (SOP): Nifty Trading Bot Cloud Deployment

This guide outlines the step-by-step procedure for deploying the Nifty Trading Bot on a remote cloud VPS (AWS, DigitalOcean, GCP, Azure, etc.) using Docker and Docker Compose.

---

## 1. Prerequisites & Server Requirements

* **Operating System**: Ubuntu 22.04 LTS or newer (Recommended)
* **Hardware Specs**:
  * Minimum: 1 vCPU, 2GB RAM
  * Recommended: 2 vCPU, 4GB RAM (to handle real-time WebSockets and multi-leg strategies smoothly)
* **Docker Engine & Compose**: Installed on the host VM.

### Inbound Firewall Settings (Security Group)
Ensure the following ports are allowed in your cloud provider's firewall configuration:
* **Port 22**: SSH Access (Restrict to your IP for safety)
* **Port 3000**: Next.js Dashboard UI (Public access or VPN-restricted)
* **Port 8000**: FastAPI Backend Server (Needs to be accessible by client browsers)

---

## 2. Server Installation Steps

### Step 1: Install Docker & Docker Compose
Log into your cloud server via SSH and run:
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y docker.io docker-compose
sudo systemctl enable --now docker
```

### Step 2: Clone and Navigate to the Repository
Clone the codebase and navigate to the project directory:
```bash
git clone <your-repository-url> nifty_trading_bot
cd nifty_trading_bot
```

### Step 3: Configure Environment Variables (`.env`)
Create the production `.env` file from the example template:
```bash
cp .env.example .env
nano .env
```

Set the following variables exactly for the remote environment:
* **BROKER**: `ANGEL` or `ZERODHA` (corresponding API details completed).
* **MONGO_URI**: Set to `mongodb://mongodb:27017/` (This points to the secure Docker-internal MongoDB service).
* **NEXT_PUBLIC_API_URL**: Set to `http://<YOUR_SERVER_PUBLIC_IP>:8000` (e.g. `http://54.210.12.34:8000`). This is critical because client-side calls in Next.js execute in the user's local browser.
* **LIVE_TRADE_ENABLED**: Keep `FALSE` for paper trading (simulation) validation, toggle to `TRUE` for live capital.

---

## 3. Deployment & Execution

### Step 1: Build and Launch the Containers
Compile the Next.js assets with the configured API URL and spin up the database and backend:
```bash
docker-compose up -d --build
```

### Step 2: Verify Service Health
Check that all three containers (`nifty_mongodb`, `backend`, and `frontend`) are up and running:
```bash
docker-compose ps
```

Verify that the dashboard is accessible in your browser at `http://<YOUR_SERVER_PUBLIC_IP>:3000`.

---

## 4. Automated Daily Execution Setup (9:15 AM Automation)

To automate the daily startup of the bot's trade decision loops exactly at market open, we configure a system cron job on the host.

### Step 1: Configure host execution privileges
```bash
chmod +x scripts/start_bot.sh scripts/autostart.sh scripts/autostop.sh
```

### Step 2: Setup Cron Scheduling
Edit the crontab:
```bash
crontab -e
```

Add the following rules to autostart the bot at **9:15 AM IST** and force-close/reconcile after market hours at **3:35 PM IST** (Monday through Friday):
```cron
# Autostart the bot lifecycle manager at market open (9:15 AM)
15 9 * * 1-5 docker-compose exec -d backend python3 -m bot.lifecycle_manager --dry-run >> /home/ubuntu/nifty_trading_bot/logs/cron.log 2>&1

# Stop the bot lifecycle manager and run final settlements (3:35 PM)
35 15 * * 1-5 docker-compose exec -d backend python3 -m bot.lifecycle_manager --stop >> /home/ubuntu/nifty_trading_bot/logs/cron.log 2>&1
```

> [!NOTE]
> Adjust `/home/ubuntu/nifty_trading_bot/` paths inside the crontab if you cloned the project to a different directory.

---

## 5. Operations & Logs Monitoring

### Read Real-time Logs
Monitor backend execution, WebSocket feed status, and active strategy decisions:
```bash
docker-compose logs -f backend
```

Monitor Next.js UI container rendering logs:
```bash
docker-compose logs -f frontend
```

### Database Access (MongoDB Shell)
To inspect raw trades inside the dockerized MongoDB container:
```bash
docker exec -it nifty_mongodb mongosh nifty_bot --eval "db.trades.find().pretty()"
```
