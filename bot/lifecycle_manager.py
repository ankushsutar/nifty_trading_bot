import time
import subprocess
import datetime
import sys
import os
import threading
import argparse
from bot.utils.logger import logger
from bot.utils.expiry_calculator import is_trading_day

class LifecycleManager:
    def __init__(self, dry_run=False, test_mode=False, with_selling=False, strategy_type="AUTO"):
        self.dry_run = dry_run
        self.test_mode = test_mode
        self.with_selling = with_selling
        self.strategy_type = strategy_type
        self.current_process = None
        self.selling_process = None
        self._current_date = None
        self.running = False
        self.thread = None
        self.output_thread = None

    def log(self, msg):
        # Use the central logger so it goes to UI and File
        logger.info(f"[Lifecycle] {msg}")

    def _monitor_output(self, process):
        """
        Reads stdout from the child process and logs it to the main logger.
        """
        try:
            for line in iter(process.stdout.readline, ''):
                if line:
                    # Strip whitespace to avoid double newlines
                    clean_line = line.strip()
                    if clean_line:
                        logger.info(f"[Bot] {clean_line}")
        except Exception as e:
            logger.error(f"Error reading child process output: {e}")
            
    def run_strategy(self, strategy_name=None, auto=False):
        """
        Runs main.py with the specified strategy or auto mode.
        """
        cmd = [sys.executable, "-u", "-m", "bot.main"]
        
        if auto:
            cmd.append("--auto")
        elif strategy_name:
            cmd.extend(["--strategy", strategy_name])
        
        if self.dry_run:
            cmd.append("--dry-run")
        if self.test_mode:
            cmd.append("--test")

        self.log(f"Executing: {' '.join(cmd)}")
        
        # Isolate environment to prevent role leakage from backend
        env = os.environ.copy()
        env["PROCESS_TYPE"] = "BOT"
        
        # Capture Output for UI Streaming
        # bufsize=1 (Line Buffered), text=True (String output)
        process = subprocess.Popen(
            cmd, 
            cwd=os.getcwd(),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, # Merge stderr into stdout
            text=True,
            bufsize=1
        )

        
        # Start a thread to consume output so we don't block
        t = threading.Thread(target=self._monitor_output, args=(process,))
        t.daemon = True
        t.start()
        
        return process

    def start_lifecycle(self):
        """Starts the lifecycle loop in a separate thread."""
        from bot.core.kill_switch import deactivate_kill_switch
        deactivate_kill_switch()

        # Phase 5: Start real-time metrics exporter
        try:
            from bot.core.metrics_exporter import metrics_exporter
            metrics_exporter.start()
        except Exception as e:
            self.log(f"[Metrics] Exporter start failed (non-critical): {e}")

        if self.running: return
        self.running = True
        self.thread = threading.Thread(target=self._run_loop)
        self.thread.daemon = True
        self.thread.start()

    def stop_lifecycle(self):
        """Stops the lifecycle and kills any child processes."""
        from bot.core.kill_switch import activate_kill_switch
        activate_kill_switch()

        try:
            from bot.core.metrics_exporter import metrics_exporter
            metrics_exporter.stop()
        except Exception:
            pass

        self.log("Stopping Lifecycle Manager...")
        self.running = False
        if self.current_process:
            self.log("Sending Kill Signal to Child Process...")
            self.current_process.terminate()
            try:
                self.current_process.wait(timeout=10)
                self.log("Child Process terminated gracefully.")
            except subprocess.TimeoutExpired:
                self.log("Child Process stuck. Force Killing...")
                self.current_process.kill()
                self.current_process.wait()
                self.log("Child Process Force Killed.")
            self.current_process = None
        
        if self.selling_process:
            self.log("Stopping Selling Engine Process...")
            self.selling_process.terminate()
            self.selling_process.wait()
            self.selling_process = None

        if self.thread:
            self.thread.join(timeout=2)

    def _run_loop(self):
        self.log("Lifecycle Loop Started 🚀")
        print("\n-------------------------------------------")
        print("   NIFTY BOT LIFECYCLE MANAGER 🤖⏰")
        print("-------------------------------------------")
        print("1. 09:16 AM -> Switch to Smart Auto Mode")
        print("2. 15:30 PM -> Auto Shutdown")
        print("-------------------------------------------\n")

        try:
            while self.running:
                # Check for global kill switch (.stop_signal)
                from bot.core.kill_switch import is_kill_switch_active
                if is_kill_switch_active():
                    self.log("🛑 Global Stop Signal (.stop_signal) active. Terminating and shutting down...")
                    self.running = False
                    if self.current_process:
                        self.log("Terminating current strategy process...")
                        self.current_process.terminate()
                        self.current_process.wait()
                        self.current_process = None
                    if self.selling_process:
                        self.log("Terminating selling engine process...")
                        self.selling_process.terminate()
                        self.selling_process.wait()
                        self.selling_process = None
                    break

                now = datetime.datetime.now().time()
                today = datetime.date.today()

                # DAY-CHANGE RESET: reset ohl_attempted when a new trading day starts
                if self._current_date != today:
                    self._current_date = today
                    self.log(f"📅 New trading day detected: {today}. State reset.")

                # WEEKEND / HOLIDAY GUARD: don't trade on non-trading days
                if not is_trading_day(today):
                    self.log(f"🚫 Today ({today}) is a weekend or NSE holiday. Sleeping 1 hour...")
                    time.sleep(3600)
                    continue

                # 1. MONITOR CHILD PROCESS
                if self.current_process:
                    return_code = self.current_process.poll()
                    if return_code is not None:
                        self.log(f"Strategy process finished with code {return_code}.")
                        self.current_process = None
                
                if self.selling_process:
                    if self.selling_process.poll() is not None:
                        self.log("Selling engine process finished. Restarting in 60s...")
                        self.selling_process = None

                # 2. SCHEDULE LOGIC
                
                # A. PRE-MARKET
                if now < datetime.time(9, 15):
                    # Heartbeat?
                    pass

                # B. MAIN SESSION (09:16 - 15:15)
                elif datetime.time(9, 16) <= now < datetime.time(15, 15):
                    if not self.current_process:
                        if self.strategy_type == "AUTO":
                            self.log("⏰ Time 09:16+ Detected. Activating Main Auto-Strategy Cycle...")
                            self.current_process = self.run_strategy(auto=True)
                        else:
                            self.log(f"⏰ Time 09:16+ Detected. Activating Custom Strategy {self.strategy_type}...")
                            self.current_process = self.run_strategy(strategy_name=self.strategy_type)
                        time.sleep(60)

                    # START SELLING ENGINE (Background - OPTIONAL)
                    if self.with_selling and not self.selling_process:
                        self.log("⏰ Starting Selling Engine in background...")
                        self.selling_process = self.run_strategy(strategy_name="SELLING")
                
                # D. MARKET CLOSE (> 15:15)
                elif now >= datetime.time(15, 15):
                    if self.current_process:
                        self.log("⏰ Market End (15:15). Sending kill signal...")
                        self.current_process.terminate()
                        self.current_process.wait()
                        self.current_process = None
                    
                    self.log("Day Complete. lifecycle waiting for stop command.")
                    break
                    
                time.sleep(5)
                
        except Exception as e:
            self.log(f"Lifecycle Loop Error: {e}")
            self.running = False


def main():
    parser = argparse.ArgumentParser(description="Lifecycle Manager")
    parser.add_argument("--dry-run", action="store_true", help="Run in dry run mode")
    parser.add_argument("--test", action="store_true", help="Run in test mode")
    parser.add_argument("--selling", action="store_true", help="Enable Selling Engine in background")
    parser.add_argument("--strategy", type=str, default="AUTO", choices=["AUTO", "MOMENTUM", "GAMMA_BLAST", "SELLING", "ZERO_TO_HERO", "PULLBACK"], help="Choose Strategy")
    args = parser.parse_args()
    
    manager = LifecycleManager(dry_run=args.dry_run, test_mode=args.test, with_selling=args.selling, strategy_type=args.strategy)
    manager.start_lifecycle()
    
    try:
        while True:
            time.sleep(1)
            if not manager.running and manager.thread and not manager.thread.is_alive():
                 break
    except KeyboardInterrupt:
        manager.stop_lifecycle()

if __name__ == "__main__":
    main()
