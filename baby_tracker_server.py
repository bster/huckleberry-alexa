import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional
from zoneinfo import ZoneInfo

import aiohttp
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

load_dotenv()

from huckleberry_api import HuckleberryAPI  # noqa: E402

logger = logging.getLogger(__name__)

HUCKLEBERRY_TIMEZONE = os.getenv("HUCKLEBERRY_TIMEZONE", "America/New_York")

ALEXA_SKILL_ID = "amzn1.ask.skill.9e90f9f8-d095-4378-bef9-5c9830317c3c"

BottleType = Literal["Breast Milk", "Formula", "Tube Feeding", "Cow Milk", "Goat Milk", "Soy Milk", "Other"]

_api: HuckleberryAPI | None = None
_http_session: aiohttp.ClientSession | None = None
_child_uid: str | None = None      # default child (from HUCKLEBERRY_CHILD_INDEX)
_children: list[dict] = []         # [{index, cid, nickname}, ...]
_last_event: dict | None = None


def _now() -> datetime:
    return datetime.now(tz=ZoneInfo(HUCKLEBERRY_TIMEZONE))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record(event_type: str, details: dict | None = None) -> None:
    global _last_event
    _last_event = {"type": event_type, "timestamp": _now_iso(), **(details or {})}


def _get_api() -> HuckleberryAPI:
    if _api is None:
        raise HTTPException(status_code=503, detail="Huckleberry API not initialized")
    return _api


def _resolve_child(child: int | str | None) -> str:
    """Resolve a child param to a Firebase cid.

    - None      → default child (HUCKLEBERRY_CHILD_INDEX)
    - int       → index into _children list
    - str       → treat as raw Firebase cid
    """
    if _child_uid is None:
        raise HTTPException(status_code=503, detail="Huckleberry API not initialized")
    if child is None:
        return _child_uid
    if isinstance(child, int):
        try:
            return _children[child]["cid"]
        except IndexError:
            raise HTTPException(
                status_code=400,
                detail=f"No child at index {child} — have {len(_children)} child(ren). Call GET /children to list them.",
            )
    return child  # raw cid string


# ---------------------------------------------------------------------------
# Internal async helpers — shared by REST endpoints and the Alexa handler
# ---------------------------------------------------------------------------


async def _do_feeding_start(side: str = "left", child: int | str | None = None) -> dict:
    api = _get_api()
    child_uid = _resolve_child(child)
    await api.start_nursing(child_uid, side=side)
    _record("feeding_start", {"side": side, "child_uid": child_uid})
    return {"side": side, "child_uid": child_uid}


async def _do_feeding_end(child: int | str | None = None) -> dict:
    api = _get_api()
    child_uid = _resolve_child(child)
    await api.complete_nursing(child_uid)
    _record("feeding_end", {"child_uid": child_uid})
    return {"child_uid": child_uid}


async def _do_feeding_bottle(
    amount_oz: float | None = None,
    bottle_type: BottleType = "Breast Milk",
    child: int | str | None = None,
) -> dict:
    api = _get_api()
    child_uid = _resolve_child(child)
    await api.log_bottle(
        child_uid,
        start_time=_now(),
        amount=amount_oz or 0.0,
        bottle_type=bottle_type,
        units="oz",
    )
    _record("feeding_bottle", {"amount_oz": amount_oz, "bottle_type": bottle_type, "child_uid": child_uid})
    return {"amount_oz": amount_oz, "bottle_type": bottle_type, "child_uid": child_uid}


async def _do_last_feeding(child: int | str | None = None) -> dict:
    api = _get_api()
    child_uid = _resolve_child(child)
    now = _now()
    intervals = await api.list_feed_intervals(
        child_uid,
        start_time=now - timedelta(hours=48),
        end_time=now + timedelta(minutes=5),
    )
    if not intervals:
        intervals = await api.list_feed_intervals(
            child_uid,
            start_time=now - timedelta(days=7),
            end_time=now + timedelta(minutes=5),
        )
    if not intervals:
        return {"type": None, "started_at": None, "minutes_ago": None}
    latest = max(intervals, key=lambda i: float(i.start))
    mode = getattr(latest, "mode", "unknown")
    start_ts = float(latest.start)
    started_at = datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat()
    minutes_ago = int((now.timestamp() - start_ts) / 60)
    if mode == "breast":
        side = getattr(latest, "lastSide", "unknown")
        feed_type = f"breast_{side}"
    elif mode == "bottle":
        feed_type = "bottle"
    else:
        feed_type = mode
    return {"type": feed_type, "started_at": started_at, "minutes_ago": minutes_ago}


