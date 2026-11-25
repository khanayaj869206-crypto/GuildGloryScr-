#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GUILD GLORY WEB PANEL & TERMUX SERVER
=====================================
Runs Flask + SQLite + 4-Bot Guild Glory BR Squad Engine on Termux.
- 1 Credit = 4 Bots added to any target Guild ID
- 1 Bot = 22 Glory/Game -> 4 Bots = 88 Glory/Game
- Target = 2,000 Glory -> Auto-calculates ceil(2000 / 88) = 23 Games & auto-stops offline
- Owner controls: generate credit coupons, grant/deduct credits, ban/unban users
"""

import argparse
import asyncio
import base64
import getpass
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from functools import wraps

from flask import Flask, jsonify, render_template, request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from guild_glory import load_all_accounts, run_guild_glory_job  # noqa: E402

DB_PATH = os.path.join(BASE_DIR, "guild_glory.db")
SECRET_PATH = os.path.join(BASE_DIR, ".panel_secret")
ACCOUNTS_PATH = os.path.join(BASE_DIR, "accounts.json")

GLORY_PER_BOT = 22
SQUAD_BOTS = 4
TARGET_GLORY_DEFAULT = 2000
VALID_REGIONS = {"IND", "BD", "SG", "BR", "ME"}
TOKEN_TTL_SECONDS = 7 * 24 * 3600

app = Flask(__name__, template_folder=os.path.join(BASE_DIR, "templates"))

# Active background worker tracking (in-memory coordination with SQLite persistence)
_WORKERS_LOCK = threading.Lock()
_ACTIVE_WORKERS = {}  # squad_id -> {"loop": asyncio.AbstractEventLoop, "stop_evt": asyncio.Event, "bot_uids": set[str]}


def _load_or_create_secret() -> bytes:
    env_sec = os.environ.get("JWT_SECRET", "").strip()
    if len(env_sec) >= 16:
        return env_sec.encode("utf-8")
    if os.path.exists(SECRET_PATH):
        with open(SECRET_PATH, "r", encoding="utf-8") as f:
            val = f.read().strip()
            if len(val) >= 32:
                return val.encode("utf-8")
    val = secrets.token_hex(32)
    with open(SECRET_PATH, "w", encoding="utf-8") as f:
        f.write(val)
    try:
        os.chmod(SECRET_PATH, 0o600)
    except Exception:
        pass
    return val.encode("utf-8")


JWT_SECRET = _load_or_create_secret()


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return f"{salt.hex()}:{dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split(":", 1)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(dk_hex)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def _b64u_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64u_decode(text: str) -> bytes:
    pad_len = (-len(text)) % 4
    return base64.urlsafe_b64decode(text + ("=" * pad_len))


def _sign_token(user_id: int, username: str, role: str) -> str:
    header = _b64u_encode(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    now = int(time.time())
    payload = _b64u_encode(
        json.dumps(
            {"sub": user_id, "username": username, "role": role, "iat": now, "exp": now + TOKEN_TTL_SECONDS},
            separators=(",", ":"),
        ).encode()
    )
    msg = f"{header}.{payload}".encode("ascii")
    sig = _b64u_encode(hmac.new(JWT_SECRET, msg, hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


def _verify_token(token: str):
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        msg = f"{parts[0]}.{parts[1]}".encode("ascii")
        expected_sig = hmac.new(JWT_SECRET, msg, hashlib.sha256).digest()
        actual_sig = _b64u_decode(parts[2])
        if not hmac.compare_digest(expected_sig, actual_sig):
            return None
        payload = json.loads(_b64u_decode(parts[1]).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def _gen_coupon_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    parts = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3)]
    return "-".join(parts)


def init_db(cli_owner_user: str = "", cli_owner_pass: str = ""):
    conn = _db()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                credits INTEGER NOT NULL DEFAULT 0,
                banned INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS coupons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                credits INTEGER NOT NULL,
                max_uses INTEGER NOT NULL DEFAULT 1,
                used_count INTEGER NOT NULL DEFAULT 0,
                created_by_id INTEGER NOT NULL,
                created_by_name TEXT NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS coupon_redemptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                coupon_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                credits INTEGER NOT NULL,
                redeemed_at INTEGER NOT NULL,
                UNIQUE(coupon_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                amount INTEGER NOT NULL,
                description TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS squads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                clan_id TEXT NOT NULL,
                region TEXT NOT NULL,
                bot_count INTEGER NOT NULL,
                accounts_created INTEGER NOT NULL DEFAULT 0,
                bot_prefix TEXT NOT NULL DEFAULT 'Glory',
                bots_active INTEGER NOT NULL DEFAULT 0,
                target_glory INTEGER NOT NULL DEFAULT 2000,
                glory_per_match INTEGER NOT NULL DEFAULT 88,
                glory_earned INTEGER NOT NULL DEFAULT 0,
                target_matches INTEGER NOT NULL DEFAULT 23,
                matches_played INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                bot_uids TEXT NOT NULL DEFAULT '[]',
                error TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL,
                ended_at INTEGER
            );
            """
        )

        # Safe column migration for existing DBs
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(squads)").fetchall()}
        if "accounts_created" not in cols:
            conn.execute("ALTER TABLE squads ADD COLUMN accounts_created INTEGER NOT NULL DEFAULT 0")
        if "bot_prefix" not in cols:
            conn.execute("ALTER TABLE squads ADD COLUMN bot_prefix TEXT NOT NULL DEFAULT 'Glory'")

        # Mark active workers' live count as 0 until resume_incomplete_squads() reconnects them
        conn.execute(
            "UPDATE squads SET bots_active = 0 WHERE status IN ('starting', 'creating_accounts', 'joining_guild', 'running')"
        )

        owner_row = conn.execute("SELECT id FROM users WHERE role = 'owner' LIMIT 1").fetchone()
        if not owner_row:
            env_user = (cli_owner_user or os.environ.get("OWNER_USERNAME") or "Suhail").strip()
            env_pass = (cli_owner_pass or os.environ.get("OWNER_PASSWORD") or "").strip()
            if not env_pass and sys.stdin.isatty():
                print("\n[Setup] No Owner account found in database yet.")
                inp_u = input(f"Enter Owner Username [{env_user}]: ").strip()
                if inp_u:
                    env_user = inp_u
                env_pass = getpass.getpass(f"Enter Owner Password for '{env_user}' (or press Enter to set via web UI): ").strip()
            if env_pass and len(env_pass) >= 6:
                now = int(time.time())
                conn.execute(
                    "INSERT INTO users (username, password_hash, role, credits, banned, created_at) VALUES (?, ?, 'owner', 999, 0, ?)",
                    (env_user, _hash_password(env_pass), now),
                )
                print(f"[+] Owner account '{env_user}' initialized securely.")
    finally:
        conn.close()


