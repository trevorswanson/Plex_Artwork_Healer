# 🖼️ Plex Artwork Healer

A powerful self-healing script for Plex that ensures your Movies, TV Shows, and Collections never appear with broken or missing posters or backgrounds.

---

## ❓ Why I Created This Script

Many users experience missing posters or backgrounds in Plex — either due to agent failures, server restarts, or cache corruption. This script was designed to **automatically detect broken or missing artwork**, try restoring it from **local backups**, and if needed, **fetch replacements from TMDb**. It can even **re-upload** these images to Plex — completely unattended.

---

## ✅ Features

- 🧠 Detects broken or missing posters and backgrounds
- 💾 Restores from local backup if available (`Posters/...`)
- 🌍 Downloads from TMDb (requires free API key)
- 🔁 Optionally re-uploads posters/backgrounds to Plex
- 🗂️ Automatically creates folders for missing backup files
- 🎬 Supports Movies, TV Shows, and Collections
- 🏷️ Also handles **clear logos**, **mobile backgrounds**, and episode **title cards**: they're backed up like posters, restored from backup when missing/dead, and otherwise the first image Plex already has is selected (TMDb doesn't provide these)
- 🎛️ Choose which art types to process with `ART_TYPES`
- 📝 Dry-run support with detailed logging

---

## 📁 Folder Structure

Backups are laid out like this:

```bash
Posters/
├── Movies/
│   └── Gladiator/
│       ├── poster.jpg
│       ├── background.jpg
│       ├── clear_logo.png
│       └── mobile_background.jpg
└── TV Shows/
    └── The Office/
        ├── poster.jpg
        ├── background.jpg
        ├── clear_logo.png
        ├── mobile_background.jpg
        └── Title Cards/
            └── S01E01.jpg
```

(Each backup also has a small `.src` file recording which Plex image it came from.)

For clear logos, mobile backgrounds and title cards the repair order is: restore the local backup, otherwise select the first image Plex already has.

If artwork is missing in Plex and a local backup exists, it will be restored from this structure.

---

## ⚙️ Requirements

- Python 3.x
- [`plexapi`](https://pypi.org/project/plexapi/) 4.17+ (needed for logo / mobile background support)
- [`requests`](https://pypi.org/project/requests/)
- [`tmdbv3api`](https://pypi.org/project/tmdbv3api/)
- [`python-dotenv`](https://pypi.org/project/python-dotenv/)

Install via pip:

```bash
pip install -r requirements.txt
```

---

## 🔑 TMDb API Key

To fetch posters and backdrops from TMDb:

1. Visit: https://www.themoviedb.org/settings/api
2. Create a (free) TMDb account if needed
3. Generate a **Developer API Key** (v3)
4. Put it in your `.env` file (see below)

---

## 🔧 Configuration

Credentials live in a local `.env` file, which is gitignored and never committed.

```bash
cp .env.example .env
```

Then edit `.env`:

```
PLEX_URL=http://your-plex-ip:32400
PLEX_TOKEN=your_plex_token
TMDB_API_KEY=your_tmdb_api_key
```

The script exits with a clear message if any of these are missing. Real environment variables also work and take precedence over `.env`.

### Optional settings

Everything else has a built-in default and can be overridden in `.env` (see `.env.example`):

| Setting | Default | Description |
|---|---|---|
| `LIBRARIES` | `Movies,TV Shows` | Comma-separated Plex library names to process |
| `BASE_DIR` | `Posters` | Where artwork backups are stored |
| `LOG_FILE` | `artwork_healer.log` | Log file path |
| `CACHE_FILE` | `tmdb_cache.csv` | TMDb ID cache path |
| `ART_TYPES` | all five | Which art types to process: `poster`, `background`, `clear_logo`, `mobile_background`, `title_card` (comma-separated) |
| `DRY_RUN` | `true` | Only log what would change |
| `ENABLE_UPLOAD` | `true` | Upload artwork to Plex (needs `DRY_RUN=false`) |
| `FORCE_UPLOAD_ALL` | `false` | Re-upload even working artwork |
| `ENABLE_LOG` | `true` | Write the log file |
| `REFRESH_BACKUPS` | `true` | Update the local backup when the artwork in Plex has changed |
| `SELECT_EXISTING_ART` | `true` | For clear logos / mobile backgrounds / title cards with no local backup, select an image Plex already has |
| `WORKERS` | `8` | Parallel worker threads |
| `DELAY` | `0.1` | Seconds each worker waits after finishing an item |

Booleans accept `true/false`, `1/0`, `yes/no`, or `on/off`.

### Performance

Items (and episode title cards) are processed in parallel across `WORKERS` threads. Most of the time is spent waiting on HTTP checks, so more workers means a much faster run on large libraries. If your Plex server gets sluggish or you see timeouts, lower `WORKERS` or raise `DELAY`. Log lines from different items will interleave.

---

## ▶️ Usage

Run the script:

```bash
python plex_artwork_healer.py
```

🟢 Start with `DRY_RUN=true` (the default)  
🟢 Inspect `artwork_healer.log`  
🟢 When confident, set `DRY_RUN=false` in `.env` to apply

---

## 🗒️ Tips & Notes

- Only affects items with broken or missing artwork
- Will not overwrite working posters or backdrops
- Local backups take priority over TMDb
- Backups follow Plex: if working artwork changes (e.g. applied by another tool such as a MediUX job), the backup is updated to match. Dead artwork never overwrites a backup. Each backup has a small `.src` file recording which Plex image it came from; set `REFRESH_BACKUPS=false` to keep the first backup forever
- Clear logos, mobile backgrounds, and title cards are never downloaded; only art Plex already has is selected (checking every episode makes the first run on large TV libraries slow)
- Keep `.env` private; if a token was ever exposed, regenerate it
- Can be run on a schedule (e.g., via cron or Task Scheduler)

---

## 📂 Logging

All actions are logged to:

```
artwork_healer.log
```

This includes:
- Items scanned
- Posters restored or skipped
- TMDb lookups
- Uploads to Plex

---

## ⚠️ Disclaimer

Use at your own risk. Always review logs before enabling upload mode.  
Not affiliated with Plex, TMDb, or any third-party services.