async def _do_last_diaper(child: int | str | None = None) -> dict:
    api = _get_api()
    child_uid = _resolve_child(child)
    now = _now()
    intervals = await api.list_diaper_intervals(
        child_uid,
        start_time=now - timedelta(hours=48),
        end_time=now + timedelta(minutes=5),
    )
    if not intervals:
        intervals = await api.list_diaper_intervals(
            child_uid,
            start_time=now - timedelta(days=7),
            end_time=now + timedelta(minutes=5),
        )
    if not intervals:
        return {"type": None, "logged_at": None, "minutes_ago": None}
    latest = max(intervals, key=lambda i: float(i.start))
    mode = latest.mode
    mode_to_type = {"pee": "wet", "poo": "dirty", "both": "both", "dry": "dry"}
    diaper_type = mode_to_type.get(mode, mode)
    start_ts = float(latest.start)
    logged_at = datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat()
    minutes_ago = int((now.timestamp() - start_ts) / 60)
    return {"type": diaper_type, "logged_at": logged_at, "minutes_ago": minutes_ago}


async def _do_sleep_start(child: int | str | None = None) -> dict:
    api = _get_api()
    child_uid = _resolve_child(child)
    await api.start_sleep(child_uid)
    _record("sleep_start", {"child_uid": child_uid})
    return {"child_uid": child_uid}


async def _do_sleep_end(child: int | str | None = None) -> dict:
    api = _get_api()
    child_uid = _resolve_child(child)
    await api.complete_sleep(child_uid)
    _record("sleep_end", {"child_uid": child_uid})
    return {"child_uid": child_uid}


async def _do_diaper_log(diaper_type: str = "wet", child: int | str | None = None) -> dict:
    api = _get_api()
    child_uid = _resolve_child(child)
    type_to_mode = {"wet": "pee", "dirty": "poo", "both": "both", "dry": "dry"}
    mode = type_to_mode.get(diaper_type, "pee")
    pee_amount: str | None = "medium" if mode in ("pee", "both") else None
    poo_amount: str | None = "medium" if mode in ("poo", "both") else None
    await api.log_diaper(
        child_uid,
        start_time=_now(),
        mode=mode,
        pee_amount=pee_amount,
        poo_amount=poo_amount,
    )
    _record("diaper_log", {"type": diaper_type, "mode": mode, "child_uid": child_uid})
    return {"mode": mode, "child_uid": child_uid}


async def _do_switch_side(child: int | str | None = None) -> dict:
    api = _get_api()
    child_uid = _resolve_child(child)
    await api.switch_nursing_side(child_uid)
    _record("feeding_switch_side", {"child_uid": child_uid})
    return {"child_uid": child_uid}


async def _do_cancel_feeding(child: int | str | None = None) -> dict:
    api = _get_api()
    child_uid = _resolve_child(child)
    await api.cancel_nursing(child_uid)
    _record("feeding_cancel", {"child_uid": child_uid})
    return {"child_uid": child_uid}


async def _do_cancel_sleep(child: int | str | None = None) -> dict:
    api = _get_api()
    child_uid = _resolve_child(child)
    await api.cancel_sleep(child_uid)
    _record("sleep_cancel", {"child_uid": child_uid})
    return {"child_uid": child_uid}


async def _do_pump_log(
    duration_minutes: float | None = None,
    amount_oz: float | None = None,
    child: int | str | None = None,
) -> dict:
    api = _get_api()
    child_uid = _resolve_child(child)
    await api.log_pump(
        child_uid,
        start_time=_now(),
        total_amount=amount_oz or 0.0,
        duration=duration_minutes * 60 if duration_minutes else 0,
        units="oz",
    )
    _record("pump_log", {"amount_oz": amount_oz, "duration_minutes": duration_minutes, "child_uid": child_uid})
    return {"amount_oz": amount_oz, "duration_minutes": duration_minutes, "child_uid": child_uid}


