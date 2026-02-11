#!/bin/bash

# Configuration
API_URL="http://localhost:8000"
STRATEGY="AUTO"
DRY_RUN=true # Change to false for live trading

echo "$(date): Attempting to start Nifty Bot (Strategy: $STRATEGY, DryRun: $DRY_RUN)..."

# Send POST request to start bot
response=$(curl -s -X POST "$API_URL/api/start" \
     -H "Content-Type: application/json" \
     -d "{\"strategy\":\"$STRATEGY\", \"dry_run\":$DRY_RUN}")

if [[ $response == *"success"* ]]; then
    echo "✅ Bot started successfully!"
    echo "Response: $response"
else
    echo "❌ Failed to start bot."
    echo "Response: $response"
fi
