"""FREEDOM SMP website - Flask + SQLite.

Run:  python app.py        (development)
See README.md for configuration and deployment notes.
"""
import csv
import io
import json
import os
import re
import secrets
import socket
import sqlite3
import struct
import threading
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from flask import (Flask, Response, abort, flash, g, jsonify, redirect,
                   render_template, request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATABASE = os.environ.get("FREEDOM_DB_PATH", os.path.join(BASE_DIR, "freedom_smp.db"))
IST = timezone(timedelta(hours=5, minutes=30))
LAUNCH_DEFAULT = "2026-10-30T22:59:00+05:30"   # 30 Oct 2026, 10:59 PM IST

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
MC_NAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
WALLET_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
HOST_RE = re.compile(r"^[A-Za-z0-9._:\-]+$")

TICKET_CATEGORIES = [
    ("general", "General question"),
    ("whitelist", "Whitelist help"),
    ("rank_payment", "Rank or payment"),
    ("password", "Password reset"),
    ("ban_appeal", "Ban appeal"),
    ("bug", "Report a bug or a player"),
    ("other", "Something else"),
]

DEFAULT_RULES = """## Respect
Be respectful to every player and staff member. No harassment, hate speech, or threats.
Keep chat friendly. No spam, and no advertising other servers or communities.
## Fair play
No cheating: x-ray, kill aura, fly, auto-clickers, or any unfair client modifications.
Do not exploit bugs or duplication glitches. Report them to staff instead.
## Builds and the world
No griefing. Do not destroy, steal from, or change other players' builds or chests without permission.
Keep builds tidy and avoid lag machines or huge redstone clocks.
## Accounts and staff
One person, one main account. Never share your account or password.
Staff decisions are final in-game. If you disagree, open a support ticket and be respectful.
Staff may update these rules at any time, so check Discord for announcements."""

DEFAULT_SETTINGS = {
    "server_ip": os.environ.get("SERVER_IP", "free2.vektalnodes.in:26843"),
    "server_version": os.environ.get("SERVER_VERSION", "Java 1.21.x"),
    "server_status": "auto",
    "discord_url": os.environ.get("DISCORD_URL", "https://discord.gg/gkdRBbfHy"),
    "server_description": "A fresh world. Your story. Your freedom.",
    "launch_at": LAUNCH_DEFAULT,
    "hide_ip_until_launch": "1",
    "countdown_url": "https://techtancs4-creator.github.io/Freedom-smp-countdown/",
    "youtube_url": "",
    "site_banner": "",
    "registrations_open": "1",
    "whitelist_open": "1",
    "response_time": "24-48 hours",
    "support_email": "techtancs4@gmail.com",
    "wallet_address": "0xCeC80B4C69Af97ee5F6a970958dB27213292c8a3",
    "rules_text": DEFAULT_RULES,
}


def load_secret_key():
    """Use SECRET_KEY if set, otherwise create and persist a random one.

    A hard-coded fallback key would let anyone forge login cookies, so we never ship one.
    """
    env = os.environ.get("SECRET_KEY")
    if env:
        return env
    path = os.path.join(os.path.dirname(DATABASE), ".secret_key")
    try:
        with open(path, encoding="utf-8") as fh:
            value = fh.read().strip()
        if value:
            return value
    except OSError:
        pass
    value = secrets.token_hex(32)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(value)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError:
        pass
    return value


app = Flask(__name__)
app.config["SECRET_KEY"] = load_secret_key()
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "0") == "1"
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
if os.environ.get("TRUST_PROXY") == "1":
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


def _asset_version():
    try:
        return str(int(max(os.path.getmtime(os.path.join(BASE_DIR, "static", n))
                           for n in ("style.css", "extra.css", "mc.css", "app.js"))))
    except OSError:
        return "1"


ASSET_V = _asset_version()