async def _do_last_pump(child: int | str | None = None) -> dict:
    api = _get_api()
    child_uid = _resolve_child(child)
    now = _now()
    intervals = await api.list_pump_intervals(
        child_uid,
        start_time=now - timedelta(hours=48),
        end_time=now + timedelta(minutes=5),
    )
    if not intervals:
        intervals = await api.list_pump_intervals(
            child_uid,
            start_time=now - timedelta(days=7),
            end_time=now + timedelta(minutes=5),
        )
    if not intervals:
        return {"amount": None, "units": None, "logged_at": None, "minutes_ago": None}
    latest = max(intervals, key=lambda i: float(i.start))
    start_ts = float(latest.start)
    logged_at = datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat()
    minutes_ago = int((now.timestamp() - start_ts) / 60)
    total_amount = (latest.leftAmount or 0.0) + (latest.rightAmount or 0.0)
    return {
        "amount": total_amount,
        "units": latest.units,
        "logged_at": logged_at,
        "minutes_ago": minutes_ago,
    }


async def _do_log_growth(weight_oz: float | None = None, child: int | str | None = None) -> dict:
    api = _get_api()
    child_uid = _resolve_child(child)
    weight_lbs = weight_oz / 16 if weight_oz else None
    await api.log_growth(
        child_uid,
        start_time=_now(),
        weight=weight_lbs,
        units="imperial",
    )
    _record("growth_log", {"weight_oz": weight_oz, "child_uid": child_uid})
    return {"weight_oz": weight_oz, "child_uid": child_uid}


async def _do_log_activity(
    mode: str,
    duration_minutes: float | None = None,
    child: int | str | None = None,
) -> dict:
    api = _get_api()
    child_uid = _resolve_child(child)
    await api.log_activity(
        child_uid,
        mode=mode,
        start_time=_now(),
        duration=duration_minutes * 60 if duration_minutes else 0,
    )
    _record("activity_log", {"mode": mode, "duration_minutes": duration_minutes, "child_uid": child_uid})
    return {"mode": mode, "duration_minutes": duration_minutes, "child_uid": child_uid}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _api, _http_session, _child_uid, _children
    _http_session = aiohttp.ClientSession()
    _api = HuckleberryAPI(
        email=os.environ["HUCKLEBERRY_EMAIL"],
        password=os.environ["HUCKLEBERRY_PASSWORD"],
        timezone=HUCKLEBERRY_TIMEZONE,
        websession=_http_session,
    )
    await _api.authenticate()
    user_doc = await _api.get_user()
    _children = [
        {"index": i, "cid": c.cid, "nickname": c.nickname}
        for i, c in enumerate(user_doc.childList)
    ]
    child_index = int(os.getenv("HUCKLEBERRY_CHILD_INDEX", "0"))
    _child_uid = user_doc.childList[child_index].cid
    yield
    await _http_session.close()


