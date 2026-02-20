import os
import time
import fcntl
from bot.utils.logger import logger

class GlobalRateLimiter:
    """
    A cross-process rate limiter that uses a lockfile to synchronize API calls
    between multiple processes (Dashboard, multiple Bot strategies).
    """
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(GlobalRateLimiter, cls).__new__(cls)
            cls._instance.lock_file = os.path.join(os.getcwd(), "data", "api_global.lock")
            cls._instance.time_file = os.path.join(os.getcwd(), "data", "api_last_call.time")
            cls._instance.min_interval = 5.0 # Strictly > 3s for security.
            
            # Ensure data dir exists
            os.makedirs(os.path.dirname(cls._instance.lock_file), exist_ok=True)
            
        return cls._instance

    def check_circuit_breaker(self):
        """
        Returns wait time in seconds if circuit breaker is active.
        Returns 0.0 if safe to proceed.
        NON-BLOCKING check (Read-Only Lock).
        """
        if not os.path.exists(self.time_file): return 0.0
        
        try:
            with open(self.time_file, "r") as f:
                lines = f.readlines()
                if len(lines) > 1:
                    cb_until = float(lines[1].strip())
                    wait_time = cb_until - time.time()
                    if wait_time > 0:
                        return wait_time
        except: pass
        return 0.0

    def wait(self):
        import random
        # 1. Micro-jitter BEFORE acquiring lock to de-sync multiple processes 
        # waking up from a shared sleep or starting simultaneously.
        time.sleep(random.uniform(0.1, 0.5))

        while True:
            wait_time = 0.0
            lock_fd = os.open(self.lock_file, os.O_RDWR | os.O_CREAT)
            try:
                # 2. Acquire exclusive lock
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                
                # 3. Read state
                last_call = 0.0
                cb_until = 0.0
                fail_count = 0
                if os.path.exists(self.time_file):
                    try:
                        with open(self.time_file, "r") as f:
                            lines = f.readlines()
                            last_call = float(lines[0].strip())
                            if len(lines) > 1:
                                cb_until = float(lines[1].strip())
                            if len(lines) > 2:
                                fail_count = int(lines[2].strip())
                    except: pass
                
                now = time.time()
                
                # 4. Check Circuit Breaker
                if now < cb_until:
                    wait_time = cb_until - now
                    # Add extra 1s jitter to recovery to prevent thundering herd
                    wait_time += random.uniform(0.5, 1.5)
                    logger.warning(f"Global Rate Limiter: CIRCUIT BREAKER ACTIVE (Failures: {fail_count}). Waiting {wait_time:.1f}s...")
                
                # 5. Check Standard Interval (only if CB not active)
                elif now - last_call < self.min_interval:
                     wait_time = self.min_interval - (now - last_call)
                
                # 6. If no wait needed, Update Timestamp and Return
                if wait_time <= 0:
                     if now - last_call > 300: fail_count = 0 # Cool-down reset
                     
                     with open(self.time_file, "w") as f:
                         f.write(f"{time.time()}\n{cb_until}\n{fail_count}")
                     return # SUCCESS
                     
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
                
            # 7. Sleep OUTSIDE the lock
            if wait_time > 0:
                time.sleep(wait_time)

    def trigger_circuit_breaker(self, base_duration=30):
        """
        Force all processes to wait. Exponential backoff based on failure count.
        """
        lock_fd = os.open(self.lock_file, os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            
            # Read current stats
            last_call = time.time()
            fail_count = 0
            if os.path.exists(self.time_file):
                try:
                    with open(self.time_file, "r") as f:
                        lines = f.readlines()
                        last_call = float(lines[0].strip())
                        if len(lines) > 2:
                            fail_count = int(lines[2].strip())
                except: pass

            fail_count += 1
            
            # Adaptive Duration: 30s -> 60s -> 300s
            if fail_count == 1:
                duration = base_duration
            elif fail_count == 2:
                duration = 60
            else:
                duration = 300 # 5 mins
                
            cb_until = time.time() + duration
                
            with open(self.time_file, "w") as f:
                f.write(f"{last_call}\n{cb_until}\n{fail_count}")
                
            logger.warning(f"!!! [System] API Circuit Breaker Triggered (Attempt {fail_count}): Paused for {duration}s 🛡️")
            
            if fail_count >= 3:
                logger.error(">>> [System] Persistent AB1004. Flagging Session Refresh 🔄")
                flag_file = os.path.join(os.getcwd(), "data", "session_refresh.flag")
                with open(flag_file, "w") as f:
                    f.write("REFRESH_REQUIRED")
                
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def reset_circuit_breaker(self):
        """Resets the circuit breaker and failure count."""
        lock_fd = os.open(self.lock_file, os.O_RDWR | os.O_CREAT)
        try:
             fcntl.flock(lock_fd, fcntl.LOCK_EX)
             with open(self.time_file, "w") as f:
                 f.write(f"{time.time()}\n0.0\n0") # Reset CB and Fail Count
             logger.info("[System] Circuit Breaker Reset. 🟢")
        except: pass
        finally:
             fcntl.flock(lock_fd, fcntl.LOCK_UN)
             os.close(lock_fd)

rate_limiter = GlobalRateLimiter()
