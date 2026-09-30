"""
FastAPI Main Application for Real-Time PSX Market Dashboard & Alert Engine.
Provides RESTful APIs, WebSocket broadcasting, and asynchronous background workers.
"""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import List, Optional, Set, Dict, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

import database as db
from models import (
    AlertCreate, AlertUpdate, AlertOut, AlertHistoryOut,
    SystemSettings, MarketSummary, StockQuote
)
from scraper import PSXScraper
from alert_engine import AlertEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("psx_app")

# Global instances
scraper = PSXScraper()
alert_engine = AlertEngine()
background_task: Optional[asyncio.Task] = None


class ConnectionManager:
    """Manages active browser WebSocket connections for live broadcasting."""
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self.active_connections.add(websocket)
        logger.info("WebSocket connected. Active clients: %d", len(self.active_connections))

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            self.active_connections.discard(websocket)
        logger.info("WebSocket disconnected. Active clients: %d", len(self.active_connections))

    async def broadcast(self, message: Dict[str, Any]):
        if not self.active_connections:
            return
        payload = json.dumps(message, default=str)
        dead = []
        async with self._lock:
            for ws in list(self.active_connections):
                try:
                    await ws.send_text(payload)
                except Exception:
                    dead.append(ws)
            for d in dead:
                self.active_connections.discard(d)


ws_manager = ConnectionManager()
alert_engine.set_ws_broadcast_callback(ws_manager.broadcast)


async def scraper_and_alert_worker():
    """
    Background worker loop:
    1. Scrapes PSX Data Portal at user-configured intervals.
    2. Runs Alert Engine against new quotes.
    3. Broadcasts market updates over WebSockets.
    """
    logger.info("Starting PSX Background Scraper & Alert Worker...")
    # Initial seed & sync
    try:
        await scraper.update()
        logger.info("Initial sync complete: %d stocks loaded", len(scraper.latest_stocks))
    except Exception as e:
        logger.error("Initial scraper sync error: %s", e)

    while True:
        try:
            settings = db.get_settings()
            poll_interval = max(3, settings.get("poll_interval_seconds", 8))

            # Execute scrape pass
            sync_res = await scraper.update()

            # Evaluate active alerts
            triggered = await alert_engine.evaluate_quotes(scraper.latest_stocks)
            if triggered:
                logger.info("Triggered %d alerts in this tick!", len(triggered))

            # Broadcast live market tick to connected browser tabs
            summary = scraper.get_summary()
            top_movers = scraper.get_top_movers(min_volume=settings.get("min_volume_gainers", 50000), limit=5)

            await ws_manager.broadcast({
                "type": "MARKET_TICK",
                "summary": summary.model_dump(mode="json"),
                "top_movers": {
                    "gainers": [s.model_dump(mode="json") for s in top_movers["gainers"]],
                    "losers": [s.model_dump(mode="json") for s in top_movers["losers"]],
                    "volume_leaders": [s.model_dump(mode="json") for s in top_movers["volume_leaders"]]
                },
                "stocks_count": sync_res["stocks_count"],
                "last_sync": sync_res["last_sync"]
            })

            await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            logger.info("Scraper worker received cancellation request.")
            break
        except Exception as e:
            logger.error("Unexpected error in scraper worker: %s", e)
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize Database & Background Worker
    db.init_db()
    global background_task
    background_task = asyncio.create_task(scraper_and_alert_worker())
    yield
    # Shutdown: Terminate Background Worker & HTTP clients
    if background_task:
        background_task.cancel()
        try:
            await background_task
        except asyncio.CancelledError:
            pass
    await scraper.close()
    await alert_engine.close()
    logger.info("PSX Dashboard shutdown complete.")


app = FastAPI(
    title="PSX Real-Time Dashboard & Alert Engine",
    description="Live Pakistan Stock Exchange Terminal with Automated Multi-Condition Alerts and Telegram Integration",
    version="1.0.0",
    lifespan=lifespan
)

# Static files mount
app.mount("/static", StaticFiles(directory="static"), name="static")


# --- Frontend Route ---

