# Sleep comparison

Compares sleep across all the kids on a Huckleberry account **by age**: everyone at
4 months, then everyone at 5 months, and so on. It's a small website you open from
a phone on the home Wi-Fi, plus a script that saves the same data as CSV files.

This folder is self-contained. It has its own `.env`, its own dependencies and its
own background job, and it shares nothing with the Alexa server in the parent
folder. You could move it anywhere. Both only read from Huckleberry; neither one
writes anything to it.

## Setup

```bash
cd sleep
cp .env.example .env      # fill in the Huckleberry email and password
uv sync
uv run sleep_site.py      # try it: http://localhost:8768/
```

To keep it running, including after a reboot:

1. Edit `com.local.sleepsite.plist`: replace `YOUR_USERNAME` and set
   `WorkingDirectory` to this folder.
2. Install it:

   ```bash
   cp com.local.sleepsite.plist ~/Library/LaunchAgents/
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.local.sleepsite.plist
   ```

Logs go to `~/Library/Logs/sleep-site.log` and `~/Library/Logs/sleep-site.error.log`.
To restart after changing code or `.env`, run
`launchctl kickstart -k gui/$(id -u)/com.local.sleepsite`.

## Opening it from a phone

On the home Wi-Fi, open `http://<mac-name>.local:8768/`. The name is under
System Settings → General → Sharing → Local hostname. Add it to the home screen to
use it like an app.

- **Firewall:** the first time the site runs, macOS may ask whether to allow
  incoming connections. Say yes, or phones can't reach it.
- **Access:** it only works on the home network, and there's no password. Anyone
  on that Wi-Fi can open it, and nobody outside it can.

The page reloads from Huckleberry at most every 15 minutes. Tap **Refresh** to
reload it right away.

## How the numbers work

- **Night sleep** is a sleep that starts between 18:00 and 06:00. Everything else is a nap.
- **Sleep days** run from 06:00 to 06:00, so a night counts toward the evening it began.
- **Averages** only include days with at least one sleep logged, so a gap in
  logging doesn't drag the numbers down.
- **Dropped sleeps:** anything over 16 hours (a timer left running) or with zero
  length. The page lists how many it dropped.
- **Birthdate required:** a child needs a birthdate in Huckleberry to appear.

Newborn sleep has no real night yet, so the night/nap split for the first couple of
months is rough.

## CSV export and Happiest Baby

```bash
uv run sleep_report.py                    # all history
uv run sleep_report.py --since 2024-01-01
uv run sleep_report.py --no-snoo          # Huckleberry only
```

This writes `sleep_reports/<timestamp>/`, which git ignores because it holds the
kids' data. It contains `report.html` and three CSV files: `by_age_month.csv`,
`daily.csv` and `sleeps.csv`.

If `SNOO_EMAIL` and `SNOO_PASSWORD` are set in `.env`, it also logs in to Happiest
Baby and saves its raw history under `snoo/`. It doesn't interpret that data yet.
There's no official API, and the unofficial library only understands diaper and
breastfeeding entries, so this step finds out what else exists. `snoo/summary.txt`
lists the entry types found for each baby.