def _safe_user_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "role": row["role"],
        "credits": row["credits"],
        "banned": bool(row["banned"]),
        "created_at": row["created_at"],
    }


def require_auth(owner_only: bool = False):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            auth_hdr = request.headers.get("Authorization", "")
            if not auth_hdr.startswith("Bearer "):
                return jsonify({"error": "Authentication required"}), 401
            tok = auth_hdr[7:].strip()
            claims = _verify_token(tok)
            if not claims:
                return jsonify({"error": "Invalid or expired session token"}), 401
            conn = _db()
            try:
                user = conn.execute("SELECT * FROM users WHERE id = ?", (int(claims["sub"]),)).fetchone()
            finally:
                conn.close()
            if not user:
                return jsonify({"error": "Account not found"}), 401
            if user["banned"]:
                return jsonify({"error": "Account is banned by owner"}), 403
            if owner_only and user["role"] != "owner":
                return jsonify({"error": "Owner permission required"}), 403
            request.current_user = user  # type: ignore[attr-defined]
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def _signal_stop_squad(squad_id: int) -> bool:
    with _WORKERS_LOCK:
        worker = _ACTIVE_WORKERS.get(squad_id)
        if not worker:
            return False
        loop = worker.get("loop")
        evt = worker.get("stop_evt")
        if loop and evt and not loop.is_closed():
            loop.call_soon_threadsafe(evt.set)
        return True


