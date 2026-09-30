import hashlib
import hmac
import os
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

DB_PATH = os.environ.get("CHATROOM_DB", str(Path(__file__).with_name("chatroom.db")))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    password TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    sender TEXT,
    receiver TEXT,
    message TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS requests (
    sender TEXT,
    receiver TEXT,
    status TEXT
);
CREATE INDEX IF NOT EXISTS messages_pair ON messages (sender, receiver);
CREATE INDEX IF NOT EXISTS requests_pair ON requests (sender, receiver);
"""

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1

# Run writes one at a time. Waiting on SQLite's own lock can time out when commits are slow.
# Request updates also read, then write, so this keeps them consistent.
_write_lock = threading.Lock()


def connect():
    return closing(sqlite3.connect(DB_PATH, timeout=10))


def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)


def reset_db():
    with connect() as conn:
        conn.executescript("""
            DROP TABLE IF EXISTS users;
            DROP TABLE IF EXISTS messages;
            DROP TABLE IF EXISTS requests;
        """)
        conn.executescript(SCHEMA)


def hash_password(password):
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    if not stored or not stored.startswith("scrypt$"):
        return False
    _, n, r, p, salt, digest = stored.split("$")
    candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p))
    return hmac.compare_digest(candidate, bytes.fromhex(digest))


def add_user(username, password):
    hashed = hash_password(password)
    with _write_lock, connect() as conn, conn:
        try:
            conn.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, hashed))
        except sqlite3.IntegrityError:
            return False
    return True


def check_user(username, password):
    with connect() as conn:
        row = conn.execute("SELECT password FROM users WHERE username = ?", (username,)).fetchone()
    return row is not None and verify_password(password, row[0])


def user_exists(username):
    with connect() as conn:
        return conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone() is not None


def add_request(sender, receiver):
    """Return 'sent', 'pending', 'friends', or 'accepted' if receiver had already asked sender."""
    with _write_lock, connect() as conn, conn:
        rows = conn.execute(
            "SELECT sender, status FROM requests WHERE (sender = ? AND receiver = ?) OR (sender = ? AND receiver = ?)",
            (sender, receiver, receiver, sender),
        ).fetchall()
        if any(status == "accepted" for _, status in rows):
            return "friends"
        if (sender, "pending") in rows:
            return "pending"
        if (receiver, "pending") in rows:
            conn.execute(
                "UPDATE requests SET status = 'accepted' WHERE sender = ? AND receiver = ? AND status = 'pending'",
                (receiver, sender),
            )
            return "accepted"
        conn.execute("DELETE FROM requests WHERE sender = ? AND receiver = ?", (sender, receiver))
        conn.execute("INSERT INTO requests (sender, receiver, status) VALUES (?, ?, 'pending')", (sender, receiver))
        return "sent"


def respond_to_request(sender, receiver, accept):
    """Return False if there was no pending request."""
    status = "accepted" if accept else "rejected"
    with _write_lock, connect() as conn, conn:
        cursor = conn.execute(
            "UPDATE requests SET status = ? WHERE sender = ? AND receiver = ? AND status = 'pending'",
            (status, sender, receiver),
        )
        return cursor.rowcount > 0


def get_requests(username):
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT sender FROM requests WHERE receiver = ? AND status = 'pending' ORDER BY sender",
            (username,),
        ).fetchall()
    return [row[0] for row in rows]


def get_friends(username):
    with connect() as conn:
        rows = conn.execute(
            "SELECT sender, receiver FROM requests WHERE (sender = ? OR receiver = ?) AND status = 'accepted'",
            (username, username),
        ).fetchall()
    return sorted({receiver if sender == username else sender for sender, receiver in rows})


def are_friends(user1, user2):
    return user2 in get_friends(user1)


def add_message(sender, receiver, message):
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    with _write_lock, connect() as conn, conn:
        conn.execute(
            "INSERT INTO messages (sender, receiver, message, timestamp) VALUES (?, ?, ?, ?)",
            (sender, receiver, message, timestamp),
        )
    return timestamp


def get_chat_history(user1, user2):
    with connect() as conn:
        return conn.execute(
            """
            SELECT sender, receiver, message, timestamp
            FROM messages
            WHERE (sender = ? AND receiver = ?) OR (sender = ? AND receiver = ?)
            ORDER BY timestamp, rowid
            """,
            (user1, user2, user2, user1),
        ).fetchall()
