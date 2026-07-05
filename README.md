# huckleberry-alexa

I built this because I kept waking up at 3am to feed my son and forgetting to log it in Huckleberry. Opening an app with a baby on you is harder than it sounds.

This is a small FastAPI server that runs on your home machine and acts as a bridge between Alexa and the Huckleberry baby tracking app. You set up a custom Alexa skill that calls your server directly (via a Tailscale Funnel URL), and the server uses [py-huckleberry-api](https://github.com/Woyken/py-huckleberry-api) to log events to Huckleberry on your behalf.

## What you can say

The invocation name is "huckleberry". You can use one-shot invocations like:

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

## Notes

This only works as a development-tier Alexa skill, meaning it's private to your Amazon account. You can't publish it — Huckleberry doesn't have an official API, so this is purely for personal use.

Weight logging expects ounces (e.g. "128 ounces" for 8 lbs). The server converts to lbs internally.
