# app.py — Postgres-only, SQLAlchemy + Flask-Migrate — upsell + bilingual fixes (updated)
import os
import logging
import requests
import secrets
import json
import re
from datetime import timedelta, datetime
from functools import wraps
import time

from flask import (
    Flask, render_template, request, jsonify,
    session, redirect, url_for, g, current_app
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from cachetools import TTLCache
from werkzeug.security import generate_password_hash, check_password_hash

# >>> PROD CHANGE: optional security middleware & proxy fix imports
try:
    from werkzeug.middleware.proxy_fix import ProxyFix
    PROXYFIX_AVAILABLE = True
except Exception:
    PROXYFIX_AVAILABLE = False

# >>> PROD CHANGE: optional Talisman for secure headers
try:
    from flask_talisman import Talisman
    TALISMAN_AVAILABLE = True
except Exception:
    TALISMAN_AVAILABLE = False

# load .env early
from dotenv import load_dotenv
load_dotenv()

# Flask-WTF / CSRF
from flask_wtf import FlaskForm, CSRFProtect
from flask_wtf.csrf import generate_csrf
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired

# SQLAlchemy / Migrations
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from sqlalchemy import text as sql_text
from sqlalchemy.exc import OperationalError  # >>> PROD CHANGE: used for health checks

# date parser
from dateutil import parser as dateparser

# language tools (optional)
try:
    from langdetect import detect
    LANGDETECT_AVAILABLE = True
except Exception:
    LANGDETECT_AVAILABLE = False

try:
    from deep_translator import GoogleTranslator
    DEEP_TRANSLATOR_AVAILABLE = True
except Exception:
    DEEP_TRANSLATOR_AVAILABLE = False

# Optional Redis / server-side session
try:
    import redis as _redis
    REDIS_AVAILABLE = True
except Exception:
    REDIS_AVAILABLE = False

try:
    from flask_session import Session as FlaskSession
    FLASK_SESSION_AVAILABLE = True
except Exception:
    FLASK_SESSION_AVAILABLE = False


# ===== App config =====
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config['VERSION'] = os.getenv("APP_VERSION", "3.0")
app.permanent_session_lifetime = timedelta(
    minutes=int(os.getenv("SESSION_MINUTES", "15"))
)

# >>> PROD CHANGE: apply ProxyFix if running behind a reverse proxy (nginx/gunicorn)
if PROXYFIX_AVAILABLE and os.getenv("USE_PROXYFIX", "True").lower() in ("1", "true", "yes"):
    # trust X-Forwarded-* from first proxy by default - can be tuned with environment
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
    logging.getLogger("restaurant_bot").info("ProxyFix applied.")

# secrets & cookies
# Enforce SECRET_KEY in production; allow a dev fallback when explicitly in debug/dev.
_secret = os.getenv("SECRET_KEY") or os.getenv("FLASK_SECRET")
if not _secret:
    # If running in production (FLASK_ENV=production or FLASK_DEBUG not true), fail fast
    if os.getenv("FLASK_ENV", "").lower() == "production" or os.getenv("FLASK_DEBUG", "False").lower() not in ("1", "true", "yes"):
        raise RuntimeError("SECRET_KEY must be set in environment for production runs")
    _secret = os.getenv("DEV_SECRET", "dev-fallback-change-me")
    logging.getLogger("restaurant_bot").warning("Using fallback SECRET_KEY — only for development!")
app.secret_key = _secret

# >>> PROD CHANGE: default to secure cookies in production unless explicitly disabled
default_secure = os.getenv("SESSION_COOKIE_SECURE", "").strip()
if default_secure == "":
    # if in production environment, enable secure cookies by default
    app.config['SESSION_COOKIE_SECURE'] = (os.getenv("FLASK_ENV", "").lower() == "production")
else:
    app.config['SESSION_COOKIE_SECURE'] = default_secure.lower() in ("1", "true", "yes")

app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")

# CSRF
csrf = CSRFProtect(app)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("restaurant_bot")

# Admin creds
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
ADMIN_PASS_HASH = os.getenv("ADMIN_PASS_HASH")
if not ADMIN_PASS_HASH:
    # hash provided plain password for dev convenience
    ADMIN_PASS_HASH = generate_password_hash(ADMIN_PASSWORD, method="pbkdf2:sha256")

# Groq (Llama 3.1) config
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_BASE = os.getenv("GROQ_BASE", "https://api.groq.com/openai/v1")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")


# Database config (Postgres required)
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    logger.error("DATABASE_URL is not set. Please set DATABASE_URL in .env (postgres connection string).")
    raise RuntimeError("DATABASE_URL not configured. Example: postgresql://user:pass@host:5432/dbname")

# >>> PROD CHANGE: SQLAlchemy engine options for pooling and pre-ping
db_pool_size = int(os.getenv("DB_POOL_SIZE", "10"))
db_max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "20"))
db_pool_timeout = int(os.getenv("DB_POOL_TIMEOUT", "30"))

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "pool_pre_ping": True,
    "pool_size": db_pool_size,
    "max_overflow": db_max_overflow,
    "pool_timeout": db_pool_timeout
}

db = SQLAlchemy(app)
migrate = Migrate(app, db)

# ===== Server-side session configuration (preferred) =====
# If REDIS_URL is present and Flask-Session + redis are installed, prefer Redis.
REDIS_URL = os.getenv("REDIS_URL", "").strip()
redis_client = None
cache = None
CACHE_TTL = int(os.getenv("CACHE_TTL", "300"))
if REDIS_URL and FLASK_SESSION_AVAILABLE:
    # Use Flask-Session to store sessions server-side (Redis recommended)
    app.config['SESSION_TYPE'] = 'redis'
    if REDIS_AVAILABLE:
        try:
            # create a single redis client and reuse it
            redis_client = _redis.from_url(REDIS_URL)
            app.config['SESSION_REDIS'] = redis_client
            FlaskSession(app)
            logger.info("Using Redis-backed server-side sessions.")

            # Create a small adapter to expose get/set semantics used in code
            class RedisCacheAdapter:
                def __init__(self, client, default_ttl=CACHE_TTL):
                    self._client = client
                    self._ttl = default_ttl

                def get(self, key):
                    try:
                        v = self._client.get(key)
                        return v.decode('utf-8') if isinstance(v, (bytes, bytearray)) else v
                    except Exception:
                        return None

                def set(self, key, value, ttl=None):
                    ttl = ttl or self._ttl
                    try:
                        # prefer setex if available
                        self._client.setex(key, ttl, value)
                    except TypeError:
                        self._client.set(key, value)
                    except Exception:
                        try:
                            self._client.set(key, value)
                        except Exception:
                            pass

                def __setitem__(self, key, value):
                    self.set(key, value)

                def __getitem__(self, key):
                    return self.get(key)

            cache = RedisCacheAdapter(redis_client, default_ttl=CACHE_TTL)
        except Exception as e:
            redis_client = None
            logger.exception("Failed to initialize Redis session store: %s. Falling back to filesystem sessions.", e)
            app.config['SESSION_TYPE'] = 'filesystem'
            FlaskSession(app)
            cache = TTLCache(maxsize=200, ttl=CACHE_TTL)
    else:
        # Redis not installed; fallback to filesystem
        app.config['SESSION_TYPE'] = 'filesystem'
        FlaskSession(app)
        cache = TTLCache(maxsize=200, ttl=CACHE_TTL)
else:
    # If Flask-Session isn't installed or no REDIS_URL, fall back to filesystem server-side sessions if possible,
    # otherwise the app will use signed cookie sessions (but cookie-size limits apply).
    if FLASK_SESSION_AVAILABLE:
        app.config['SESSION_TYPE'] = 'filesystem'
        FlaskSession(app)
        cache = TTLCache(maxsize=200, ttl=CACHE_TTL)
        logger.info("Using filesystem server-side sessions.")
    else:
        logger.warning("Flask-Session not installed. Using default signed cookie sessions (watch for size limits).")
        cache = TTLCache(maxsize=200, ttl=CACHE_TTL)

