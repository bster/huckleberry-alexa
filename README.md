# huckleberry-alexa

An Alexa skill and FastAPI server for controlling Huckleberry baby tracker by voice. If you've ever searched for a Huckleberry Alexa integration or a way to log feeds and diapers hands-free, this is what you're looking for.

I built this because I kept waking up at 3am to feed my son and forgetting to log it in Huckleberry. Opening an app with a baby on you is harder than it sounds.

This is a small FastAPI server that runs on your home machine and acts as a bridge between Alexa and the Huckleberry baby tracking app. You set up a custom Alexa skill that calls your server directly (via a Tailscale Funnel URL), and the server uses [py-huckleberry-api](https://github.com/Woyken/py-huckleberry-api) to log events to Huckleberry on your behalf.

## What you can say

The invocation name is **"huckleberry"** — say "ask huckleberry to...". This is
independent of whatever the skill is titled in the Alexa developer console (it's
currently named "Baby Tracker" there, which is a misleading thing to go looking for —
the console title never appears in speech, only the invocation name does).

**Say "ask", not "tell."** "Alexa, tell huckleberry to log a wet diaper" does not
reach this skill — Alexa's built-in Communications feature intercepts "tell
\<name\> to..." as an attempt to send a message to a contact named "huckleberry" and
never routes it to a custom skill at all. Always use "ask" (or "open huckleberry"
first, then talk to it directly). This has tripped up testing more than once.

You can use one-shot invocations like:

- "Alexa, ask huckleberry to start feeding on the left"
- "Alexa, ask huckleberry to end feeding"
- "Alexa, ask huckleberry to switch sides"
- "Alexa, ask huckleberry to cancel feeding"
- "Alexa, ask huckleberry to log a 3 ounce breast milk bottle"
- "Alexa, ask huckleberry to log a formula bottle"
- "Alexa, ask huckleberry when was the last feeding"
- "Alexa, ask huckleberry to log a wet diaper"
- "Alexa, ask huckleberry to log a large mixed diaper"
- "Alexa, ask huckleberry to log a small wet diaper"
- "Alexa, ask huckleberry to log a blowout dirty diaper"
- "Alexa, ask huckleberry when was the last diaper"
- "Alexa, ask huckleberry to start sleep"
- "Alexa, ask huckleberry to end sleep"
- "Alexa, ask huckleberry to cancel sleep"
- "Alexa, ask huckleberry to log a pump session"
- "Alexa, ask huckleberry to log a 10 minute 3 ounce pump"
- "Alexa, ask huckleberry when was the last pump"
- "Alexa, ask huckleberry he weighs 128 ounces" (log weight — say it in ounces)
- "Alexa, ask huckleberry to log tummy time"
- "Alexa, ask huckleberry to log 5 minutes of tummy time"
- "Alexa, ask huckleberry to log a bath"

### Diaper size

Size is optional and never assumed. Say one — **small**, **medium**, or **large**,
or a synonym like "little", "big", "huge", "blowout" — and it's recorded; say
nothing and the record carries **no size field at all**, exactly like a diaper
logged in the Huckleberry app without one. Both orderings work ("a large mixed
diaper", "a mixed large diaper").

Sizes map onto the app's 0–100 quantity scale:

| said | stored as |
|---|---|
| small | `quantity: 0.0` |
| medium | `quantity: 50.0` |
| large | `quantity: 100.0` |
| *(nothing said)* | no `quantity` field |

For a mixed diaper the size applies to both halves (`{"pee": 100.0, "poo": 100.0}`).
A dry diaper has nothing to size, so a size said with one is ignored.

Those three numbers aren't guesses. Across 4,081 archived diaper records in this
account written by the Huckleberry app itself — years before this server existed —
`quantity` only ever takes the values **0.0, 50.0, and 100.0**, and 2,693 of those
records carry no `quantity` field at all. Logging without a size is what the app
does most of the time, which is why it is the default here too. (Older records use
an earlier schema where `quantity` is a bare number rather than a per-side object,
but the same three values.)

## How it works

The server is a FastAPI app running on port 8765. Tailscale Funnel gives it a static public HTTPS URL (something like `https://your-machine.tailnet.ts.net`). When you talk to Alexa, it calls `POST /alexa` on that URL with a standard Alexa request payload, and the server translates it into Huckleberry API calls.

The [py-huckleberry-api](https://github.com/Woyken/py-huckleberry-api) library reverse-engineers Huckleberry's Firebase backend. It's not an official API, so it could break if Huckleberry changes something — but it's been solid so far.

## Setup

### Prerequisites

- Python 3.14+ (the py-huckleberry-api library requires it)
- [uv](https://docs.astral.sh/uv/) for package management
- [Tailscale](https://tailscale.com/) with Funnel enabled
- An [Amazon Developer account](https://developer.amazon.com/) (free)
- A Huckleberry account

### 1. Clone and configure

```bash
git clone https://github.com/YOUR_USERNAME/huckleberry-alexa.git
cd huckleberry-alexa
cp .env.example .env
# Fill in your Huckleberry credentials in .env
uv sync
```

### 2. Start the server

```bash
uv run uvicorn baby_tracker_server:app --port 8765
```

For autostart on macOS, edit `com.local.babytracker.plist` to replace `YOUR_USERNAME` with your actual username, copy it to `~/Library/LaunchAgents/`, and load it:

```bash
cp com.local.babytracker.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.local.babytracker.plist
```

### 3. Set up Tailscale Funnel

```bash
tailscale funnel 8765
```

This gives you a stable public URL like `https://your-machine.tailnet.ts.net`. Test it:

```bash
curl https://your-machine.tailnet.ts.net/status
```

### 4. Create the Alexa skill

1. Go to [developer.amazon.com](https://developer.amazon.com) → Alexa Developer Console → Create Skill
2. Choose: Custom model, Alexa-hosted: No (provision your own)
3. Under "Endpoint", choose HTTPS and enter `https://your-machine.tailnet.ts.net/alexa`
4. For SSL cert type, choose **"My development endpoint is a sub-domain of a domain that has a wildcard certificate"** (Tailscale uses `*.ts.net`)
5. In the "JSON Editor" tab, paste the contents of `alexa/interactionModel.json`
6. Save and Build the model

### 5. Enable the skill

In the Alexa app on your phone: More → Skills & Games → Your Skills → Dev tab → find the skill → enable it.

## Working on this without taking Alexa down

**launchd runs uvicorn straight out of this working directory.** There is no build
step and no deploy artifact, so every save to `baby_tracker_server.py` edits
production, and a file saved mid-thought is a file the next restart will try to
import. Worse, the plist sets `KeepAlive=true` with `ThrottleInterval=10`: a syntax
error makes uvicorn exit 1, launchd relaunches it ten seconds later into the same
error, and Alexa has no endpoint until someone notices. Verified — a deliberately
broken file exits 1 on import.

So don't edit the live file. Work in a second checkout on a second port:

```bash
git worktree add ../baby-tracker-dev -b my-change
cd ../baby-tracker-dev
ln -s ../baby-tracker/.env  .env      # same Huckleberry account
ln -s ../baby-tracker/.venv .venv     # same deps, no second install
./run-dev.sh                          # 127.0.0.1:8767
```

`run-dev.sh` binds to loopback on **8767**. The funnel only proxies `/alexa` to
**8765**, so the dev instance is unreachable from Alexa no matter what state its
code is in. It's a separate file (separate inode), so editing it cannot touch the
running service.

Point tests at `http://localhost:8767/alexa` with real Alexa payloads. It writes to
the real Huckleberry account — that's the point, since the thing worth verifying is
what lands in Firestore — so delete test records afterwards and repoint
`prefs.lastDiaper` at the newest real one.

When it's proven, merge and cut over **once**:

```bash
cd ../baby-tracker && git merge my-change && ./restart.sh
```

`restart.sh` compiles `baby_tracker_server.py` and validates the interaction model
JSON *before* touching launchd, and refuses to restart if either fails — so a broken
file leaves the old process serving instead of starting a crash loop. It then polls
`/status` until healthy. A clean cutover takes about a second.

## Troubleshooting

If a voice command isn't working, check things in this order:

1. **Is the server up and connected to Huckleberry?**
   ```bash
   curl -s localhost:8765/status | python3 -m json.tool
   ```
   `"ok": true` and `"huckleberry": "connected"` means the server itself is healthy.
   `last_alexa` shows the last request Alexa actually delivered (or `null` if none
   ever arrived — a sign the problem is upstream of this machine entirely, e.g.
   endpoint URL, Tailscale, or the "ask" vs "tell" issue above). `tailscale_hostname`
   shows whether the public endpoint currently matches what the Alexa skill is
   configured for — see below if `matches` is `false`.

2. **Is the public endpoint reachable at all?** If `/status` looks fine locally but
   Alexa still gets no response, the public hostname may not be resolving. See
   "Tailscale hostname" below — the fix is usually `tailscale down && tailscale up`.

3. **Did you say "ask" and not "tell"?** See the invocation note above — this is the
   most common false alarm.

## Tailscale hostname

The Alexa skill's HTTPS endpoint is a URL hardcoded in the Amazon developer console
to this machine's Tailscale Funnel hostname — currently:

```
https://benss-mac-mini-1.tailbded15.ts.net/alexa
```

Tailscale can silently re-register this Mac under a **different** hostname (e.g.
appending `-1`) without the node ever going offline. This already happened once: the
daemon looked healthy, `tailscale status` showed "Running", the server's own
`/status` looked fine — but Amazon's servers couldn't reach the old hostname anymore,
and the skill failed with no local symptom pointing at Tailscale at all.

**You'll now find out immediately instead of by accident.** The server checks its own
Tailscale hostname against `EXPECTED_TAILSCALE_HOSTNAME` on startup and every 30
minutes, and:
- logs a loud `ERROR` line (visible in the launchd log) if they've diverged
- surfaces it at `GET /status` → `tailscale_hostname: {expected, actual, matches}`

**If they diverge, fix it in this order:**
1. Confirm the new hostname: `tailscale status --json | python3 -c "import json,sys; print(json.load(sys.stdin)['Self']['DNSName'])"` (or just check `/status`)
2. Update the Alexa skill's endpoint — developer console → **Build → Endpoint** →
   paste `https://<new-hostname>/alexa` → **Save Endpoints**. (Not "Build Model" —
   that's only needed for interaction-model changes, not endpoint URL changes.)
3. Update the expected hostname so the server stops alerting: either set
   `EXPECTED_TAILSCALE_HOSTNAME=<new-hostname>` in `.env`, or edit the default in
   `baby_tracker_server.py`, then restart the service:
   ```bash
   launchctl kickstart -k gui/$(id -u)/com.local.babytracker
   ```

**Why the `-1` appeared — two Tailscale daemons.** The trigger used to be unclear.
It isn't: this Mac runs *two* tailscaled instances at once, and they registered as
two separate nodes.

```bash
ps aux | grep -i tailscale | grep -v grep
```
```
/Applications/Tailscale.app/.../io.tailscale.ipn.macsys.network-extension   # utun9
/opt/homebrew/opt/tailscale/bin/tailscaled                                  # utun0
```
```bash
tailscale status
```
```
100.96.243.74   benss-mac-mini-1   ← Tailscale.app; this is the node Funnel serves
100.118.252.19  benss-mac-mini     ← Homebrew tailscaled; still online, serves nothing
```

The Homebrew daemon claimed `benss-mac-mini` first, so when the Mac App registered it
had to take `benss-mac-mini-1` — which is exactly the hostname drift that broke the
skill endpoint. Removing the redundant daemon prevents a recurrence; leaving both
running means the next re-registration can shuffle the names again.

## Intermittent "the skill is not available"

Distinct from the hostname problem above: the skill works, then sporadically doesn't,
then works again, with **nothing in the server log** — because the request never
arrived. Two things on this machine cause that, and neither is visible from inside
the server.

**1. The default route drops, several times a day.** The Mac is on Wi-Fi (`en1`);
`en0` Ethernet is cabled in the service order but unconfigured. The Homebrew
tailscaled log is a convenient witness to a system-wide event:

```bash
grep "LinkChange: major" /opt/homebrew/var/log/tailscaled.log | tail
```
```
LinkChange: major, rebinding: ... diff: DefaultRoute: ""->"en1" rebind-reason=[default-if-changed]
```

Every one of these is macOS momentarily having **no default route at all**. Each one
tears down the control connection (`use of closed network connection`) and re-pings
DERP. While that is happening, inbound Funnel traffic cannot reach this node, so
Alexa gets nothing and tells you the skill is unavailable — and the server, which was
never contacted, logs nothing. Frequency has been climbing: 1–4/day through Aug 18,
8 on Aug 19, 12 on Aug 20.

The durable fix is to **put the Mac on Ethernet** (`en0` is already first in the
service order — it just needs a cable and DHCP). Removing the second tailscaled also
helps: two daemons both rewriting system DNS and both competing for NAT port mappings
against the same router doubles the reconfiguration churn.

**2. Not latency.** Worth ruling out explicitly, because it looks like the obvious
suspect. Alexa's budget is ~8s; measured against the real Funnel path:

| where | p50 | p95 | max | over 8s |
|---|---|---|---|---|
| end-to-end through Funnel (111 samples) | 349ms | 476ms | 1311ms | 0 |
| server-side handler (112 samples) | 195ms | 295ms | 351ms | 0 |

That is roughly 20x under budget. The Firebase token refresh that used to happen
inline costs ~50ms, and the Firestore read right after it ~300ms versus ~235ms warm —
so it was never the multi-second stall it looked like from outside. It has been moved
to a background task anyway (see `_session_keepalive_forever`), and it no longer
matters which request lands in the token's last five minutes.

**Reading the latency yourself:**
```bash
curl -s localhost:8765/status | python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin)['alexa_latency'], indent=2))"
```
`alexa_latency` holds p50/p95/max over a rolling 300-request window plus the five
slowest. Every request also logs its own duration:
```
Alexa request done: LogDiaperIntent outcome=ok 243ms (token_ttl=3456.5s)
```
Anything over 3s logs a `WARNING`; anything over 8s logs an `ERROR` saying the user
heard a failure. So if the skill fails and there is **no** such line, the request
never reached this machine — look at the network, not the server.

## Comparing sleep across kids

`sleep_report.py` is a separate, read-only script. It isn't part of the server. It
pulls every sleep Huckleberry has for each child on the account and compares them
**by age**, not by calendar date: everyone at 4 months, then everyone at 5 months,
and so on.

```bash
.venv/bin/python sleep_report.py                    # all history
.venv/bin/python sleep_report.py --since 2024-01-01
.venv/bin/python sleep_report.py --no-snoo          # Huckleberry only
```

It writes to `sleep_reports/<timestamp>/` (gitignored, since it holds the kids' data):

- `report.html`: charts of total, night and nap sleep, naps per day, longest
  stretch and night wakings, by month of age
- `by_age_month.csv`, `daily.csv`, `sleeps.csv`: the same data at each level of detail

The rules behind the numbers:

- **Night sleep** is a sleep that starts between 18:00 and 06:00. Everything else is a nap.
- **Sleep days** run from 06:00 to 06:00, so a night counts toward the evening it began.
- **Averages** only include days with at least one sleep logged, so a gap in
  logging doesn't drag the numbers down.
- **Dropped sleeps:** anything over 16 hours (a timer left running) or with zero
  length. The script logs how many it dropped.
- **Birthdate required:** a child needs a birthdate in Huckleberry to appear in the
  by-age comparison.

Newborn sleep has no real night yet, so the night/nap split for the first couple of
months is rough.

### Happiest Baby (SNOO)

If `SNOO_EMAIL` and `SNOO_PASSWORD` are in `.env`, the script also logs in to
Happiest Baby and saves its raw history to `sleep_reports/<timestamp>/snoo/`.
It doesn't interpret that data yet. There's no official API, and the unofficial
library (python-snoo) only understands diaper and breastfeeding entries, so this
first step finds out what else exists. `snoo/summary.txt` lists the entry types
found per baby, and whether the old session-stats endpoints still answer.

## Notes

This only works as a development-tier Alexa skill, meaning it's private to your Amazon account. You can't publish it — Huckleberry doesn't have an official API, so this is purely for personal use.

Weight logging expects ounces (e.g. "128 ounces" for 8 lbs). The server converts to lbs internally.