@app.get("/", response_class=FileResponse)
async def get_dashboard():
    return FileResponse("templates/index.html")


# --- WebSocket Stream ---

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        # Send instant initial payload upon connection
        settings = db.get_settings()
        summary = scraper.get_summary()
        top_movers = scraper.get_top_movers(min_volume=settings.get("min_volume_gainers", 50000), limit=5)
        await websocket.send_text(json.dumps({
            "type": "INITIAL_STATE",
            "summary": summary.model_dump(mode="json"),
            "top_movers": {
                "gainers": [s.model_dump(mode="json") for s in top_movers["gainers"]],
                "losers": [s.model_dump(mode="json") for s in top_movers["losers"]],
                "volume_leaders": [s.model_dump(mode="json") for s in top_movers["volume_leaders"]]
            },
            "stocks": [s.model_dump(mode="json") for s in scraper.latest_stocks.values()]
        }, default=str))

        while True:
            # Handle incoming ping / messages from client
            data = await websocket.receive_text()
            msg = json.loads(data)
            if msg.get("action") == "PING":
                await websocket.send_text(json.dumps({"type": "PONG"}))
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket)
    except Exception as e:
        logger.warning("WebSocket connection exception: %s", e)
        await ws_manager.disconnect(websocket)


# --- REST API: Market Data ---

@app.get("/api/market/summary", response_model=MarketSummary)
async def get_market_summary():
    return scraper.get_summary()


@app.get("/api/market/top-movers")
async def get_top_movers(min_volume: Optional[int] = None):
    settings = db.get_settings()
    vol = min_volume if min_volume is not None else settings.get("min_volume_gainers", 50000)
    return scraper.get_top_movers(min_volume=vol, limit=5)


@app.get("/api/market/stocks")
async def get_all_stocks(
    search: Optional[str] = Query(None, description="Search symbol or name"),
    sector: Optional[str] = Query(None, description="Filter by sector"),
    filter_type: Optional[str] = Query("all", description="Filter: all, gainers, losers, active"),
    sort_by: str = Query("volume", description="Column to sort by: volume, change_pct, current, symbol"),
    direction: str = Query("desc", description="Sort direction: asc or desc"),
    limit: int = Query(100, ge=1, le=600)
):
    stocks = list(scraper.latest_stocks.values())

    # Filter by Gainers / Losers / Active
    if filter_type == "gainers":
        stocks = [s for s in stocks if s.change > 0]
    elif filter_type == "losers":
        stocks = [s for s in stocks if s.change < 0]
    elif filter_type == "active":
        stocks = [s for s in stocks if s.volume >= 1000000]

    # Filter by Search Query
    if search:
        q = search.lower().strip()
        stocks = [s for s in stocks if q in s.symbol.lower() or q in s.name.lower()]

    if sector and sector != "ALL":
        stocks = [s for s in stocks if s.sector.upper() == sector.upper()]

    # Sort
    reverse = (direction.lower() == "desc")
    if sort_by == "change_pct":
        stocks.sort(key=lambda s: s.change_pct, reverse=reverse)
    elif sort_by == "current":
        stocks.sort(key=lambda s: s.current, reverse=reverse)
    elif sort_by == "change":
        stocks.sort(key=lambda s: s.change, reverse=reverse)
    elif sort_by == "symbol":
        stocks.sort(key=lambda s: s.symbol, reverse=reverse)
    else:  # default volume
        stocks.sort(key=lambda s: s.volume, reverse=reverse)

    return {
        "total": len(stocks),
        "stocks": [s.model_dump() for s in stocks[:limit]]
    }


@app.get("/api/market/sectors")
async def get_sectors():
    sectors = sorted(list({s.sector for s in scraper.latest_stocks.values() if s.sector}))
    return sectors


@app.post("/api/market/refresh")
async def trigger_refresh():
    res = await scraper.update()
    summary = scraper.get_summary()
    return {"message": "Market data refreshed successfully", "stocks_count": res["stocks_count"], "summary": summary}


# --- REST API: Alert Management ---

@app.get("/api/alerts", response_model=List[AlertOut])
async def list_alerts():
    return db.get_all_alerts()


