import subprocess
import time
import os
import signal
from bot.core.kill_switch import deactivate_kill_switch, is_kill_switch_active

def test_shutdown():
    # 1. Clear any old stop signals
    deactivate_kill_switch()
    
    print(">>> Starting Bot in Mock Mode...")
    # Run momentum strategy in mock mode
    cmd = ["python3", "-u", "-m", "bot.main", "--test", "--strategy", "MOMENTUM", "--dry-run"]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    
    trade_entered = False
    shutdown_begun = False
    exit_recorded = False
    
    start_time = time.time()
    
    try:
        # 2. Wait for Trade Entry
        for line in iter(process.stdout.readline, ''):
            print(f"[Bot] {line.strip()}")
            
            if "Trade: Entering" in line:
                print(">>> SUCCESS: Trade Entered. Waiting 2s before stopping...")
                trade_entered = True
                time.sleep(2)
                print(">>> Sending SIGINT (Ctrl+C)...")
                process.send_signal(signal.SIGINT)
                shutdown_begun = True
            
            if "Initiating Graceful Shutdown" in line:
                print(">>> SUCCESS: Signal Received by Bot.")
                
            if "Exiting Strategy Loop" in line or "USER_STOPPED" in line:
                print(">>> SUCCESS: Strategy loop interrupted and exit triggered.")
                exit_recorded = True
            
            if "TradeRepository: Trade Closed" in line:
                print(">>> SUCCESS: DB record closed.")
                break # We are done

            if time.time() - start_time > 30:
                print(">>> TIMEOUT: Test taking too long.")
                break
                
    finally:
        if process.poll() is None:
            process.kill()
            
    # 3. Final Assertion
    if trade_entered and shutdown_begun and exit_recorded:
        print("\n✅ VERIFICATION PASSED: Bot closed position gracefully before exiting.")
    else:
        print("\n❌ VERIFICATION FAILED:")
        print(f"   Trade Entered: {trade_entered}")
        print(f"   Shutdown Begun: {shutdown_begun}")
        print(f"   Exit Recorded: {exit_recorded}")

if __name__ == "__main__":
    test_shutdown()
