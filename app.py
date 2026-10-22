"""VidFetch - a small, private video downloader built on yt-dlp and FFmpeg.

Runs a local web server bound to 127.0.0.1 and shows the interface in a native
window (pywebview) when available, otherwise in your default browser.
"""
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

APP_NAME = "VidFetch"
VERSION = "1.0.0"  # replaced from the git tag by the release build
REPO = "SithumAluvihare/vidfetch"
FROZEN = getattr(sys, "frozen", False)
# Bundled resources (ui/) live in _MEIPASS when packaged; helper tools sit next to the exe.
HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else HERE
DATA_DIR = Path(os.environ.get("APPDATA") or (Path.home() / ".config")) / APP_NAME
DATA_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_FILE = DATA_DIR / "settings.json"
DB_FILE = DATA_DIR / "history.db"
LOG_FILE = DATA_DIR / "vidfetch.log"
ENGINE_DIR = DATA_DIR / "engine"  # newer yt-dlp downloaded by "Update yt-dlp"
IS_WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WIN else 0  # CREATE_NO_WINDOW

# pythonw.exe has no console: send stray output to a log file instead of crashing.
if sys.stdout is None or sys.stderr is None:
    _log = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or _log
    sys.stderr = sys.stderr or _log


def vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v or "")))


def _purge_engine_modules():
    for name in [m for m in sys.modules if m.split(".")[0] in ("yt_dlp", "yt_dlp_ejs")]:
        del sys.modules[name]


def _import_engine():
    """Import the bundled yt-dlp, or a newer one previously downloaded by the updater."""
    from yt_dlp.version import __version__ as bundled
    try:
        active = json.loads((ENGINE_DIR / "active.json").read_text(encoding="utf-8"))
        files = [str(ENGINE_DIR / f) for f in active.get("files", [])]
        if files and all(os.path.exists(f) for f in files) and \
                vtuple(active.get("version")) > vtuple(bundled):
            sys.path[:0] = files
            _purge_engine_modules()
            try:
                import yt_dlp as mod
                from yt_dlp.version import __version__ as v
                return mod, v
            except Exception:  # noqa: BLE001 - broken download: fall back to the bundled copy
                for f in files:
                    sys.path.remove(f)
                _purge_engine_modules()
    except (OSError, ValueError):
        pass
    import yt_dlp as mod
    from yt_dlp.version import __version__ as v
    return mod, v


yt_dlp, YTDLP_VERSION = _import_engine()
import yt_dlp.utils  # noqa: E402,F401

ANSI = re.compile(r"\x1b\[[0-9;]*m")
TOKEN = secrets.token_urlsafe(24)
QUIT = threading.Event()
ALLOWED_HOSTS = set()


