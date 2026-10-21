"""Compare sleep across all children on the account.

Pulls every sleep interval Huckleberry has for each child, summarises it by day
and by age, and writes CSVs plus an HTML page of charts. Optionally logs in to
Happiest Baby (SNOO) as well and saves whatever its history endpoints return,
untouched, so we can see what sleep data it actually exposes before trying to
merge it in.

Read-only: nothing here writes to either account.

    .venv/bin/python sleep_report.py                  # all history
    .venv/bin/python sleep_report.py --since 2024-01-01
    .venv/bin/python sleep_report.py --no-snoo

Output goes to sleep_reports/<timestamp>/ (gitignored — it holds the kids' data).
"""

import argparse
import asyncio
import csv
import inspect
import json
import logging
import os
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
from dotenv import load_dotenv

load_dotenv()

from huckleberry_api import HuckleberryAPI  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("sleep_report")

TZ = ZoneInfo(os.getenv("HUCKLEBERRY_TIMEZONE", "America/New_York"))

# A "sleep day" runs from DAY_START to DAY_START the next morning, so a night's
# sleep counts toward the evening it began rather than being split at midnight.
DAY_START = time(6, 0)
# A sleep that starts in this window is night sleep; anything else is a nap.
# Wall-clock rules misclassify newborn sleep, which has no real night yet —
# read the first couple of months' night/nap split with that in mind.
NIGHT_START = time(18, 0)
NIGHT_END = time(6, 0)

# Longer than this is almost certainly a timer someone forgot to stop.
MAX_PLAUSIBLE_SLEEP = timedelta(hours=16)


@dataclass
class Sleep:
    child: str
    start: datetime          # local time
    end: datetime
    kind: str                # "night" or "nap"
    sleep_day: date
    age_days: int | None


# ---------------------------------------------------------------- Huckleberry


def _field(obj, name):
    """Read a field from either a pydantic model or a plain dict.

    huckleberry_api isn't pinned (uv.lock is gitignored), and its return types
    have changed between releases, so don't assume one shape.
    """
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _parse_birthdate(raw) -> date | None:
    if raw in (None, ""):
        return None
    if isinstance(raw, (int, float)):
        # Firestore timestamps here are seconds; tolerate milliseconds too.
        ts = raw / 1000 if raw > 1e11 else raw
        return datetime.fromtimestamp(ts, tz=TZ).date()
    text = str(raw)
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            pass
    logger.warning("Could not parse birthdate %r", raw)
    return None


async def _list_sleep(api: HuckleberryAPI, cid: str, since: datetime, until: datetime) -> list:
    # Older releases take start_time/end_time datetimes (what the server uses
    # for feeds and diapers); 0.2.x takes start_timestamp/end_timestamp ints.
    params = inspect.signature(api.list_sleep_intervals).parameters
    if "start_timestamp" in params:
        return await api.list_sleep_intervals(
            cid, start_timestamp=int(since.timestamp()), end_timestamp=int(until.timestamp())
        )
    return await api.list_sleep_intervals(cid, start_time=since, end_time=until)


async def fetch_huckleberry(session: aiohttp.ClientSession, since: datetime, until: datetime) -> tuple[list[dict], dict]:
    """Return ([{cid, name, birthdate, intervals: [{start, duration}]}], account_info)."""
    api = HuckleberryAPI(
        email=os.environ["HUCKLEBERRY_EMAIL"],
        password=os.environ["HUCKLEBERRY_PASSWORD"],
        timezone=str(TZ),
        websession=session,
    )
    await api.authenticate()
    user_doc = await api.get_user()
    # hbChilds looks like Huckleberry's own link to Happiest Baby children. If
    # it's populated, SNOO sleep may already be syncing into Huckleberry.
    account_info = {"hbChilds": list((_field(user_doc, "hbChilds") or {}).keys())}

    children = []
    for ref in user_doc.childList:
        name, birthdate = ref.nickname, None
        try:
            child_doc = await api.get_child(ref.cid)
            if child_doc is not None:
                name = child_doc.childsName or name
                birthdate = _parse_birthdate(child_doc.birthdate)
        except Exception as err:  # strict models reject unknown fields; carry on without them
            logger.warning("Could not read child document for %s: %s", ref.nickname or ref.cid, err)
        name = name or ref.cid[:6]

        raw = await _list_sleep(api, ref.cid, since, until)
        intervals = [
            {"start": float(_field(i, "start")), "duration": float(_field(i, "duration") or 0)}
            for i in raw
            if _field(i, "start") is not None
        ]
        logger.info("Huckleberry: %s — %d sleeps (born %s)", name, len(intervals), birthdate or "unknown")
        children.append({"cid": ref.cid, "name": name, "birthdate": birthdate, "intervals": intervals})
    return children, account_info