def _start_squad_worker(
    squad_id: int,
    clan_id: int,
    region: str,
    target_glory: int,
    bot_prefix: str = "Glory",
    accounts_subset=None,
    initial_matches: int = 0,
    initial_glory: int = 0,
):
    with _WORKERS_LOCK:
        _ACTIVE_WORKERS[squad_id] = {"loop": None, "stop_evt": None}

    def _on_progress(update: dict):
        conn = _db()
        try:
            fields = []
            params = []
            for col in (
                "status",
                "accounts_created",
                "bot_uids",
                "bots_active",
                "glory_per_match",
                "target_matches",
                "matches_played",
                "glory_earned",
                "error",
            ):
                if col in update and update[col] is not None:
                    fields.append(f"{col} = ?")
                    params.append(update[col])
            if update.get("status") in ("completed", "stopped", "failed"):
                fields.append("ended_at = ?")
                params.append(int(time.time()))
            if fields:
                params.append(squad_id)
                conn.execute(f"UPDATE squads SET {', '.join(fields)} WHERE id = ?", params)
        finally:
            conn.close()

    def _thread_runner():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        stop_evt = asyncio.Event()
        with _WORKERS_LOCK:
            if squad_id in _ACTIVE_WORKERS:
                _ACTIVE_WORKERS[squad_id]["loop"] = loop
                _ACTIVE_WORKERS[squad_id]["stop_evt"] = stop_evt
        try:
            loop.run_until_complete(
                run_guild_glory_job(
                    accounts_subset=accounts_subset,
                    clan_id=int(clan_id),
                    region=region,
                    target_glory=target_glory,
                    glory_per_bot=GLORY_PER_BOT,
                    stop_evt=stop_evt,
                    on_progress=_on_progress,
                    auto_create_accounts=True,
                    accounts_json_path=ACCOUNTS_PATH,
                    name_prefix=bot_prefix,
                    initial_matches=initial_matches,
                    initial_glory=initial_glory,
                )
            )
        except Exception as exc:
            _on_progress({"status": "failed", "bots_active": 0, "error": str(exc)})
        finally:
            with _WORKERS_LOCK:
                _ACTIVE_WORKERS.pop(squad_id, None)
            try:
                loop.close()
            except Exception:
                pass

    t = threading.Thread(target=_thread_runner, daemon=True, name=f"squad-{squad_id}")
    t.start()