# --------------------------------------------------------------------------- database
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _has_column(conn, table, column):
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def _add_column(conn, table, column, ddl):
    if not _has_column(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db():
    conn = sqlite3.connect(DATABASE)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT NOT NULL COLLATE NOCASE UNIQUE,
      email TEXT NOT NULL COLLATE NOCASE UNIQUE,
      password_hash TEXT NOT NULL,
      role TEXT NOT NULL DEFAULT 'player' CHECK(role IN ('player','admin')),
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS announcements (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      title TEXT NOT NULL, body TEXT NOT NULL, tag TEXT NOT NULL DEFAULT 'NEWS',
      created_at TEXT NOT NULL, author_id INTEGER,
      FOREIGN KEY(author_id) REFERENCES users(id) ON DELETE SET NULL
    );
    CREATE TABLE IF NOT EXISTS whitelist_requests (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL, minecraft_name TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
      status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','declined')),
      created_at TEXT NOT NULL,
      FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS vote_sites (
      id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, url TEXT NOT NULL,
      description TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS ranks (
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
      price TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS rank_orders (
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
      rank_id INTEGER NOT NULL, minecraft_name TEXT NOT NULL, tx_hash TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','declined')),
      created_at TEXT NOT NULL,
      FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
      FOREIGN KEY(rank_id) REFERENCES ranks(id)
    );
    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS tickets (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER, contact_name TEXT NOT NULL DEFAULT '', contact_email TEXT NOT NULL DEFAULT '',
      category TEXT NOT NULL DEFAULT 'general', subject TEXT NOT NULL, message TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','answered','closed')),
      reply TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, replied_at TEXT, replied_by INTEGER,
      FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
    );
    CREATE TABLE IF NOT EXISTS audit_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id INTEGER, actor_name TEXT NOT NULL DEFAULT '',
      action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
    );
    """)
    # ---- additive migrations for databases created by older versions
    _add_column(conn, "users", "is_banned", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "users", "last_login", "TEXT")
    _add_column(conn, "announcements", "pinned", "INTEGER NOT NULL DEFAULT 0")
    for table in ("whitelist_requests", "rank_orders"):
        _add_column(conn, table, "admin_note", "TEXT NOT NULL DEFAULT ''")
        _add_column(conn, table, "reviewed_at", "TEXT")
        _add_column(conn, table, "reviewed_by", "INTEGER")
    try:  # the same payment hash must not unlock more than one order
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_rank_orders_tx ON rank_orders(tx_hash COLLATE NOCASE)")
    except sqlite3.DatabaseError:
        pass
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, value))
    # v2: status used to be a manual switch that defaulted to 'offline'; it is now automatic.
    version = conn.execute("SELECT value FROM settings WHERE key='schema_version'").fetchone()
    if not version:
        conn.execute("UPDATE settings SET value='auto' WHERE key='server_status' AND value='offline'")
        conn.execute("INSERT INTO settings(key,value) VALUES('schema_version','2')")
    if conn.execute("SELECT COUNT(*) FROM ranks").fetchone()[0] == 0:
        conn.executemany("INSERT INTO ranks(name,price,description) VALUES (?,?,?)", [
            ("Supporter", "2 USDT", "Support the server and stand out in the community."),
            ("Champion", "5 USDT", "A bigger contribution to help keep the server running."),
            ("Legend", "10 USDT", "Top-tier community supporter rank.")])
    if conn.execute("SELECT COUNT(*) FROM announcements").fetchone()[0] == 0:
        now = now_iso()
        conn.execute("INSERT INTO announcements(title, body, tag, created_at) VALUES (?,?,?,?)",
                     ("We're back. Freedom SMP launches soon", "A brand-new world, new players and a new story. Create your account and request your whitelist spot now so you're ready on launch day.", "LAUNCH", now))
        conn.execute("INSERT INTO announcements(title, body, tag, created_at) VALUES (?,?,?,?)",
                     ("Join the community on Discord", "Updates, events and staff support all live on our Discord. Come say hi.", "COMMUNITY", now))
    admin_username = os.environ.get("ADMIN_USERNAME")
    admin_password = os.environ.get("ADMIN_PASSWORD")
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@example.com")
    if admin_username and admin_password:
        existing = conn.execute("SELECT id FROM users WHERE username = ? COLLATE NOCASE", (admin_username,)).fetchone()
        password_hash = generate_password_hash(admin_password)
        if existing:
            conn.execute("UPDATE users SET email=?, password_hash=?, role='admin', is_banned=0 WHERE id=?",
                         (admin_email, password_hash, existing[0]))
        else:
            conn.execute("INSERT INTO users(username,email,password_hash,role,created_at) VALUES (?,?,?,?,?)",
                         (admin_username, admin_email, password_hash, "admin", now_iso()))
    conn.commit()
    conn.close()


def get_settings():
    if "settings" not in g:
        merged = dict(DEFAULT_SETTINGS)
        merged.update({r["key"]: r["value"] for r in db().execute("SELECT key, value FROM settings")})
        g.settings = merged
    return g.settings


def log_action(action, detail=""):
    user = current_user()
    db().execute("INSERT INTO audit_log(actor_id, actor_name, action, detail, created_at) VALUES (?,?,?,?,?)",
                 (user["id"] if user else None, user["username"] if user else "system", action, detail[:300], now_iso()))


# --------------------------------------------------------------------------- helpers
def parse_dt(value):
    try:
        dt = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=IST)


def launch_info(settings=None):
    settings = settings or get_settings()
    dt = parse_dt(settings.get("launch_at", ""))
    if not dt:
        return {"iso": "", "set": False, "prelaunch": False, "locked": False, "ts": 0,
                "date_label": "", "time_label": "", "local_input": ""}
    prelaunch = datetime.now(timezone.utc) < dt
    ist = dt.astimezone(IST)
    return {
        "iso": dt.isoformat(), "set": True, "prelaunch": prelaunch,
        "locked": prelaunch and settings.get("hide_ip_until_launch") == "1",
        "ts": int(dt.timestamp() * 1000),
        "date_label": f"{ist.day} {ist.strftime('%b %Y')}",
        "time_label": ist.strftime("%I:%M %p").lstrip("0") + " IST",
        "local_input": ist.strftime("%Y-%m-%dT%H:%M"),
    }


def client_ip():
    return request.remote_addr or "unknown"


_rate = {}
_rate_lock = threading.Lock()


def rate_blocked(key, limit, window):
    now = time.time()
    with _rate_lock:
        hits = [t for t in _rate.get(key, []) if now - t < window]
        _rate[key] = hits
        return len(hits) >= limit


def rate_hit(key):
    now = time.time()
    with _rate_lock:
        if len(_rate) > 5000:
            for k in [k for k, v in _rate.items() if not v or now - v[-1] > 3600]:
                _rate.pop(k, None)
        _rate.setdefault(key, []).append(now)


def rate_clear(key):
    with _rate_lock:
        _rate.pop(key, None)


def safe_next(target):
    """Only allow same-site relative redirects (blocks //evil.com and /\\evil.com)."""
    if not target or "\\" in target or not target.startswith("/") or target.startswith("//"):
        return None
    parsed = urlparse(target)
    return target if not parsed.scheme and not parsed.netloc else None


def valid_https_url(value, max_len=500):
    if len(value) > max_len:
        return False
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


COMMON_PASSWORDS = {"password12", "password123", "1234567890", "12345678910", "qwertyuiop", "iloveyou12",
                    "minecraft1", "minecraft12", "minecraft123", "freedomsmp", "freedom123", "letmein123"}


def password_errors(password, username=""):
    errors = []
    if len(password) < 10:
        errors.append("Password must be at least 10 characters.")
    if len(password) > 128:
        errors.append("Password must be 128 characters or fewer.")
    if password and len(set(password)) < 4:
        errors.append("Password is too repetitive. Mix in more different characters.")
    if password.lower() in COMMON_PASSWORDS:
        errors.append("That password is too common. Pick something more unique.")
    if username and len(username) >= 3 and username.lower() in password.lower():
        errors.append("Password should not contain your username.")
    return errors


def csv_safe(value):
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@app.template_filter("d")
def fmt_date(value):
    dt = parse_dt(value) if value else None
    return f"{dt.day} {dt.strftime('%b %Y')}" if dt else ""


@app.template_filter("dt")
def fmt_datetime(value):
    dt = parse_dt(value) if value else None
    return f"{dt.day} {dt.strftime('%b %Y, %H:%M')} UTC" if dt else ""


@app.template_filter("category_label")
def category_label(value):
    return dict(TICKET_CATEGORIES).get(value, value)


# --------------------------------------------------------------------------- request hooks
@app.before_request
def csrf_setup():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    if request.method == "POST":
        sent = request.form.get("csrf_token", "")
        expected = session.get("csrf_token", "")
        # compare as bytes: comparing non-ASCII str values would raise TypeError (a 500 error)
        if not sent or not secrets.compare_digest(sent.encode("utf-8"), expected.encode("utf-8")):
            abort(400, description="Your security token expired. Go back, refresh the page and try again.")


@app.after_request
def security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if session.get("user_id") and not request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def current_user():
    if "current_user" in g:
        return g.current_user
    user = None
    uid = session.get("user_id")
    if uid:
        user = db().execute("SELECT id, username, email, role, created_at, is_banned FROM users WHERE id = ?", (uid,)).fetchone()
        if user is None or user["is_banned"]:
            session.pop("user_id", None)
            user = None
    g.current_user = user
    return user


@app.context_processor
def inject_globals():
    settings = get_settings()
    launch = launch_info(settings)
    user = current_user()
    is_admin = bool(user and user["role"] == "admin")
    return {
        "current_user": user, "csrf_token": session.get("csrf_token", ""), "site_settings": settings,
        "launch": launch, "ip_visible": (not launch["locked"]) or is_admin,
        "response_time": settings.get("response_time", "24-48 hours"),
        "support_email": settings.get("support_email", ""), "discord_url": settings.get("discord_url", ""),
        "asset_v": ASSET_V, "ticket_categories": TICKET_CATEGORIES,
    }


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not current_user():
            flash("Log in to access your player dashboard.", "info")
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)
    return wrapped


def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user or user["role"] != "admin":
            abort(403)
        return fn(*args, **kwargs)
    return wrapped


# --------------------------------------------------------------------------- live server status
def _varint(number):
    out = b""
    while True:
        byte = number & 0x7F
        number >>= 7
        out += bytes([byte | (0x80 if number else 0)])
        if not number:
            return out


def _read_varint(sock):
    result = 0
    for i in range(5):
        chunk = sock.recv(1)
        if not chunk:
            raise OSError("connection closed")
        value = chunk[0]
        result |= (value & 0x7F) << (7 * i)
        if not value & 0x80:
            return result
    raise OSError("varint too long")


def _read_exact(sock, size):
    buf = b""
    while len(buf) < size:
        chunk = sock.recv(min(4096, size - len(buf)))
        if not chunk:
            raise OSError("connection closed")
        buf += chunk
    return buf


def slp_status(host, port, timeout=4):
    """Query a Java server directly with the Minecraft Server List Ping protocol."""
    started = time.time()
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        host_bytes = host.encode("utf-8")
        payload = b"\x00" + _varint(765) + _varint(len(host_bytes)) + host_bytes + struct.pack(">H", port) + _varint(1)
        sock.sendall(_varint(len(payload)) + payload)
        sock.sendall(b"\x01\x00")
        _read_varint(sock)
        if _read_varint(sock) != 0:
            raise OSError("unexpected packet")
        length = _read_varint(sock)
        if length > 262144:
            raise OSError("response too large")
        data = json.loads(_read_exact(sock, length).decode("utf-8"))
    players = data.get("players") or {}
    version = data.get("version") or {}
    return {"online": True, "players": players.get("online"), "max": players.get("max"),
            "version": str(version.get("name", ""))[:60] or None, "latency": int((time.time() - started) * 1000)}


def web_status(host, port):
    """Fallback for hosts that block raw outgoing sockets: ask the public mcsrvstat.us API."""
    req = Request(f"https://api.mcsrvstat.us/3/{host}:{port}",
                  headers={"User-Agent": "FreedomSMP-Website/2.0", "Accept": "application/json"})
    with urlopen(req, timeout=6) as response:
        data = json.loads(response.read().decode("utf-8"))
    players = data.get("players") or {}
    return {"online": bool(data.get("online")), "players": players.get("online"), "max": players.get("max"),
            "version": str(data.get("version", ""))[:60] or None, "latency": None}


def fetch_live_status(host, port):
    refused = False
    try:
        return slp_status(host, port)
    except ConnectionRefusedError:
        refused = True
    except (OSError, ValueError):
        pass
    try:
        return web_status(host, port)
    except (URLError, HTTPError, OSError, ValueError):
        pass
    return {"online": False} if refused else {"online": None}


def parse_address(address):
    address = (address or "").strip()
    host, port = address, 25565
    if ":" in address:
        head, tail = address.rsplit(":", 1)
        if tail.isdigit() and 1 <= int(tail) <= 65535 and head:
            host, port = head, int(tail)
    return host, port


_status_cache = {"key": None, "at": 0.0, "ttl": 30, "data": None}
_status_lock = threading.Lock()


def cached_status(address):
    with _status_lock:
        fresh = (_status_cache["key"] == address and _status_cache["data"] is not None
                 and time.time() - _status_cache["at"] < _status_cache["ttl"])
        if fresh:
            return _status_cache["data"]
    host, port = parse_address(address)
    result = fetch_live_status(host, port)
    with _status_lock:
        _status_cache.update(key=address, at=time.time(), data=result, ttl=30 if result.get("online") is not None else 10)
    return result


@app.route("/api/server-status")
def api_server_status():
    settings = get_settings()
    launch = launch_info(settings)
    override = settings.get("server_status", "auto")
    payload = {"state": "unknown", "checked_at": now_iso()}
    if launch["locked"]:
        payload.update(state="prelaunch", launch_at=launch["iso"])
    elif override in ("maintenance", "online", "offline"):
        payload["state"] = override
    else:
        result = cached_status(settings.get("server_ip", ""))
        if result.get("online") is True:
            payload.update(state="online", players=result.get("players"), max=result.get("max"),
                           version=result.get("version"), latency=result.get("latency"))
        elif result.get("online") is False:
            payload["state"] = "offline"
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


# --------------------------------------------------------------------------- public pages
@app.route("/")
def index():
    announcements = db().execute(
        "SELECT a.*, u.username AS author FROM announcements a LEFT JOIN users u ON a.author_id=u.id "
        "ORDER BY a.pinned DESC, a.id DESC LIMIT 4").fetchall()
    vote_sites = db().execute("SELECT * FROM vote_sites WHERE active=1 ORDER BY id").fetchall()
    ranks = db().execute("SELECT * FROM ranks WHERE active=1 ORDER BY id").fetchall()
    facts = {
        "accounts": db().execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "whitelisted": db().execute("SELECT COUNT(DISTINCT minecraft_name COLLATE NOCASE) FROM whitelist_requests WHERE status='approved'").fetchone()[0],
    }
    return render_template("index.html", announcements=announcements, vote_sites=vote_sites, ranks=ranks, facts=facts)


def rules_blocks(text):
    blocks, current = [], None
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("## "):
            current = {"title": line[3:].strip(), "items": []}
            blocks.append(current)
        else:
            if current is None:
                current = {"title": "Rules", "items": []}
                blocks.append(current)
            current["items"].append(line)
    return blocks


@app.route("/rules")
def rules():
    return render_template("rules.html", blocks=rules_blocks(get_settings().get("rules_text", "")))


@app.route("/join")
def join():
    return render_template("join.html")


@app.route("/faq")
def faq():
    return render_template("faq.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/vote")
def vote():
    return redirect(url_for("index") + "#vote")


@app.route("/launch.ics")
def launch_ics():
    launch = launch_info()
    dt = parse_dt(launch["iso"]) if launch["set"] else None
    if not dt:
        abort(404)
    start = dt.astimezone(timezone.utc)
    stamp = "%Y%m%dT%H%M%SZ"
    body = "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//FREEDOM SMP//Launch//EN", "BEGIN:VEVENT",
        "UID:freedom-smp-launch@freedomsmp", f"DTSTAMP:{datetime.now(timezone.utc).strftime(stamp)}",
        f"DTSTART:{start.strftime(stamp)}", f"DTEND:{(start + timedelta(hours=1)).strftime(stamp)}",
        "SUMMARY:FREEDOM SMP launch", f"DESCRIPTION:The server address is revealed at launch. Details: {request.url_root}",
        "END:VEVENT", "END:VCALENDAR", ""])
    return Response(body, mimetype="text/calendar", headers={"Content-Disposition": "attachment; filename=freedom-smp-launch.ics"})


@app.route("/robots.txt")
def robots():
    return Response("User-agent: *\nDisallow: /admin\nDisallow: /dashboard\nDisallow: /api/\n", mimetype="text/plain")


@app.route("/healthz")
def healthz():
    return jsonify(ok=True)


# --------------------------------------------------------------------------- accounts
@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user():
        return redirect(url_for("dashboard"))
    settings = get_settings()
    form = {"username": "", "email": ""}
    errors = []
    if request.method == "POST":
        form["username"] = request.form.get("username", "").strip()
        form["email"] = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if settings.get("registrations_open") != "1":
            errors.append("Registrations are temporarily closed. Please check Discord for news.")
        elif rate_blocked(f"register:{client_ip()}", 8, 3600):
            errors.append("Too many sign-up attempts from your network. Please try again in an hour.")
        else:
            rate_hit(f"register:{client_ip()}")
            if not USERNAME_RE.match(form["username"]):
                errors.append("Username must be 3-20 characters: letters, numbers or underscores only.")
            if len(form["email"]) > 254 or not EMAIL_RE.match(form["email"]):
                errors.append("Enter a valid email address.")
            errors += password_errors(password, form["username"])
            if password != confirm:
                errors.append("The two passwords do not match.")
            if not errors:
                taken_user = db().execute("SELECT 1 FROM users WHERE username = ? COLLATE NOCASE", (form["username"],)).fetchone()
                taken_mail = db().execute("SELECT 1 FROM users WHERE email = ? COLLATE NOCASE", (form["email"],)).fetchone()
                if taken_user:
                    errors.append("That username is already taken. Try another one.")
                if taken_mail:
                    errors.append("That email is already registered. Try logging in instead.")
            if not errors:
                try:
                    cur = db().execute("INSERT INTO users(username,email,password_hash,role,created_at,last_login) VALUES (?,?,?,?,?,?)",
                                       (form["username"], form["email"], generate_password_hash(password), "player", now_iso(), now_iso()))
                    db().commit()
                except sqlite3.IntegrityError:
                    errors.append("That username or email is already registered.")
                else:
                    session.clear()
                    session["user_id"] = cur.lastrowid
                    session["csrf_token"] = secrets.token_urlsafe(32)
                    flash("Welcome to the community! Your account is ready. Next step: request your whitelist spot below.", "success")
                    return redirect(url_for("dashboard") + "#whitelist")
    return render_template("auth.html", mode="register", errors=errors, form=form,
                           registrations_open=settings.get("registrations_open") == "1"), (400 if errors and request.method == "POST" else 200)


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("dashboard"))
    errors = []
    form = {"identity": ""}
    status = 200
    if request.method == "POST":
        form["identity"] = request.form.get("identity", "").strip()[:254]
        password = request.form.get("password", "")
        key = f"login:{client_ip()}:{form['identity'].lower()}"
        ip_key = f"login-ip:{client_ip()}"
        if rate_blocked(key, 5, 600) or rate_blocked(ip_key, 30, 600):
            errors.append("Too many failed attempts. Please wait about 10 minutes and try again.")
            status = 429
        else:
            user = db().execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE OR email = ? COLLATE NOCASE",
                                (form["identity"], form["identity"])).fetchone()
            if user and check_password_hash(user["password_hash"], password):
                if user["is_banned"]:
                    errors.append("This account is suspended. You can appeal from the Support page (choose 'Ban appeal').")
                    status = 403
                else:
                    rate_clear(key)
                    session.clear()
                    session["user_id"] = user["id"]
                    session["csrf_token"] = secrets.token_urlsafe(32)
                    session.permanent = request.form.get("remember") == "1"
                    db().execute("UPDATE users SET last_login=? WHERE id=?", (now_iso(), user["id"]))
                    db().commit()
                    flash("You're back. Welcome in!", "success")
                    return redirect(safe_next(request.args.get("next")) or url_for("dashboard"))
            else:
                rate_hit(key)
                rate_hit(ip_key)
                errors.append("We couldn't match that username/email and password. Check them and try again.")
                status = 401
    return render_template("auth.html", mode="login", errors=errors, form=form,
                           registrations_open=get_settings().get("registrations_open") == "1"), status


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))