# ------------------------------------------------------------------- analysis


def _is_night(start: datetime) -> bool:
    t = start.time()
    return t >= NIGHT_START or t < NIGHT_END


def _sleep_day(start: datetime) -> date:
    return (start - timedelta(hours=DAY_START.hour, minutes=DAY_START.minute)).date()


def build_sleeps(children: list[dict]) -> tuple[list[Sleep], Counter]:
    sleeps, skipped = [], Counter()
    for child in children:
        for interval in child["intervals"]:
            duration = timedelta(seconds=interval["duration"])
            if duration <= timedelta(0):
                skipped[(child["name"], "zero length")] += 1
                continue
            if duration > MAX_PLAUSIBLE_SLEEP:
                skipped[(child["name"], f"over {MAX_PLAUSIBLE_SLEEP.seconds // 3600}h")] += 1
                continue
            start = datetime.fromtimestamp(interval["start"], tz=TZ)
            age = (start.date() - child["birthdate"]).days if child["birthdate"] else None
            sleeps.append(Sleep(
                child=child["name"],
                start=start,
                end=start + duration,
                kind="night" if _is_night(start) else "nap",
                sleep_day=_sleep_day(start),
                age_days=age,
            ))
    sleeps.sort(key=lambda s: (s.child, s.start))
    return sleeps, skipped


def _hours(td: timedelta) -> float:
    return round(td.total_seconds() / 3600, 2)


def _clock_minutes(dt: datetime, pivot_hour: int) -> float:
    """Minutes past midnight, shifted so times either side of pivot_hour average sensibly.

    Averaging 23:30 and 00:30 should give midnight, not noon; measuring bedtimes
    from noon (and wake times from midnight) keeps them on one side of the wrap.
    """
    minutes = dt.hour * 60 + dt.minute
    if dt.hour < pivot_hour:
        minutes += 24 * 60
    return minutes


def _fmt_clock(minutes: float | None) -> str:
    if minutes is None:
        return ""
    m = int(round(minutes)) % (24 * 60)
    return f"{m // 60:02d}:{m % 60:02d}"


def daily_summary(sleeps: list[Sleep]) -> list[dict]:
    by_day: dict[tuple[str, date], list[Sleep]] = defaultdict(list)
    for s in sleeps:
        by_day[(s.child, s.sleep_day)].append(s)

    rows = []
    for (child, day), items in sorted(by_day.items()):
        night = [s for s in items if s.kind == "night"]
        naps = [s for s in items if s.kind == "nap"]
        rows.append({
            "child": child,
            "sleep_day": day.isoformat(),
            "age_days": items[0].age_days,
            "total_hours": _hours(sum((s.end - s.start for s in items), timedelta())),
            "night_hours": _hours(sum((s.end - s.start for s in night), timedelta())),
            "nap_hours": _hours(sum((s.end - s.start for s in naps), timedelta())),
            "nap_count": len(naps),
            "night_wakings": max(len(night) - 1, 0),
            "longest_stretch_hours": _hours(max(s.end - s.start for s in items)),
            "bedtime": _fmt_clock(_clock_minutes(min(night, key=lambda s: s.start).start, 12)) if night else "",
            "wake_time": _fmt_clock(_clock_minutes(max(night, key=lambda s: s.end).end, 0)) if night else "",
            "_bed_min": _clock_minutes(min(night, key=lambda s: s.start).start, 12) if night else None,
            "_wake_min": _clock_minutes(max(night, key=lambda s: s.end).end, 0) if night else None,
        })
    return rows


