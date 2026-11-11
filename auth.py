"""Login and role control.

Roles: beekeeper, lab, distributor, admin. Reading the ledger, tracing a batch and the QR page are public,
so a customer can scan a bottle without an account. Everything that writes needs a role (see PERMISSIONS).
Passwords are stored as salted hashes (Werkzeug) in users.json. This is demo-grade access control for a
review build, not a production identity system.
"""
import json
import os
import re
import secrets
import threading
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse

from flask import abort, g, jsonify, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

BASE = Path(__file__).parent
USERS_FILE = BASE / "users.json"
SECRET_FILE = BASE / ".secret_key"
_lock = threading.Lock()

ROLES = {
    "admin": "Administrator",
    "lab": "Laboratory analyst",
    "beekeeper": "Beekeeper",
    "distributor": "Distributor",
}

# What each role may do.
PERMISSIONS = {
    "check": {"admin", "lab", "beekeeper"},          # run a quality check, which registers a batch
    "advance": {"admin", "distributor", "lab"},      # move a batch to Processed, Distributed, Sold
    "retrain": {"admin", "lab"},                     # upload a lab dataset and retrain the model
    "spectrum": {"admin", "lab"},                    # run the spectral second opinion
    "photo": {"admin", "lab", "beekeeper"},          # photo screening of a jar (never writes to the ledger)
    "tamper": {"admin"},                             # presentation-only tamper demo
}

DEMO_USERS = [
    ("admin", "admin", "Admin"),
    ("lab", "lab", "Lab Analyst"),
    ("beekeeper", "beekeeper", "Beekeeper"),
    ("distributor", "distributor", "Distributor"),
]
DEMO_PASSWORD = "honey123"


def _load():
    if USERS_FILE.exists():
        return json.loads(USERS_FILE.read_text())
    return None


def init_users(demo):
    """Create users.json on first run. Demo mode uses a known password; otherwise passwords are random
    and printed once in the terminal."""
    with _lock:
        if _load() is not None:
            return
        users, printed = {}, []
        for name, role, display in DEMO_USERS:
            pw = DEMO_PASSWORD if demo else secrets.token_urlsafe(9)
            users[name] = {"role": role, "name": display, "hash": generate_password_hash(pw)}
            printed.append((name, pw))
        USERS_FILE.write_text(json.dumps(users, indent=2))
        if not demo:
            print("HoneyChain: created users.json. Passwords (shown once):")
            for name, pw in printed:
                print(f"  {name}: {pw}")


_fails = {}          # (ip, username) -> [count, locked_until]
MAX_FAILS, LOCK_SECONDS = 5, 60


def locked(key):
    import time
    rec = _fails.get(key)
    return bool(rec and rec[1] > time.time())


def note_failure(key):
    import time
    rec = _fails.setdefault(key, [0, 0])
    rec[0] += 1
    if rec[0] >= MAX_FAILS:
        rec[0], rec[1] = 0, time.time() + LOCK_SECONDS


def clear_failures(key):
    _fails.pop(key, None)


def authenticate(username, password):
    users = _load() or {}
    u = users.get((username or "").strip().lower())
    # Always run one hash check so a wrong username takes about as long as a wrong password.
    stored = u["hash"] if u else generate_password_hash("not-a-user")
    if check_password_hash(stored, password or "") and u:
        return {"username": username.strip().lower(), "role": u["role"], "name": u["name"]}
    return None


def secret_key():
    env = os.environ.get("SECRET_KEY")
    if env:
        return env
    if not SECRET_FILE.exists():
        SECRET_FILE.write_text(secrets.token_hex(32))
    return SECRET_FILE.read_text().strip()


def current_user():
    return session.get("user")


def can(permission, user=None):
    user = user if user is not None else current_user()
    return bool(user) and user["role"] in PERMISSIONS.get(permission, set())


def safe_next(target):
    """Only allow redirects back into this site."""
    if not target or not re.fullmatch(r"/[A-Za-z0-9_\-./?=&%]*", target) or target.startswith("//"):
        return url_for("index")
    return target


def require(permission, api=True):
    """Decorator. API routes answer 401/403 as JSON; page routes redirect to the login page."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            user = current_user()
            if not user:
                if api:
                    return jsonify(error="Please log in first."), 401
                return redirect(url_for("login", next=request.path))
            if not can(permission, user):
                if api:
                    return jsonify(error=f"Your role ({ROLES[user['role']]}) cannot do this."), 403
                abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco


def same_origin_only():
    """Refuse state-changing requests that were sent from another website (basic CSRF protection)."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            return jsonify(error="Cross-site request blocked."), 403
    return None
