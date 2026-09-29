"""
Pydantic data models for PSX Market Dashboard & Alert System.
"""
from datetime import datetime
from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator


class AlertConditionType(str, Enum):
    PRICE_ABOVE = "price_above"
    PRICE_BELOW = "price_below"
    PCT_CHANGE_ABOVE = "pct_change_above"
    PCT_CHANGE_BELOW = "pct_change_below"
    VOLUME_ABOVE = "volume_above"


class AlertTriggerMode(str, Enum):
    COOLDOWN = "cooldown"  # Re-triggers after cooldown minutes
    ONCE = "once"          # Triggers once and disables until manually reset


class MarketSessionStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    RECESS = "RECESS"     # Friday prayer recess (12:00 - 14:30 PKT)


class StockQuote(BaseModel):
    symbol: str
    name: str
    sector: str = "GENERAL"
    ldcp: float = Field(0.0, description="Last Day Close Price")
    open: float = Field(0.0, description="Open Price")
    high: float = Field(0.0, description="Day High")
    low: float = Field(0.0, description="Day Low")
    current: float = Field(0.0, description="Current Price / Last Traded Price (LTP)")
    change: float = Field(0.0, description="Net Price Change")
    change_pct: float = Field(0.0, description="Percentage Change")
    volume: int = Field(0, description="Total Traded Shares")
    last_updated: datetime = Field(default_factory=datetime.utcnow)
    is_up: bool = False
    is_down: bool = False

    @field_validator("change_pct", mode="before")
    @classmethod
    def parse_change_pct(cls, v):
        if isinstance(v, str):
            v = v.replace("%", "").replace(",", "").strip()
            try:
                return float(v)
            except ValueError:
                return 0.0
        return float(v or 0.0)

    @field_validator("volume", mode="before")
    @classmethod
    def parse_volume(cls, v):
        if isinstance(v, str):
            v = v.replace(",", "").strip()
            try:
                return int(float(v))
            except ValueError:
                return 0
        return int(v or 0)

    @field_validator("ldcp", "open", "high", "low", "current", "change", mode="before")
    @classmethod
    def parse_float_fields(cls, v):
        if isinstance(v, str):
            v = v.replace(",", "").strip()
            try:
                return float(v)
            except ValueError:
                return 0.0
        return float(v or 0.0)


class IndexQuote(BaseModel):
    code: str
    name: str
    high: float = 0.0
    low: float = 0.0
    current: float = 0.0
    change: float = 0.0
    change_pct: float = 0.0
    volume: int = 0
    last_updated: datetime = Field(default_factory=datetime.utcnow)


class MarketSummary(BaseModel):
    status: MarketSessionStatus
    status_reason: str
    pkt_time: str
    next_session_info: str
    kse100: Optional[IndexQuote] = None
    allshr: Optional[IndexQuote] = None
    kse30: Optional[IndexQuote] = None
    total_volume: int = 0
    advancers: int = 0
    decliners: int = 0
    unchanged: int = 0
    total_symbols: int = 0
    last_sync: str


class AlertCreate(BaseModel):
    symbol: str
    condition_type: AlertConditionType
    target_value: float = Field(..., gt=0, description="Target price, percentage, or volume threshold")
    trigger_mode: AlertTriggerMode = AlertTriggerMode.COOLDOWN
    cooldown_minutes: int = Field(15, ge=1, le=1440, description="Cooldown period in minutes between triggers")
    notes: Optional[str] = Field(None, max_length=250)


class AlertUpdate(BaseModel):
    is_active: Optional[bool] = None
    target_value: Optional[float] = Field(None, gt=0)
    cooldown_minutes: Optional[int] = Field(None, ge=1, le=1440)
    trigger_mode: Optional[AlertTriggerMode] = None
    notes: Optional[str] = None


class AlertOut(BaseModel):
    id: int
    symbol: str
    name: str
    condition_type: AlertConditionType
    target_value: float
    trigger_mode: AlertTriggerMode
    cooldown_minutes: int
    is_active: bool
    last_triggered_at: Optional[str] = None
    created_at: str
    notes: Optional[str] = None
    trigger_count: int = 0


class AlertHistoryOut(BaseModel):
    id: int
    alert_id: int
    symbol: str
    condition_type: AlertConditionType
    target_value: float
    actual_value: float
    message: str
    triggered_at: str
    telegram_sent: bool
    telegram_error: Optional[str] = None


class SystemSettings(BaseModel):
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    telegram_enabled: bool = False
    poll_interval_seconds: int = Field(8, ge=3, le=60)
    min_volume_gainers: int = Field(50000, ge=0)
    sound_alerts_enabled: bool = True
