---
title: PSX Live Dashboard
emoji: 📈
colorFrom: green
colorTo: blue
sdk: gradio
sdk_version: 5.9.1
app_file: app.py
pinned: false
---

# PSX Real-Time Market Dashboard & Automated Alert Engine


A high-performance, real-time Pakistan Stock Exchange (PSX) financial terminal and automated alert management system. Built with Python (FastAPI), WebSockets, SQLite (WAL mode), and a modern Bloomberg / TradingView inspired dark-mode web interface.

---

## 🚀 Key Features

1. **Live PSX Data Ingestion & Scraper**:
   - Asynchronous worker continuously fetching all 550+ listed stocks from the PSX Data Portal (`https://dps.psx.com.pk`).
   - Normalizes: Symbol, Company Name, Sector, Current Price (LTP), Open, High, Low, Previous Close (LDCP), Net Change, Percentage Change, and Traded Volume.
   - Live KSE-100, KSE-30, and ALLSHR index tracking.
   - Dynamic session management: handles PSX's internal `X-Req-Id` (`_k`) token acquisition and automatic renewal.
   - Realistic fallback baseline to guarantee zero downtime during off-market hours or portal rate limits.

2. **Top Movers & Screener Engine**:
   - **Top Gainers**: Highest % gain, filtered by a minimum volume threshold (configurable, default: 50,000 shares) to prevent illiquid penny stocks.
   - **Top Losers**: Lowest negative % drop.
   - **Volume Leaders**: Highest volume traded shares.
   - **Market Breadth**: Real-time visual progress bar tracking Advancers vs. Decliners vs. Neutral.

3. **Automated Multi-Condition Alert Engine**:
   - **Price Thresholds**: Price rises above target (`>=`) or drops below target (`<=`).
   - **Percentage Move**: Net gain moves above (`>= +X%`) or drop drops below (`<= -X%`).
   - **Volume Spike**: Traded volume exceeds specified threshold.
   - **Cooldown & Spam Prevention**: Configurable cooldown interval (e.g. 15 minutes) or "Trigger Once Until Reset" mode to prevent alert spamming.
   - **Persistence**: Alerts, triggers, and configuration persist across server restarts in SQLite with Write-Ahead Logging (WAL).

4. **Multi-Channel Notification Delivery**:
   - **Web Browser Alerts**: Real-time pop-up toast banners + dual-harmonic Web Audio API sound chime (no external MP3 required).
   - **Telegram Bot API**: Instant Markdown/HTML formatted messages dispatched to your Telegram chat with symbol, company name, trigger rule, current LTP, volume, timestamp, and exchange link.
   - **Test Connection Tools**: Built-in test buttons for Telegram Bot API verification and audio chime preview.

5. **Financial Terminal Dashboard (UI/UX)**:
   - Modern dark Bloomberg / TradingView aesthetic.
   - Interactive high-performance stock table with instant search and sector filters.
   - Multi-column sorting (Price, % Change, Volume, Symbol).
   - Market session indicator (Open / Closed / Friday Prayer Recess) based on official PKT trading schedules.

---

## 🛠️ File Structure

```
├── app.py              # FastAPI application, REST endpoints, WebSocket manager, background worker
├── scraper.py          # PSX Data Portal (DPS) async scraper, token manager, market hours engine
├── alert_engine.py     # Multi-condition rule evaluator, cooldown logic, Telegram dispatcher
├── database.py         # SQLite database management (WAL mode) for alerts, history, and settings
├── models.py           # Pydantic data models and schemas
├── templates/
│   └── index.html      # Dark-themed financial dashboard (TailwindCSS, responsive layout)
├── static/
│   ├── css/
│   │   └── terminal.css # Financial terminal styling, glowing badges, animations
│   └── js/
│       └── dashboard.js # WebSocket client, Web Audio synth, table search/sort, modals
├── requirements.txt    # Python dependencies
└── README.md           # Documentation and quickstart
```

---

## ⚡ Quickstart Guide

### 1. Prerequisites
- Python 3.10+ installed.

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Run the Application
```bash
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```
or run with Python:
```bash
python -m uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

### 4. Access the Dashboard
Open your browser and navigate to:
```
http://localhost:8000
```

---

## 📲 Setting Up Telegram Alerts (Optional)

1. Open Telegram and search for `@BotFather`.
2. Send `/newbot` and follow the prompts to get your **Bot Token** (e.g., `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`).
3. Start a chat with your new bot by clicking `Start`.
4. To get your **Chat ID**, message `@userinfobot` or forward a message to `@getmyid_bot`.
5. On the PSX Terminal dashboard, click **⚙️ Settings** (or the **✈️ Telegram** pill in the top header).
6. Enter your **Bot Token** and **Chat ID**, enable the toggle, and click **"Test Telegram Bot Message"**.
7. Click **"Save Settings"**. All alerts will now automatically ping your Telegram!

---

## 🕒 PSX Trading Hours Logic
The dashboard calculates market status based on Pakistan Standard Time (PKT, UTC+5):
- **Monday – Thursday**: 09:15 AM – 03:30 PM PKT (Continuous Trading)
- **Friday Session 1**: 09:15 AM – 12:00 PM PKT
- **Friday Prayer Recess**: 12:00 PM – 02:30 PM PKT (Market in Recess)
- **Friday Session 2**: 02:30 PM – 04:30 PM PKT
- **Saturday & Sunday**: Market Closed