@app.route("/account/password", methods=["POST"])
@login_required
def change_password():
    user = db().execute("SELECT * FROM users WHERE id=?", (current_user()["id"],)).fetchone()
    current = request.form.get("current_password", "")
    new = request.form.get("new_password", "")
    confirm = request.form.get("confirm_password", "")
    errors = []
    if rate_blocked(f"pwchange:{user['id']}", 5, 600):
        errors.append("Too many attempts. Please wait a few minutes.")
    elif not check_password_hash(user["password_hash"], current):
        rate_hit(f"pwchange:{user['id']}")
        errors.append("Your current password is not correct.")
    else:
        errors += password_errors(new, user["username"])
        if new != confirm:
            errors.append("The new passwords do not match.")
        if new and new == current:
            errors.append("Choose a new password that is different from the current one.")
    if errors:
        for message in errors:
            flash(message, "error")
    else:
        db().execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new), user["id"]))
        db().commit()
        flash("Password updated.", "success")
    return redirect(url_for("dashboard") + "#account")


# --------------------------------------------------------------------------- player area
@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    requests_list = db().execute("SELECT * FROM whitelist_requests WHERE user_id = ? ORDER BY id DESC", (user["id"],)).fetchall()
    orders = db().execute("SELECT o.*, r.name AS rank_name, r.price FROM rank_orders o JOIN ranks r ON r.id=o.rank_id "
                          "WHERE o.user_id=? ORDER BY o.id DESC", (user["id"],)).fetchall()
    rank_options = db().execute("SELECT id,name,price FROM ranks WHERE active=1 ORDER BY id").fetchall()
    tickets = db().execute("SELECT * FROM tickets WHERE user_id=? ORDER BY id DESC LIMIT 20", (user["id"],)).fetchall()
    has_pending = any(r["status"] == "pending" for r in requests_list)
    is_approved = any(r["status"] == "approved" for r in requests_list)
    return render_template("dashboard.html", requests_list=requests_list, orders=orders, rank_options=rank_options,
                           tickets=tickets, has_pending=has_pending, is_approved=is_approved)