def log(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + str(msg) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- ffmpeg

def find_ffmpeg():
    exe = "ffmpeg.exe" if IS_WIN else "ffmpeg"
    bundled = APP_DIR / "ffmpeg" / exe
    if bundled.exists():
        return str(bundled)
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


FFMPEG = find_ffmpeg()


def find_js_runtimes():
    """YouTube needs a JavaScript runtime to unlock most formats."""
    bundled = APP_DIR / "deno" / ("deno.exe" if IS_WIN else "deno")
    if bundled.exists():
        return {"deno": {"path": str(bundled)}}
    return {name: {} for name in ("deno", "node") if shutil.which(name)} or {"deno": {}}


JS_RUNTIMES = find_js_runtimes()

# ---------------------------------------------------------------- settings

DEFAULTS = {
    "download_dir": str(Path.home() / "Downloads" / APP_NAME),
    "max_parallel": 3,
}
_settings_lock = threading.Lock()


def load_settings():
    s = dict(DEFAULTS)
    try:
        s.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return s


def save_settings(s):
    with _settings_lock:
        SETTINGS_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- history

_db_lock = threading.Lock()


def db_exec(sql, args=(), fetch=False):
    with _db_lock:
        con = sqlite3.connect(DB_FILE)
        try:
            cur = con.execute(sql, args)
            rows = cur.fetchall() if fetch else None
            con.commit()
            return rows
        finally:
            con.close()


def init_db():
    db_exec("""CREATE TABLE IF NOT EXISTS history (
        id TEXT PRIMARY KEY, url TEXT, title TEXT, quality TEXT,
        thumbnail TEXT, filepath TEXT, finished_at REAL)""")


def history(limit=200):
    rows = db_exec("SELECT id,url,title,quality,thumbnail,filepath,finished_at FROM history "
                   "ORDER BY finished_at DESC LIMIT ?", (limit,), fetch=True)
    keys = ("id", "url", "title", "quality", "thumbnail", "filepath", "finished_at")
    out = []
    for r in rows:
        d = dict(zip(keys, r))
        d["exists"] = bool(d["filepath"]) and os.path.exists(d["filepath"])
        out.append(d)
    return out


# ---------------------------------------------------------------- yt-dlp glue

class QuietLogger:
    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        log("WARN " + ANSI.sub("", msg))

    def error(self, msg):
        log("ERROR " + ANSI.sub("", msg))


def clean_error(e):
    msg = ANSI.sub("", str(e)).strip()
    msg = re.sub(r"^ERROR:\s*", "", msg)
    msg = re.sub(r"^\[[\w:]+\]\s*[\w-]+:\s*", "", msg)
    return msg[:400] or "Download failed"


def fetch_info(url):
    opts = {"quiet": True, "no_warnings": True, "skip_download": True,
            "extract_flat": "in_playlist", "noplaylist": True, "logger": QuietLogger(),
            "js_runtimes": JS_RUNTIMES}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        thumb = info.get("thumbnail") or next(
            (t.get("url") for t in reversed(info.get("thumbnails") or []) if t.get("url")), None)
        if not thumb and entries:
            ts = entries[0].get("thumbnails") or []
            thumb = entries[0].get("thumbnail") or (ts[-1].get("url") if ts else None)
        return {"playlist": True, "title": info.get("title") or "Playlist", "count": len(entries),
                "thumbnail": thumb, "uploader": info.get("uploader") or info.get("channel"),
                "site": info.get("extractor_key"), "url": url}
    heights = sorted({f["height"] for f in info.get("formats") or []
                      if f.get("height") and f.get("vcodec") not in (None, "none")}, reverse=True)
    return {"playlist": False, "title": info.get("title") or url, "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"), "uploader": info.get("uploader") or info.get("channel"),
            "site": info.get("extractor_key"), "heights": heights,
            "url": info.get("webpage_url") or url}


QUALITY_LABELS = {"best": "Best", "1080": "1080p", "720": "720p", "480": "480p", "audio": "MP3"}


def build_opts(job, outdir):
    sub = "%(playlist_title).80B" + os.sep if job.playlist else ""
    tag = f" {job.quality}p" if job.quality.isdigit() else ""
    opts = {
        "outtmpl": {"default": os.path.join(outdir, sub + "%(title).150B [%(id)s]" + tag + ".%(ext)s")},
        "progress_hooks": [job.on_progress],
        "postprocessor_hooks": [job.on_postprocess],
        "logger": QuietLogger(),
        "quiet": True, "no_warnings": True, "noprogress": True,
        "windowsfilenames": True, "continuedl": True,
        "retries": 5, "fragment_retries": 5, "concurrent_fragment_downloads": 4,
        "noplaylist": not job.playlist, "ignoreerrors": job.playlist,
        "js_runtimes": JS_RUNTIMES,
    }
    if FFMPEG:
        opts["ffmpeg_location"] = FFMPEG
    if job.quality == "audio":
        opts["format"] = "ba/b"
        if FFMPEG:
            opts["postprocessors"] = [{"key": "FFmpegExtractAudio",
                                       "preferredcodec": "mp3", "preferredquality": "192"}]
    else:
        sort = ["ext:mp4:m4a"]
        if job.quality != "best":
            sort.insert(0, "res:" + job.quality)
        opts["format_sort"] = sort
        if FFMPEG:
            opts["format"] = "bv*+ba/b"
            opts["merge_output_format"] = "mp4"
        else:
            opts["format"] = "b"  # without FFmpeg only pre-merged files work
    return opts