# ===== Rate limiter (init after REDIS detection so storage URI works) =====
# Default rate limit: 500/min (can be overridden with RATE_LIMIT env var)
# NOTE: limits library expects words like 'minute' or 'second' — normalize common shorthand.
def normalize_rate_limit(s: str) -> str:
    """
    Normalize common shorthand tokens to forms accepted by 'limits' parser.
    Examples:
      "500/min" -> "500/minute"
      "100/hr"  -> "100/hour"
    """
    if not s:
        return s
    s = s.strip()
    # replace common shorthand tokens with full words
    s = re.sub(r'\bmins\b', 'minutes', s, flags=re.I)
    s = re.sub(r'\bmin\b', 'minute', s, flags=re.I)
    s = re.sub(r'\bsecs\b', 'seconds', s, flags=re.I)
    s = re.sub(r'\bsec\b', 'second', s, flags=re.I)
    s = re.sub(r'\bhrs\b', 'hours', s, flags=re.I)
    s = re.sub(r'\bhr\b', 'hour', s, flags=re.I)
    s = re.sub(r'\bper\s+min\b', 'per minute', s, flags=re.I)
    s = re.sub(r'\bper\s+hr\b', 'per hour', s, flags=re.I)
    # accept "500/minute" or "500 per minute" or comma-separated limits
    return s

# get env and normalize; use a safe default if env is invalid
raw_rl = os.getenv("RATE_LIMIT", "500/minute")
RATE_LIMIT_DEFAULT = normalize_rate_limit(raw_rl)
# basic sanity check: must contain a digit and a known unit word or 'per'
if not re.search(r'\d', RATE_LIMIT_DEFAULT) or not re.search(r'(second|minute|hour|day|month|year|per)', RATE_LIMIT_DEFAULT, re.I):
    logger.warning("RATE_LIMIT env invalid (%s) — falling back to '500/minute'", raw_rl)
    RATE_LIMIT_DEFAULT = "500/minute"

# Use RATELIMIT_STORAGE_URI if set, otherwise fall back to REDIS_URL (if provided)
RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "") or (REDIS_URL if REDIS_URL else "")

if RATELIMIT_STORAGE_URI:
    limiter = Limiter(
        key_func=get_remote_address,
        default_limits=[RATE_LIMIT_DEFAULT],
        storage_uri=RATELIMIT_STORAGE_URI
    )
else:
    limiter = Limiter(
        key_func=get_remote_address,
        default_limits=[RATE_LIMIT_DEFAULT]
    )
limiter.init_app(app)

# >>> PROD CHANGE: enable Talisman if available for secure headers (CSP left permissive - tune it)
# Safest behavior:
#  - If TALISMAN_AVAILABLE and ENABLE_TALISMAN env true -> enable Talisman.
#  - If FLASK_DEBUG is true -> disable force_https so local dev won't redirect to HTTPS.
#  - If ENABLE_TALISMAN env explicitly set to false -> skip Talisman entirely.
enable_talisman_env = os.getenv("ENABLE_TALISMAN", "True").lower()
enable_talisman = TALISMAN_AVAILABLE and enable_talisman_env in ("1", "true", "yes")

if enable_talisman:
    # when running in debug mode, do NOT force HTTPS (prevents HSTS / auto-redirect issues during local dev)
    is_debug = os.getenv("FLASK_DEBUG", "False").lower() in ("1", "true", "yes")
    try:
        if is_debug:
            # keep Talisman available but disable HTTPS redirect and CSP when debugging locally
            Talisman(app, force_https=False, content_security_policy=None)
            logger.info("Talisman enabled in debug mode with HTTPS redirect disabled (force_https=False).")
        else:
            # production-like: enable Talisman with a permissive CSP (tweak as necessary)
            csp = {
                'default-src': [
                    '\'self\'',
                    'https:'
                ]
            }
            Talisman(app, content_security_policy=csp)
            logger.info("Talisman enabled for security headers.")
    except Exception:
        # if Talisman init fails for any reason, log and continue without crashing the app
        logger.exception("Talisman initialization failed; continuing without Talisman.")
        logger.info("Talisman not active due to error during initialization.")
else:
    logger.info("Talisman not available or explicitly disabled (ENABLE_TALISMAN=%s).", enable_talisman_env)


# ===== Models =====
class Order(db.Model):
    __tablename__ = "orders"
    id = db.Column(db.Integer, primary_key=True)
    items = db.Column(db.Text, nullable=False)
    order_type = db.Column(db.String(50), nullable=False)
    timestamp = db.Column(db.DateTime, server_default=db.func.now())


class Reservation(db.Model):
    __tablename__ = "reservations"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    contact = db.Column(db.String(200), nullable=False)
    # Keep legacy string fields for compatibility; add typed fields for future migrations
    date = db.Column(db.String(200), nullable=True)
    time = db.Column(db.String(100), nullable=True)
    # Optional typed columns (nullable) — migrate when ready
    date_ts = db.Column(db.Date, nullable=True)
    time_ts = db.Column(db.Time, nullable=True)
    guests = db.Column(db.Integer, nullable=False, default=1)
    timestamp = db.Column(db.DateTime, server_default=db.func.now())


class Feedback(db.Model):
    __tablename__ = "feedback"
    id = db.Column(db.Integer, primary_key=True)
    message = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, server_default=db.func.now())


