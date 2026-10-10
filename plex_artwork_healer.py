# === IMPORTS ===
import os
import re
import time
import csv
import datetime
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from dotenv import load_dotenv
from plexapi.server import PlexServer
from tmdbv3api import TMDb, Movie

# === CONFIGURATION ===
load_dotenv()                              # Reads settings from a local .env file (see .env.example)

def require_env(name):
    """Returns a required environment variable or exits with a helpful message"""
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing required setting {name}. Copy .env.example to .env and fill it in.")
    return value

def env_str(name, default):
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else default

def env_bool(name, default):
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'on')

def env_number(name, default, cast=int):
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return cast(value.strip())
    except ValueError:
        raise SystemExit(f"Invalid value for {name}: {value!r} (expected {cast.__name__})")

def env_list(name, default):
    value = os.environ.get(name)
    items = [v.strip() for v in value.split(',') if v.strip()] if value else []
    return items or default

# Required (no defaults)
PLEX_URL = require_env('PLEX_URL')         # Plex server base URL
PLEX_TOKEN = require_env('PLEX_TOKEN')     # Plex API token
TMDB_API_KEY = require_env('TMDB_API_KEY') # TMDb API key

# Optional (defaults here, overridable in .env)
LIBRARIES = env_list('LIBRARIES', ['Movies', 'TV Shows'])  # Plex libraries to process (comma-separated)
BASE_DIR = env_str('BASE_DIR', 'Posters')                  # Directory to store artwork backups
LOG_FILE = env_str('LOG_FILE', 'artwork_healer.log')       # Output log file
CACHE_FILE = env_str('CACHE_FILE', 'tmdb_cache.csv')       # TMDb ID cache CSV file

ENABLE_UPLOAD = env_bool('ENABLE_UPLOAD', True)            # Whether to upload artwork to Plex
FORCE_UPLOAD_ALL = env_bool('FORCE_UPLOAD_ALL', False)     # If True, re-upload even working images
DRY_RUN = env_bool('DRY_RUN', True)                        # If True, don't make changes
ENABLE_LOG = env_bool('ENABLE_LOG', True)                  # If True, write to log file
REFRESH_BACKUPS = env_bool('REFRESH_BACKUPS', True)        # Update local backups when Plex's current artwork has changed
SELECT_EXISTING_ART = env_bool('SELECT_EXISTING_ART', True)  # Select existing Plex logo/mobile bg/title card when unset or dead
ART_TYPES = env_list('ART_TYPES', None)                   # Art types to process (comma-separated); default: all
WORKERS = max(1, env_number('WORKERS', 8))                 # Parallel worker threads
DELAY = env_number('DELAY', 0.1, float)                    # Delay (seconds) each worker waits after finishing an item

# === ART TYPES ===
# key -> display label, how to read the current Plex image path, upload method, candidate list / select methods
# (tmdb=True means TMDb can supply a replacement; the others can only be restored from backup or picked from what Plex has)
ART_KINDS = {
    'poster':            dict(label='poster',            url=lambda i: i.thumb,     file='poster.jpg',            upload='uploadPoster',    tmdb=True),
    'background':        dict(label='background',        url=lambda i: i.art,       file='background.jpg',        upload='uploadArt',       tmdb=True),
    'clear_logo':        dict(label='clear logo',        url=lambda i: i.logo,      file='clear_logo.png',        upload='uploadLogo',      candidates='logos',      select='setLogo'),
    'mobile_background': dict(label='mobile background', url=lambda i: i.squareArt, file='mobile_background.jpg', upload='uploadSquareArt', candidates='squareArts', select='setSquareArt'),
    'title_card':        dict(label='title card',        url=lambda i: i.thumb,     file='title_card.jpg',        upload='uploadPoster',    candidates='posters',    select='setPoster'),  # episodes only
}
if ART_TYPES is None:
    ART_TYPES = list(ART_KINDS)
else:
    ART_TYPES = [t.lower() for t in ART_TYPES]
    unknown = [t for t in ART_TYPES if t not in ART_KINDS]
    if unknown:
        raise SystemExit(f"Unknown ART_TYPES value(s): {', '.join(unknown)}. Valid: {', '.join(ART_KINDS)}")