# ---------------------------------------------------------------- jobs

JOBS = {}
_jobs_lock = threading.Lock()
_slots = threading.Semaphore(load_settings().get("max_parallel", 3))


class Job:
    def __init__(self, url, quality, title=None, thumbnail=None, playlist=False):
        self.id = uuid.uuid4().hex[:12]
        self.url, self.quality, self.playlist = url, quality, playlist
        self.title, self.thumbnail = title or url, thumbnail
        self.status, self.stage = "queued", "Waiting"
        self.progress, self.speed, self.eta = 0.0, None, None
        self.item, self.filepath, self.error = None, None, None
        self.stage_title = None
        self.created = time.time()
        self.cancel = threading.Event()

    def to_dict(self):
        return {k: getattr(self, k) for k in (
            "id", "url", "quality", "playlist", "title", "thumbnail", "status", "stage",
            "progress", "speed", "eta", "item", "stage_title", "filepath", "error", "created")}

    def on_progress(self, d):
        if self.cancel.is_set():
            raise yt_dlp.utils.DownloadCancelled("Cancelled")
        info = d.get("info_dict") or {}
        if self.playlist and info.get("playlist_index"):
            n = info.get("n_entries") or info.get("playlist_count")
            self.item = f"{info['playlist_index']}/{n}" if n else str(info["playlist_index"])
            if info.get("title"):
                self.stage_title = info["title"]
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                self.progress = min(100.0, d.get("downloaded_bytes", 0) * 100.0 / total)
            elif d.get("fragment_count"):
                self.progress = d.get("fragment_index", 0) * 100.0 / d["fragment_count"]
            self.speed, self.eta = d.get("speed"), d.get("eta")
            kind = "audio" if info.get("vcodec") == "none" and self.quality != "audio" else (
                "video" if info.get("acodec") == "none" else "")
            self.stage = ("Downloading " + kind).strip()
        elif d["status"] == "finished":
            self.progress, self.speed, self.eta = 100.0, None, None
            self.filepath = d.get("filename") or self.filepath

    def on_postprocess(self, d):
        if d.get("status") == "started":
            pp = d.get("postprocessor", "")
            self.stage = {"Merger": "Merging video and audio",
                          "ExtractAudio": "Converting to MP3"}.get(pp, "Finishing")
        elif d.get("status") == "finished":
            fp = (d.get("info_dict") or {}).get("filepath")
            if fp:
                self.filepath = fp

    def run(self):
        with _slots:
            if self.cancel.is_set():
                self.status, self.stage = "cancelled", "Cancelled"
                return
            self.status, self.stage = "running", "Starting"
            outdir = load_settings()["download_dir"]
            try:
                os.makedirs(outdir, exist_ok=True)
                with yt_dlp.YoutubeDL(build_opts(self, outdir)) as ydl:
                    info = ydl.extract_info(self.url, download=True)
                if self.cancel.is_set():
                    raise yt_dlp.utils.DownloadCancelled("Cancelled")
                if info is None:
                    raise RuntimeError("Nothing could be downloaded from this link")
                if self.playlist:
                    first = next((e for e in info.get("entries") or [] if e), None)
                    files = (first or {}).get("requested_downloads") or [{}]
                    fp = files[0].get("filepath")
                    self.filepath = os.path.dirname(fp) if fp else outdir
                else:
                    files = info.get("requested_downloads") or [{}]
                    self.filepath = files[0].get("filepath") or self.filepath
                    self.title = info.get("title") or self.title
                    self.thumbnail = self.thumbnail or info.get("thumbnail")
                self.status, self.stage, self.progress = "done", "Done", 100.0
                self.speed = self.eta = None
                db_exec("INSERT OR REPLACE INTO history VALUES (?,?,?,?,?,?,?)",
                        (self.id, self.url, self.title, QUALITY_LABELS.get(self.quality, self.quality),
                         self.thumbnail, self.filepath, time.time()))
            except BaseException as e:  # noqa: BLE001 - report every failure to the UI
                self.speed = self.eta = None
                if self.cancel.is_set() or isinstance(e, yt_dlp.utils.DownloadCancelled):
                    self.status, self.stage = "cancelled", "Cancelled"
                else:
                    self.status, self.stage, self.error = "error", "Failed", clean_error(e)
                    log(f"job {self.id} failed: {e!r}")