def by_age_summary(days: list[dict]) -> list[dict]:
    """Average each child's days by month of age — the fair way to compare siblings."""
    buckets: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for d in days:
        if d["age_days"] is None or d["age_days"] < 0:
            continue
        buckets[(d["child"], int(d["age_days"] // 30.44))].append(d)

    def avg(rows, key):
        values = [r[key] for r in rows if r[key] is not None]
        return round(statistics.mean(values), 2) if values else None

    out = []
    for (child, month), rows in sorted(buckets.items()):
        out.append({
            "child": child,
            "age_month": month,
            "days_logged": len(rows),
            "avg_total_hours": avg(rows, "total_hours"),
            "avg_night_hours": avg(rows, "night_hours"),
            "avg_nap_hours": avg(rows, "nap_hours"),
            "avg_naps": avg(rows, "nap_count"),
            "avg_night_wakings": avg(rows, "night_wakings"),
            "avg_longest_stretch_hours": avg(rows, "longest_stretch_hours"),
            "avg_bedtime": _fmt_clock(avg(rows, "_bed_min")),
            "avg_wake_time": _fmt_clock(avg(rows, "_wake_min")),
        })
    return out


# ---------------------------------------------------------------------- SNOO

SNOO_API = "https://api-us-east-1-prod.happiestbaby.com"
COGNITO_URL = "https://cognito-idp.us-east-1.amazonaws.com/"
# The Happiest Baby app's public Cognito client id, as used by python-snoo.
COGNITO_CLIENT_ID = "6kqofhc8hm394ielqdkvli0oea"
SNOO_HEADERS = {
    "accept-language": "US",
    "content-type": "application/json; charset=UTF-8",
    "user-agent": "okhttp/4.12.0",
    "accept": "application/json",
}


async def _snoo_get(session, token, path, params=None) -> tuple[int, object]:
    async with session.get(
        SNOO_API + path, params=params, headers={**SNOO_HEADERS, "authorization": f"Bearer {token}"}
    ) as r:
        text = await r.text()
        try:
            return r.status, json.loads(text)
        except json.JSONDecodeError:
            return r.status, text[:2000]


async def probe_snoo(session: aiohttp.ClientSession, since: datetime, until: datetime, out: Path) -> None:
    """Save raw Happiest Baby history so we can see what sleep data exists.

    This deliberately doesn't interpret anything: python-snoo only understands
    diaper and breastfeeding entries, so the point is to find out what else the
    endpoint returns (and whether SNOO sleep sessions are among it).
    """
    out.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []

    async with session.post(
        COGNITO_URL,
        data=json.dumps({
            "AuthFlow": "USER_PASSWORD_AUTH",
            "ClientId": COGNITO_CLIENT_ID,
            "AuthParameters": {"USERNAME": os.environ["SNOO_EMAIL"], "PASSWORD": os.environ["SNOO_PASSWORD"]},
        }),
        headers={
            "x-amz-target": "AWSCognitoIdentityProviderService.InitiateAuth",
            "content-type": "application/x-amz-json-1.1",
        },
    ) as r:
        auth = await r.json(content_type=None)
    if "AuthenticationResult" not in auth:
        # Never write the response verbatim: on success it holds tokens, and on
        # failure the type/message is all that's useful.
        raise RuntimeError(f"Happiest Baby login failed: {auth.get('__type')}: {auth.get('message')}")
    token = auth["AuthenticationResult"]["IdToken"]

    status, babies = await _snoo_get(session, token, "/us/me/v10/babies")
    (out / "babies.json").write_text(json.dumps(babies, indent=2))
    notes.append(f"GET /us/me/v10/babies -> {status}")
    if status != 200 or not isinstance(babies, list):
        (out / "summary.txt").write_text("\n".join(notes) + "\n")
        raise RuntimeError(f"Could not list Happiest Baby babies ({status})")

    for baby in babies:
        baby_id, name = baby.get("_id"), baby.get("babyName") or baby.get("_id")
        notes.append(f"\n== {name} (born {baby.get('birthDate')}, SNOO since {baby.get('startedUsingSnooAt')})")

        # The journal endpoint python-snoo uses, fetched a month at a time so a
        # server-side range limit can't silently truncate years of history.
        entries, types, statuses = [], Counter(), Counter()
        lo = since
        if baby.get("birthDate"):
            lo = max(lo, datetime.fromisoformat(baby["birthDate"][:10]).replace(tzinfo=TZ))
        while lo < until:
            hi = min(lo + timedelta(days=30), until)
            status, body = await _snoo_get(
                session, token, f"/cs/me/v11/babies/{baby_id}/journals/grouped-tracking",
                {"group": "activity",
                 "fromDateTime": lo.isoformat(timespec="milliseconds"),
                 "toDateTime": hi.isoformat(timespec="milliseconds")},
            )
            statuses[status] += 1
            if status == 200 and isinstance(body, list):
                entries.extend(body)
                types.update(str(e.get("type")) for e in body if isinstance(e, dict))
            elif statuses[status] == 1:
                notes.append(f"  journal {lo.date()}..{hi.date()} -> {status}: {str(body)[:300]}")
            lo = hi
        (out / f"journal_{baby_id}.json").write_text(json.dumps(entries, indent=2))
        notes.append(f"  journal: {len(entries)} entries, statuses {dict(statuses)}, types {dict(types)}")

        # Endpoints the older pysnoo library used for SNOO session stats. They
        # predate Happiest Baby's move to Cognito logins and may well be gone;
        # this just records whether they still answer.
        start_param = since.strftime("%Y-%m-%d %H:%M:%S.000")
        for path, params in [
            (f"/ss/v2/babies/{baby_id}/sessions/aggregated/daily", {"startTime": start_param}),
            (f"/ss/v2/babies/{baby_id}/sessions/aggregated/avg",
             {"startTime": start_param, "interval": "week", "days": "true"}),
            ("/ss/v2/sessions/last", None),
        ]:
            status, body = await _snoo_get(session, token, path, params)
            notes.append(f"  GET {path} -> {status}")
            if status == 200:
                slug = path.strip("/").replace("/", "_").replace(baby_id, "baby")
                (out / f"{slug}_{baby_id}.json").write_text(json.dumps(body, indent=2))

    (out / "summary.txt").write_text("\n".join(notes) + "\n")
    logger.info("SNOO: %s", "\n".join(notes))


# --------------------------------------------------------------------- output


def write_csv(path: Path, rows: list[dict]) -> None:
    rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


HTML_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sleep Comparison</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
  :root { --bg:#fbfaf7; --fg:#1f2328; --muted:#6a6f76; --card:#fff; --line:#e4e1da; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#16181c; --fg:#e8e6e3; --muted:#9aa0a6; --card:#1f2228; --line:#30343b; }
  }
  body { background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, sans-serif; margin:0; padding:24px 16px; }
  main { max-width:1100px; margin:0 auto; }
  h1 { margin:0 0 4px; } p.meta { color:var(--muted); margin:0 0 24px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(320px, 1fr)); gap:16px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; }
  .card h2 { font-size:15px; margin:0 0 8px; }
  table { border-collapse:collapse; width:100%; font-size:13px; }
  th, td { text-align:right; padding:4px 8px; border-bottom:1px solid var(--line); white-space:nowrap; }
  th:first-child, td:first-child { text-align:left; }
  .scroll { overflow-x:auto; }
</style></head><body><main>
<h1>Sleep comparison</h1>
<p class="meta">__META__</p>
<div class="grid" id="charts"></div>
<div class="card" style="margin-top:16px"><h2>By month of age</h2><div class="scroll" id="table"></div></div>
<script>
const rows = __ROWS__;
const charts = [
  ["avg_total_hours", "Total sleep per day (hours)"],
  ["avg_night_hours", "Night sleep (hours)"],
  ["avg_nap_hours", "Nap sleep (hours)"],
  ["avg_naps", "Naps per day"],
  ["avg_longest_stretch_hours", "Longest stretch (hours)"],
  ["avg_night_wakings", "Night wakings"],
];
const kids = [...new Set(rows.map(r => r.child))];
const colors = ["#3b6fd8", "#d8693b", "#2e9e6a", "#9b4fc9", "#c9a227"];
const muted = getComputedStyle(document.documentElement).getPropertyValue("--muted");
for (const [key, title] of charts) {
  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `<h2>${title}</h2><canvas></canvas>`;
  document.getElementById("charts").append(card);
  new Chart(card.querySelector("canvas"), {
    type: "line",
    data: {
      // Plotted on a numeric axis so months nobody logged still take up space.
      datasets: kids.map((kid, i) => ({
        label: kid, borderColor: colors[i % colors.length], backgroundColor: colors[i % colors.length],
        tension: 0.25, pointRadius: 2,
        data: rows.filter(r => r.child === kid && r[key] != null).map(r => ({ x: r.age_month, y: r[key] })),
      })),
    },
    options: {
      plugins: { legend: { labels: { color: muted } } },
      scales: {
        x: { type: "linear", title: { display: true, text: "age (months)", color: muted },
             ticks: { color: muted, stepSize: 1, precision: 0 } },
        y: { ticks: { color: muted } },
      },
    },
  });
}
const cols = ["child", "age_month", "days_logged", "avg_total_hours", "avg_night_hours", "avg_nap_hours",
  "avg_naps", "avg_night_wakings", "avg_longest_stretch_hours", "avg_bedtime", "avg_wake_time"];
document.getElementById("table").innerHTML = "<table><tr>" + cols.map(c => `<th>${c.replace(/_/g, " ")}</th>`).join("")
  + "</tr>" + rows.map(r => "<tr>" + cols.map(c => `<td>${r[c] ?? ""}</td>`).join("") + "</tr>").join("") + "</table>";
</script></main></body></html>
"""


def write_html(path: Path, age_rows: list[dict], meta: str) -> None:
    html = HTML_TEMPLATE.replace("__META__", meta).replace("__ROWS__", json.dumps(age_rows))
    path.write_text(html)


# ----------------------------------------------------------------------- main


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", type=date.fromisoformat, default=date(2010, 1, 1),
                        help="earliest date to fetch (YYYY-MM-DD); default: all history")
    parser.add_argument("--out", type=Path, default=Path("sleep_reports"))
    parser.add_argument("--no-snoo", action="store_true", help="skip Happiest Baby")
    args = parser.parse_args()

    since = datetime.combine(args.since, time(), tzinfo=TZ)
    until = datetime.now(tz=TZ) + timedelta(minutes=5)
    out = args.out / datetime.now(tz=TZ).strftime("%Y-%m-%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)

    async with aiohttp.ClientSession() as session:
        children, account_info = await fetch_huckleberry(session, since, until)

        sleeps, skipped = build_sleeps(children)
        days = daily_summary(sleeps)
        ages = by_age_summary(days)

        write_csv(out / "sleeps.csv", [
            {**asdict(s), "start": s.start.isoformat(), "end": s.end.isoformat(),
             "hours": _hours(s.end - s.start)} for s in sleeps
        ])
        write_csv(out / "daily.csv", days)
        write_csv(out / "by_age_month.csv", ages)
        meta = (f"Huckleberry, {len(sleeps)} sleeps across {len({s.child for s in sleeps})} children. "
                f"Night = sleep starting {NIGHT_START:%H:%M}–{NIGHT_END:%H:%M}; only days with something logged "
                f"are averaged. Generated {datetime.now(tz=TZ):%Y-%m-%d %H:%M}.")
        write_html(out / "report.html", ages, meta)

        for child in children:
            if child["birthdate"] is None:
                logger.warning("%s has no birthdate in Huckleberry, so is missing from the by-age comparison",
                               child["name"])
        for (name, reason), n in sorted(skipped.items()):
            logger.warning("Skipped %d %s sleep(s) for %s", n, reason, name)
        if account_info["hbChilds"]:
            logger.info("Huckleberry account links %d Happiest Baby child(ren): %s",
                        len(account_info["hbChilds"]), ", ".join(account_info["hbChilds"]))

        if not args.no_snoo:
            if not (os.getenv("SNOO_EMAIL") and os.getenv("SNOO_PASSWORD")):
                logger.warning("SNOO_EMAIL / SNOO_PASSWORD not set in .env — skipping Happiest Baby")
            else:
                try:
                    await probe_snoo(session, since, until, out / "snoo")
                except Exception as err:
                    # The Huckleberry report is already written; don't lose it over this.
                    logger.error("Happiest Baby probe failed: %s", err)

    logger.info("Wrote %s — open %s", out, out / "report.html")


if __name__ == "__main__":
    asyncio.run(main())
