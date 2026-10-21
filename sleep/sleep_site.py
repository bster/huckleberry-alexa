"""Small website showing the sleep comparison, for viewing from phones at home.

Its own process with its own Huckleberry login — nothing here touches the
Alexa server. Listens on all interfaces so phones on the home Wi-Fi can reach it
at http://<this-mac>.local:8768/.

    uv run sleep_site.py
"""

import asyncio
import html
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import aiohttp
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

import sleep_report  # loads this folder's .env
from huckleberry_api import HuckleberryAPI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S %z",
)
logger = logging.getLogger("sleep_site")

# 8765 is the Alexa server, 8766 its credential setup server, 8767 its dev instance.
PORT = int(os.getenv("SLEEP_SITE_PORT", "8768"))

# Building the page reads every sleep ever logged for every child, which takes a
# few seconds; caching keeps a phone refresh from repeating that, and the lock
# stops two phones opening it at once from doing it twice.
PAGE_TTL_S = 15 * 60

_session: aiohttp.ClientSession | None = None
_api: HuckleberryAPI | None = None
_page: tuple[float, str] | None = None   # (monotonic time built, html)
_lock = asyncio.Lock()


async def _get_api() -> HuckleberryAPI:
    # Logged in on first use rather than at startup: a bad password should show
    # up on the page, not as a process launchd keeps restarting.
    global _api
    if _api is None:
        api = HuckleberryAPI(
            email=os.environ["HUCKLEBERRY_EMAIL"],
            password=os.environ["HUCKLEBERRY_PASSWORD"],
            timezone=str(sleep_report.TZ),
            websession=_session,
        )
        await api.authenticate()
        _api = api
    return _api


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _session
    _session = aiohttp.ClientSession()
    yield
    await _session.close()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/", response_class=HTMLResponse)
async def page(refresh: bool = False):
    global _page, _api
    async with _lock:
        if refresh or _page is None or time.monotonic() - _page[0] > PAGE_TTL_S:
            started = time.perf_counter()
            try:
                children, _ = await sleep_report.fetch_children(
                    await _get_api(),
                    since=datetime(2010, 1, 1, tzinfo=timezone.utc),
                    until=datetime.now(tz=timezone.utc) + timedelta(minutes=5),
                )
            except Exception as err:
                logger.exception("Could not load sleep data")
                _api = None  # log in afresh next time
                return HTMLResponse(
                    f"<!doctype html><meta name=viewport content='width=device-width'>"
                    f"<p style='font:16px system-ui;padding:16px'>Couldn't load sleep data from Huckleberry: "
                    f"{html.escape(type(err).__name__)}. <a href='?refresh=1'>Try again</a></p>",
                    status_code=502,
                )
            _page = (time.monotonic(), sleep_report.build_report(children, refresh_link=True)["html"])
            logger.info("Page built in %.0fms", (time.perf_counter() - started) * 1000)
    return _page[1]


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