def start_job(**kw):
    job = Job(**kw)
    with _jobs_lock:
        JOBS[job.id] = job
    threading.Thread(target=job.run, daemon=True).start()
    return job


# ---------------------------------------------------------------- helpers

def reveal(path):
    if not path or not os.path.exists(path):
        raise FileNotFoundError("That file is no longer there")
    if IS_WIN:
        if os.path.isdir(path):
            os.startfile(path)
        else:
            subprocess.Popen(f'explorer /select,"{os.path.normpath(path)}"')
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    else:
        subprocess.Popen(["xdg-open", path if os.path.isdir(path) else os.path.dirname(path)])


WINDOW = None  # pywebview window, when running in one


def choose_folder(initial):
    if WINDOW is not None:
        import webview
        kind = getattr(getattr(webview, "FileDialog", None), "FOLDER", None)
        kind = kind if kind is not None else webview.FOLDER_DIALOG
        picked = WINDOW.create_file_dialog(kind, directory=initial)
        return picked[0] if picked else None
    result = {}

    def ask():
        import tkinter
        from tkinter import filedialog
        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        result["dir"] = filedialog.askdirectory(initialdir=initial, title="Choose download folder")
        root.destroy()

    t = threading.Thread(target=ask)
    t.start()
    t.join()
    return result.get("dir") or None


