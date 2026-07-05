# Server setup notes

## Tailscale exposure (configured 2026-07-05, Tailscale 1.98.5)

Funnel is scoped so that only the Alexa webhook is reachable from the public
internet. Everything else is tailnet-only.

```bash
# Wipe any previous serve/funnel config (the old config funneled ALL of / publicly)
tailscale serve reset

# Expose ONLY /alexa to the public internet on port 443.
# Funnel is per-port, so this works because /alexa is the only handler on 443 —
# every other path returns 404 at the Tailscale edge.
tailscale funnel --bg --set-path=/alexa http://localhost:8765/alexa

# Full REST API over HTTPS, tailnet-only, on port 8443
tailscale serve --bg --https=8443 http://localhost:8765
```

Resulting state (`tailscale serve status`):

- `https://benss-mac-mini.tailbded15.ts.net/alexa` — public (Funnel), proxies to `http://localhost:8765/alexa`. All other paths on 443 → 404.
- `https://benss-mac-mini.tailbded15.ts.net:8443/` — tailnet only, proxies the full API.
- `http://localhost:8765` / port 8765 on the tailnet — direct access to uvicorn, unchanged.

Note: do NOT test port 8443 from public DNS — the hostname resolves to Funnel
ingress IPs, which only accept 443. From a tailnet device it resolves to the
tailnet IP (100.118.252.19) and works.

This config persists across reboots (stored in the tailscaled state).

## Power management

To keep the Mac from sleeping (required for the server + tunnel to stay up),
run manually (requires sudo password):

```bash
sudo pmset -a sleep 0
sudo pmset -a disksleep 0
```
