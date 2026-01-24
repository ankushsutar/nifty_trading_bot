import time
import subprocess
import datetime
import sys
import os
import threading
from utils.logger import logger

class LifecycleManager:
    def __init__(self, dry_run=False, test_mode=False):
        self.dry_run = dry_run
        self.test_mode = test_mode
        self.current_process = None
        self.ohl_attempted = False
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
        cmd = [sys.executable, "main.py"]
        
        if auto:
            cmd.append("--auto")
        elif strategy_name:
            cmd.extend(["--strategy", strategy_name])
        
        if self.dry_run:
            cmd.append("--dry-run")
        if self.test_mode:
            cmd.append("--test")

        self.log(f"Executing: {' '.join(cmd)}")
        
        # Capture Output for UI Streaming
        # bufsize=1 (Line Buffered), text=True (String output)
        process = subprocess.Popen(
            cmd, 
            cwd=os.getcwd(),
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
        if self.running: return
        self.running = True
        self.thread = threading.Thread(target=self._run_loop)
        self.thread.daemon = True
        self.thread.start()

    def stop_lifecycle(self):
        """Stops the lifecycle and kills any child processes."""
        self.log("Stopping Lifecycle Manager...")
        self.running = False
        if self.current_process:
            self.log("Sending Kill Signal to Child Process...")
            self.current_process.terminate()
            self.current_process.wait()
            self.current_process = None
        if self.thread:
            self.thread.join(timeout=2)

    def _run_loop(self):
        self.log("Lifecycle Loop Started 🚀")
        print("\n-------------------------------------------")
        print("   NIFTY BOT LIFECYCLE MANAGER 🤖⏰")
        print("-------------------------------------------")
        print("1. 09:15 AM -> Attempt OHL Scalp")
        print("2. 09:20 AM -> Switch to Smart Auto Mode")
        print("3. 15:30 PM -> Auto Shutdown")
        print("-------------------------------------------\n")

        try:
            while self.running:
                now = datetime.datetime.now().time()
                
                # 1. MONITOR CHILD PROCESS
                if self.current_process:
                    return_code = self.current_process.poll()
                    if return_code is not None:
                        self.log(f"Strategy process finished with code {return_code}.")
                        self.current_process = None
                
                # 2. SCHEDULE LOGIC
                
                # A. PRE-MARKET
                if now < datetime.time(9, 15):
                    # Heartbeat?
                    pass

                # B. MARKET OPEN (09:15 - 09:20) -> OHL SCALP
                elif datetime.time(9, 15) <= now < datetime.time(9, 20):
                    if not self.ohl_attempted and not self.current_process:
                        self.log("⏰ Time 09:15 Noticed. Attempting OHL Scalp...")
                        self.current_process = self.run_strategy(strategy_name="OHL")
                        self.ohl_attempted = True

                # C. MAIN SESSION (09:20 - 15:15) -> AUTO MODE
                elif datetime.time(9, 20) <= now < datetime.time(15, 15):
                    if not self.current_process:
                        self.log("⏰ Time 09:20+ Detected. Switching to Main Auto-Strategy...")
                        self.current_process = self.run_strategy(auto=True)
                        time.sleep(10)
                
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
    # Parse Args manually since we are in main
    dry_run = "--dry-run" in sys.argv
    test_mode = "--test" in sys.argv
    
    manager = LifecycleManager(dry_run, test_mode)
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