# === TMDb SETUP ===
tmdb = TMDb()
tmdb.api_key = TMDB_API_KEY
tmdb.language = 'en'
movie_search = Movie()                     # TMDb movie search instance

# === LOGGING UTILITY ===
_log_lock = threading.Lock()

def log(msg):
    """Prints and logs messages with timestamp (thread-safe)"""
    with _log_lock:
        print(msg)
        if ENABLE_LOG:
            with open(LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(f"{datetime.datetime.now()} - {msg}\n")

# === FILE SYSTEM HELPERS ===
def safe_filename(name):
    """Cleans a string to make it a safe filename"""
    return re.sub(r'[\\/*?:"<>|]', "", name)

def ensure_folder(path):
    """Creates directory if it doesn't exist"""
    os.makedirs(path, exist_ok=True)

def artwork_url_invalid(url):
    """Checks if a given artwork URL is invalid or unreachable"""
    try:
        with requests.get(url, timeout=5, stream=True) as r:   # stream: don't download the body
            return r.status_code != 200
    except:
        return True

def get_artwork_path(library, item, kind):
    """Returns full path to the local backup file for an item's art of the given kind"""
    if kind == 'title_card':   # item is an episode
        name = f"S{item.seasonNumber or 0:02}E{item.episodeNumber or 0:02}.jpg"
        return os.path.join(BASE_DIR, library, safe_filename(item.grandparentTitle), 'Title Cards', name)
    return os.path.join(BASE_DIR, library, safe_filename(item.title), ART_KINDS[kind]['file'])

def item_label(item, kind):
    """Human-readable name for log lines"""
    if kind == 'title_card':
        return f"{item.grandparentTitle} S{item.seasonNumber or 0:02}E{item.episodeNumber or 0:02}"
    return item.title

def extract_tmdb_id_from_guid(guid):
    """Extracts TMDb ID from a Plex GUID string (if available)"""
    match = re.search(r'themoviedb://(\d+)', guid or '')
    return int(match.group(1)) if match else None

# === ARTWORK BACKUP / DOWNLOAD / UPLOAD ===
def source_marker_path(path):
    """Sidecar file recording which Plex image URL a backup was taken from"""
    return os.path.splitext(path)[0] + '.src'

def backup_is_stale(rel_url, path):
    """True if there is no backup, or it was taken from a different Plex image than the current one"""
    if not os.path.exists(path):
        return True
    try:
        with open(source_marker_path(path), encoding='utf-8') as f:
            return f.read().strip() != rel_url
    except OSError:
        return True   # No record of the source; refresh once to establish it

def backup_from_plex(name, kind, rel_url, path):
    """Downloads and saves an image from Plex (replaces any existing backup only on success)"""
    if not rel_url:
        return False
    try:
        url = f"{PLEX_URL}{rel_url}?X-Plex-Token={PLEX_TOKEN}"
        r = requests.get(url, timeout=30)
        if r.status_code == 200 and r.content:
            tmp = path + '.tmp'
            with open(tmp, 'wb') as f:
                f.write(r.content)
            os.replace(tmp, path)
            with open(source_marker_path(path), 'w', encoding='utf-8') as f:
                f.write(rel_url)
            log(f"[BACKUP] Saved {ART_KINDS[kind]['label']} for {name} from Plex")
            return True
    except Exception as e:
        log(f"[ERROR] Failed backup for {name} ({kind}): {e}")
    return False

def download_tmdb_art(tmdb_data, art_type, path):
    """Downloads poster or backdrop from TMDb"""
    key = 'poster_path' if art_type == 'poster' else 'backdrop_path'
    art_path = getattr(tmdb_data, key, None)
    if not art_path:
        return False
    url = f"https://image.tmdb.org/t/p/original{art_path}"
    try:
        r = requests.get(url)
        if r.status_code == 200:
            with open(path, 'wb') as f:
                f.write(r.content)
            log(f"[TMDb] Downloaded {art_type} for {tmdb_data.title}")
            return True
    except Exception as e:
        log(f"[ERROR] Failed TMDb download for {tmdb_data.title} ({art_type}): {e}")
    return False

def upload_to_plex(item, name, kind, path):
    """Uploads an image to Plex as the item's art of the given kind"""
    try:
        getattr(item, ART_KINDS[kind]['upload'])(filepath=path)
        log(f"[UPLOAD] Uploaded {ART_KINDS[kind]['label']} for {name}")
    except Exception as e:
        log(f"[ERROR] Upload failed for {name} ({kind}): {e}")

# === TMDb ID CACHING ===
def build_tmdb_cache(plex):
    """Builds TMDb ID cache from all libraries + movie collections"""
    cache = {}
    log("[CACHE] Generating new TMDb cache...")

    for lib in LIBRARIES:
        try:
            section = plex.library.section(lib)
            for item in section.all():
                guid = getattr(item, 'guid', '')
                tmdb_id = extract_tmdb_id_from_guid(guid)
                if tmdb_id:
                    cache[item.title] = tmdb_id
        except Exception as e:
            log(f"[ERROR] Failed to cache {lib}: {e}")

    # Also try caching Collections under "Movies"
    try:
        movies_section = plex.library.section('Movies')
        for coll in movies_section.collections():
            guid = getattr(coll, 'guid', '')
            tmdb_id = extract_tmdb_id_from_guid(guid)
            if tmdb_id:
                cache[coll.title] = tmdb_id
    except Exception as e:
        log(f"[ERROR] Failed to cache collections: {e}")

    # Write cache to CSV
    with open(CACHE_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['title', 'tmdb_id'])
        for title, tmdb_id in cache.items():
            writer.writerow([title, tmdb_id])
    log(f"[CACHE] Saved TMDb ID cache to {CACHE_FILE}")
    return cache

def load_tmdb_cache():
    """Loads TMDb cache from CSV file"""
    cache = {}
    if not os.path.exists(CACHE_FILE):
        return cache
    with open(CACHE_FILE, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row['title'] and row['tmdb_id'].isdigit():
                cache[row['title']] = int(row['tmdb_id'])
    return cache

# === MAIN FUNCTION TO HEAL ONE ART TYPE ===
def heal_art(item, library, kind, cache):
    """Backs up working art; if it's missing or dead, restores from backup, then TMDb (poster/background) or existing Plex art"""
    cfg = ART_KINDS[kind]
    label = cfg['label']
    name = item_label(item, kind)
    rel_url = cfg['url'](item)
    dead = not rel_url or artwork_url_invalid(f"{PLEX_URL}{rel_url}?X-Plex-Token={PLEX_TOKEN}")
    force = FORCE_UPLOAD_ALL and cfg.get('tmdb', False)   # forcing only applies to types TMDb can supply
    broken = force or dead
    backup_path = get_artwork_path(library, item, kind)
    ensure_folder(os.path.dirname(backup_path))

    # Step 1: Back up working artwork if there's no backup yet, or (optionally) if Plex's artwork has changed.
    # Never back up dead artwork, so a good backup is never replaced by a bad one.
    if not dead and (not os.path.exists(backup_path) or (REFRESH_BACKUPS and backup_is_stale(rel_url, backup_path))):
        if os.path.exists(backup_path):
            log(f"[REFRESH] {name} {label} changed in Plex; updating backup")
        backup_from_plex(name, kind, rel_url, backup_path)

    # Step 2: Skip if not broken and not forcing
    if not broken:
        log(f"[OK] {name} {label} is fine.")
        return

    log(f"[{'FORCE' if force else 'FIX'}] Repairing {label} for {name}")

    # Step 3: Restore from local backup
    if os.path.exists(backup_path) and not force:
        log(f"[RESTORE] Found local {label} for {name}")
        if ENABLE_UPLOAD and not DRY_RUN:
            upload_to_plex(item, name, kind, backup_path)
        return

    # Types TMDb can't supply: pick from what Plex already has
    if not cfg.get('tmdb'):
        select_existing_art(item, name, kind, rel_url)
        return

    # Step 4: TMDb Lookup from cache or search
    title = item.title
    tmdb_id = cache.get(title)
    tmdb_data = None
    if tmdb_id:
        try:
            tmdb_data = movie_search.details(tmdb_id)
        except Exception as e:
            log(f"[ERROR] TMDb lookup failed for {title} (ID {tmdb_id}): {e}")
    else:
        try:
            results = movie_search.search(title)
            tmdb_data = results[0] if results else None
        except Exception as e:
            log(f"[ERROR] TMDb search failed for {title}: {e}")

    # Step 5: Download and Upload artwork
    if tmdb_data and download_tmdb_art(tmdb_data, kind, backup_path):
        if ENABLE_UPLOAD and not DRY_RUN:
            upload_to_plex(item, name, kind, backup_path)
    else:
        log(f"[MISSING] No {label} found for {title}")

def select_existing_art(item, name, kind, current_url):
    """Selects the first art of this kind that Plex already has (TMDb has no clear logos, mobile backgrounds or title cards)"""
    cfg = ART_KINDS[kind]
    label = cfg['label']
    if not SELECT_EXISTING_ART:
        log(f"[MISSING] No {label} backup for {name} (SELECT_EXISTING_ART is off)")
        return
    try:
        candidates = getattr(item, cfg['candidates'])()
        if current_url:
            # Skip the dead one that is already selected
            candidates = [c for c in candidates if not c.selected] or candidates
        if not candidates:
            log(f"[MISSING] No {label} available in Plex for {name}")
            return
        log(f"[SELECT] Selecting first available {label} for {name}")
        if ENABLE_UPLOAD and not DRY_RUN:
            getattr(item, cfg['select'])(candidates[0])
    except Exception as e:
        log(f"[ERROR] Failed selecting {label} for {name}: {e}")

def process_item(item, library, cache):
    """Heals all enabled artwork types for a single movie or show"""
    if any(k in ART_TYPES for k in ('clear_logo', 'mobile_background')) and not item.isFullObject():
        item.reload()   # list results can omit the image info that logos / mobile backgrounds are read from
    for kind in ('poster', 'background', 'clear_logo', 'mobile_background'):
        if kind in ART_TYPES:
            heal_art(item, library, kind, cache)

def process_episode(ep, library):
    """Heals the title card for a single episode"""
    heal_art(ep, library, 'title_card', None)

def run_parallel(tasks):
    """Runs (func, args) tasks across WORKERS threads; each worker pauses DELAY after a task"""
    def run(func, args):
        try:
            func(*args)
        except Exception as e:
            log(f"[ERROR] Unhandled error in {func.__name__}: {e}")
        time.sleep(DELAY)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for future in as_completed([pool.submit(run, f, a) for f, a in tasks]):
            future.result()

def process_section(section, library, cache):
    """Processes all items in a Plex library section"""
    tasks = []
    if any(k in ART_TYPES for k in ('poster', 'background', 'clear_logo', 'mobile_background')):
        tasks += [(process_item, (item, library, cache)) for item in section.all()]
    if 'title_card' in ART_TYPES and section.type == 'show':
        # One query for every episode in the library instead of one per show
        tasks += [(process_episode, (ep, library)) for ep in section.searchEpisodes()]
    log(f"Queued {len(tasks)} tasks across {WORKERS} workers")
    run_parallel(tasks)

# === MAIN ENTRY POINT ===
def main():
    if ENABLE_LOG:
        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            f.write("=== Plex Artwork Healer Log ===\n")

    plex = PlexServer(PLEX_URL, PLEX_TOKEN)

    # Step 1: Generate ID Cache
    cache = build_tmdb_cache(plex)

    # Step 2: Process all libraries
    for lib in LIBRARIES:
        try:
            log(f"\n=== Processing Library: {lib} ===")
            section = plex.library.section(lib)
            process_section(section, lib, cache)
        except Exception as e:
            log(f"[ERROR] Failed processing {lib}: {e}")

if __name__ == '__main__':
    main()