@app.route("/whitelist", methods=["POST"])
@login_required
def whitelist():
    user = current_user()
    minecraft_name = request.form.get("minecraft_name", "").strip()
    note = request.form.get("note", "").strip()
    if get_settings().get("whitelist_open") != "1":
        flash("Whitelist requests are temporarily closed. Check Discord for news.", "info")
    elif not MC_NAME_RE.match(minecraft_name):
        flash("Enter a valid Minecraft Java username (3-16 letters, numbers or underscores).", "error")
    elif len(note) > 300:
        flash("Your note must be 300 characters or fewer.", "error")
    elif rate_blocked(f"wl:{user['id']}", 6, 3600):
        flash("Too many requests. Please wait a while before trying again.", "error")
    else:
        rate_hit(f"wl:{user['id']}")
        mine = db().execute("SELECT 1 FROM whitelist_requests WHERE user_id=? AND status='pending'", (user["id"],)).fetchone()
        dup = db().execute("SELECT 1 FROM whitelist_requests WHERE minecraft_name=? COLLATE NOCASE AND status IN ('pending','approved') AND user_id<>?",
                           (minecraft_name, user["id"])).fetchone()
        already = db().execute("SELECT 1 FROM whitelist_requests WHERE minecraft_name=? COLLATE NOCASE AND status='approved' AND user_id=?",
                               (minecraft_name, user["id"])).fetchone()
        if mine:
            flash("You already have a pending request. Staff will review it within " + get_settings()["response_time"] + ".", "info")
        elif already:
            flash(f"{minecraft_name} is already approved on your account.", "info")
        elif dup:
            flash("That Minecraft name is already used on another request. If it's really yours, open a support ticket.", "error")
        else:
            db().execute("INSERT INTO whitelist_requests(user_id,minecraft_name,note,created_at) VALUES (?,?,?,?)",
                         (user["id"], minecraft_name, note, now_iso()))
            db().commit()
            flash(f"Whitelist request submitted. Staff review requests within {get_settings()['response_time']}. "
                  "The status will appear on your dashboard.", "success")
    return redirect(url_for("dashboard") + "#whitelist")


