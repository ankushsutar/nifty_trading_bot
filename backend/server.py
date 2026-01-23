from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import sys
import os
import asyncio
import logging

# Add parent dir to path to find core/utils
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.bot_manager import bot_manager
from backend.socket_manager import socket_manager
from utils.logger import log_queue, logger

app = FastAPI(title="Nifty Bot API", version="1.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class StartRequest(BaseModel):
    strategy: str = "MOMENTUM"
    dry_run: bool = True

@app.on_event("startup")
async def startup_event():
    # Start the log queue processor as a background task
    asyncio.create_task(process_log_queue())

import queue

# ... imports ...

# ...

async def process_log_queue():
    """
    Consumer task that reads from the thread-safe queue 
    and broadcasts to WebSockets.
    """
    while True:
        try:
            # Non-blocking get
            record = log_queue.get_nowait()
            
            # Convert LogRecord to formatted string
            log_msg = f"{record.levelname}: {record.getMessage()}"
            
            import datetime
            ts = datetime.datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S')
            
            payload = {
                "timestamp": ts,
                "level": record.levelname,
                "message": record.getMessage()
            }
            
            # Broadcast as JSON string
            import json
            await socket_manager.broadcast(json.dumps(payload))
            
        except queue.Empty:
            # No logs, sleep a bit to yield
            await asyncio.sleep(0.1)
        except Exception as e:
            # Print error to server console to debug silence
            print(f"[Server Error] Log Queue Consumer Failed: {e}")
            await asyncio.sleep(0.1)

@app.get("/")
def home():
    return {"message": "Nifty Bot API is Online 🚀"}

@app.post("/api/start")
def start_bot(req: StartRequest):
    # INJECT TEST LOG TO QUEUE DIRECTLY
    import logging
    # Create a fake record to test queue -> ws path
    test_record = logging.LogRecord(
        name="Test", level=logging.INFO, pathname=__file__, lineno=0,
        msg="DEBUG: API Received Start Request", args=(), exc_info=None
    )
    log_queue.put(test_record)

    result = bot_manager.start_bot(req.strategy, req.dry_run)
    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])
    return result

@app.post("/api/stop")
def stop_bot():
    return bot_manager.stop_bot()

@app.get("/api/status")
def get_status():
    return bot_manager.get_status()

@app.get("/api/trade")
def get_trade():
    return bot_manager.get_active_trade()

@app.websocket("/ws/logs")
async def websocket_endpoint(websocket: WebSocket):
    await socket_manager.connect(websocket)
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        socket_manager.disconnect(websocket)

if __name__ == "__main__":
    uvicorn.run("backend.server:app", host="0.0.0.0", port=8000, reload=True)
