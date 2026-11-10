"""Piyasa Atlas community comments beta.

Anonymous comments are public. Basic anti-spam and report/author-block interfaces
are provided. Deploy with ATLAS_COMMENTS_DB_PATH pointing to a persistent disk for
durability; default /tmp storage is NOT durable across server redeploys.
"""
from flask import Blueprint, jsonify, request, make_response
import os, re, sqlite3, threading, time, uuid
from datetime import datetime, timezone

DB_PATH = os.getenv("ATLAS_COMMENTS_DB_PATH", "/tmp/piyasa_atlas_comments.sqlite3")
API = Blueprint("atlas_comments_api", __name__)
_LOCK = threading.RLock()
_RECENT_POSTS = {}


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=8)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=6000")
    conn.execute("""CREATE TABLE IF NOT EXISTS comments (
        id TEXT PRIMARY KEY, asset TEXT NOT NULL, author TEXT NOT NULL,
        body TEXT NOT NULL, created INTEGER NOT NULL, reports INTEGER NOT NULL DEFAULT 0
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS comment_reports (
        comment_id TEXT NOT NULL, reporter TEXT NOT NULL,
        PRIMARY KEY(comment_id, reporter)
    )""")
    conn.commit()
    return conn


def _json(data, status=200):
    out = make_response(jsonify(data), status)
    out.headers["Access-Control-Allow-Origin"] = "https://app.local"
    out.headers["Vary"] = "Origin"
    out.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    out.headers["Access-Control-Allow-Headers"] = "Content-Type"
    out.headers["Cache-Control"] = "no-store"
    return out


@API.route("/api/atlas/comments", methods=["GET", "POST", "OPTIONS"])
def atlas_comments():
    if request.method == "OPTIONS":
        return _json({"ok": True})
    if request.method == "GET":
        asset = request.args.get("asset", "")[:40]
        if asset and not re.fullmatch(r"[a-zA-Z0-9:._-]{2,40}", asset):
            return _json({"ok": False, "error": "Geçersiz varlık"}, 400)
        try:
            with _LOCK:
                with _connect() as db:
                    if asset:
                        rows = db.execute("SELECT id,asset,author,body,created FROM comments WHERE asset=? AND reports<3 ORDER BY created DESC LIMIT 35", (asset,)).fetchall()
                    else:
                        rows = db.execute("SELECT id,asset,author,body,created FROM comments WHERE reports<3 ORDER BY created DESC LIMIT 35").fetchall()
            return _json({"ok": True, "comments": [dict(r) for r in rows],
                          "persistence": "persistent" if os.getenv("ATLAS_COMMENTS_DB_PATH") else "temporary_beta"})
        except Exception:
            return _json({"ok": False, "error": "Yorum veritabanına erişilemiyor"}, 503)
    data = request.get_json(silent=True) or {}
    asset = str(data.get("asset", ""))[:60]
    author = str(data.get("author", "")).strip()[:24]
    body = str(data.get("body", "")).strip()
    if not re.fullmatch(r"[a-zA-Z0-9:._-]{2,40}", asset):
        return _json({"ok": False, "error": "Bir varlık seçin"}, 400)
    if not (2 <= len(author) <= 24) or not (5 <= len(body) <= 450):
        return _json({"ok": False, "error": "İsim 2–24, yorum 5–450 karakter olmalı"}, 400)
    if any(ord(ch) < 32 and ch not in "\n\t" for ch in body) or re.search(r"https?://|www\.", body, re.I):
        return _json({"ok": False, "error": "Yorumlarda bağlantı paylaşımı desteklenmiyor"}, 400)
    ident = (request.headers.get("X-Forwarded-For", request.remote_addr or "anon").split(",")[0].strip())[:100]
    now = int(time.time())
    with _LOCK:
        previous = [t for t in _RECENT_POSTS.get(ident, []) if now - t < 600]
        if previous and now - previous[-1] < 25:
            return _json({"ok": False, "error": "Yeni yorum için 25 saniye bekleyin"}, 429)
        if len(previous) >= 5:
            return _json({"ok": False, "error": "10 dakika içinde en fazla 5 yorum"}, 429)
        _RECENT_POSTS[ident] = previous + [now]
        if len(_RECENT_POSTS) > 10000:
            _RECENT_POSTS.clear()
        comment_id = uuid.uuid4().hex
        try:
            with _connect() as db:
                db.execute("INSERT INTO comments(id,asset,author,body,created) VALUES(?,?,?,?,?)",
                           (comment_id, asset, author, body, now))
                db.commit()
        except Exception:
            return _json({"ok": False, "error": "Yorum kaydedilemedi"}, 503)
    return _json({"ok": True, "id": comment_id}, 201)


@API.route("/api/atlas/comments/report", methods=["POST", "OPTIONS"])
def report_comment():
    if request.method == "OPTIONS":
        return _json({"ok": True})
    data = request.get_json(silent=True) or {}
    comment_id = str(data.get("id", ""))
    reporter = str(data.get("reporter", ""))
    if not re.fullmatch(r"[a-f0-9]{32}", comment_id) or not re.fullmatch(r"[a-zA-Z0-9_-]{12,80}", reporter):
        return _json({"ok": False, "error": "Geçersiz bildirim"}, 400)
    try:
        with _LOCK:
            with _connect() as db:
                existing = db.execute("SELECT id FROM comments WHERE id=?", (comment_id,)).fetchone()
                if existing is None:
                    return _json({"ok": False, "error": "Yorum bulunamadı"}, 404)
                cursor = db.execute("INSERT OR IGNORE INTO comment_reports(comment_id,reporter) VALUES(?,?)", (comment_id, reporter))
                if cursor.rowcount:
                    db.execute("UPDATE comments SET reports=reports+1 WHERE id=?", (comment_id,))
                db.commit()
        return _json({"ok": True})
    except Exception:
        return _json({"ok": False, "error": "Bildirim kaydedilemedi"}, 503)


def register_comments(app):
    app.register_blueprint(API)