@app.route("/whitelist/<int:request_id>/cancel", methods=["POST"])
@login_required
def cancel_whitelist(request_id):
    db().execute("DELETE FROM whitelist_requests WHERE id=? AND user_id=? AND status='pending'", (request_id, current_user()["id"]))
    db().commit()
    flash("Pending request cancelled. You can submit a new one.", "success")
    return redirect(url_for("dashboard") + "#whitelist")


@app.route("/buy-rank", methods=["POST"])
@login_required
def buy_rank():
    user = current_user()
    rank_id = request.form.get("rank_id", type=int)
    minecraft_name = request.form.get("minecraft_name", "").strip()
    tx_hash = request.form.get("tx_hash", "").strip()
    rank = db().execute("SELECT id FROM ranks WHERE id=? AND active=1", (rank_id,)).fetchone()
    if not rank or not MC_NAME_RE.match(minecraft_name) or not (20 <= len(tx_hash) <= 180) or " " in tx_hash:
        flash("Choose a rank and enter a valid Minecraft name and transaction hash (no spaces).", "error")
    elif rate_blocked(f"order:{user['id']}", 5, 3600):
        flash("Too many orders submitted. Please wait a while before trying again.", "error")
    else:
        rate_hit(f"order:{user['id']}")
        try:
            db().execute("INSERT INTO rank_orders(user_id,rank_id,minecraft_name,tx_hash,created_at) VALUES (?,?,?,?,?)",
                         (user["id"], rank_id, minecraft_name, tx_hash, now_iso()))
            db().commit()
            flash(f"Rank order submitted. Staff verify payments manually within {get_settings()['response_time']}. "
                  "Your rank is not granted until an admin approves it.", "success")
        except sqlite3.IntegrityError:
            flash("That transaction hash was already submitted. Each payment can only be used once. Open a support ticket if this is a mistake.", "error")
    return redirect(url_for("dashboard") + "#rank-orders")


# --------------------------------------------------------------------------- support
@app.route("/support", methods=["GET", "POST"])
def support():
    user = current_user()
    form = {"category": request.args.get("category", "general"), "subject": "", "message": "", "contact_email": "", "contact_name": ""}
    if form["category"] not in dict(TICKET_CATEGORIES):
        form["category"] = "general"
    if request.method == "POST":
        for key in form:
            form[key] = request.form.get(key, "").strip()
        errors = []
        if request.form.get("website"):  # honeypot field - real people leave it empty
            return redirect(url_for("support"))
        if form["category"] not in dict(TICKET_CATEGORIES):
            errors.append("Choose a valid category.")
        if not (3 <= len(form["subject"]) <= 100):
            errors.append("Subject must be 3-100 characters.")
        if not (10 <= len(form["message"]) <= 1500):
            errors.append("Message must be 10-1,500 characters.")
        if not user:
            if not (2 <= len(form["contact_name"]) <= 40):
                errors.append("Enter your name or Minecraft username so we know who you are.")
            if len(form["contact_email"]) > 254 or not EMAIL_RE.match(form["contact_email"]):
                errors.append("Enter a valid email so staff can reply to you.")
        who = f"user:{user['id']}" if user else f"ip:{client_ip()}"
        if not errors and rate_blocked(f"ticket:{who}", 5 if user else 3, 3600):
            errors.append("You've sent several tickets recently. Please wait a while before sending another.")
        if errors:
            for message in errors:
                flash(message, "error")
        else:
            rate_hit(f"ticket:{who}")
            db().execute("INSERT INTO tickets(user_id,contact_name,contact_email,category,subject,message,created_at) VALUES (?,?,?,?,?,?,?)",
                         (user["id"] if user else None, user["username"] if user else form["contact_name"],
                          user["email"] if user else form["contact_email"].lower(), form["category"], form["subject"], form["message"], now_iso()))
            db().commit()
            flash(f"Ticket sent! Staff will reply within {get_settings()['response_time']}."
                  + (" You can follow the reply on your dashboard." if user else " We'll answer at the email you gave."), "success")
            return redirect(url_for("support") + "#sent")
    return render_template("support.html", form=form)