def resume_incomplete_squads():
    """
    Auto-resumes every incomplete squad after a server restart using its saved
    accounts, Guild ID, matches_played, and glory_earned — nothing starts over from 0.
    """
    conn = _db()
    try:
        conn.execute(
            """
            UPDATE squads
            SET status = 'stopped', bots_active = 0, ended_at = ?
            WHERE status IN ('starting', 'creating_accounts', 'joining_guild', 'running')
              AND user_id IN (SELECT id FROM users WHERE banned = 1)
            """,
            (int(time.time()),),
        )
        rows = conn.execute(
            """
            SELECT s.*
            FROM squads s
            JOIN users u ON u.id = s.user_id
            WHERE u.banned = 0
              AND s.status IN ('starting', 'creating_accounts', 'joining_guild', 'running')
            ORDER BY s.id ASC
            """
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return

    acc_by_uid = {}
    if os.path.exists(ACCOUNTS_PATH):
        try:
            with open(ACCOUNTS_PATH, "r", encoding="utf-8") as f:
                raw_accs = json.load(f)
            if isinstance(raw_accs, list):
                for a in raw_accs:
                    uid_val = str(a.get("uid") or a.get("login_uid") or "")
                    if uid_val:
                        acc_by_uid[uid_val] = a
        except Exception:
            acc_by_uid = {}

    resumed = 0
    for row in rows:
        squad_id = row["id"]
        with _WORKERS_LOCK:
            if squad_id in _ACTIVE_WORKERS:
                continue
        try:
            saved_uids = json.loads(row["bot_uids"] or "[]")
        except Exception:
            saved_uids = []
        matched_accounts = [acc_by_uid[str(u)] for u in saved_uids if str(u) in acc_by_uid]

        _start_squad_worker(
            squad_id=squad_id,
            clan_id=int(row["clan_id"]),
            region=row["region"],
            target_glory=int(row["target_glory"]),
            bot_prefix=row["bot_prefix"] or "Glory",
            accounts_subset=matched_accounts,
            initial_matches=int(row["matches_played"] or 0),
            initial_glory=int(row["glory_earned"] or 0),
        )
        resumed += 1

    if resumed:
        print(f"[+] Resumed {resumed} incomplete squad(s) from saved history (continuing matches & glory)!")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/auth/setup-status", methods=["GET"])
def setup_status():
    conn = _db()
    try:
        owner = conn.execute("SELECT id FROM users WHERE role = 'owner' LIMIT 1").fetchone()
        return jsonify({
            "needs_owner_setup": owner is None,
            "default_owner_username": os.environ.get("OWNER_USERNAME", "Suhail"),
        })
    finally:
        conn.close()


@app.route("/api/auth/setup-owner", methods=["POST"])
def setup_owner():
    body = request.get_json(silent=True) or {}
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    if not re.match(r"^[A-Za-z0-9_]{3,24}$", username):
        return jsonify({"error": "Username must be 3-24 letters, numbers, or underscore"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400

    conn = _db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT id FROM users WHERE role = 'owner' LIMIT 1").fetchone()
        if existing:
            conn.execute("ROLLBACK")
            return jsonify({"error": "Owner account already initialized"}), 403
        now = int(time.time())
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role, credits, banned, created_at) VALUES (?, ?, 'owner', 999, 0, ?)",
            (username, _hash_password(password), now),
        )
        uid = cur.lastrowid
        conn.execute("COMMIT")
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        tok = _sign_token(uid, username, "owner")
        return jsonify({"token": tok, "user": _safe_user_dict(row)})
    except sqlite3.IntegrityError:
        conn.execute("ROLLBACK")
        return jsonify({"error": "Username already exists"}), 409
    finally:
        conn.close()


@app.route("/api/auth/register", methods=["POST"])
def register():
    body = request.get_json(silent=True) or {}
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    if not re.match(r"^[A-Za-z0-9_]{3,24}$", username):
        return jsonify({"error": "Username must be 3-24 letters, numbers, or underscore"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400

    conn = _db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        owner = conn.execute("SELECT id FROM users WHERE role = 'owner' LIMIT 1").fetchone()
        if not owner:
            conn.execute("ROLLBACK")
            return jsonify({"error": "Owner setup must be completed first"}), 400
        dup = conn.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(?)", (username,)).fetchone()
        if dup:
            conn.execute("ROLLBACK")
            return jsonify({"error": "Username already exists"}), 409
        now = int(time.time())
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role, credits, banned, created_at) VALUES (?, ?, 'user', 0, 0, ?)",
            (username, _hash_password(password), now),
        )
        uid = cur.lastrowid
        conn.execute("COMMIT")
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        tok = _sign_token(uid, username, "user")
        return jsonify({"token": tok, "user": _safe_user_dict(row)})
    finally:
        conn.close()


@app.route("/api/auth/login", methods=["POST"])
def login():
    body = request.get_json(silent=True) or {}
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400

    conn = _db()
    try:
        row = conn.execute("SELECT * FROM users WHERE LOWER(username) = LOWER(?)", (username,)).fetchone()
        if not row or not _verify_password(password, row["password_hash"]):
            return jsonify({"error": "Invalid username or password"}), 401
        if row["banned"]:
            return jsonify({"error": "Your account has been banned by the owner"}), 403
        tok = _sign_token(row["id"], row["username"], row["role"])
        return jsonify({"token": tok, "user": _safe_user_dict(row)})
    finally:
        conn.close()


@app.route("/api/auth/me", methods=["GET"])
@require_auth()
def me():
    user = request.current_user  # type: ignore[attr-defined]
    try:
        all_accounts = load_all_accounts(ACCOUNTS_PATH)
    except Exception:
        all_accounts = []
    with _WORKERS_LOCK:
        active_jobs = len(_ACTIVE_WORKERS)
    return jsonify({
        "user": _safe_user_dict(user),
        "bot_pool": {"total": len(all_accounts), "busy": active_jobs * SQUAD_BOTS, "idle": len(all_accounts)},
    })


@app.route("/api/auth/change-password", methods=["POST"])
@require_auth()
def change_password():
    user = request.current_user  # type: ignore[attr-defined]
    body = request.get_json(silent=True) or {}
    current_pw = str(body.get("current_password") or "")
    new_pw = str(body.get("new_password") or "")
    if not _verify_password(current_pw, user["password_hash"]):
        return jsonify({"error": "Current password is incorrect"}), 400
    if len(new_pw) < 6:
        return jsonify({"error": "New password must be at least 6 characters"}), 400

    conn = _db()
    try:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (_hash_password(new_pw), user["id"]))
        return jsonify({"ok": True})
    finally:
        conn.close()