class ClientToken(db.Model):
    __tablename__ = "client_tokens"
    id = db.Column(db.Integer, primary_key=True)
    token_hash = db.Column(db.String(300), nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    expires_at = db.Column(db.DateTime, nullable=True)  # optional expiry for tokens


# ===== DB helpers =====
def init_db():
    # create tables if migrations not applied; call only in dev/debug
    with app.app_context():
        db.create_all()


def save_order(items, order_type):
    try:
        o = Order(items=items, order_type=order_type)
        db.session.add(o)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception("DB Error (save_order): %s", e)
        raise


def save_reservation_from_string(msg):
    """
    Robust save: accepts fewer than 5 comma-separated parts and fills defaults.
    Format expected: name, contact, date, time, guests
    """
    try:
        parts = [p.strip() for p in msg.split(",")]
        # ensure we have 5 elements
        if len(parts) < 5:
            parts += [""] * (5 - len(parts))
        name, contact, date_s, time_s, guests = parts[:5]
        try:
            guests_int = int(guests) if guests else 1
        except Exception:
            guests_int = 1
        r = Reservation(
            name=(name or "Guest"),
            contact=(contact or ""),
            date=(date_s or ""),
            time=(time_s or ""),
            guests=guests_int
        )
        # attempt to fill typed columns if possible
        try:
            if date_s:
                dt_date = dateparser.parse(date_s, fuzzy=True)
                if dt_date:
                    r.date_ts = dt_date.date()
            if time_s:
                dt_time = dateparser.parse(time_s, fuzzy=True)
                if dt_time:
                    r.time_ts = dt_time.time()
        except Exception:
            pass
        db.session.add(r)
        db.session.commit()
        return True
    except Exception as e:
        db.session.rollback()
        logger.exception("DB Error (save_reservation): %s", e)
        return False


def save_feedback(msg):
    try:
        f = Feedback(message=msg)
        db.session.add(f)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception("DB Error (save_feedback): %s", e)
        raise


def get_last_orders(limit=5):
    try:
        return Order.query.order_by(Order.id.desc()).limit(limit).all()
    except Exception as e:
        db.session.rollback()
        logger.exception("get_last_orders failed: %s", e)
        return []


def get_last_reservations(limit=5):
    """
    Returns a list of dicts with keys:
    id, name, contact, date, time, guests, timestamp

    Defensive: handles DBs without date_ts/time_ts columns and safely reads raw query rows.
    """
    try:
        # inspect columns locally without executing a failing query
        cols = Reservation.__table__.columns.keys()
        # if date_ts/time_ts exist, use ORM for convenience
        if 'date_ts' in cols and 'time_ts' in cols:
            rows = Reservation.query.order_by(Reservation.id.desc()).limit(limit).all()
            out = []
            for r in rows:
                out.append({
                    "id": r.id,
                    "name": r.name,
                    "contact": r.contact,
                    "date": r.date or "",
                    "time": r.time or "",
                    "guests": int(r.guests) if r.guests is not None else 1,
                    "timestamp": r.timestamp
                })
            return out
        else:
            # Query only stable columns (avoid referencing missing ones)
            q = db.session.query(
                Reservation.id,
                Reservation.name,
                Reservation.contact,
                Reservation.date,
                Reservation.time,
                Reservation.guests,
                Reservation.timestamp
            ).order_by(Reservation.id.desc()).limit(limit)
            rows = q.all()
            out = []
            for row in rows:
                # be defensive: row may be a tuple-like or row object with attributes
                try:
                    # prefer attribute access
                    rid = getattr(row, "id", None)
                    name = getattr(row, "name", "") or ""
                    contact = getattr(row, "contact", "") or ""
                    datev = getattr(row, "date", "") or ""
                    timev = getattr(row, "time", "") or ""
                    guests = int(getattr(row, "guests", 1) or 1)
                    ts = getattr(row, "timestamp", None)
                except Exception:
                    # fallback to index-based
                    try:
                        rid = row[0]
                        name = row[1] or ""
                        contact = row[2] or ""
                        datev = row[3] or ""
                        timev = row[4] or ""
                        guests = int(row[5] or 1)
                        ts = row[6] if len(row) > 6 else None
                    except Exception:
                        # ultimate fallback empty defaults
                        rid = None
                        name = ""
                        contact = ""
                        datev = ""
                        timev = ""
                        guests = 1
                        ts = None
                out.append({
                    "id": rid,
                    "name": name,
                    "contact": contact,
                    "date": datev,
                    "time": timev,
                    "guests": guests,
                    "timestamp": ts
                })
            return out
    except Exception as e:
        # If something bad happened (e.g. previous aborted transaction), rollback and try a safe textual select
        logger.warning("Reservation ORM query failed (falling back to raw select): %s", e)
        try:
            db.session.rollback()
        except Exception:
            pass
        try:
            raw_sql = sql_text("SELECT id, name, contact, date, time, guests, timestamp FROM reservations ORDER BY id DESC LIMIT :lim")
            rows = db.session.execute(raw_sql, {"lim": limit}).fetchall()
            out = []
            for row in rows:
                # Row may be a mapping-style row with ._mapping or a simple tuple.
                rid = None
                name = ""
                contact = ""
                datev = ""
                timev = ""
                guests = 1
                ts = None

                # prefer mapping access (modern SQLAlchemy Row has _mapping)
                try:
                    mapping = getattr(row, "_mapping", None)
                    if mapping is not None:
                        rid = mapping.get("id") or mapping.get(0)
                        name = mapping.get("name") or mapping.get(1) or ""
                        contact = mapping.get("contact") or mapping.get(2) or ""
                        datev = mapping.get("date") or mapping.get(3) or ""
                        timev = mapping.get("time") or mapping.get(4) or ""
                        guests_raw = mapping.get("guests") or mapping.get(5) or 1
                        try:
                            guests = int(guests_raw)
                        except Exception:
                            guests = 1
                        ts = mapping.get("timestamp") or mapping.get(6)
                    else:
                        # no mapping — try dictionary-like access (older Row)
                        try:
                            # some Row implementations support .keys() but may raise; guard it
                            keys = None
                            try:
                                keys = row.keys()
                            except Exception:
                                keys = None
                            if keys:
                                # safe attempt
                                rid = row['id'] if 'id' in keys else row[0]
                                name = row['name'] if 'name' in keys else row[1]
                                contact = row['contact'] if 'contact' in keys else row[2]
                                datev = row['date'] if 'date' in keys else row[3]
                                timev = row['time'] if 'time' in keys else row[4]
                                guests_val = row['guests'] if 'guests' in keys else row[5]
                                try:
                                    guests = int(guests_val)
                                except Exception:
                                    guests = 1
                                ts = row['timestamp'] if 'timestamp' in keys else (row[6] if len(row) > 6 else None)
                            else:
                                # index-based fallback
                                rid = row[0]
                                name = row[1] or ""
                                contact = row[2] or ""
                                datev = row[3] or ""
                                timev = row[4] or ""
                                guests = int(row[5] or 1)
                                ts = row[6] if len(row) > 6 else None
                        except Exception:
                            # final fallback
                            try:
                                rid = row[0]
                                name = row[1] or ""
                                contact = row[2] or ""
                                datev = row[3] or ""
                                timev = row[4] or ""
                                guests = int(row[5] or 1)
                                ts = row[6] if len(row) > 6 else None
                            except Exception:
                                rid = None
                                name = ""
                                contact = ""
                                datev = ""
                                timev = ""
                                guests = 1
                                ts = None
                except Exception as e2:
                    logger.exception("Error reading reservation raw row: %s", e2)

                out.append({
                    "id": rid,
                    "name": name or "",
                    "contact": contact or "",
                    "date": datev or "",
                    "time": timev or "",
                    "guests": int(guests or 1),
                    "timestamp": ts
                })
            return out
        except Exception as e2:
            logger.exception("Fallback raw reservation SELECT failed: %s", e2)
            try:
                db.session.rollback()
            except Exception:
                pass
            return []


def get_last_feedback(limit=10):
    try:
        return Feedback.query.order_by(Feedback.id.desc()).limit(limit).all()
    except Exception as e:
        db.session.rollback()
        logger.exception("get_last_feedback failed: %s", e)
        return []


# >>> PROD CHANGE: DB health check helper & wait_for_db
def db_is_healthy(timeout: int = 1) -> bool:
    """
    Try a very small quick DB call to verify connectivity.
    Returns True if DB responded, False otherwise.

    This function always runs the quick check inside an application context
    (so it works when invoked during startup or inside request handlers).
    """
    try:
        # Ensure we are inside an application context before touching db.engine
        with app.app_context():
            with db.engine.connect() as conn:
                # Use a very short timeout SQL if the DB supports it; simple SELECT 1 is fine.
                conn.execute(sql_text("SELECT 1"))
        return True
    except OperationalError as e:
        logger.debug("DB health check failed (OperationalError): %s", e)
        return False
    except Exception as e:
        logger.exception("Unexpected error during DB health check: %s", e)
        return False


def wait_for_db(max_retries: int = 12, delay: int = 5):
    """
    Wait until DB is reachable or raise RuntimeError after retries.
    Useful for container startup to fail fast if DB unreachable.

    This wrapper ensures calls are run inside app.app_context so it is safe
    to call from the `__main__` startup path (outside request handling).
    """
    attempt = 0
    # Use an outer app_context to ensure db.engine is available even when called
    # from the main startup path (before the server has pushed a request context).
    with app.app_context():
        while attempt < max_retries:
            if db_is_healthy():
                logger.info("Database is reachable.")
                return True
            attempt += 1
            logger.warning("Database not reachable yet — attempt %d/%d. Retrying in %ds...", attempt, max_retries, delay)
            time.sleep(delay)
    raise RuntimeError(f"Database not reachable after {max_retries} attempts.")


# initialize DB only in debug/dev (avoid create_all in production)
if os.getenv("FLASK_DEBUG", "False").lower() in ("1", "true", "yes"):
    try:
        init_db()
    except Exception as e:
        logger.warning("init_db() failed (you may still use flask-migrate). Error: %s", e)


# ===== Forms =====
class AdminLoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Login")


# ===== Session helpers =====
def ensure_session():
    try:
        if "orders" not in session: session["orders"] = []
        if "state" not in session: session["state"] = {}
        if "last_food" not in session: session["last_food"] = None
        if "lang" not in session: session["lang"] = "en"
        if "pending_reservation" not in session: session["pending_reservation"] = None
        if "conv" not in session: session["conv"] = {}
    except RuntimeError:
        # no request context — safe no-op
        pass


def clear_orders_session():
    session["orders"] = []
    session["state"] = {}
    session["last_food"] = None
    session["pending_reservation"] = None
    session["conv"] = {}
    session.modified = True


def reset_state_only():
    session["state"] = {}
    session["last_food"] = None
    session["pending_reservation"] = None
    session.modified = True


# ===== Conversation helpers (new) =====
def append_conv_message(role: str, text: str):
    """
    Append a message to session['conv']['history'] (keeps last N messages).
    role: 'user' or 'assistant'
    """
    try:
        conv = session.get("conv", {})
    except RuntimeError:
        conv = {}
    history = conv.get("history", [])
    history.append({"role": role, "content": text})
    history = history[-10:]
    conv["history"] = history
    if role == "assistant":
        conv["last_bot"] = text
    session["conv"] = conv
    session.modified = True


def unknown_intent_handler(user_msg: str, reply_lang: str = "en", intent_info: dict = None):
    """
    Use LLM to answer when router falls back to open-ended replies.
    Asks a clarifying question if the input is ambiguous.
    Returns assistant text.
    """
    sys_instr = (
        "You are a concise, helpful restaurant assistant. "
        "Be honest about what you know. Do NOT invent real-world facts (opening hours, phone numbers) "
        "unless specified in the system data below. If the user's request is ambiguous, ask a single clarifying question. "
        "Keep answer under 120 words."
    )
    conv = session.get("conv", {}) or {}
    history_msgs = conv.get("history", [])

    messages = [{"role": "system", "content": sys_instr}]
    # language instruction
    if reply_lang == "de":
        messages.append({"role": "system", "content": "Reply in German."})
    else:
        messages.append({"role": "system", "content": "Reply in English."})

    # include history
    for m in history_msgs:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": user_msg})

    try:
        if GROQ_API_KEY:
            out = call_groq_chat(messages, temperature=0.25, max_tokens=220)
            append_conv_message("assistant", out)
            return out
        else:
            fallback = "🙂 Sorry, AI is not available right now. Can you rephrase or ask about the menu/reservation?"
            append_conv_message("assistant", fallback)
            return fallback
    except Exception as e:
        logger.exception("unknown_intent_handler LLM error: %s", e)
        fallback = "⚠️ I couldn't process that just now. Can you try rephrasing or ask about the menu/reservations?"
        append_conv_message("assistant", fallback)
        return fallback

# ===== Language helpers =====
HARD_TRANSLATIONS_DE = {
    "🗑️ Your orders have been cleared.": "🗑️ Ihre Bestellungen wurden gelöscht.",
    "🗑️ All orders cleared.": "🗑️ Alle Bestellungen wurden gelöscht.",
    "🗑️ Reservation process has been reset.": "🗑️ Reservierungsprozess wurde zurückgesetzt.",
    "🗑️ Reservation state reset.": "Reservierungsstatus zurückgesetzt.",
    "✅ Thank you for your feedback!": "✅ Danke für Ihr Feedback!",
    "✅ Reservation confirmed. Details:": "✅ Reservierung bestätigt. Details:",
    "You have no past orders.": "Sie haben keine früheren Bestellungen.",
    "You have no past reservations.": "Sie haben keine früheren Reservierungen.",
    "Please specify delivery or table reservation.": "Bitte geben Sie Lieferung oder Tischreservierung an.",
    "Please choose between fries, drink, coffee, or combo for your meal.": "Bitte wählen Sie Pommes, Getränk, Kaffee oder Menü für Ihre Mahlzeit.",
    "I didn't quite catch that. Could you rephrase?": "Ich habe das nicht ganz verstanden. Könnten Sie es umformulieren?",
    "⚠️ Reservation format not recognized. Please provide: name, contact, date, time, guests (comma separated).":
        "⚠️ Reservierungsformat nicht erkannt. Bitte geben Sie ein: Name, Kontakt, Datum, Uhrzeit, Gäste (kommagetrennt).",
    "✅ Thanks for your feedback!": "✅ Danke für Ihr Feedback!",
}


def detect_language(text: str) -> str:
    """
    Order of detection:
     1) session['lang'] if set (keeps language stable)
     2) Accept-Language header
     3) langdetect on text (if available)
     4) fallback 'en'
    """
    try:
        if session.get("lang"):
            return session.get("lang")
    except RuntimeError:
        pass

    al = request.headers.get("Accept-Language", "")
    if al:
        if al.lower().startswith("de"):
            return "de"

    if text and LANGDETECT_AVAILABLE:
        try:
            lang = detect(text)
            return "de" if lang and lang.startswith("de") else "en"
        except Exception:
            pass

    return "en"


def translate_text(text: str, target_lang: str) -> str:
    if not text or target_lang == "en":
        return text

    if target_lang == "de":
        if text in HARD_TRANSLATIONS_DE:
            return HARD_TRANSLATIONS_DE[text]
        for k, v in HARD_TRANSLATIONS_DE.items():
            if text.startswith(k):
                return text.replace(k, v)

    if DEEP_TRANSLATOR_AVAILABLE:
        try:
            return GoogleTranslator(source='auto', target=target_lang).translate(text)
        except Exception:
            logger.exception("Translation failed; falling back to original text.")
            return text

    return text


def localize_response(text: str, target_lang: str) -> str:
    if not text:
        return text
    if target_lang == "en":
        return text
    return translate_text(text, target_lang)


# ===== Reservation parsing helpers =====
def normalize_phone(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"[^\d+]", "", s)


def parse_reservation(msg: str):
    """
    Returns dict or None:
    { name, contact, date (YYYY-MM-DD), time (HH:MM), guests (int) }
    Attempts multiple heuristics; returns None if no reasonable parse.
    """
    txt = (msg or "").strip()
    if not txt:
        return None

    parts = [p.strip() for p in txt.split(",") if p.strip()]
    if len(parts) >= 4:
        guests = None
        try:
            guests = int(re.findall(r"\d+", parts[-1])[0])
        except Exception:
            guests = None

        name = parts[0]
        contact = ""
        for p in parts[1:3]:
            if re.search(r"\d", p):
                contact = normalize_phone(p)
                break

        rest = " ".join(parts[1:])
        date_str = ""
        time_str = ""
        try:
            dt = dateparser.parse(rest, fuzzy=True)
            if dt:
                date_str = dt.date().isoformat()
                if dt.hour == 0 and dt.minute == 0:
                    if re.search(r'\b(am|pm|:)\b', rest, re.I):
                        time_str = dt.time().strftime("%H:%M")
                else:
                    time_str = dt.time().strftime("%H:%M")
        except Exception:
            pass

        return {
            "name": name or "Guest",
            "contact": contact or "",
            "date": date_str or "",
            "time": time_str or "",
            "guests": guests or 1
        }

    guests = None
    m = re.search(r'\bfor\s+(\d+)\b', txt, re.I)
    if m:
        try:
            guests = int(m.group(1))
        except Exception:
            guests = None

    phone = None
    phone_match = re.search(r'(\+?\d[\d\-\s]{6,}\d)', txt)
    if phone_match:
        phone = normalize_phone(phone_match.group(1))

    date_str = ""
    time_str = ""
    try:
        dt = dateparser.parse(txt, fuzzy=True)
        if dt:
            date_str = dt.date().isoformat()
            if dt.hour == 0 and dt.minute == 0:
                if re.search(r'\b(am|pm|:)\b', txt, re.I):
                    time_str = dt.time().strftime("%H:%M")
            else:
                time_str = dt.time().strftime("%H:%M")
    except Exception:
        pass

    name_guess = ""
    name_match = re.match(r'^\s*([A-Za-z][A-Za-z\s\-\.]{1,40})', txt)
    if name_match:
        name_guess = name_match.group(1).strip()

    if any([name_guess, phone, date_str, guests]):
        return {
            "name": name_guess or "Guest",
            "contact": phone or "",
            "date": date_str or "",
            "time": time_str or "",
            "guests": guests or 1
        }

    return None


# ===== Intent logic (upsell/new-item fix) =====
def handle_restaurant_intents(msg):
    ensure_session()
    msg_lower = (msg or "").lower().strip()
    state = session.get("state", {})
    orders = session.get("orders", [])
    menu_items = ["pizza", "burger", "pasta", "salad", "coffee", "dessert"]
    mentioned_items = [item for item in menu_items if item in msg_lower]

    if msg_lower in ["clear orders", "reset orders"]:
        clear_orders_session()
        return "🗑️ Your orders have been cleared."
    if msg_lower in ["clear reservations", "reset reservations"]:
        reset_state_only()
        return "🗑️ Reservation process has been reset."
    if msg_lower.startswith("feedback:"):
        save_feedback(msg[len("feedback:"):].strip())
        return "✅ Thank you for your feedback!"
    if msg_lower in ["my orders", "orders"]:
        rows = get_last_orders(limit=5)
        if not rows:
            session["state"] = {}
            session["last_food"] = None
            return "You have no past orders."
        reply = "🛒 Your last 5 orders:\n" + \
                "\n".join(f"- {r.items} ({r.order_type}) at {r.timestamp}" for r in rows)
        session["state"] = {}
        session["last_food"] = None
        return reply
    if msg_lower in ["my reservations", "reservations"]:
        rows = get_last_reservations(limit=5)
        if not rows:
            session["state"] = {}
            session["last_food"] = None
            return "You have no past reservations."
        reply = "🍽️ Your last 5 reservations:\n" + \
                "\n".join(f"- {r['name']}, {r['guests']} guests on {r['date']} at {r['time']}" for r in rows)
        session["state"] = {}
        session["last_food"] = None
        return reply
    if "menu" in msg_lower:
        menu = None
        try:
            menu = cache.get("menu")
        except Exception:
            menu = None

        if not menu:
            menu = "📜 Our menu: Pizza 🍕, Pasta 🍝, Burger 🍔, Salad 🥗, Coffee ☕, Dessert 🍰."
            try:
                cache.set("menu", menu)
            except Exception:
                pass
        return menu

    skip_words = ["no", "no thanks", "skip", "cancel", "nothing", "not now"]
    if state.get("expecting") == "upsell":
        if mentioned_items:
            added_items = []
            for item in mentioned_items:
                if item not in orders:
                    orders.append(item)
                    added_items.append(item)
            session["orders"] = orders
            session.modified = True
            if added_items:
                last_added = added_items[-1]
                session["last_food"] = last_added
                session["state"] = {"expecting": "upsell"}
                return f"Great choice! Would you like fries or a drink with your {last_added}?"

        if msg_lower in ["orders", "my orders"]:
            rows = get_last_orders(limit=5)
            if not rows:
                session["state"] = {}
                session["last_food"] = None
                return "You have no past orders."
            reply = "🛒 Your last 5 orders:\n" + \
                    "\n".join(f"- {r.items} ({r.order_type}) at {r.timestamp}" for r in rows)
            session["state"] = {}
            session["last_food"] = None
            return reply

        if msg_lower in ["reservations", "my reservations"]:
            rows = get_last_reservations(limit=5)
            if not rows:
                session["state"] = {}
                session["last_food"] = None
                return "You have no past reservations."
            reply = "🍽️ Your last 5 reservations:\n" + \
                    "\n".join(f"- {r['name']}, {r['guests']} guests on {r['date']} at {r['time']}" for r in rows)
            session["state"] = {}
            session["last_food"] = None
            return reply

        if any(word == msg_lower for word in skip_words) or \
           any(msg_lower.startswith(w + " ") for w in skip_words):
            session["state"] = {"expecting": "delivery_or_table"}
            session["last_food"] = None
            session.modified = True
            return "Okay, no extras. Delivery or table reservation?"

        if msg_lower in ["what can i do", "help"]:
            session["state"] = {}
            session.modified = True
            return "You can order food, ask for recommendations, view past orders or reservations, or book a table."

        last_food = session.get("last_food")
        if any(word in msg_lower for word in ["fries", "drink", "coffee", "combo"]):
            upsell_added = ""
            if "fries" in msg_lower:
                upsell_added = "with fries"
            elif "drink" in msg_lower:
                upsell_added = "with a drink"
            elif "coffee" in msg_lower:
                upsell_added = "with coffee"
            elif "combo" in msg_lower:
                upsell_added = "combo"

            updated = False
            for i, o in enumerate(orders):
                if last_food and (o == last_food or o.startswith(last_food)):
                    orders[i] = f"{last_food} {upsell_added}".strip()
                    updated = True
                    break

            if not updated and last_food:
                orders.append(f"{last_food} {upsell_added}".strip())

            session["orders"] = orders
            session["state"] = {"expecting": "delivery_or_table"}
            session["last_food"] = None
            session.modified = True
            return f"Noted! Your {last_food if last_food else 'item'} {upsell_added} is added. Delivery or table?"

        return "Please choose between fries, drink, coffee, or combo for your meal."

    if mentioned_items or any(k in msg_lower for k in ["order", "add", "i want", "i'd like"]):
        added_items = []
        for item in mentioned_items:
            if item not in orders:
                orders.append(item)
                added_items.append(item)

        session["orders"] = orders
        session.modified = True
        if added_items:
            last_added = added_items[-1]
            session["last_food"] = last_added
            session["state"] = {"expecting": "upsell"}
            session.modified = True
            return f"Great choice! Would you like fries or a drink with your {last_added}?"
        else:
            if mentioned_items:
                return "You already added these items. Anything else?"
            return f"⚠️ Sorry, we don’t have that item. Our menu: {', '.join(item.title() for item in menu_items)}."

    if state.get("expecting") == "delivery_or_table":
        session["state"] = {}
        session.modified = True

        if "delivery" in msg_lower:
            delivered_items = ', '.join(orders) if orders else "No items"
            save_order(delivered_items, "Delivery")
            session["orders"] = []
            session["last_food"] = None
            session.modified = True
            return f"✅ Your order ({delivered_items}) will be delivered soon!"

        elif "table" in msg_lower or "reservation" in msg_lower:
            session["state"] = {"expecting": "reservation"}
            session["pending_reservation"] = None
            session.modified = True
            return (
                "Sure! Please provide your name, contact, date, time, and number of guests "
                "(comma separated) or say it naturally (e.g. 'Book table for Rahul tomorrow at 8pm "
                "for 3, phone +91...')."
            )

        else:
            return "Please specify delivery or table reservation."

    if state.get("expecting") in ("reservation", "reservation_confirm"):
        if msg_lower in ("confirm", "yes", "y", "ok", "confirm booking"):
            pending = session.get("pending_reservation")
            if not pending:
                return "I have no pending reservation to confirm. Please provide reservation details first."

            details = f"{pending.get('name','Guest')},{pending.get('contact','')},{pending.get('date','')},{pending.get('time','')},{pending.get('guests',1)}"
            success = save_reservation_from_string(details)

            session["state"] = {}
            session["orders"] = []
            session["last_food"] = None
            session["pending_reservation"] = None
            session.modified = True

            if success:
                return f"✅ Reservation confirmed. Details: {details}"
            else:
                return "⚠️ Failed to save reservation — try again or provide: name, contact, date, time, guests (comma separated)."

        if msg_lower in ("cancel", "no", "abort", "stop"):
            session["pending_reservation"] = None
            session["state"] = {}
            session.modified = True
            return "Reservation cancelled."

        if msg_lower in ("edit", "change"):
            session["pending_reservation"] = None
            session["state"] = {"expecting": "reservation"}
            session.modified = True
            return "Okay — please send corrected reservation details."

        parsed = parse_reservation(msg)
        if parsed:
            session["pending_reservation"] = parsed
            session["state"] = {"expecting": "reservation_confirm"}
            session.modified = True

            formatted = (
                f"{parsed['name']}, {parsed['contact'] or 'no contact'}, "
                f"{parsed['date'] or 'date unknown'} at {parsed['time'] or 'time unknown'}, "
                f"{parsed['guests']} guests"
            )

            return f"I parsed: {formatted}. Reply 'confirm' to book, 'edit' to change, or 'cancel' to abort."

        if GROQ_API_KEY:
            sys_msg = (
                "You are a strict parser. Respond only with valid JSON containing keys: "
                "name, contact, date, time, guests. If a piece of information is unknown, "
                "return an empty string (or 1 for guests)."
            )
            user_prompt = f"Extract reservation fields from this text: '''{msg}'''"

            try:
                slot_out = call_groq_chat(
                    [{"role": "system", "content": sys_msg},
                     {"role": "user", "content": user_prompt}],
                    temperature=0.0,
                    max_tokens=160
                )

                try:
                    parsed_json = json.loads(slot_out)
                    parsed_standard = {
                        "name": parsed_json.get("name", "").strip() or "Guest",
                        "contact": normalize_phone(parsed_json.get("contact", "")),
                        "date": parsed_json.get("date", "").strip(),
                        "time": parsed_json.get("time", "").strip(),
                        "guests": int(parsed_json.get("guests", 1)) if str(parsed_json.get("guests", "")).isdigit() else 1
                    }

                    session["pending_reservation"] = parsed_standard
                    session["state"] = {"expecting": "reservation_confirm"}
                    session.modified = True

                    formatted = (
                        f"{parsed_standard['name']}, {parsed_standard['contact'] or 'no contact'}, "
                        f"{parsed_standard['date'] or 'date unknown'} at {parsed_standard['time'] or 'time unknown'}, "
                        f"{parsed_standard['guests']} guests"
                    )

                    return f"I extracted: {formatted}. Reply 'confirm' to book, 'edit' to change, or 'cancel' to abort."

                except Exception:
                    logger.info("LLM extraction returned non-JSON: %s", slot_out[:200])
                    return "⚠️ I couldn't extract reservation details reliably. Please send: name, contact, date, time, guests (comma separated)."

            except Exception as e:
                logger.exception("Reservation LLM extraction failed: %s", e)
                return "⚠️ I couldn't parse that — please provide: name, contact, date, time, guests (comma separated)."

        return "⚠️ Please provide reservation details in format: name, contact, date, time, guests (comma separated)."

    if "how are you" in msg_lower:
        return "I'm doing great, thanks! Hungry today?"

    if "your name" in msg_lower or "who are you" in msg_lower:
        return "I'm the Spice Villa assistant 🤖 — here to help with orders and reservations!"

    if any(g in msg_lower for g in ["hi", "hello", "hey", "welcome"]):
        return "👋 Hello! Welcome to Spice Villa. You can order food 🍕 or book a table 🪑."

    return None


# ===== Groq (Llama-3.1) helper =====
def call_groq_chat(messages, model: str = None, temperature: float = 0.2,
                   max_tokens: int = 256, timeout: int = 30):
    """
    Returns assistant text only.
    """
    model = model or GROQ_MODEL
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not set")

    url = f"{GROQ_BASE}/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    try:
        logger.info("Calling Groq model %s (messages=%d)", model, len(messages))
        r = requests.post(url, headers=headers, json=payload, timeout=timeout)
        r.raise_for_status()
        j = r.json()

        try:
            if "choices" in j and j["choices"]:
                choice = j["choices"][0]
                if isinstance(choice.get("message"), dict):
                    return choice["message"].get("content", "").strip()
                if "text" in choice:
                    return choice["text"].strip()

            if isinstance(j.get("output"), str):
                return j["output"].strip()

            return str(j)
        except Exception:
            return str(j)

    except requests.exceptions.RequestException as e:
        logger.exception("Groq request failed: %s", e)
        raise
# ---------- Conversation router (stateful flow manager) ----------
def conversational_router(msg: str):
    """
    Use session['conv'] as a small state machine:
      - conv: { last_intent, expecting, attempts, last_bot }
    Routes to rule-based handlers first, uses intent classifier to
    disambiguate, asks clarifying questions, confirms actions
    (reservations/orders), and only calls Groq for open-ended replies.
    """
    ensure_session()
    conv = session.get("conv", {}) or {}

    conv.setdefault("last_intent", None)
    conv.setdefault("expecting", None)
    conv.setdefault("attempts", 0)
    conv.setdefault("last_bot", "")

    text = (msg or "").strip()
    low = text.lower()

    if text:
        append_conv_message("user", text)

    # 1) rule-based path
    rule_resp = handle_restaurant_intents(msg)
    if rule_resp:
        append_conv_message("assistant", rule_resp)
        conv["last_bot"] = rule_resp
        conv["attempts"] = 0
        conv["expecting"] = session.get("state", {}).get("expecting")
        session["conv"] = conv
        return rule_resp

    # 2) classify intent
    try:
        intent_info = classify_intent(text, use_model_fallback=True)
    except Exception as e:
        logger.exception("Intent classification failed: %s", e)
        intent_info = {"intent": "unknown", "score": 0.0, "explain": "error"}

    intent = intent_info.get("intent", "unknown")
    conv["last_intent"] = intent

    # 3) intent-based small dialogs
    if intent == "order":
        conv["expecting"] = "order_item"
        conv["last_bot"] = (
            "What would you like to order? We have pizza, burger, pasta, salad, coffee, dessert."
        )
        conv["attempts"] = 0
        session["conv"] = conv
        append_conv_message("assistant", conv["last_bot"])
        return conv["last_bot"]

    if intent == "reservation":
        parsed = parse_reservation(msg)
        if parsed:
            session["pending_reservation"] = parsed
            session["state"] = {"expecting": "reservation_confirm"}
            conv["expecting"] = "reservation_confirm"

            formatted = (
                f"I understood: {parsed['name']}, {parsed['contact'] or 'no contact'}, "
                f"{parsed['date'] or 'date unknown'} at {parsed['time'] or 'time unknown'}, "
                f"{parsed['guests']} guests. Reply 'confirm' to book or 'edit' to change."
            )

            conv["last_bot"] = formatted
            session["conv"] = conv
            append_conv_message("assistant", formatted)
            return formatted

        conv["expecting"] = "reservation"
        conv["last_bot"] = (
            "Sure — please provide name, contact, date, time and number of guests "
            "(comma separated), or say it naturally."
        )
        conv["attempts"] = 0
        session["conv"] = conv
        append_conv_message("assistant", conv["last_bot"])
        return conv["last_bot"]

    if intent == "greet":
        conv["last_bot"] = "👋 Hello! How can I help? Order, reservation, or something else?"
        conv["attempts"] = 0
        session["conv"] = conv
        append_conv_message("assistant", conv["last_bot"])
        return conv["last_bot"]

    if intent == "info":
        if re.search(r"\b(hours|open|close|when)\b", low):
            conv["last_bot"] = "We're open 11:00–22:00 Mon–Thu, 11:00–23:00 Fri–Sat. Closed Sun."
            session["conv"] = conv
            append_conv_message("assistant", conv["last_bot"])
            return conv["last_bot"]

        if "menu" in low:
            conv["last_bot"] = cache.get("menu") or \
                "📜 Our menu: Pizza, Pasta, Burger, Salad, Coffee, Dessert."
            session["conv"] = conv
            append_conv_message("assistant", conv["last_bot"])
            return conv["last_bot"]

    # 4) follow-ups from conversation state
    expecting = conv.get("expecting") or session.get("state", {}).get("expecting")

    if expecting == "order_item":
        menu_items = ["pizza", "burger", "pasta", "salad", "coffee", "dessert"]
        mentioned = [m for m in menu_items if m in low]

        if mentioned:
            session["conv"] = {}
            return handle_restaurant_intents("I want " + mentioned[0])

        conv["attempts"] += 1
        session["conv"] = conv

        if conv["attempts"] >= 2:
            conv["expecting"] = None
            session["conv"] = conv
            conv["last_bot"] = (
                "Sorry — I still don't see that item. Our menu: "
                "Pizza, Burger, Pasta, Salad, Coffee, Dessert."
            )
            append_conv_message("assistant", conv["last_bot"])
            return conv["last_bot"]

        conv["last_bot"] = (
            "Which item would you like? Pizza, burger, pasta, salad, coffee or dessert?"
        )
        append_conv_message("assistant", conv["last_bot"])
        session["conv"] = conv
        return conv["last_bot"]

    if expecting == "reservation_confirm":
        if low in ("confirm", "yes", "y", "ok"):
            pending = session.get("pending_reservation")
            if pending:
                details = (
                    f"{pending.get('name','Guest')},"
                    f"{pending.get('contact','')},"
                    f"{pending.get('date','')},"
                    f"{pending.get('time','')},"
                    f"{pending.get('guests',1)}"
                )

                success = save_reservation_from_string(details)

                session["state"] = {}
                session["orders"] = []
                session["last_food"] = None
                session["pending_reservation"] = None
                session["conv"] = {}
                session.modified = True

                if success:
                    resp = f"✅ Reservation confirmed. Details: {details}"
                    append_conv_message("assistant", resp)
                    return resp
                else:
                    resp = (
                        "⚠️ Failed to save reservation — try again or provide: "
                        "name, contact, date, time, guests (comma separated)."
                    )
                    append_conv_message("assistant", resp)
                    return resp

            resp = "I don't have reservation details to confirm. Please provide them first."
            append_conv_message("assistant", resp)
            return resp

        if low in ("cancel", "no", "abort"):
            session["pending_reservation"] = None
            session["state"] = {}
            session["conv"] = {}
            session.modified = True

            resp = "Reservation cancelled."
            append_conv_message("assistant", resp)
            return resp

        if low in ("edit", "change"):
            session["pending_reservation"] = None
            session["state"] = {"expecting": "reservation"}
            session["conv"] = {"expecting": "reservation"}
            session.modified = True

            resp = "Okay — please send corrected reservation details."
            append_conv_message("assistant", resp)
            return resp

    # 5) LLM fallback
    if GROQ_API_KEY:
        try:
            reply_lang = session.get("lang", "en")
            out = unknown_intent_handler(text, reply_lang=reply_lang, intent_info=intent_info)

            conv["last_bot"] = out
            conv["expecting"] = None
            conv["attempts"] = 0
            session["conv"] = conv
            return out

        except Exception as e:
            logger.exception("LLM fallback failed: %s", e)
            conv["last_bot"] = "🙂 Sorry, I couldn't form a good reply just now. Could you rephrase?"
            append_conv_message("assistant", conv["last_bot"])
            session["conv"] = conv
            return conv["last_bot"]

    # 6) last fallback
    conv["last_bot"] = (
        "🙂 Sorry, I didn't understand that. You can order, book a table, or ask for the menu."
    )
    conv["attempts"] += 1
    append_conv_message("assistant", conv["last_bot"])
    session["conv"] = conv
    return conv["last_bot"]


# ===== Hybrid intent classifier (rules + LLM fallback) =====
INTENT_PATTERNS = {
    "order": re.compile(
        r"\b(order|i want|i'd like|add|buy|menu|pizza|burger|pasta|salad|coffee|dessert)\b",
        re.I
    ),
    "reservation": re.compile(
        r"\b(reserv(e|ation)|book|table|reserve|guests|party)\b",
        re.I
    ),
    "feedback": re.compile(
        r"\b(feedback|complain|complaint|rate|review)\b",
        re.I
    ),
    "clear": re.compile(
        r"\b(clear|reset|cancel)\b",
        re.I
    ),
    "greet": re.compile(
        r"\b(hi|hello|hey|welcome)\b",
        re.I
    ),
    "info": re.compile(
        r"\b(hours|open|close|location|where|address|menu)\b",
        re.I
    ),
}


def classify_intent(text: str, use_model_fallback: bool = True) -> dict:
    """
    Returns a dict:
      { 'intent': <str>, 'score': float, 'explain': <str> }
    Rule-based first, then LLM fallback.
    """
    text = (text or "").strip()
    if not text:
        return {"intent": "unknown", "score": 1.0, "explain": "empty text"}

    scores = {}
    for name, pat in INTENT_PATTERNS.items():
        scores[name] = 1.0 if pat.search(text) else 0.0

    top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[0]
    if top[1] > 0:
        return {"intent": top[0], "score": top[1], "explain": "rule-match"}

    if use_model_fallback and GROQ_API_KEY:
        system = (
            "You are an intent classifier. Choose one intent from: "
            "order, reservation, feedback, clear, greet, info, unknown. "
            "Return ONLY JSON {\"intent\":\"...\",\"reason\":\"...\"}."
        )
        user_prompt = f"Classify this: '''{text}'''"

        try:
            out = call_groq_chat(
                [{"role": "system", "content": system},
                 {"role": "user", "content": user_prompt}],
                temperature=0.0,
                max_tokens=80
            )
            try:
                parsed = json.loads(out)
                intent = parsed.get("intent") or "unknown"
                reason = parsed.get("reason", "")
                return {"intent": intent, "score": 0.8, "explain": f"llm:{reason}"}
            except Exception:
                intent_guess = (
                    "order" if "order" in out.lower()
                    else "reservation" if "reserv" in out.lower()
                    else "unknown"
                )
                return {"intent": intent_guess, "score": 0.6, "explain": f"llm_raw:{out[:120]}"}

        except Exception as e:
            logger.exception("Intent LLM fallback failed: %s", e)
            return {"intent": "unknown", "score": 0.0, "explain": "model-failed"}

    return {"intent": "unknown", "score": 0.0, "explain": "no-rule-no-model"}


# ===== Recommendation generator =====
def get_user_order_history(user_session_id=None, limit=10):
    try:
        return [o.items for o in Order.query.order_by(Order.id.desc()).limit(limit).all()]
    except Exception:
        return []


def generate_recommendation(user_session_id=None, recent_message: str = None):
    menu_text = "Pizza, Burger, Pasta, Salad, Coffee, Dessert"

    history = []
    try:
        history = session.get("orders", []) or []
    except RuntimeError:
        history = []

    try:
        if user_session_id:
            history = history + get_user_order_history(user_session_id, limit=5)
    except Exception:
        pass

    sys = "You are a friendly assistant making a short personalized recommendation."
    user_parts = [
        f"Menu: {menu_text}.",
        f"User recent message: {recent_message or ''}.",
        f"User past orders: {', '.join(history) if history else 'none'}."
    ]
    user_prompt = (
        "Suggest one short recommendation (1–2 sentences). "
        "Use past orders if helpful."
    )

    msgs = [
        {"role": "system", "content": sys},
        {"role": "user", "content": " ".join(user_parts) + " " + user_prompt}
    ]

    try:
        out = call_groq_chat(msgs, temperature=0.6, max_tokens=120)
        return out.strip()[:400]
    except Exception:
        if history:
            last = history[-1]
            return f"Would you like the same {last} again? We can add fries or a drink."
        return "Can I recommend our pizza today? It's a crowd favorite — would you like fries with that?"
# ===== Client token protection for API =====
def require_client_token(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return jsonify({"error": "missing authorization"}), 401

        raw_token = auth.split(" ", 1)[1].strip()

        CLIENT_TOKEN_ENV = os.getenv("CLIENT_TOKEN")
        if CLIENT_TOKEN_ENV and raw_token == CLIENT_TOKEN_ENV:
            g.client_token = raw_token
            return f(*args, **kwargs)

        try:
            tokens = ClientToken.query.all()
            for entry in tokens:
                try:
                    if check_password_hash(entry.token_hash, raw_token):
                        g.client_token = raw_token
                        return f(*args, **kwargs)
                except Exception:
                    continue
        except Exception:
            logger.exception("Client token lookup failed.")

        logger.warning("Unauthorized client token attempt")
        return jsonify({"error": "unauthorized"}), 401
    return decorated


# ===== Routes =====
@app.route("/health")
def health():
    return {"status": "ok", "version": app.config.get("VERSION")}, 200


@app.route("/ready")
def readiness():
    ok = True
    reasons = {}

    try:
        if not db_is_healthy():
            ok = False
            reasons["database"] = "unreachable"
        else:
            reasons["database"] = "ok"
    except Exception as e:
        ok = False
        reasons["database"] = f"error: {str(e)}"

    if REDIS_URL:
        try:
            if REDIS_AVAILABLE and 'redis_client' in globals():
                redis_client.ping()
                reasons["redis"] = "ok"
            else:
                reasons["redis"] = "redis-pkg-missing"
        except Exception as e:
            ok = False
            reasons["redis"] = f"error: {str(e)}"

    return jsonify({"ready": ok, "details": reasons}), 200 if ok else 503


@app.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    form = AdminLoginForm()
    if form.validate_on_submit():
        username = form.username.data
        password = form.password.data

        if username == ADMIN_USER and check_password_hash(ADMIN_PASS_HASH, password):
            session["admin_logged_in"] = True
            session.permanent = True
            return redirect(url_for("admin_dashboard"))

        return render_template("admin_login.html", form=form, error="Invalid credentials")

    return render_template("admin_login.html", form=form)


@app.route("/admin")
def admin_dashboard():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))

    orders = [(o.items, o.order_type, o.timestamp) for o in get_last_orders(limit=10)]
    res_rows = get_last_reservations(limit=10)
    reservations = [
        (
            r.get("name", ""),
            r.get("contact", ""),
            r.get("date", ""),
            r.get("time", ""),
            r.get("guests", 1)
        )
        for r in res_rows
    ]
    feedbacks = [(f.message, f.timestamp) for f in get_last_feedback(limit=10)]

    return render_template(
        "admin.html",
        orders=orders,
        reservations=reservations,
        feedback=feedbacks
    )