# --------------------------------------------------------------------------- admin
def review_fields(status, note):
    return status, note.strip()[:300], now_iso(), current_user()["id"]


@app.route("/admin")
@admin_required
def admin():
    q = request.args.get("q", "").strip()[:60]
    conn = db()
    stats = {
        "users": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "new_week": conn.execute("SELECT COUNT(*) FROM users WHERE created_at >= ?", ((datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),)).fetchone()[0],
        "pending_wl": conn.execute("SELECT COUNT(*) FROM whitelist_requests WHERE status='pending'").fetchone()[0],
        "approved_wl": conn.execute("SELECT COUNT(*) FROM whitelist_requests WHERE status='approved'").fetchone()[0],
        "pending_orders": conn.execute("SELECT COUNT(*) FROM rank_orders WHERE status='pending'").fetchone()[0],
        "open_tickets": conn.execute("SELECT COUNT(*) FROM tickets WHERE status='open'").fetchone()[0],
        "banned": conn.execute("SELECT COUNT(*) FROM users WHERE is_banned=1").fetchone()[0],
    }
    announcements = conn.execute("SELECT a.*, u.username AS author FROM announcements a LEFT JOIN users u ON a.author_id=u.id "
                                 "ORDER BY a.pinned DESC, a.id DESC").fetchall()
    requests_list = conn.execute("SELECT w.*, u.username AS account_name, u.email FROM whitelist_requests w JOIN users u ON w.user_id=u.id "
                                 "ORDER BY CASE w.status WHEN 'pending' THEN 0 ELSE 1 END, w.id DESC").fetchall()
    approved_names = [r["minecraft_name"] for r in conn.execute(
        "SELECT DISTINCT minecraft_name FROM whitelist_requests WHERE status='approved' ORDER BY minecraft_name COLLATE NOCASE")]
    if q:
        like = f"%{q}%"
        users = conn.execute("SELECT id,username,email,role,created_at,is_banned,last_login FROM users "
                             "WHERE username LIKE ? OR email LIKE ? ORDER BY id DESC LIMIT 200", (like, like)).fetchall()
    else:
        users = conn.execute("SELECT id,username,email,role,created_at,is_banned,last_login FROM users ORDER BY id DESC LIMIT 200").fetchall()
    vote_sites = conn.execute("SELECT * FROM vote_sites ORDER BY id DESC").fetchall()
    ranks = conn.execute("SELECT r.*, (SELECT COUNT(*) FROM rank_orders o WHERE o.rank_id=r.id) AS order_count FROM ranks r ORDER BY r.id DESC").fetchall()
    orders = conn.execute("SELECT o.*,u.username AS account_name,u.email,r.name AS rank_name,r.price FROM rank_orders o "
                          "JOIN users u ON u.id=o.user_id JOIN ranks r ON r.id=o.rank_id "
                          "ORDER BY CASE o.status WHEN 'pending' THEN 0 ELSE 1 END,o.id DESC").fetchall()
    tickets = conn.execute("SELECT t.*, u.username AS account_name FROM tickets t LEFT JOIN users u ON u.id=t.user_id "
                           "ORDER BY CASE t.status WHEN 'open' THEN 0 WHEN 'answered' THEN 1 ELSE 2 END, t.id DESC LIMIT 200").fetchall()
    audit = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 100").fetchall()
    return render_template("admin.html", stats=stats, announcements=announcements, requests_list=requests_list,
                           approved_names=approved_names, users=users, vote_sites=vote_sites, ranks=ranks,
                           orders=orders, tickets=tickets, audit=audit, q=q)


@app.route("/admin/announcement", methods=["POST"])
@admin_required
def create_announcement():
    title = request.form.get("title", "").strip()
    body = request.form.get("body", "").strip()
    tag = request.form.get("tag", "NEWS").strip().upper()[:20] or "NEWS"
    pinned = 1 if request.form.get("pinned") == "1" else 0
    if not title or len(title) > 90 or not body or len(body) > 1200:
        flash("Add a title (up to 90 characters) and message (up to 1,200 characters).", "error")
    else:
        db().execute("INSERT INTO announcements(title,body,tag,created_at,author_id,pinned) VALUES (?,?,?,?,?,?)",
                     (title, body, tag, now_iso(), current_user()["id"], pinned))
        log_action("announcement.create", title)
        db().commit()
        flash("Announcement published.", "success")
    return redirect(url_for("admin") + "#announcements")


@app.route("/admin/announcement/<int:item_id>/edit", methods=["POST"])
@admin_required
def edit_announcement(item_id):
    title = request.form.get("title", "").strip()
    body = request.form.get("body", "").strip()
    tag = request.form.get("tag", "NEWS").strip().upper()[:20] or "NEWS"
    if not title or len(title) > 90 or not body or len(body) > 1200:
        flash("Add a title (up to 90 characters) and message (up to 1,200 characters).", "error")
    else:
        db().execute("UPDATE announcements SET title=?, body=?, tag=? WHERE id=?", (title, body, tag, item_id))
        log_action("announcement.edit", title)
        db().commit()
        flash("Announcement updated.", "success")
    return redirect(url_for("admin") + "#announcements")


@app.route("/admin/announcement/<int:item_id>/pin", methods=["POST"])
@admin_required
def pin_announcement(item_id):
    db().execute("UPDATE announcements SET pinned = CASE pinned WHEN 1 THEN 0 ELSE 1 END WHERE id=?", (item_id,))
    log_action("announcement.pin", f"#{item_id}")
    db().commit()
    return redirect(url_for("admin") + "#announcements")


@app.route("/admin/announcement/<int:announcement_id>/delete", methods=["POST"])
@admin_required
def delete_announcement(announcement_id):
    db().execute("DELETE FROM announcements WHERE id = ?", (announcement_id,))
    log_action("announcement.delete", f"#{announcement_id}")
    db().commit()
    flash("Announcement deleted.", "success")
    return redirect(url_for("admin") + "#announcements")