@app.route("/api/squads/launch", methods=["POST"])
@require_auth()
def launch_squad():
    user = request.current_user  # type: ignore[attr-defined]
    body = request.get_json(silent=True) or {}
    clan_id_raw = str(body.get("clan_id") or "").strip()
    region = str(body.get("region") or "IND").upper().strip()
    raw_prefix = str(body.get("bot_prefix") or "Glory").strip()
    bot_prefix = re.sub(r"[^A-Za-z0-9]", "", raw_prefix)[:10] or "Glory"

    if not re.match(r"^\d{5,15}$", clan_id_raw):
        return jsonify({"error": "Enter a valid numeric Clan / Guild ID (5-15 digits)"}), 400
    if region not in VALID_REGIONS:
        return jsonify({"error": "Unsupported region selected"}), 400

    glory_per_match = SQUAD_BOTS * GLORY_PER_BOT  # 88 glory per match
    target_glory = TARGET_GLORY_DEFAULT  # 2,000 glory
    target_matches = math.ceil(target_glory / glory_per_match)  # 23 games

    conn = _db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        fresh_user = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
        if fresh_user["banned"]:
            conn.execute("ROLLBACK")
            return jsonify({"error": "Account is banned"}), 403

        if fresh_user["role"] != "owner":
            cur = conn.execute(
                "UPDATE users SET credits = credits - 1 WHERE id = ? AND credits >= 1",
                (user["id"],),
            )
            if cur.rowcount == 0:
                conn.execute("ROLLBACK")
                return jsonify({"error": "Insufficient credits (Need 1, Have 0)"}), 402
            tx_amount = -1
            tx_desc = f"Created 4 bots & launched Clan {clan_id_raw} ({region}) — 2,000 Glory ({target_matches} games)"
        else:
            tx_amount = 0
            tx_desc = f"Owner created 4 bots & launched Clan {clan_id_raw} ({region}) — 2,000 Glory ({target_matches} games)"

        now = int(time.time())
        cur = conn.execute(
            """
            INSERT INTO squads (
                user_id, username, clan_id, region, bot_count, accounts_created, bot_prefix, bots_active,
                target_glory, glory_per_match, glory_earned, target_matches, matches_played,
                status, bot_uids, error, created_at
            ) VALUES (?, ?, ?, ?, ?, 0, ?, 0, ?, ?, 0, ?, 0, 'creating_accounts', '[]', '', ?)
            """,
            (
                user["id"],
                user["username"],
                clan_id_raw,
                region,
                SQUAD_BOTS,
                bot_prefix,
                target_glory,
                glory_per_match,
                target_matches,
                now,
            ),
        )
        squad_id = cur.lastrowid
        conn.execute(
            "INSERT INTO transactions (user_id, type, amount, description, created_at) VALUES (?, 'launch', ?, ?, ?)",
            (user["id"], tx_amount, tx_desc, now),
        )
        conn.execute("COMMIT")
        squad_row = dict(conn.execute("SELECT * FROM squads WHERE id = ?", (squad_id,)).fetchone())
        updated_user = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
    except Exception as exc:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        return jsonify({"error": f"Failed to launch squad: {exc}"}), 500
    finally:
        conn.close()

    _start_squad_worker(squad_id, int(clan_id_raw), region, target_glory, bot_prefix=bot_prefix)
    return jsonify({"squad": squad_row, "user": _safe_user_dict(updated_user)})


@app.route("/api/squads/active", methods=["GET"])
@require_auth()
def active_squads():
    user = request.current_user  # type: ignore[attr-defined]
    conn = _db()
    try:
        if user["role"] == "owner" and request.args.get("scope") == "all":
            rows = conn.execute(
                "SELECT * FROM squads WHERE status IN ('starting', 'creating_accounts', 'joining_guild', 'running') ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM squads WHERE user_id = ? AND status IN ('starting', 'creating_accounts', 'joining_guild', 'running') ORDER BY created_at DESC",
                (user["id"],),
            ).fetchall()
        return jsonify({"squads": [dict(r) for r in rows]})
    finally:
        conn.close()


@app.route("/api/squads/history", methods=["GET"])
@require_auth()
def squad_history():
    user = request.current_user  # type: ignore[attr-defined]
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT * FROM squads WHERE user_id = ? ORDER BY created_at DESC LIMIT 100",
            (user["id"],),
        ).fetchall()
        return jsonify({"squads": [dict(r) for r in rows]})
    finally:
        conn.close()


@app.route("/api/squads/archive", methods=["GET"])
@require_auth()
def squad_archive():
    user = request.current_user  # type: ignore[attr-defined]
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT * FROM squads WHERE user_id = ? ORDER BY created_at DESC LIMIT 200",
            (user["id"],),
        ).fetchall()
        return jsonify({"squads": [dict(r) for r in rows]})
    finally:
        conn.close()


