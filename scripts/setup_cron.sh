#!/bin/bash
# setup_cron.sh: Configures the crontab for automatic trading hours

AUTOSTART="/home/cwd/ankush/agent/nifty_trading_bot/scripts/autostart.sh"
AUTOSTOP="/home/cwd/ankush/agent/nifty_trading_bot/scripts/autostop.sh"

# Ensure scripts are executable
chmod +x $AUTOSTART
chmod +x $AUTOSTOP

# Create temporary crontab file
crontab -l > mycron 2>/dev/null

# Add Start Job (09:10 AM Monday-Friday)
sed -i '/autostart.sh/d' mycron
echo "10 09 * * 1-5 /bin/bash $AUTOSTART" >> mycron

# Add Stop Job (15:35 PM Monday-Friday)
sed -i '/autostop.sh/d' mycron
echo "35 15 * * 1-5 /bin/bash $AUTOSTOP" >> mycron

# Install new crontab
crontab mycron
rm mycron

echo "✅ Crontab updated successfully!"
echo "🚀 Auto-Start: 09:10 AM (Mon-Fri)"
echo "🛑 Auto-Stop:  03:35 PM (Mon-Fri)"