@app.route("/admin/request/<int:request_id>", methods=["POST"])
@admin_required
def update_request(request_id):
    status = request.form.get("status")
    if status not in ("pending", "approved", "declined"):
        abort(400)
    status, note, when, who = review_fields(status, request.form.get("admin_note", ""))
    db().execute("UPDATE whitelist_requests SET status=?, admin_note=?, reviewed_at=?, reviewed_by=? WHERE id=?",
                 (status, note, when, who, request_id))
    row = db().execute("SELECT minecraft_name FROM whitelist_requests WHERE id=?", (request_id,)).fetchone()
    log_action("whitelist." + status, row["minecraft_name"] if row else f"#{request_id}")
    db().commit()
    flash("Whitelist request updated. Remember to add approved names to the actual Minecraft whitelist.", "success")
    return redirect(url_for("admin") + "#whitelist")


@app.route("/admin/export/whitelist.txt")
@admin_required
def export_whitelist():
    names = [r["minecraft_name"] for r in db().execute(
        "SELECT DISTINCT minecraft_name FROM whitelist_requests WHERE status='approved' ORDER BY minecraft_name COLLATE NOCASE")]
    return Response("\n".join(names) + ("\n" if names else ""), mimetype="text/plain",
                    headers={"Content-Disposition": "attachment; filename=approved-whitelist.txt"})


@app.route("/admin/export/orders.csv")
@admin_required
def export_orders():
    rows = db().execute("SELECT o.id,o.created_at,u.username,u.email,o.minecraft_name,r.name AS rank_name,r.price,o.tx_hash,o.status,o.admin_note "
                        "FROM rank_orders o JOIN users u ON u.id=o.user_id JOIN ranks r ON r.id=o.rank_id ORDER BY o.id DESC").fetchall()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "created_at", "account", "email", "minecraft_name", "rank", "price", "tx_hash", "status", "admin_note"])
    for r in rows:
        writer.writerow([csv_safe(r[k]) for k in r.keys()])
    return Response(buf.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=rank-orders.csv"})


@app.route("/admin/vote-site", methods=["POST"])
@admin_required
def add_vote_site():
    title = request.form.get("title", "").strip()[:80]
    url = request.form.get("url", "").strip()
    description = request.form.get("description", "").strip()[:200]
    if not title or not valid_https_url(url):
        flash("Enter a title and a secure https:// vote URL.", "error")
    else:
        db().execute("INSERT INTO vote_sites(title,url,description) VALUES (?,?,?)", (title, url, description))
        log_action("vote.add", title)
        db().commit()
        flash("Vote link added.", "success")
    return redirect(url_for("admin") + "#votes")


@app.route("/admin/vote-site/<int:item_id>/toggle", methods=["POST"])
@admin_required
def toggle_vote_site(item_id):
    db().execute("UPDATE vote_sites SET active = CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=?", (item_id,))
    db().commit()
    return redirect(url_for("admin") + "#votes")


@app.route("/admin/vote-site/<int:item_id>/delete", methods=["POST"])
@admin_required
def delete_vote_site(item_id):
    db().execute("DELETE FROM vote_sites WHERE id=?", (item_id,))
    log_action("vote.delete", f"#{item_id}")
    db().commit()
    flash("Vote link removed.", "success")
    return redirect(url_for("admin") + "#votes")


@app.route("/admin/rank", methods=["POST"])
@admin_required
def add_rank():
    name = request.form.get("name", "").strip()[:50]
    price = request.form.get("price", "").strip()[:30]
    description = request.form.get("description", "").strip()[:200]
    if not name or not price:
        flash("Rank name and price are required.", "error")
    else:
        try:
            db().execute("INSERT INTO ranks(name,price,description) VALUES (?,?,?)", (name, price, description))
            log_action("rank.add", name)
            db().commit()
            flash("Rank added.", "success")
        except sqlite3.IntegrityError:
            flash("That rank name already exists.", "error")
    return redirect(url_for("admin") + "#ranks")


@app.route("/admin/rank/<int:rank_id>/edit", methods=["POST"])
@admin_required
def edit_rank(rank_id):
    name = request.form.get("name", "").strip()[:50]
    price = request.form.get("price", "").strip()[:30]
    description = request.form.get("description", "").strip()[:200]
    if not name or not price:
        flash("Rank name and price are required.", "error")
    else:
        try:
            db().execute("UPDATE ranks SET name=?, price=?, description=? WHERE id=?", (name, price, description, rank_id))
            log_action("rank.edit", name)
            db().commit()
            flash("Rank updated.", "success")
        except sqlite3.IntegrityError:
            flash("Another rank already uses that name.", "error")
    return redirect(url_for("admin") + "#ranks")


@app.route("/admin/rank/<int:rank_id>/toggle", methods=["POST"])
@admin_required
def toggle_rank(rank_id):
    db().execute("UPDATE ranks SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=?", (rank_id,))
    log_action("rank.toggle", f"#{rank_id}")
    db().commit()
    return redirect(url_for("admin") + "#ranks")


@app.route("/admin/rank/<int:rank_id>/delete", methods=["POST"])
@admin_required
def delete_rank(rank_id):
    used = db().execute("SELECT COUNT(*) FROM rank_orders WHERE rank_id=?", (rank_id,)).fetchone()[0]
    if used:
        flash("This rank has orders, so it can't be deleted. Hide it instead.", "error")
    else:
        db().execute("DELETE FROM ranks WHERE id=?", (rank_id,))
        log_action("rank.delete", f"#{rank_id}")
        db().commit()
        flash("Rank deleted.", "success")
    return redirect(url_for("admin") + "#ranks")


@app.route("/admin/order/<int:order_id>", methods=["POST"])
@admin_required
def update_order(order_id):
    status = request.form.get("status")
    if status not in ("pending", "approved", "declined"):
        abort(400)
    status, note, when, who = review_fields(status, request.form.get("admin_note", ""))
    db().execute("UPDATE rank_orders SET status=?, admin_note=?, reviewed_at=?, reviewed_by=? WHERE id=?", (status, note, when, who, order_id))
    log_action("order." + status, f"#{order_id}")
    db().commit()
    flash("Order updated. If approved, remember to grant the rank in your server/plugin separately.", "success")
    return redirect(url_for("admin") + "#rank-orders")


@app.route("/admin/ticket/<int:ticket_id>", methods=["POST"])
@admin_required
def update_ticket(ticket_id):
    status = request.form.get("status", "answered")
    reply = request.form.get("reply", "").strip()[:1500]
    if status not in ("open", "answered", "closed"):
        abort(400)
    if reply and status == "open":
        status = "answered"
    if reply:
        db().execute("UPDATE tickets SET status=?, reply=?, replied_at=?, replied_by=? WHERE id=?",
                     (status, reply, now_iso(), current_user()["id"], ticket_id))
    else:
        db().execute("UPDATE tickets SET status=? WHERE id=?", (status, ticket_id))
    log_action("ticket." + status, f"#{ticket_id}")
    db().commit()
    flash("Ticket updated.", "success")
    return redirect(url_for("admin") + "#tickets")


@app.route("/admin/user/<int:user_id>/ban", methods=["POST"])
@admin_required
def toggle_ban(user_id):
    target = db().execute("SELECT id, username, role, is_banned FROM users WHERE id=?", (user_id,)).fetchone()
    if not target:
        abort(404)
    if target["id"] == current_user()["id"] or target["role"] == "admin":
        flash("You can't suspend an admin account (including your own).", "error")
    else:
        new_state = 0 if target["is_banned"] else 1
        db().execute("UPDATE users SET is_banned=? WHERE id=?", (new_state, user_id))
        log_action("user.suspend" if new_state else "user.unsuspend", target["username"])
        db().commit()
        flash(f"{target['username']} {'suspended' if new_state else 'restored'}.", "success")
    return redirect(url_for("admin") + "#users")


@app.route("/admin/user/<int:user_id>/role", methods=["POST"])
@admin_required
def change_role(user_id):
    role = request.form.get("role")
    target = db().execute("SELECT id, username, role FROM users WHERE id=?", (user_id,)).fetchone()
    if role not in ("player", "admin") or not target:
        abort(400)
    if target["id"] == current_user()["id"]:
        flash("You can't change your own role.", "error")
    else:
        db().execute("UPDATE users SET role=? WHERE id=?", (role, user_id))
        log_action("user.role", f"{target['username']} -> {role}")
        db().commit()
        flash(f"{target['username']} is now {role}.", "success")
    return redirect(url_for("admin") + "#users")


@app.route("/admin/user/<int:user_id>/reset-password", methods=["POST"])
@admin_required
def reset_password(user_id):
    target = db().execute("SELECT id, username, role FROM users WHERE id=?", (user_id,)).fetchone()
    if not target:
        abort(404)
    if target["role"] == "admin" and target["id"] != current_user()["id"]:
        flash("Reset another admin's password from the server console instead.", "error")
    else:
        temp = secrets.token_urlsafe(9)
        db().execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(temp), user_id))
        log_action("user.reset_password", target["username"])
        db().commit()
        flash(f"Temporary password for {target['username']}: {temp}  (shown once - send it privately and ask them to change it in their dashboard)", "success")
    return redirect(url_for("admin") + "#users")


