import hashlib
import hmac
import os
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

DB_PATH = os.environ.get("CHATROOM_DB", str(Path(__file__).with_name("chatroom.db")))

# Each entry takes the database up one version, which is kept in PRAGMA user_version.
# Never change an entry once it has shipped. Add a new one instead.
MIGRATIONS = [
    # 1: NOT NULLs, foreign keys, one request per sender and receiver. Old rows that break these are dropped.
    [
        # The tables as they were before versioning, so the steps below always have something to copy.
        "CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password TEXT)",
        "CREATE TABLE IF NOT EXISTS messages (sender TEXT, receiver TEXT, message TEXT, timestamp DATETIME)",
        "CREATE TABLE IF NOT EXISTS requests (sender TEXT, receiver TEXT, status TEXT)",
        "ALTER TABLE users RENAME TO old_users",
        "ALTER TABLE messages RENAME TO old_messages",
        "ALTER TABLE requests RENAME TO old_requests",
        """
        CREATE TABLE users (
            username TEXT PRIMARY KEY NOT NULL,
            password TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE messages (
            sender TEXT NOT NULL REFERENCES users (username) ON DELETE CASCADE,
            receiver TEXT NOT NULL REFERENCES users (username) ON DELETE CASCADE,
            message TEXT NOT NULL,
            timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE requests (
            sender TEXT NOT NULL REFERENCES users (username) ON DELETE CASCADE,
            receiver TEXT NOT NULL REFERENCES users (username) ON DELETE CASCADE,
            status TEXT NOT NULL CHECK (status IN ('pending', 'accepted', 'rejected')),
            UNIQUE (sender, receiver),
            CHECK (sender <> receiver)
        )
        """,
        """
        INSERT INTO users (username, password)
        SELECT username, password FROM old_users
        WHERE username IS NOT NULL AND password IS NOT NULL
        """,
        """
        INSERT INTO messages (sender, receiver, message, timestamp)
        SELECT sender, receiver, message, timestamp FROM old_messages
        WHERE sender IN (SELECT username FROM users)
            AND receiver IN (SELECT username FROM users)
            AND message IS NOT NULL
            AND timestamp IS NOT NULL
        ORDER BY rowid
        """,
        # Of duplicate requests, keep an accepted one, else the newest.
        """
        INSERT INTO requests (sender, receiver, status)
        SELECT sender, receiver, status FROM old_requests AS o
        WHERE rowid = (
                SELECT rowid FROM old_requests
                WHERE sender = o.sender AND receiver = o.receiver AND status IN ('pending', 'accepted', 'rejected')
                ORDER BY status = 'accepted' DESC, rowid DESC
                LIMIT 1
            )
            AND sender <> receiver
            AND sender IN (SELECT username FROM users)
            AND receiver IN (SELECT username FROM users)
        """,
        "DROP TABLE old_users",
        "DROP TABLE old_messages",
        "DROP TABLE old_requests",
        "CREATE INDEX messages_pair ON messages (sender, receiver)",
        "CREATE INDEX requests_receiver ON requests (receiver)",
    ],
]

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1

# Run writes one at a time. Waiting on SQLite's own lock can time out when commits are slow.
# Request updates also read, then write, so this keeps them consistent.
_write_lock = threading.Lock()


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    # SQLite checks foreign keys only on connections that turn them on.
    conn.execute("PRAGMA foreign_keys = ON")
    return closing(conn)


def schema_version():
    with connect() as conn:
        return conn.execute("PRAGMA user_version").fetchone()[0]


def init_db():
    # A plain connection: foreign keys stay off while tables are rebuilt, and the transaction is ours to run.
    with closing(sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > len(MIGRATIONS):
                raise RuntimeError(f"{DB_PATH} is schema version {version}, newer than this app knows.")
            for number, statements in enumerate(MIGRATIONS[version:], start=version + 1):
                for statement in statements:
                    conn.execute(statement)
                conn.execute(f"PRAGMA user_version = {number}")
                if conn.execute("PRAGMA foreign_key_check").fetchone():
                    raise RuntimeError(f"Migration {number} left rows pointing at missing users.")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise


def reset_db():
    with closing(sqlite3.connect(DB_PATH, timeout=10)) as conn:
        conn.executescript("""
            DROP TABLE IF EXISTS messages;
            DROP TABLE IF EXISTS requests;
            DROP TABLE IF EXISTS users;
            PRAGMA user_version = 0;
        """)
    init_db()


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


# Checked when the user doesn't exist, so that takes as long as a wrong password.
_DUMMY_HASH = hash_password("not a real password")


def check_user(username, password):
    with connect() as conn:
        row = conn.execute("SELECT password FROM users WHERE username = ?", (username,)).fetchone()
    if row is None:
        verify_password(password, _DUMMY_HASH)
        return False
    return verify_password(password, row[0])


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
        conn.execute(
            """
            INSERT INTO requests (sender, receiver, status) VALUES (?, ?, 'pending')
            ON CONFLICT (sender, receiver) DO UPDATE SET status = 'pending'
            """,
            (sender, receiver),
        )
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
            "SELECT sender FROM requests WHERE receiver = ? AND status = 'pending' ORDER BY sender",
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
