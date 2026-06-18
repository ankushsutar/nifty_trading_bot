# Nifty Trading Bot: Cloud Hosting & 9:15 AM Automation Guide

This guide explains how to host your bot on a Cloud VPS (AWS, DigitalOcean, GCP) so you can start and monitor it from anywhere.

## 1. Server Requirements

- **OS**: Ubuntu 22.04 LTS (Recommended)
- **Specs**: 2 vCPU, 4GB RAM (Minimum)
- **Tools**: Docker & Docker Compose

## 2. Infrastructure Setup (Quick Start)

### Step A: Install Docker

```bash
sudo apt update
sudo apt install docker.io docker-compose -y
```

### Step B: Clone & Configure

1. Clone your repository to the server.
2. Create/Update your `.env` file with your **Angel One Credentials** and **MongoDB URI**.

### Step C: Launch System

```bash
docker-compose up -d --build
```

This will start:

- **Backend API**: `http://YOUR_SERVER_IP:8000`
- **Dashboard**: `http://YOUR_SERVER_IP:3000`

---

## 3. Automated 9:15 AM Start (Cron)

To ensure your bot starts exactly at market open, use the built-in automation script.

### 1. Make script executable

```bash
chmod +x scripts/autostart.sh
```

### 2. Configure Cron

Open your crontab:

```bash
crontab -e
```

Add this line to start at **09:15 AM** every Monday-Friday:

```cron
15 9 * * 1-5 /home/ubuntu/nifty_trading_bot/scripts/autostart.sh >> /home/ubuntu/nifty_trading_bot/logs/cron.log 2>&1
```

---

## 4. Remote Access from Anywhere

You can now access your dashboard by typing your server's IP in any browser:
`http://YOUR_SERVER_IP:3000`

### Security Best Practices

- **Firewall**: Ensure ports 3000 and 8000 are open in your Cloud Provider's Security Group.
- **Nginx & SSL**: In a real production environment, use Nginx as a reverse proxy with an SSL certificate (Let's Encrypt) to secure the dashboard.

---

## 5. Persistence & Monitoring

- **Logs**: View live logs in the dashboard or via `docker-compose logs -f backend`.
- **Database**: All trades are saved to SQLite (local) and MongoDB (historical), ensuring you never lose data even if the server restarts.