@app.route("/dashboard")
def dashboard_stats():
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 401

    total_orders = Order.query.count()
    total_reservations = Reservation.query.count()
    total_feedback = Feedback.query.count()

    data_storage = (
        "Postgres" if "postgres" in DATABASE_URL.lower()
        else "SQLite" if "sqlite" in DATABASE_URL.lower()
        else "Unknown"
    )

    return jsonify({
        "total_orders": total_orders,
        "total_reservations": total_reservations,
        "total_feedback": total_feedback,
        "model_ready": bool(GROQ_API_KEY),
        "data_storage": data_storage,
        "last_update": datetime.utcnow().isoformat() + "Z"
    })


@app.route("/logout")
def logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("admin_login"))


@app.route("/")
def home():
    ensure_session()

    lang = detect_language("")
    session["lang"] = lang

    welcome_en = (
        "👋 Hello! Welcome to Spice Villa.\n"
        "You can order food, ask for recommendations, or reserve a table.\n"
        "Try: “I’d like to order pizza” or “Book a table for 2 at 8 PM”."
    )
    welcome_de = (
        "👋 Hallo! Willkommen bei Spice Villa.\n"
        "Sie können Essen bestellen, Empfehlungen erhalten oder einen Tisch reservieren.\n"
        "Versuchen Sie: „Ich möchte eine Pizza bestellen“ oder "
        "„Einen Tisch für 2 um 20 Uhr reservieren\"."
    )

    welcome = welcome_de if lang == "de" else welcome_en

    is_admin = session.get("admin_logged_in", False)
    csrf_token = generate_csrf()

    return render_template("index.html", is_admin=is_admin,
                           welcome_message=welcome, csrf_token=csrf_token)