app = FastAPI(title="Baby Tracker API", version="1.0.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Request models
# All include an optional `child` field:
#   null / omitted → HUCKLEBERRY_CHILD_INDEX default
#   integer        → index into the childList (0, 1, 2…)
#   string         → raw Firebase cid
# ---------------------------------------------------------------------------


class FeedingStartRequest(BaseModel):
    side: Literal["left", "right"] = "left"
    child: int | str | None = None


class FeedingEndRequest(BaseModel):
    child: int | str | None = None


class FeedingLogRequest(BaseModel):
    side: Literal["left", "right"] = "left"
    start_time: Optional[str] = None  # ISO 8601
    end_time: Optional[str] = None    # ISO 8601; defaults to now
    type: Literal["nursing", "bottle"] = "nursing"
    # Bottle-only fields
    amount: Optional[float] = None
    units: Literal["oz", "ml"] = "oz"
    bottle_type: BottleType = "Breast Milk"
    child: int | str | None = None


class BottleFeedingRequest(BaseModel):
    amount_oz: Optional[float] = None
    bottle_type: BottleType = "Breast Milk"
    milk_type: Optional[str] = None
    child: int | str | None = None


class SleepStartRequest(BaseModel):
    child: int | str | None = None


class SleepEndRequest(BaseModel):
    child: int | str | None = None


class SleepLogRequest(BaseModel):
    start_time: Optional[str] = None  # ISO 8601
    end_time: Optional[str] = None    # ISO 8601; defaults to now
    child: int | str | None = None


class DiaperLogRequest(BaseModel):
    type: Literal["wet", "dirty", "both", "dry"] = "wet"
    color: Optional[Literal["yellow", "brown", "black", "green", "red", "gray"]] = None
    consistency: Optional[
        Literal["solid", "loose", "runny", "mucousy", "hard", "pebbles", "diarrhea"]
    ] = None
    pee_amount: Optional[Literal["little", "medium", "big"]] = None
    poo_amount: Optional[Literal["little", "medium", "big"]] = None
    notes: Optional[str] = None
    child: int | str | None = None


class SwitchSideRequest(BaseModel):
    child: int | str | None = None


class CancelFeedingRequest(BaseModel):
    child: int | str | None = None


class CancelSleepRequest(BaseModel):
    child: int | str | None = None


class PumpLogRequest(BaseModel):
    amount_oz: Optional[float] = None
    duration_minutes: Optional[float] = None
    child: int | str | None = None


class GrowthLogRequest(BaseModel):
    weight_oz: Optional[float] = None
    child: int | str | None = None


class ActivityLogRequest(BaseModel):
    mode: Literal[
        "tummyTime", "bath", "storyTime", "screenTime", "skinToSkin", "outdoorPlay", "indoorPlay", "brushTeeth"
    ]
    duration_minutes: Optional[float] = None
    child: int | str | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/status")
async def status():
    return {
        "ok": True,
        "default_child_uid": _child_uid,
        "last_event": _last_event,
    }


@app.get("/children")
async def list_children():
    """List all children in the Huckleberry account with their index and cid."""
    return {
        "children": _children,
        "default_index": int(os.getenv("HUCKLEBERRY_CHILD_INDEX", "0")),
    }


@app.post("/feeding/start")
async def feeding_start(req: FeedingStartRequest = FeedingStartRequest()):
    result = await _do_feeding_start(side=req.side, child=req.child)
    return {"ok": True, **result}


@app.post("/feeding/end")
async def feeding_end(req: FeedingEndRequest = FeedingEndRequest()):
    result = await _do_feeding_end(child=req.child)
    return {"ok": True, **result}


@app.post("/feeding/log")
async def feeding_log(req: FeedingLogRequest = FeedingLogRequest()):
    api = _get_api()
    child_uid = _resolve_child(req.child)
    now = _now()

    if req.type == "bottle":
        start_time = datetime.fromisoformat(req.start_time) if req.start_time else now
        await api.log_bottle(
            child_uid,
            start_time=start_time,
            amount=req.amount or 0.0,
            bottle_type=req.bottle_type,
            units=req.units,
        )
        _record("feeding_log_bottle", {"amount": req.amount, "units": req.units, "bottle_type": req.bottle_type, "child_uid": child_uid})
    else:
        end_time = datetime.fromisoformat(req.end_time) if req.end_time else now
        start_time = datetime.fromisoformat(req.start_time) if req.start_time else end_time - timedelta(minutes=10)
        await api.log_nursing(
            child_uid,
            start_time=start_time,
            end_time=end_time,
            side=req.side,
        )
        _record("feeding_log_nursing", {"side": req.side, "child_uid": child_uid})

    return {"ok": True, "type": req.type, "child_uid": child_uid}


@app.post("/feeding/bottle")
async def feeding_bottle(req: BottleFeedingRequest = BottleFeedingRequest()):
    """Log a bottle feeding. amount_oz is optional; milk_type/bottle_type default to 'Breast Milk'."""
    bottle_type = req.milk_type or req.bottle_type
    result = await _do_feeding_bottle(amount_oz=req.amount_oz, bottle_type=bottle_type, child=req.child)
    return {"ok": True, **result}


@app.get("/feeding/last")
async def feeding_last(child: int | str | None = None):
    """Return the most recent completed feeding (breast or bottle)."""
    result = await _do_last_feeding(child=child)
    return {"ok": True, **result}


@app.get("/diaper/last")
async def diaper_last(child: int | str | None = None):
    """Return the most recent diaper change."""
    result = await _do_last_diaper(child=child)
    return {"ok": True, **result}


@app.post("/sleep/start")
async def sleep_start(req: SleepStartRequest = SleepStartRequest()):
    result = await _do_sleep_start(child=req.child)
    return {"ok": True, **result}


@app.post("/sleep/end")
async def sleep_end(req: SleepEndRequest = SleepEndRequest()):
    result = await _do_sleep_end(child=req.child)
    return {"ok": True, **result}


@app.post("/sleep/log")
async def sleep_log(req: SleepLogRequest = SleepLogRequest()):
    api = _get_api()
    child_uid = _resolve_child(req.child)
    now = _now()
    end_time = datetime.fromisoformat(req.end_time) if req.end_time else now
    start_time = datetime.fromisoformat(req.start_time) if req.start_time else end_time - timedelta(hours=1)
    await api.log_sleep(child_uid, start_time=start_time, end_time=end_time)
    _record("sleep_log", {"start_time": start_time.isoformat(), "end_time": end_time.isoformat(), "child_uid": child_uid})
    return {"ok": True, "child_uid": child_uid}


@app.post("/diaper/log")
async def diaper_log(req: DiaperLogRequest = DiaperLogRequest()):
    api = _get_api()
    child_uid = _resolve_child(req.child)

    type_to_mode = {"wet": "pee", "dirty": "poo", "both": "both", "dry": "dry"}
    mode = type_to_mode[req.type]

    pee_amount = req.pee_amount or ("medium" if mode in ("pee", "both") else None)
    poo_amount = req.poo_amount or ("medium" if mode in ("poo", "both") else None)

    await api.log_diaper(
        child_uid,
        start_time=_now(),
        mode=mode,
        pee_amount=pee_amount,
        poo_amount=poo_amount,
        color=req.color,
        consistency=req.consistency,
        notes=req.notes,
    )
    _record("diaper_log", {"type": req.type, "mode": mode, "child_uid": child_uid})
    return {"ok": True, "mode": mode, "child_uid": child_uid}


@app.post("/feeding/switch")
async def feeding_switch(req: SwitchSideRequest = SwitchSideRequest()):
    """Switch the active nursing side."""
    result = await _do_switch_side(child=req.child)
    return {"ok": True, **result}


@app.post("/feeding/cancel")
async def feeding_cancel(req: CancelFeedingRequest = CancelFeedingRequest()):
    """Cancel the current nursing session without saving it."""
    result = await _do_cancel_feeding(child=req.child)
    return {"ok": True, **result}


@app.post("/sleep/cancel")
async def sleep_cancel(req: CancelSleepRequest = CancelSleepRequest()):
    """Cancel the current sleep session without saving it."""
    result = await _do_cancel_sleep(child=req.child)
    return {"ok": True, **result}


@app.post("/pump")
async def pump_log(req: PumpLogRequest = PumpLogRequest()):
    """Log a pump session."""
    result = await _do_pump_log(duration_minutes=req.duration_minutes, amount_oz=req.amount_oz, child=req.child)
    return {"ok": True, **result}


@app.get("/pump/last")
async def pump_last(child: int | str | None = None):
    """Return the most recent pump session."""
    result = await _do_last_pump(child=child)
    return {"ok": True, **result}


@app.post("/growth")
async def growth_log(req: GrowthLogRequest = GrowthLogRequest()):
    """Log a weight measurement."""
    result = await _do_log_growth(weight_oz=req.weight_oz, child=req.child)
    return {"ok": True, **result}


@app.get("/growth/last")
async def growth_last(child: int | str | None = None):
    """Return the most recent growth entry."""
    api = _get_api()
    child_uid = _resolve_child(child)
    result = await api.get_latest_growth(child_uid)
    return {"ok": True, "growth": result.model_dump() if result else None}


@app.post("/activity")
async def activity_log(req: ActivityLogRequest):
    """Log an activity (tummy time, bath, etc.)."""
    result = await _do_log_activity(mode=req.mode, duration_minutes=req.duration_minutes, child=req.child)
    return {"ok": True, **result}


# ---------------------------------------------------------------------------
# Alexa skill endpoint
# ---------------------------------------------------------------------------


def _alexa_response(text: str, end_session: bool = True) -> dict:
    return {
        "version": "1.0",
        "response": {
            "outputSpeech": {"type": "PlainText", "text": text},
            "shouldEndSession": end_session,
        },
    }


def _format_oz(oz: float) -> str:
    return str(int(oz)) if oz == int(oz) else str(oz)


def _format_minutes(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours = minutes // 60
    mins = minutes % 60
    if mins == 0:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{hours} hour{'s' if hours != 1 else ''} and {mins} minute{'s' if mins != 1 else ''}"


@app.post("/alexa")
async def alexa(request: Request) -> Any:
    try:
        body = await request.json()
    except Exception:
        return _alexa_response("Sorry, I couldn't understand the request.")

    # LaunchRequests may omit the top-level "session" key; fall back to context.System.
    app_id = (
        body.get("session", {}).get("application", {}).get("applicationId", "")
        or body.get("context", {}).get("System", {}).get("application", {}).get("applicationId", "")
    )
    if app_id != ALEXA_SKILL_ID:
        logger.warning("Rejected request with applicationId: %s", app_id)
        raise HTTPException(status_code=403, detail="Invalid application ID")

    req = body.get("request", {})
    req_type = req.get("type", "")

    try:
        if req_type == "LaunchRequest":
            return _alexa_response(
                "Huckleberry ready. You can say: start feeding, end feeding, "
                "log a diaper, start sleep, end sleep, log a bottle, "
                "last feeding, or last diaper.",
                end_session=False,
            )

        if req_type == "IntentRequest":
            intent = req.get("intent", {})
            intent_name = intent.get("name", "")
            slots = intent.get("slots", {})

            if intent_name == "StartFeedingIntent":
                side_slot = slots.get("side", {})
                side = (side_slot.get("value") or "left").lower()
                if side not in ("left", "right"):
                    side = "left"
                await _do_feeding_start(side=side)
                return _alexa_response(f"Starting {side} side feeding. Good luck!")

            if intent_name == "EndFeedingIntent":
                await _do_feeding_end()
                return _alexa_response("Feeding ended and logged.")

            if intent_name == "LogBottleIntent":
                amount_slot = slots.get("bottle_amount", {})
                amount_val = amount_slot.get("value")
                amount_oz = float(amount_val) if amount_val else None
                milk_slot = (slots.get("milk_type", {}).get("value") or "").lower()
                bottle_type = "Formula" if "formula" in milk_slot else "Breast Milk"
                await _do_feeding_bottle(amount_oz=amount_oz, bottle_type=bottle_type)
                type_word = "formula" if bottle_type == "Formula" else "breast milk"
                if amount_oz is not None:
                    return _alexa_response(f"Logged a {_format_oz(amount_oz)} ounce {type_word} bottle.")
                return _alexa_response(f"Logged a {type_word} bottle.")

            if intent_name == "LastFeedingIntent":
                result = await _do_last_feeding()
                if result["minutes_ago"] is None:
                    return _alexa_response("I couldn't find any recent feedings.")
                ago = _format_minutes(result["minutes_ago"])
                feed_type = result["type"] or ""
                if feed_type == "bottle":
                    return _alexa_response(f"Last feeding was a bottle, {ago} ago.")
                elif feed_type.startswith("breast_"):
                    side = feed_type.split("_", 1)[1]
                    return _alexa_response(f"Last feeding was {ago} ago, on the {side} side.")
                return _alexa_response(f"Last feeding was {ago} ago.")

            if intent_name == "LastDiaperIntent":
                result = await _do_last_diaper()
                if result["minutes_ago"] is None:
                    return _alexa_response("I couldn't find any recent diaper changes.")
                ago = _format_minutes(result["minutes_ago"])
                diaper_type = result["type"] or "diaper"
                return _alexa_response(f"Last diaper was a {diaper_type} diaper, {ago} ago.")

            if intent_name == "LogDiaperIntent":
                type_slot = slots.get("diaper_type", {})
                diaper_type = (type_slot.get("value") or "wet").lower()
                if diaper_type not in ("wet", "dirty", "both", "dry"):
                    diaper_type = "wet"
                await _do_diaper_log(diaper_type=diaper_type)
                return _alexa_response(f"Logged a {diaper_type} diaper.")

            if intent_name == "StartSleepIntent":
                await _do_sleep_start()
                return _alexa_response("Sleep started. Sweet dreams!")

            if intent_name == "EndSleepIntent":
                await _do_sleep_end()
                return _alexa_response("Sleep ended and logged.")

            if intent_name == "SwitchSideIntent":
                await _do_switch_side()
                return _alexa_response("Switched to the other side.")

            if intent_name == "CancelFeedingIntent":
                await _do_cancel_feeding()
                return _alexa_response("Feeding cancelled.")

            if intent_name == "CancelSleepIntent":
                await _do_cancel_sleep()
                return _alexa_response("Sleep session cancelled.")

            if intent_name == "LogPumpIntent":
                amount_slot = slots.get("pump_amount", {})
                amount_val = amount_slot.get("value")
                amount_oz = float(amount_val) if amount_val else None
                duration_slot = slots.get("pump_duration", {})
                duration_val = duration_slot.get("value")
                duration_minutes = float(duration_val) if duration_val else None
                await _do_pump_log(duration_minutes=duration_minutes, amount_oz=amount_oz)
                if amount_oz is not None:
                    return _alexa_response(f"Logged a {_format_oz(amount_oz)} ounce pump session.")
                return _alexa_response("Logged a pump session.")

            if intent_name == "LastPumpIntent":
                result = await _do_last_pump()
                if result["minutes_ago"] is None:
                    return _alexa_response("I couldn't find any recent pump sessions.")
                ago = _format_minutes(result["minutes_ago"])
                return _alexa_response(f"Last pump was {ago} ago.")

            if intent_name == "LogWeightIntent":
                weight_slot = slots.get("weight_oz", {})
                weight_val = weight_slot.get("value")
                weight_oz = float(weight_val) if weight_val else None
                await _do_log_growth(weight_oz=weight_oz)
                return _alexa_response("Logged her weight.")

            if intent_name == "LogTummyTimeIntent":
                duration_slot = slots.get("duration", {})
                duration_val = duration_slot.get("value")
                duration_minutes = float(duration_val) if duration_val else None
                await _do_log_activity("tummyTime", duration_minutes)
                return _alexa_response("Logged tummy time.")

            if intent_name == "LogBathIntent":
                await _do_log_activity("bath")
                return _alexa_response("Logged bath time.")

            if intent_name == "AMAZON.FallbackIntent":
                return _alexa_response(
                    "Sorry, I didn't catch that. Try saying something like "
                    "'start feeding on the left' or 'log a diaper'."
                )

            if intent_name == "AMAZON.HelpIntent":
                return _alexa_response(
                    "You can say: start left feeding, end feeding, log a bottle, "
                    "log a wet diaper, start sleep, end sleep, "
                    "last feeding, or last diaper.",
                    end_session=False,
                )

            if intent_name in ("AMAZON.CancelIntent", "AMAZON.StopIntent"):
                return _alexa_response("Goodbye!")

            return _alexa_response(f"Sorry, I don't know how to handle {intent_name}.")

        return _alexa_response("Sorry, I received an unexpected request type.")

    except Exception:
        logger.exception("Alexa handler error")
        return _alexa_response("Sorry, something went wrong. Please try again.")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("baby_tracker_server:app", host="0.0.0.0", port=8765, reload=False)
