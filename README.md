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

**Will the underlying re-registration happen again?** Possibly — the exact trigger
(stale daemon control-plane state after weeks of uptime, resolved here with
`tailscale down && tailscale up`) isn't something this project can prevent from the
client side, and it isn't a well-documented "runs every N days" Tailscale failure
mode either, so there's no schedule to defend against. If it does recur, the fix is
the same one-liner:
```bash
tailscale down && tailscale up
```
The difference now is you'll be told immediately (loud log line + `/status`) instead
of finding out next time you happen to test the skill.

## Notes

This only works as a development-tier Alexa skill, meaning it's private to your Amazon account. You can't publish it — Huckleberry doesn't have an official API, so this is purely for personal use.

Weight logging expects ounces (e.g. "128 ounces" for 8 lbs). The server converts to lbs internally.