def _setting_https(value, label, required=False):
    if not value:
        if required:
            raise ValueError(f"{label} is required.")
        return ""
    if not valid_https_url(value):
        raise ValueError(f"{label} must be a secure https:// link.")
    return value


@app.route("/admin/settings", methods=["POST"])
@admin_required
def update_settings():
    form = request.form
    errors, values = [], {}

    def take(key, fn):
        try:
            values[key] = fn((form.get(key) or "").strip())
        except ValueError as exc:
            errors.append(str(exc))

    def need_text(label, limit, required=True, pattern=None):
        def run(v):
            if required and not v:
                raise ValueError(f"{label} is required.")
            if len(v) > limit:
                raise ValueError(f"{label} must be {limit} characters or fewer.")
            if pattern and v and not pattern.match(v):
                raise ValueError(f"{label} has invalid characters.")
            return v
        return run

    def yes_no(v):
        return "1" if v == "1" else "0"

    def launch(v):
        if not v:
            return ""
        try:
            local = datetime.strptime(v, "%Y-%m-%dT%H:%M").replace(tzinfo=IST)
        except ValueError:
            raise ValueError("Launch date/time is not valid.")
        return local.isoformat()

    def wallet(v):
        if v and not WALLET_RE.match(v):
            raise ValueError("Wallet address must look like 0x followed by 40 hex characters.")
        return v

    def email(v):
        if v and (len(v) > 120 or not EMAIL_RE.match(v)):
            raise ValueError("Support email is not valid.")
        return v

    def status(v):
        if v not in ("auto", "online", "offline", "maintenance"):
            raise ValueError("Status mode is not valid.")
        return v

    take("server_ip", need_text("Server address", 100, pattern=HOST_RE))
    take("server_version", need_text("Version label", 80))
    take("server_status", status)
    take("server_description", need_text("Short description", 160, required=False))
    take("discord_url", lambda v: _setting_https(v, "Discord invite URL"))
    take("youtube_url", lambda v: _setting_https(v, "YouTube URL"))
    take("countdown_url", lambda v: _setting_https(v, "Countdown page URL"))
    take("site_banner", need_text("Banner message", 200, required=False))
    take("launch_at", launch)
    take("hide_ip_until_launch", yes_no)
    take("registrations_open", yes_no)
    take("whitelist_open", yes_no)
    take("response_time", need_text("Response time text", 40))
    take("support_email", email)
    take("wallet_address", wallet)
    take("rules_text", need_text("Rules", 6000, required=False))
    if errors:
        for message in errors:
            flash(message, "error")
    else:
        for key, value in values.items():
            db().execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        log_action("settings.update", ", ".join(sorted(values)))
        db().commit()
        with _status_lock:
            _status_cache.update(key=None, data=None)
        flash("Settings saved.", "success")
    return redirect(url_for("admin") + "#settings")


# --------------------------------------------------------------------------- errors
def _error(code, heading, message):
    return render_template("error.html", code=code, heading=heading, message=message), code


@app.errorhandler(400)
def bad_request(error):
    return _error(400, "Request expired", getattr(error, "description", "Refresh and try again."))


@app.errorhandler(403)
def forbidden(_error_):
    return _error(403, "Staff only", "This area is reserved for the FREEDOM SMP admin team.")


@app.errorhandler(404)
def not_found(_error_):
    return _error(404, "Chunk not found", "That page doesn't exist in this world.")


@app.errorhandler(413)
def too_large(_error_):
    return _error(413, "That's too big", "The data you sent was too large.")


@app.errorhandler(500)
def server_error(_error_):
    return _error(500, "Something broke", "Something went wrong on our side. Please try again, and tell staff if it keeps happening.")


init_db()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1", host=os.environ.get("HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", "5000")))
