#!/usr/bin/env python3
"""
telegram_control.py - Bridge script for controlling Nifty Trading Bot
Designed for remote invocation via Hermes Agent / Telegram.
"""

import sys
import os
import subprocess
import time
from pathlib import Path

BOT_DIR = Path("/home/cwd/ankush/agent/nifty_trading_bot")
LOG_FILE = BOT_DIR / "logs" / "system.log"
BOT_LOG = BOT_DIR / "bot.log"
TRADING_LOG = BOT_DIR / "logs" / "trading_bot.log"


def get_pids(pattern: str) -> list[int]:
    """Find process IDs matching a pattern."""
    try:
        output = subprocess.check_output(["pgrep", "-f", pattern], text=True)
        return [int(pid) for pid in output.strip().split() if pid]
    except subprocess.CalledProcessError:
        return []


def is_port_in_use(port: int) -> bool:
    """Check if a port is in use."""
    try:
        res = subprocess.run(["fuser", f"{port}/tcp"], capture_output=True, text=True)
        return res.returncode == 0
    except Exception:
        return False


def get_system_status() -> dict:
    """Get active status of backend, bot, and frontend."""
    backend_pids = get_pids("uvicorn backend.server:app")
    bot_pids = get_pids("bot\\.lifecycle_manager") or get_pids("bot\\.main")
    frontend_pids = get_pids("npm run dev") or get_pids("node.*vite")

    return {
        "backend": backend_pids,
        "bot": bot_pids,
        "frontend": frontend_pids,
        "port_8000": is_port_in_use(8000),
        "port_3000": is_port_in_use(3000),
        "stop_signal": (BOT_DIR / ".stop_signal").exists(),
    }


def cmd_start() -> str:
    """Start the trading bot system in the background."""
    status = get_system_status()
    if status["backend"] or status["bot"]:
        b_str = ", ".join(map(str, status["backend"])) or "N/A"
        bot_str = ", ".join(map(str, status["bot"])) or "N/A"
        return f"⚠️ Trading Bot is already running!\n• Backend PID: {b_str}\n• Bot PID: {bot_str}\nSend `/nifty-bot status` to check details."

    bg_script = BOT_DIR / "run_bg.sh"
    if not bg_script.exists():
        return "❌ Error: run_bg.sh not found."

    res = subprocess.run(
        ["bash", str(bg_script)],
        cwd=BOT_DIR,
        capture_output=True,
        text=True,
        timeout=30,
    )

    output = (res.stdout.strip() + "\n" + res.stderr.strip()).strip()
    return output or "🚀 Launch initiated. Check `/nifty-bot status` in a few moments."


def cmd_stop() -> str:
    """Stop the trading bot system."""
    stop_script = BOT_DIR / "stop.sh"
    if not stop_script.exists():
        return "❌ Error: stop.sh not found."

    res = subprocess.run(
        ["bash", str(stop_script)],
        cwd=BOT_DIR,
        capture_output=True,
        text=True,
        timeout=30,
    )

    time.sleep(1)
    status = get_system_status()
    if not status["backend"] and not status["bot"]:
        return "🛑 Nifty Trading Bot stopped successfully.\nAll backend and bot processes terminated."
    else:
        return f"⚠️ Stop executed, but some processes may still be lingering:\nBackend: {status['backend']}, Bot: {status['bot']}"


def cmd_status() -> str:
    """Return status of all components."""
    status = get_system_status()

    backend_str = "🟢 Running" if status["backend"] else "🔴 Stopped"
    if status["backend"]:
        backend_str += f" (PID: {status['backend'][0]})"

    bot_str = "🟢 Running" if status["bot"] else "🔴 Stopped"
    if status["bot"]:
        bot_str += f" (PID: {status['bot'][0]})"

    frontend_str = "🟢 Running" if status["port_3000"] else "🔴 Inactive"

    lines = [
        "📊 **Nifty Bot Status**",
        f"• Backend API (8000): {backend_str}",
        f"• Bot Lifecycle: {bot_str}",
        f"• Frontend UI (3000): {frontend_str}",
    ]

    if status["stop_signal"]:
        lines.append("⚠️ Global kill-switch lock `.stop_signal` is currently ACTIVE.")

    # Tail recent log snippet
    recent_log = ""
    for path in [BOT_LOG, TRADING_LOG, LOG_FILE]:
        if path.exists() and path.stat().st_size > 0:
            try:
                res = subprocess.run(
                    ["tail", "-n", "4", str(path)], capture_output=True, text=True
                )
                if res.stdout.strip():
                    recent_log = res.stdout.strip()
                    break
            except Exception:
                pass

    if recent_log:
        lines.append(f"\n📝 **Recent Log:**\n```\n{recent_log}\n```")

    return "\n".join(lines)


def cmd_logs(lines: int = 15) -> str:
    """Return latest log lines."""
    for path in [BOT_LOG, TRADING_LOG, LOG_FILE]:
        if path.exists() and path.stat().st_size > 0:
            res = subprocess.run(
                ["tail", "-n", str(lines), str(path)],
                capture_output=True,
                text=True,
            )
            return f"📜 **Latest logs ({path.name}):**\n```\n{res.stdout.strip()}\n```"

    return "No active logs found."


def cmd_help() -> str:
    return (
        "🤖 **Nifty Trading Bot Remote Control**\n\n"
        "Commands:\n"
        "• `/nifty-bot start`  - Launch bot stack in background\n"
        "• `/nifty-bot stop`   - Stop all bot processes and free ports\n"
        "• `/nifty-bot status` - Check health, PIDs, and latest logs\n"
        "• `/nifty-bot logs`   - View recent log activity\n"
        "• `/nifty-bot help`   - Display this menu"
    )


def main():
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else "status"

    if arg in ("start", "run"):
        print(cmd_start())
    elif arg in ("stop", "kill"):
        print(cmd_stop())
    elif arg in ("status", "info"):
        print(cmd_status())
    elif arg in ("logs", "log"):
        print(cmd_logs())
    elif arg in ("help", "-h", "--help"):
        print(cmd_help())
    else:
        print(f"Unknown command: '{arg}'. Use 'help' for options.")


if __name__ == "__main__":
    main()
