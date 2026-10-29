# Telegram Arr Bridge

Lets Radarr (and, later, Sonarr) search Telegram **search bots** and download
from them, the same way they use a Usenet indexer.

It covers "auto-filter" movie bots. You send these bots a title, they reply
with buttons like `1.42 GB • Movie (2026) Malayalam...`, and they often make
you join a channel or two before they send the file.

```
Overseerr → Radarr ──search──▶ bridge ──message──▶ @SomeMovieBot
                   ◀─results──        ◀──buttons──
            Radarr ──grab────▶ bridge ──press button, join channels, Try Again──▶ bot
                                      ◀──file────
            Radarr ◀─done───── /downloads/complete/radarr/<release>/file.mkv ──▶ import
```

Inside Radarr the bridge is added twice: as a **Newznab indexer** (search) and
as a **SABnzbd download client** (grab and download). Radarr can't tell it apart
from real Usenet.

## What you need

- Telegram API credentials from https://my.telegram.org/apps (`API_ID`, `API_HASH`).
- A Telegram account to log in with. **Use a second account if you can**, because
  automated messaging to bots can get an account limited.
- The @usernames of the bots you want to search.

## Install with Portainer

1. **Stacks → Add stack → Repository**
   - Repository URL: `https://github.com/ausyp/telegram-arr-bridge`
   - Compose path: `docker-compose.yaml`
2. Add these environment variables:

   | Variable | Example | What it is |
   |---|---|---|
   | `API_ID` | `1234567` | From my.telegram.org |
   | `API_HASH` | `abcdef…` | From my.telegram.org |
   | `BRIDGE_API_KEY` | any long random string | Radarr uses it for both the indexer and the download client |
   | `BOTS` | `SomeMovieBot,OtherBot` | Bot usernames to search, without `@`, comma separated |
   | `DOWNLOADS_PATH` | `/srv/media/downloads` | Host folder for finished downloads (see "Paths" below) |

3. **Deploy the stack.**
4. **Log in to Telegram once:** in Portainer, open **Containers → telegram-arr-bridge
   → Console**, choose `/bin/sh`, click **Connect** and run:

   ```
   python -m bridge.login
   ```

   Enter your phone number and the code Telegram sends you. Within about 15
   seconds the logs show `Telegram connected as …`. You don't need to restart anything.

Check it's up at `http://<docker-host>:8765/health`. It shows
`"telegram": true` once you're logged in.

## Paths (important)

The bridge saves finished files to `/downloads/complete/<category>/<release>/`
inside its container. Radarr must be able to reach that **same folder at the same path**.

- Easiest: mount the same host folder at `/downloads` in both the bridge and
  Radarr containers.
- If Radarr uses a different path (for example `/data/downloads`), add a
  **Remote Path Mapping** in Radarr (Settings → Download Clients): host
  `<bridge host>`, remote path `/downloads/complete/`, local path
  `/data/downloads/complete/`.

## Set up Radarr

**Settings → Indexers → + → Newznab → Custom**
- URL: `http://<docker-host>:8765`
- API Path: `/api`
- API Key: your `BRIDGE_API_KEY`
- Categories: leave the defaults (2000 Movies)
- Click **Test**, then **Save**.

**Settings → Download Clients → + → SABnzbd**
- Host: `<docker-host>`, Port: `8765`
- URL Base: leave empty
- API Key: your `BRIDGE_API_KEY`
- Category: `radarr`
- Click **Test**, then **Save**.

That's it. Requests from Overseerr go to Radarr, Radarr searches the bots, and
the bridge downloads whatever Radarr picks.

## Joining channels

When a bot replies "join these channels, then press Try Again", the bridge
joins (or sends a join request to) each channel the bot links to and presses
Try Again. If a channel only accepts members after an admin approves the
request, the first grab can fail. Radarr will then try another result, and
once the request is approved, later grabs work. You can also join the bots'
channels once by hand from the same Telegram account.

## Limits

- **Names are cut off.** Bots shorten file names on their buttons. When the
  quality isn't visible, the bridge guesses the resolution from the file size
  (under 0.9 GB is 480p, under 1.9 GB is 720p, under 8 GB is 1080p, anything
  larger is 2160p).
- **Language.** Many of these releases are tagged with a language (for example
  "Malayalam"). If your Radarr quality profile only allows English, Radarr will
  reject them. Set the profile's language to *Original* or *Any*.
- **Rate limits.** Each bot gets at most one message every `BOT_MIN_INTERVAL`
  seconds (default 5). Search results are cached for an hour.
- **Website links.** Bots that send you through a short-link or ad website
  before the file can't be automated.
- **TV shows (Sonarr)** aren't supported yet.

## All settings

| Variable | Default | |
|---|---|---|
| `SEARCH_TIMEOUT` | `25` | Seconds to wait for a bot's results |
| `FILE_TIMEOUT` | `120` | Seconds to wait for a bot to send a file |
| `BOT_MIN_INTERVAL` | `5` | Minimum seconds between messages to the same bot |
| `MAX_PAGES` | `2` | Result pages to read (presses "Next") |
| `MAX_DOWNLOADS` | `2` | Downloads running at once |
| `USE_INLINE` | `true` | Try the bot's inline mode first (gives full file names) |
| `CATEGORIES` | `movies,radarr,tv,sonarr` | Download categories offered to Radarr/Sonarr |
| `LOG_LEVEL` | `INFO` | `DEBUG` for more detail |

## Development

```
pip install -r requirements-dev.txt
ruff check . && pytest -q
```