@app.route("/api/squads/stop", methods=["POST"])
@require_auth()
def stop_squad():
    user = request.current_user  # type: ignore[attr-defined]
    body = request.get_json(silent=True) or {}
    try:
        squad_id = int(body.get("squad_id") or 0)
    except Exception:
        return jsonify({"error": "Invalid squad_id"}), 400

    conn = _db()
    try:
        squad = conn.execute("SELECT * FROM squads WHERE id = ?", (squad_id,)).fetchone()
        if not squad:
            return jsonify({"error": "Squad not found"}), 404
        if squad["user_id"] != user["id"] and user["role"] != "owner":
            return jsonify({"error": "Access denied"}), 403
        signaled = _signal_stop_squad(squad_id)
        if not signaled and squad["status"] in ("starting", "creating_accounts", "joining_guild", "running"):
            conn.execute(
                "UPDATE squads SET status = 'stopped', bots_active = 0, ended_at = ? WHERE id = ?",
                (int(time.time()), squad_id),
            )
        return jsonify({"ok": True})
    finally:
        conn.close()


@app.route("/api/transactions", methods=["GET"])
@require_auth()
def get_transactions():
    user = request.current_user  # type: ignore[attr-defined]
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT * FROM transactions WHERE user_id = ? ORDER BY created_at DESC LIMIT 100",
            (user["id"],),
        ).fetchall()
        return jsonify({"transactions": [dict(r) for r in rows]})
    finally:
        conn.close()


@app.route("/api/coupons/list", methods=["GET"])
@require_auth()
def list_coupons():
    user = request.current_user  # type: ignore[attr-defined]
    conn = _db()
    try:
        created = conn.execute(
            "SELECT * FROM coupons WHERE created_by_id = ? ORDER BY created_at DESC LIMIT 50",
            (user["id"],),
        ).fetchall()
        redeemed = conn.execute(
            "SELECT * FROM coupon_redemptions WHERE user_id = ? ORDER BY redeemed_at DESC LIMIT 50",
            (user["id"],),
        ).fetchall()
        return jsonify({
            "created": [dict(r) for r in created],
            "redeemed": [dict(r) for r in redeemed],
        })
    finally:
        conn.close()


@app.route("/api/coupons/create", methods=["POST"])
@require_auth()
def create_coupon():
    user = request.current_user  # type: ignore[attr-defined]
    body = request.get_json(silent=True) or {}
    try:
        credits_amt = int(body.get("credits") or 0)
        max_uses = int(body.get("max_uses") or 1)
    except Exception:
        return jsonify({"error": "Invalid coupon values"}), 400

    if credits_amt < 1 or credits_amt > 100:
        return jsonify({"error": "Credits per coupon must be 1 to 100"}), 400
    if user["role"] != "owner":
        max_uses = 1
    elif max_uses < 1 or max_uses > 500:
        return jsonify({"error": "Max uses must be 1 to 500"}), 400

    conn = _db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        fresh = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
        if fresh["role"] != "owner":
            cur = conn.execute(
                "UPDATE users SET credits = credits - ? WHERE id = ? AND credits >= ?",
                (credits_amt, user["id"], credits_amt),
            )
            if cur.rowcount == 0:
                conn.execute("ROLLBACK")
                return jsonify({"error": "Insufficient credits to create coupon"}), 400

        code = _gen_coupon_code()
        now = int(time.time())
        cur = conn.execute(
            """
            INSERT INTO coupons (code, credits, max_uses, used_count, created_by_id, created_by_name, revoked, created_at)
            VALUES (?, ?, ?, 0, ?, ?, 0, ?)
            """,
            (code, credits_amt, max_uses, user["id"], user["username"], now),
        )
        cid = cur.lastrowid
        if fresh["role"] != "owner":
            conn.execute(
                "INSERT INTO transactions (user_id, type, amount, description, created_at) VALUES (?, 'coupon_create', ?, ?, ?)",
                (user["id"], -credits_amt, f"Created coupon {code} ({credits_amt} credits)", now),
            )
        conn.execute("COMMIT")
        coupon_row = dict(conn.execute("SELECT * FROM coupons WHERE id = ?", (cid,)).fetchone())
        updated_user = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
        return jsonify({"coupon": coupon_row, "user": _safe_user_dict(updated_user)})
    finally:
        conn.close()


