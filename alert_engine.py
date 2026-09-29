"""
Automated Alert Management & Notification Engine for PSX.
Evaluates multi-condition rules, enforces cooldowns / one-shot triggers,
and dispatches notifications via Telegram Bot API and WebSockets.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any, Callable, Awaitable

import httpx

from models import StockQuote, AlertConditionType, AlertTriggerMode
import database as db

logger = logging.getLogger("alert_engine")

PKT = timezone(timedelta(hours=5))


class AlertEngine:
    def __init__(self, ws_broadcast_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None):
        """
        :param ws_broadcast_callback: Async callable to broadcast alerts to live WebSocket clients.
        """
        self.ws_broadcast = ws_broadcast_callback
        self._http_client: Optional[httpx.AsyncClient] = None

    async def get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=10.0)
        return self._http_client

    async def close(self):
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    def set_ws_broadcast_callback(self, callback: Callable[[Dict[str, Any]], Awaitable[None]]):
        self.ws_broadcast = callback

    async def evaluate_quotes(self, quotes: Dict[str, StockQuote]) -> List[Dict[str, Any]]:
        """
        Evaluates active alerts against the latest market stock quotes.
        Returns a list of triggered alert events.
        """
        active_alerts = db.get_active_alerts()
        if not active_alerts:
            return []

        settings = db.get_settings()
        telegram_token = settings.get("telegram_bot_token")
        telegram_chat_id = settings.get("telegram_chat_id")
        telegram_enabled = settings.get("telegram_enabled", False) and bool(telegram_token) and bool(telegram_chat_id)

        triggered_events = []
        now_utc = datetime.utcnow()

        for alert in active_alerts:
            symbol = alert["symbol"].upper().strip()
            quote = quotes.get(symbol)
            if not quote:
                continue

            # Check cooldown / one-shot status
            last_triggered_str = alert.get("last_triggered_at")
            if last_triggered_str:
                try:
                    last_triggered = datetime.fromisoformat(last_triggered_str)
                    cooldown_delta = timedelta(minutes=int(alert.get("cooldown_minutes", 15)))
                    if now_utc < (last_triggered + cooldown_delta):
                        # Still in cooldown period
                        continue
                except (ValueError, TypeError):
                    pass

            condition = alert["condition_type"]
            target = float(alert["target_value"])
            is_triggered = False
            actual_value = 0.0
            msg = ""

            if condition == AlertConditionType.PRICE_ABOVE.value:
                actual_value = quote.current
                if actual_value >= target:
                    is_triggered = True
                    msg = f"Price reached PKR {actual_value:,.2f} (Target >= {target:,.2f})"

            elif condition == AlertConditionType.PRICE_BELOW.value:
                actual_value = quote.current
                if actual_value <= target:
                    is_triggered = True
                    msg = f"Price dropped to PKR {actual_value:,.2f} (Target <= {target:,.2f})"

            elif condition == AlertConditionType.PCT_CHANGE_ABOVE.value:
                actual_value = quote.change_pct
                if actual_value >= target:
                    is_triggered = True
                    msg = f"Net gain reached +{actual_value:.2f}% (Target >= +{target:.2f}%)"

            elif condition == AlertConditionType.PCT_CHANGE_BELOW.value:
                actual_value = quote.change_pct
                # Expecting target as negative or positive magnitude (e.g. -3.0 or 3.0)
                threshold = -abs(target)
                if actual_value <= threshold:
                    is_triggered = True
                    msg = f"Net drop reached {actual_value:.2f}% (Target <= {threshold:.2f}%)"

            elif condition == AlertConditionType.VOLUME_ABOVE.value:
                actual_value = float(quote.volume)
                if actual_value >= target:
                    is_triggered = True
                    msg = f"Volume reached {int(actual_value):,} shares (Target >= {int(target):,})"

            if is_triggered:
                trigger_time_str = now_utc.isoformat()
                disable_alert = (alert.get("trigger_mode") == AlertTriggerMode.ONCE.value)

                # Update alert state in SQLite
                db.update_alert_last_triggered(alert["id"], trigger_time_str, disable_alert=disable_alert)

                # Dispatch Telegram Notification
                tg_sent = False
                tg_err = None
                if telegram_enabled:
                    tg_sent, tg_err = await self.send_telegram_alert(
                        token=telegram_token,
                        chat_id=telegram_chat_id,
                        alert=alert,
                        quote=quote,
                        trigger_message=msg,
                        actual_value=actual_value
                    )

                # Record in History
                hist_id = db.record_alert_history(
                    alert_id=alert["id"],
                    symbol=symbol,
                    condition_type=condition,
                    target_value=target,
                    actual_value=actual_value,
                    message=msg,
                    telegram_sent=tg_sent,
                    telegram_error=tg_err
                )

                event_payload = {
                    "history_id": hist_id,
                    "alert_id": alert["id"],
                    "symbol": symbol,
                    "name": quote.name,
                    "condition_type": condition,
                    "target_value": target,
                    "actual_value": actual_value,
                    "current_price": quote.current,
                    "change": quote.change,
                    "change_pct": quote.change_pct,
                    "volume": quote.volume,
                    "message": msg,
                    "triggered_at": datetime.now(PKT).strftime("%I:%M:%S %p PKT"),
                    "telegram_sent": tg_sent,
                    "one_shot_disabled": disable_alert,
                    "notes": alert.get("notes")
                }
                triggered_events.append(event_payload)

                # Broadcast live via WebSockets
                if self.ws_broadcast:
                    try:
                        await self.ws_broadcast({
                            "type": "ALERT_TRIGGERED",
                            "data": event_payload
                        })
                    except Exception as ex:
                        logger.error("Error broadcasting alert over websocket: %s", ex)

        return triggered_events

    async def send_telegram_alert(self, token: str, chat_id: str, alert: Dict[str, Any],
                                  quote: StockQuote, trigger_message: str, actual_value: float) -> (bool, Optional[str]):
        """
        Sends formatted alert notification to Telegram chat.
        """
        client = await self.get_http_client()
        url = f"https://api.telegram.org/bot{token}/sendMessage"

        pkt_time_str = datetime.now(PKT).strftime("%Y-%m-%d %I:%M:%S %p PKT")

        # HTML formatting for Telegram message
        text = (
            f"🚨 <b>PSX MARKET ALERT TRIGGERED</b> 🚨\n\n"
            f"<b>Symbol:</b> <code>{quote.symbol}</code>\n"
            f"<b>Company:</b> {quote.name}\n"
            f"<b>Sector:</b> {quote.sector}\n\n"
            f"🎯 <b>Rule:</b> {trigger_message}\n"
            f"💵 <b>Current LTP:</b> PKR {quote.current:,.2f} ({'+' if quote.change >= 0 else ''}{quote.change:,.2f} | {'+' if quote.change_pct >= 0 else ''}{quote.change_pct:.2f}%)\n"
            f"📊 <b>Volume:</b> {quote.volume:,} shares\n"
            f"⏱ <b>Timestamp:</b> {pkt_time_str}\n"
        )

        if alert.get("notes"):
            text += f"📝 <b>Note:</b> {alert['notes']}\n"

        if alert.get("trigger_mode") == AlertTriggerMode.ONCE.value:
            text += "⚠️ <i>One-shot alert disabled until reset.</i>\n"

        text += f"\n🔗 <a href='https://dps.psx.com.pk/company/{quote.symbol}'>View on PSX Data Portal</a>"

        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }

        try:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                logger.info("Successfully sent Telegram alert for %s", quote.symbol)
                return True, None
            else:
                err_msg = f"HTTP {resp.status_code}: {resp.text}"
                logger.warning("Telegram dispatch error: %s", err_msg)
                return False, err_msg
        except Exception as e:
            logger.error("Failed to connect to Telegram API: %s", e)
            return False, str(e)

    async def send_test_telegram(self, token: str, chat_id: str) -> (bool, str):
        """
        Sends a test message to verify the Telegram Bot Token and Chat ID.
        """
        client = await self.get_http_client()
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        pkt_time_str = datetime.now(PKT).strftime("%Y-%m-%d %I:%M:%S %p PKT")

        text = (
            f"✅ <b>PSX Real-Time Dashboard Connected!</b>\n\n"
            f"Your Telegram notification channel is active and verified.\n"
            f"<b>Time:</b> {pkt_time_str}\n"
            f"You will receive instant automated alerts whenever your price or volume rules trigger."
        )

        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }

        try:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return True, "Test alert sent successfully! Check your Telegram chat."
            else:
                data = resp.json()
                desc = data.get("description", resp.text)
                return False, f"Telegram API error ({resp.status_code}): {desc}"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"