@app.route("/set-lang", methods=["POST"])
def set_lang():
    data = request.get_json(silent=True) or {}
    lang = data.get("lang", "en")

    if lang not in ("en", "de"):
        lang = "en"

    session["lang"] = lang
    session.modified = True
    return jsonify({"ok": True, "lang": lang})


@app.route("/get-lang")
def get_lang():
    try:
        lang = session.get("lang", "en")
    except RuntimeError:
        lang = "en"
    return jsonify({"lang": lang})


@app.route("/get", methods=["POST"])
def get_bot_response():
    ensure_session()

    msg = (request.form.get("msg", "") or "").strip()
    ui_lang = (request.form.get("lang") or "").strip().lower()

    if ui_lang in ("en", "de"):
        lang = ui_lang
    else:
        lang = detect_language(msg) if msg else session.get("lang", "en")

    session["lang"] = lang

    try:
        response = conversational_router(msg)
    except Exception as e:
        logger.exception("Conversational router error: %s", e)
        response = None

    if response:
        localized = localize_response(response, session.get("lang", "en"))
        return jsonify({"response": localized})

    try:
        intent_info = classify_intent(msg)
    except Exception as e:
        logger.exception("Intent classification failed: %s", e)
        intent_info = {"intent": "unknown", "score": 0.0, "explain": "error"}

    reply_lang = session.get("lang", "en")

    sys_instr = (
        "You are a helpful restaurant assistant. Be concise and do not invent availability."
    )
    sys_instr += " Please reply in German." if reply_lang == "de" else " Please reply in English."

    prompt_user = f"User: {msg}\nAssistant:"
    msgs = [
        {"role": "system", "content": sys_instr},
        {"role": "user", "content": prompt_user}
    ]

    try:
        if GROQ_API_KEY:
            generated = call_groq_chat(msgs)
        else:
            logger.warning("No Groq API key configured.")
            generated = "🙂 Sorry, AI is not available right now."
    except Exception as e:
        logger.exception("AI generation failure: %s", e)
        generated = "⚠️ Sorry, I couldn't understand. Please try again."

    try:
        low = msg.lower()
        if intent_info.get("intent") in ("order", "info") and \
           ("recommend" in low or "suggest" in low or "what should i" in low):
            rec = generate_recommendation(None, msg)
            generated = f"{generated}\n\nRecommendation: {rec}"
    except Exception:
        pass

    if reply_lang != "en":
        generated = translate_text(generated, reply_lang)

    return jsonify({"response": generated})