@app.route("/api/coupons/redeem", methods=["POST"])
@require_auth()
def redeem_coupon():
    user = request.current_user  # type: ignore[attr-defined]
    body = request.get_json(silent=True) or {}
    code = str(body.get("code") or "").upper().strip()
    if not code:
        return jsonify({"error": "Enter a coupon code"}), 400

    conn = _db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        coupon = conn.execute("SELECT * FROM coupons WHERE code = ?", (code,)).fetchone()
        if not coupon:
            conn.execute("ROLLBACK")
            return jsonify({"error": "Coupon code not found"}), 404
        if coupon["revoked"]:
            conn.execute("ROLLBACK")
            return jsonify({"error": "Coupon has been revoked"}), 400
        if coupon["used_count"] >= coupon["max_uses"]:
            conn.execute("ROLLBACK")
            return jsonify({"error": "Coupon usage limit already reached"}), 400

        already = conn.execute(
            "SELECT id FROM coupon_redemptions WHERE coupon_id = ? AND user_id = ?",
            (coupon["id"], user["id"]),
        ).fetchone()
        if already:
            conn.execute("ROLLBACK")
            return jsonify({"error": "You have already redeemed this coupon"}), 400

        now = int(time.time())
        conn.execute("UPDATE coupons SET used_count = used_count + 1 WHERE id = ?", (coupon["id"],))
        conn.execute(
            "INSERT INTO coupon_redemptions (coupon_id, code, user_id, username, credits, redeemed_at) VALUES (?, ?, ?, ?, ?, ?)",
            (coupon["id"], coupon["code"], user["id"], user["username"], coupon["credits"], now),
        )
        conn.execute("UPDATE users SET credits = credits + ? WHERE id = ?", (coupon["credits"], user["id"]))
        conn.execute(
            "INSERT INTO transactions (user_id, type, amount, description, created_at) VALUES (?, 'redeem', ?, ?, ?)",
            (user["id"], coupon["credits"], f"Redeemed coupon {coupon['code']}", now),
        )
        conn.execute("COMMIT")
        updated_user = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
        return jsonify({"credits_added": coupon["credits"], "user": _safe_user_dict(updated_user)})
    finally:
        conn.close()


@app.route("/api/coupons/revoke", methods=["POST"])
@require_auth()
def revoke_coupon():
    user = request.current_user  # type: ignore[attr-defined]
    body = request.get_json(silent=True) or {}
    try:
        coupon_id = int(body.get("coupon_id") or 0)
    except Exception:
        return jsonify({"error": "Invalid coupon_id"}), 400

    conn = _db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        c = conn.execute("SELECT * FROM coupons WHERE id = ?", (coupon_id,)).fetchone()
        if not c:
            conn.execute("ROLLBACK")
            return jsonify({"error": "Coupon not found"}), 404
        if c["created_by_id"] != user["id"] and user["role"] != "owner":
            conn.execute("ROLLBACK")
            return jsonify({"error": "Access denied"}), 403
        if c["revoked"]:
            conn.execute("ROLLBACK")
            return jsonify({"error": "Already revoked"}), 400

        conn.execute("UPDATE coupons SET revoked = 1 WHERE id = ?", (coupon_id,))
        creator = conn.execute("SELECT * FROM users WHERE id = ?", (c["created_by_id"],)).fetchone()
        if creator and creator["role"] != "owner" and c["used_count"] == 0:
            now = int(time.time())
            conn.execute("UPDATE users SET credits = credits + ? WHERE id = ?", (c["credits"], creator["id"]))
            conn.execute(
                "INSERT INTO transactions (user_id, type, amount, description, created_at) VALUES (?, 'coupon_revoke', ?, ?, ?)",
                (creator["id"], c["credits"], f"Refunded revoked coupon {c['code']}", now),
            )
        conn.execute("COMMIT")
        updated_user = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
        return jsonify({"ok": True, "user": _safe_user_dict(updated_user)})
    finally:
        conn.close()


@app.route("/api/owner/users", methods=["GET"])
@require_auth(owner_only=True)
def owner_users():
    conn = _db()
    try:
        rows = conn.execute(
            """
            SELECT u.id, u.username, u.role, u.credits, u.banned, u.created_at,
                   (SELECT COUNT(*) FROM squads s WHERE s.user_id = u.id AND s.status IN ('starting', 'creating_accounts', 'joining_guild', 'running')) AS active_squads,
                   (SELECT COALESCE(SUM(s.glory_earned), 0) FROM squads s WHERE s.user_id = u.id) AS total_glory
            FROM users u
            ORDER BY CASE WHEN u.role = 'owner' THEN 0 ELSE 1 END, u.created_at DESC
            """
        ).fetchall()
        return jsonify({"users": [dict(r) for r in rows]})
    finally:
        conn.close()