def http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/{VERSION}",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def download_wheel(package):
    """Fetch the newest pure-Python wheel of a package from PyPI and verify its hash."""
    meta = http_json(f"https://pypi.org/pypi/{package}/json")
    wheel = next(u for u in meta["urls"] if u["packagetype"] == "bdist_wheel"
                 and u["filename"].endswith("-none-any.whl"))
    dest = ENGINE_DIR / wheel["filename"]
    if not dest.exists():
        import hashlib
        req = urllib.request.Request(wheel["url"], headers={"User-Agent": f"{APP_NAME}/{VERSION}"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
        if hashlib.sha256(data).hexdigest() != wheel["digests"]["sha256"]:
            raise RuntimeError(f"{package} download was corrupted")
        tmp = dest.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(dest)
    return meta["info"]["version"], dest.name


UPDATE = {"running": False, "message": None}


def update_ytdlp():
    UPDATE.update(running=True, message="Checking for a newer yt-dlp...")
    try:
        latest = http_json("https://pypi.org/pypi/yt-dlp/json")["info"]["version"]
        if vtuple(latest) <= vtuple(YTDLP_VERSION):
            UPDATE["message"] = f"yt-dlp is up to date ({YTDLP_VERSION})."
            return
        UPDATE["message"] = f"Downloading yt-dlp {latest}..."
        ENGINE_DIR.mkdir(parents=True, exist_ok=True)
        version, main_file = download_wheel("yt-dlp")
        _, ejs_file = download_wheel("yt-dlp-ejs")
        (ENGINE_DIR / "active.json").write_text(json.dumps(
            {"version": version, "files": [main_file, ejs_file]}), encoding="utf-8")
        for old in ENGINE_DIR.glob("*.whl"):  # keep only the active pair
            if old.name not in (main_file, ejs_file):
                old.unlink(missing_ok=True)
        UPDATE["message"] = f"Updated to {version}. Restart VidFetch to use it."
    except Exception as e:  # noqa: BLE001
        log(f"yt-dlp update failed: {e!r}")
        UPDATE["message"] = "Update failed. Check your internet connection and try again."
    finally:
        UPDATE["running"] = False


APP_UPDATE = {}


def check_app_update():
    """Look for a newer VidFetch release on GitHub (once per launch)."""
    try:
        rel = http_json(f"https://api.github.com/repos/{REPO}/releases/latest")
        tag = rel.get("tag_name", "").lstrip("v")
        if vtuple(tag) > vtuple(VERSION):
            APP_UPDATE.update(version=tag, url=rel.get("html_url"))
    except Exception as e:  # noqa: BLE001 - offline or rate-limited: stay quiet
        log(f"update check skipped: {e!r}")


# ---------------------------------------------------------------- API

def api_state(_):
    with _jobs_lock:
        jobs = [j.to_dict() for j in sorted(JOBS.values(), key=lambda j: j.created, reverse=True)]
    return {"jobs": jobs, "settings": load_settings(), "ffmpeg": bool(FFMPEG),
            "ytdlp": YTDLP_VERSION, "version": VERSION, "update": UPDATE,
            "app_update": APP_UPDATE or None}


def api_info(b):
    url = (b.get("url") or "").strip()
    if not re.match(r"^https?://", url, re.I):
        raise ValueError("Paste a link that starts with http:// or https://")
    try:
        return fetch_info(url)
    except yt_dlp.utils.DownloadError as e:
        raise ValueError(clean_error(e)) from None


def api_download(b):
    url = (b.get("url") or "").strip()
    q = b.get("quality", "best")
    if not re.match(r"^https?://", url, re.I) or q not in QUALITY_LABELS:
        raise ValueError("Invalid request")
    with _jobs_lock:
        dup = any(j.url == url and j.quality == q and j.status in ("queued", "running")
                  for j in JOBS.values())
    if dup:
        raise ValueError("That video is already downloading in this quality")
    job = start_job(url=url, quality=q, title=b.get("title"), thumbnail=b.get("thumbnail"),
                    playlist=bool(b.get("playlist")))
    return job.to_dict()


def _job(b):
    job = JOBS.get(b.get("id", ""))
    if not job:
        raise ValueError("Unknown download")
    return job


def api_cancel(b):
    _job(b).cancel.set()
    return {"ok": True}


def api_retry(b):
    old = _job(b)
    with _jobs_lock:
        JOBS.pop(old.id, None)
    return start_job(url=old.url, quality=old.quality, title=old.title,
                     thumbnail=old.thumbnail, playlist=old.playlist).to_dict()


def api_remove(b):
    job = _job(b)
    job.cancel.set()
    with _jobs_lock:
        JOBS.pop(job.id, None)
    return {"ok": True}


def api_clear(_):
    with _jobs_lock:
        for k in [k for k, j in JOBS.items() if j.status in ("done", "error", "cancelled")]:
            del JOBS[k]
    return {"ok": True}


def api_reveal(b):
    path = b.get("path")
    if b.get("id") in JOBS:
        path = JOBS[b["id"]].filepath
    reveal(path or load_settings()["download_dir"])
    return {"ok": True}


def api_open_folder(_):
    d = load_settings()["download_dir"]
    os.makedirs(d, exist_ok=True)
    reveal(d)
    return {"ok": True}


def api_settings(b):
    s = load_settings()
    if "download_dir" in b:
        d = os.path.expanduser(str(b["download_dir"]).strip())
        if not d:
            raise ValueError("Choose a folder")
        os.makedirs(d, exist_ok=True)
        s["download_dir"] = os.path.abspath(d)
    save_settings(s)
    return s


def api_choose_folder(_):
    picked = choose_folder(load_settings()["download_dir"])
    return api_settings({"download_dir": picked}) if picked else load_settings()


def api_history(_):
    return {"items": history()}


def api_history_delete(b):
    if b.get("all"):
        db_exec("DELETE FROM history")
    else:
        db_exec("DELETE FROM history WHERE id=?", (b.get("id", ""),))
    return {"ok": True}


def api_update(_):
    if not UPDATE["running"]:
        threading.Thread(target=update_ytdlp, daemon=True).start()
    return UPDATE


def api_open_release(_):
    webbrowser.open(APP_UPDATE.get("url") or f"https://github.com/{REPO}/releases/latest")
    return {"ok": True}


def api_quit(_):
    for j in list(JOBS.values()):
        j.cancel.set()
    threading.Timer(0.4, QUIT.set).start()
    return {"ok": True}


ROUTES_GET = {"/api/state": api_state, "/api/history": api_history}
ROUTES_POST = {
    "/api/info": api_info, "/api/download": api_download, "/api/cancel": api_cancel,
    "/api/retry": api_retry, "/api/remove": api_remove, "/api/clear": api_clear,
    "/api/reveal": api_reveal, "/api/open_folder": api_open_folder, "/api/settings": api_settings,
    "/api/choose_folder": api_choose_folder, "/api/history/delete": api_history_delete,
    "/api/update": api_update, "/api/quit": api_quit, "/api/open_release": api_open_release,
}


class Handler(BaseHTTPRequestHandler):
    server_version = APP_NAME

    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _guard(self):
        # Block other websites (DNS rebinding / CSRF) from driving the local API.
        if self.headers.get("Host", "") not in ALLOWED_HOSTS:
            self._send(403, {"error": "Forbidden"})
            return False
        return True

    def _api(self, routes, body):
        path = urlparse(self.path).path
        fn = routes.get(path)
        if not fn:
            return self._send(404, {"error": "Not found"})
        if not secrets.compare_digest(self.headers.get("X-Token", ""), TOKEN):
            return self._send(403, {"error": "Forbidden"})
        try:
            self._send(200, fn(body))
        except (ValueError, FileNotFoundError) as e:
            self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            log(f"{path} crashed: {e!r}")
            self._send(500, {"error": clean_error(e)})

    def do_GET(self):
        if not self._guard():
            return
        path = urlparse(self.path).path
        if path == "/":
            html = (HERE / "ui" / "index.html").read_text(encoding="utf-8")
            return self._send(200, html.replace("__TOKEN__", TOKEN).encode(), "text/html; charset=utf-8")
        if path == "/icon.svg":
            return self._send(200, (HERE / "ui" / "icon.svg").read_bytes(), "image/svg+xml")
        self._api(ROUTES_GET, {})

    def do_POST(self):
        if not self._guard():
            return
        n = int(self.headers.get("Content-Length") or 0)
        if n > 65536:
            return self._send(413, {"error": "Too large"})
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, {"error": "Bad JSON"})
        self._api(ROUTES_POST, body if isinstance(body, dict) else {})


def make_server():
    for port in range(int(os.environ.get("VIDFETCH_PORT", 8765)), 8800):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            srv.daemon_threads = True
            ALLOWED_HOSTS.update({f"127.0.0.1:{port}", f"localhost:{port}"})
            return srv, port
        except OSError:
            continue
    raise SystemExit("No free port between 8765 and 8799")


def main():
    init_db()
    srv, port = make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"
    log(f"VidFetch {VERSION} started on {url} (yt-dlp {YTDLP_VERSION}, ffmpeg={FFMPEG}, "
        f"js={JS_RUNTIMES})")
    if FROZEN:
        threading.Thread(target=check_app_update, daemon=True).start()

    if os.environ.get("VIDFETCH_HEADLESS"):
        print(url, flush=True)
        QUIT.wait()
    else:
        try:
            import webview  # optional native window
        except ImportError:
            webview = None
        opened = False
        if webview:
            try:
                global WINDOW
                win = WINDOW = webview.create_window(APP_NAME, url, width=1040, height=760,
                                            min_size=(760, 560), background_color="#111214")
                threading.Thread(target=lambda: (QUIT.wait(), win.destroy()), daemon=True).start()
                webview.start()
                opened = True
            except Exception as e:  # noqa: BLE001 - fall back to the browser
                log(f"native window unavailable: {e!r}")
        if not opened:
            webbrowser.open(url)
            QUIT.wait()
    srv.shutdown()
    os._exit(0)


if __name__ == "__main__":
    main()