# API route
@csrf.exempt
@app.route("/api/chat", methods=["POST"])
@require_client_token
@limiter.limit("30/minute")
def api_chat():
    ensure_session()

    data = request.get_json(force=True, silent=True) or {}
    user_message = (data.get("message") or "").strip()

    if not user_message:
        return jsonify({"error": "empty message"}), 400

    client_lang = (data.get("lang") or "").strip().lower()

    if client_lang in ("en", "de"):
        lang = client_lang
    else:
        lang = detect_language(user_message)

    session["lang"] = lang

    try:
        response = conversational_router(user_message)
    except Exception as e:
        logger.exception("Conversational router error: %s", e)
        response = None

    if response:
        localized = localize_response(response, session.get("lang", "en"))
        return jsonify({"reply": localized})

    try:
        intent_info = classify_intent(user_message)
    except Exception as e:
        logger.exception("Intent classification failed: %s", e)
        intent_info = {"intent": "unknown", "score": 0.0, "explain": "error"}

    reply_lang = session.get("lang", "en")

    sys_instr = (
        "You are a helpful restaurant assistant. Be concise and do not invent availability."
    )
    sys_instr += " Please reply in German." if reply_lang == "de" else " Please reply in English."

    prompt_user = f"User: {user_message}\nAssistant:"
    msgs = [
        {"role": "system", "content": sys_instr},
        {"role": "user", "content": prompt_user}
    ]

    try:
        if GROQ_API_KEY:
            bot_reply = call_groq_chat(msgs)
        else:
            bot_reply = "🙂 AI unavailable at the moment."
    except Exception as e:
        logger.exception("AI generation failed: %s", e)
        bot_reply = "⚠️ Sorry, I couldn't generate a response."

    try:
        low = user_message.lower()
        if intent_info.get("intent") in ("order", "info") and \
           ("recommend" in low or "suggest" in low):
            rec = generate_recommendation(None, user_message)
            bot_reply = f"{bot_reply}\n\nRecommendation: {rec}"
    except Exception:
        pass

    if reply_lang != "en":
        bot_reply = translate_text(bot_reply, reply_lang)

    if not bot_reply.strip():
        bot_reply = "🙂 Sorry, I didn’t understand that."

    return jsonify({"reply": bot_reply})