@app.route("/api/owner/credits", methods=["POST"])
@require_auth(owner_only=True)
def owner_adjust_credits():
    owner = request.current_user  # type: ignore[attr-defined]
    body = request.get_json(silent=True) or {}
    try:
        target_id = int(body.get("user_id") or 0)
        delta = int(body.get("delta") or 0)
    except Exception:
        return jsonify({"error": "Invalid parameters"}), 400

    if delta == 0 or abs(delta) > 10000:
        return jsonify({"error": "Credit delta must be non-zero (-10000 to +10000)"}), 400

    conn = _db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        target = conn.execute("SELECT * FROM users WHERE id = ?", (target_id,)).fetchone()
        if not target:
            conn.execute("ROLLBACK")
            return jsonify({"error": "User not found"}), 404

        new_credits = max(0, int(target["credits"]) + delta)
        actual_delta = new_credits - int(target["credits"])
        conn.execute("UPDATE users SET credits = ? WHERE id = ?", (new_credits, target_id))
        now = int(time.time())
        conn.execute(
            "INSERT INTO transactions (user_id, type, amount, description, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                target_id,
                "owner_grant" if actual_delta >= 0 else "owner_deduct",
                actual_delta,
                f"Credit adjustment by {owner['username']}",
                now,
            ),
        )
        conn.execute("COMMIT")
        updated = conn.execute("SELECT * FROM users WHERE id = ?", (target_id,)).fetchone()
        return jsonify({"user": _safe_user_dict(updated)})
    finally:
        conn.close()


@app.route("/api/owner/ban", methods=["POST"])
@require_auth(owner_only=True)
def owner_ban_user():
    owner = request.current_user  # type: ignore[attr-defined]
    body = request.get_json(silent=True) or {}
    try:
        target_id = int(body.get("user_id") or 0)
        banned_flag = 1 if bool(body.get("banned")) else 0
    except Exception:
        return jsonify({"error": "Invalid parameters"}), 400

    if target_id == owner["id"]:
        return jsonify({"error": "Owner account cannot be banned"}), 400

    conn = _db()
    try:
        target = conn.execute("SELECT * FROM users WHERE id = ?", (target_id,)).fetchone()
        if not target:
            return jsonify({"error": "User not found"}), 404
        if target["role"] == "owner":
            return jsonify({"error": "Cannot ban owner account"}), 400

        conn.execute("UPDATE users SET banned = ? WHERE id = ?", (banned_flag, target_id))
        if banned_flag == 1:
            active = conn.execute(
                "SELECT id FROM squads WHERE user_id = ? AND status IN ('starting', 'creating_accounts', 'joining_guild', 'running')",
                (target_id,),
            ).fetchall()
            for row in active:
                _signal_stop_squad(row["id"])
        updated = conn.execute("SELECT * FROM users WHERE id = ?", (target_id,)).fetchone()
        return jsonify({"user": _safe_user_dict(updated)})
    finally:
        conn.close()


@app.route("/api/owner/create-user", methods=["POST"])
@require_auth(owner_only=True)
def owner_create_user():
    owner = request.current_user  # type: ignore[attr-defined]
    body = request.get_json(silent=True) or {}
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    try:
        credits_amt = max(0, int(body.get("credits") or 0))
    except Exception:
        credits_amt = 0

    if not re.match(r"^[A-Za-z0-9_]{3,24}$", username):
        return jsonify({"error": "Username must be 3-24 letters, numbers, or underscore"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400

    conn = _db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        dup = conn.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(?)", (username,)).fetchone()
        if dup:
            conn.execute("ROLLBACK")
            return jsonify({"error": "Username already exists"}), 409
        now = int(time.time())
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role, credits, banned, created_at) VALUES (?, ?, 'user', ?, 0, ?)",
            (username, _hash_password(password), credits_amt, now),
        )
        uid = cur.lastrowid
        if credits_amt > 0:
            conn.execute(
                "INSERT INTO transactions (user_id, type, amount, description, created_at) VALUES (?, 'owner_grant', ?, ?, ?)",
                (uid, credits_amt, f"Initial credits assigned by {owner['username']}", now),
            )
        conn.execute("COMMIT")
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        return jsonify({"user": _safe_user_dict(row)})
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser(description="Guild Glory Termux Web Panel Server")
    ap.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"), help="Bind host (default 0.0.0.0)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "5000")), help="Port (default 5000)")
    ap.add_argument("--owner-user", default="", help="Initial Owner username (if setting up via CLI)")
    ap.add_argument("--owner-pass", default="", help="Initial Owner password (if setting up via CLI)")
    args = ap.parse_args()

    init_db(cli_owner_user=args.owner_user, cli_owner_pass=args.owner_pass)
    resume_incomplete_squads()
    print(f"\n[+] Guild Glory Panel running on http://127.0.0.1:{args.port}")
    app.run(host=args.host, port=args.port, threaded=True, debug=False)


if __name__ == "__main__":
    main()