@app.post("/api/alerts", response_model=AlertOut)
async def create_alert_endpoint(payload: AlertCreate):
    sym = payload.symbol.upper().strip()
    # Resolve stock name from cache if available
    stock = scraper.latest_stocks.get(sym)
    name = stock.name if stock else f"{sym} Corporation"

    alert_id = db.create_alert(
        symbol=sym,
        name=name,
        condition_type=payload.condition_type.value,
        target_value=payload.target_value,
        trigger_mode=payload.trigger_mode.value,
        cooldown_minutes=payload.cooldown_minutes,
        notes=payload.notes
    )
    alert = db.get_alert_by_id(alert_id)
    return alert


@app.put("/api/alerts/{alert_id}", response_model=AlertOut)
async def update_alert_endpoint(alert_id: int, payload: AlertUpdate):
    existing = db.get_alert_by_id(alert_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Alert not found")

    updates = {}
    if payload.is_active is not None:
        updates["is_active"] = 1 if payload.is_active else 0
    if payload.target_value is not None:
        updates["target_value"] = payload.target_value
    if payload.cooldown_minutes is not None:
        updates["cooldown_minutes"] = payload.cooldown_minutes
    if payload.trigger_mode is not None:
        updates["trigger_mode"] = payload.trigger_mode.value
    if payload.notes is not None:
        updates["notes"] = payload.notes

    if updates:
        db.update_alert(alert_id, **updates)

    return db.get_alert_by_id(alert_id)


@app.post("/api/alerts/{alert_id}/toggle")
async def toggle_alert_endpoint(alert_id: int):
    new_state = db.toggle_alert(alert_id)
    if new_state is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"id": alert_id, "is_active": new_state}


@app.delete("/api/alerts/{alert_id}")
async def delete_alert_endpoint(alert_id: int):
    success = db.delete_alert(alert_id)
    if not success:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"message": "Alert deleted successfully", "id": alert_id}


@app.get("/api/alerts/history", response_model=List[AlertHistoryOut])
async def get_alert_logs(limit: int = 50):
    return db.get_alert_history(limit=limit)


# --- REST API: Settings & Telegram ---

@app.get("/api/settings", response_model=SystemSettings)
async def get_system_settings():
    return db.get_settings()


@app.post("/api/settings", response_model=SystemSettings)
async def update_system_settings(settings: SystemSettings):
    db.save_settings(settings.model_dump())
    return db.get_settings()


@app.post("/api/telegram/test")
async def test_telegram_channel(req: Dict[str, str]):
    token = req.get("bot_token")
    chat_id = req.get("chat_id")

    if not token or not chat_id:
        # Fallback to saved settings
        current = db.get_settings()
        token = token or current.get("telegram_bot_token")
        chat_id = chat_id or current.get("telegram_chat_id")

    if not token or not chat_id:
        raise HTTPException(status_code=400, detail="Bot Token and Chat ID are both required.")

    success, message = await alert_engine.send_test_telegram(token, chat_id)
    if not success:
        raise HTTPException(status_code=400, detail=message)
    return {"success": True, "message": message}

import gradio as gr

# Gradio interface banayein jo app ko alive rakhega
with gr.Blocks(title="PSX Terminal") as demo:
    gr.HTML("""
        <div style="text-align: center; padding: 20px; font-family: monospace;">
            <h2>📈 PSX Real-Time Dashboard is Active!</h2>
            <p>Direct web interface access karne ke liye neeche button par click karein:</p>
            <a href="/" target="_top" style="display:inline-block; padding: 12px 24px; background: #2563eb; color: white; border-radius: 6px; text-decoration: none; font-weight: bold;">Open Full Dashboard 🚀</a>
        </div>
        <script>
            // Auto redirect to main dashboard
            window.location.href = '/';
        </script>
    """)

# FastAPI app ko mount karein
app = gr.mount_gradio_app(app, demo, path="/gradio")

# Process ko zinda rakhne ke liye blocking launch
demo.launch(server_name="0.0.0.0", server_port=7860)