# ===== Admin Token Management =====
@app.route("/admin/tokens", methods=["GET", "POST", "DELETE"])
def admin_tokens():
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 401

    # Create a new API client token
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        raw = data.get("token")
        expiry = data.get("expires_at")

        if not raw:
            return jsonify({"error": "missing 'token'"}), 400

        try:
            token_hash = generate_password_hash(raw)
            expires_at = None
            if expiry:
                try:
                    expires_at = dateparser.parse(expiry)
                except Exception:
                    expires_at = None

            ct = ClientToken(token_hash=token_hash, expires_at=expires_at)
            db.session.add(ct)
            db.session.commit()

            return jsonify({"ok": True, "msg": "token stored"})

        except Exception as e:
            db.session.rollback()
            logger.exception("Failed to add token: %s", e)
            return jsonify({"error": "db error"}), 500

    # Get all tokens
    if request.method == "GET":
        try:
            rows = ClientToken.query.all()
            out = []
            for r in rows:
                out.append({
                    "id": r.id,
                    "created_at": str(r.created_at),
                    "expires_at": str(r.expires_at) if r.expires_at else None
                })
            return jsonify({"tokens": out})
        except Exception as e:
            logger.exception("Failed to fetch tokens: %s", e)
            return jsonify({"error": "db error"}), 500

    # Delete a token by ID
    if request.method == "DELETE":
        data = request.get_json(silent=True) or {}
        token_id = data.get("id")

        if not token_id:
            return jsonify({"error": "missing id"}), 400

        try:
            ct = ClientToken.query.get(token_id)
            if not ct:
                return jsonify({"error": "not found"}), 404

            db.session.delete(ct)
            db.session.commit()
            return jsonify({"ok": True, "msg": "deleted"})

        except Exception as e:
            db.session.rollback()
            logger.exception("Failed to delete token: %s", e)
            return jsonify({"error": "db error"}), 500


# ===== Graceful shutdown routes =====
@app.route("/shutdown", methods=["POST"])
def shutdown():
    """
    Allows admin to gracefully shut down the server.
    """
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 401

    func = request.environ.get("werkzeug.server.shutdown")
    if func is None:
        return jsonify({"error": "Not running with Werkzeug"}), 500

    func()
    return jsonify({"status": "shutting down"}), 200


# ===== Error handlers =====
@app.errorhandler(404)
def page_not_found(e):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(429)
def too_many_requests(e):
    return jsonify({"error": "rate limit exceeded"}), 429


@app.errorhandler(500)
def server_error(e):
    logger.exception("Server error: %s", e)
    return jsonify({"error": "internal server error"}), 500


# ===== Main entry point =====
if __name__ == "__main__":
    # Optional: Wait for database readiness on startup
    try:
        wait_for_db(max_retries=10, delay=3)
    except Exception as e:
        logger.error("Database not reachable during startup: %s", e)
        # Continue regardless, or call sys.exit(1)
        pass

    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "False").lower() in ("1", "true", "yes")

    logger.info("Starting server on port %s (debug=%s)", port, debug)

    # Run Flask app
    app.run(
        host="0.0.0.0",
        port=port,
        debug=debug,
        use_reloader=debug  # Only use reloader in debug mode
    )

