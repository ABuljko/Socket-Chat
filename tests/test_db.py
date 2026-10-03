import re
import sqlite3
import threading
from contextlib import closing

import pytest

import chatroom_db as db


def make_users(*names):
    """Add users without paying for scrypt."""
    with db.connect() as conn, conn:
        conn.executemany("INSERT INTO users (username, password) VALUES (?, 'x')", [(name,) for name in names])


def test_password_hash_round_trip():
    stored = db.hash_password("correct horse")
    assert stored.startswith("scrypt$")
    assert db.verify_password("correct horse", stored)
    assert not db.verify_password("wrong horse", stored)


def test_password_hashes_are_salted():
    assert db.hash_password("same") != db.hash_password("same")


def test_verify_rejects_unhashed_or_missing_values():
    assert not db.verify_password("plain", "plain")
    assert not db.verify_password("x", "")
    assert not db.verify_password("x", None)


def test_add_and_check_user(db_path):
    assert db.add_user("alice", "password1")
    assert not db.add_user("alice", "other-password")
    assert db.check_user("alice", "password1")
    assert not db.check_user("alice", "other-password")
    assert not db.check_user("nobody", "password1")
    assert db.user_exists("alice")
    assert not db.user_exists("nobody")


def test_unknown_user_still_runs_scrypt(db_path, monkeypatch):
    db.add_user("alice", "password1")
    calls = []
    real_verify = db.verify_password

    def spy(password, stored):
        calls.append(stored)
        return real_verify(password, stored)

    monkeypatch.setattr(db, "verify_password", spy)
    assert not db.check_user("nobody", "password1")
    assert not db.check_user("alice", "wrong-password")
    assert len(calls) == 2
    assert calls[0].split("$")[:4] == calls[1].split("$")[:4]


def test_friend_request_lifecycle(db_path):
    make_users("alice", "bob")
    assert db.add_request("alice", "bob") == "sent"
    assert db.add_request("alice", "bob") == "pending"
    assert db.get_requests("bob") == ["alice"]
    assert db.get_requests("alice") == []

    assert db.respond_to_request("alice", "bob", accept=True)
    assert not db.respond_to_request("alice", "bob", accept=True)
    assert db.get_friends("alice") == ["bob"]
    assert db.get_friends("bob") == ["alice"]
    assert db.are_friends("alice", "bob")
    assert db.add_request("bob", "alice") == "friends"


def test_crossed_requests_become_friends(db_path):
    make_users("alice", "bob")
    assert db.add_request("alice", "bob") == "sent"
    assert db.add_request("bob", "alice") == "accepted"
    assert db.are_friends("alice", "bob")


def test_rejected_request_can_be_sent_again(db_path):
    make_users("alice", "bob")
    db.add_request("alice", "bob")
    assert db.respond_to_request("alice", "bob", accept=False)
    assert not db.are_friends("alice", "bob")
    assert db.get_requests("bob") == []
    assert db.add_request("alice", "bob") == "sent"
    assert db.get_requests("bob") == ["alice"]


def test_responding_without_a_request_does_nothing(db_path):
    assert not db.respond_to_request("alice", "bob", accept=True)
    assert db.get_friends("bob") == []


def test_chat_history_keeps_order_and_content(db_path):
    make_users("alice", "bob", "carol")
    texts = ["first", "second | with pipe", "third\nwith newline", "fourth {[$x]}"]
    for i, text in enumerate(texts):
        sender, receiver = ("alice", "bob") if i % 2 == 0 else ("bob", "alice")
        timestamp = db.add_message(sender, receiver, text)
        assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", timestamp)
    db.add_message("alice", "carol", "not in this chat")

    history = db.get_chat_history("bob", "alice")
    assert [row[2] for row in history] == texts
    assert history[0][:2] == ("alice", "bob")


def test_concurrent_writes_do_not_wait_on_sqlite(db_path, monkeypatch):
    # No busy timeout, so any wait on SQLite's lock fails. Slow CI disks made the real timeout run out.
    monkeypatch.setattr(db, "connect", lambda: closing(sqlite3.connect(db_path, timeout=0)))
    errors = []

    def work(i):
        try:
            for n in range(25):
                db.add_message(f"u{i}", f"v{i}", "x" * 1900)
                db.add_request(f"u{i}", f"w{n}")
        except sqlite3.OperationalError as e:
            errors.append(repr(e))

    threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(db.get_chat_history("u0", "v0")) == 25
    assert db.get_requests("w0") == [f"u{i}" for i in range(8)]


def test_init_db_is_idempotent_and_reset_clears(db_path):
    db.add_user("alice", "password1")
    db.init_db()
    assert db.user_exists("alice")
    db.reset_db()
    assert not db.user_exists("alice")


def test_new_database_is_at_the_latest_version(db_path):
    assert db.schema_version() == len(db.MIGRATIONS)
    db.init_db()
    assert db.schema_version() == len(db.MIGRATIONS)


@pytest.mark.parametrize(
    "sql, values",
    [
        ("INSERT INTO users (username, password) VALUES (?, ?)", ("bob", None)),
        ("INSERT INTO messages (sender, receiver, message) VALUES (?, ?, ?)", ("alice", "bob", None)),
        ("INSERT INTO messages (sender, receiver, message) VALUES (?, ?, ?)", ("alice", "nobody", "hi")),
        ("INSERT INTO requests (sender, receiver, status) VALUES (?, ?, ?)", ("alice", "nobody", "pending")),
        ("INSERT INTO requests (sender, receiver, status) VALUES (?, ?, ?)", ("alice", "alice", "pending")),
        ("INSERT INTO requests (sender, receiver, status) VALUES (?, ?, ?)", ("alice", "bob", "maybe")),
        ("INSERT INTO requests (sender, receiver, status) VALUES (?, ?, ?)", ("alice", "bob", "rejected")),
    ],
    ids=[
        "null-password",
        "null-message",
        "message-to-missing-user",
        "request-to-missing-user",
        "self-request",
        "bad-status",
        "duplicate-request",
    ],
)
def test_schema_rejects_bad_rows(db_path, sql, values):
    make_users("alice", "bob")
    db.add_request("alice", "bob")
    with pytest.raises(sqlite3.IntegrityError), db.connect() as conn:
        conn.execute(sql, values)


def test_deleting_a_user_removes_their_rows(db_path):
    make_users("alice", "bob")
    db.add_request("alice", "bob")
    db.add_message("alice", "bob", "hi")
    with db.connect() as conn, conn:
        conn.execute("DELETE FROM users WHERE username = 'bob'")
    assert db.get_friends("alice") == []
    assert db.get_chat_history("alice", "bob") == []


def test_old_database_is_migrated(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript("""
            CREATE TABLE users (username TEXT PRIMARY KEY, password TEXT);
            CREATE TABLE messages (sender TEXT, receiver TEXT, message TEXT,
                                   timestamp DATETIME DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE requests (sender TEXT, receiver TEXT, status TEXT);
            CREATE INDEX messages_pair ON messages (sender, receiver);
            CREATE INDEX requests_pair ON requests (sender, receiver);
            INSERT INTO users VALUES ('alice', 'a'), ('bob', 'b'), ('carol', 'c'), ('dave', 'd'), ('eve', NULL);
            INSERT INTO messages VALUES
                ('alice', 'bob', 'first', '2024-01-01 10:00:00'),
                ('bob', 'alice', 'second', '2024-01-01 10:00:00'),
                ('alice', 'bob', NULL, '2024-01-01 10:01:00'),
                ('alice', 'ghost', 'to nobody', '2024-01-01 10:02:00'),
                ('eve', 'alice', 'no password', '2024-01-01 10:03:00');
            INSERT INTO requests VALUES
                ('alice', 'bob', 'accepted'),
                ('alice', 'bob', 'pending'),
                ('carol', 'alice', 'rejected'),
                ('carol', 'alice', 'pending'),
                ('dave', 'dave', 'pending'),
                ('dave', 'ghost', 'pending'),
                ('dave', 'alice', 'maybe'),
                ('bob', 'dave', 'pending');
        """)
    db.init_db()

    assert db.schema_version() == len(db.MIGRATIONS)
    assert db.check_user("eve", "anything") is False
    assert not db.user_exists("eve")
    assert [(s, r, m) for s, r, m, _ in db.get_chat_history("alice", "bob")] == [
        ("alice", "bob", "first"),
        ("bob", "alice", "second"),
    ]
    assert db.get_friends("alice") == ["bob"]
    assert db.get_requests("alice") == ["carol"]
    assert db.get_requests("dave") == ["bob"]
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM requests").fetchone()[0] == 3
        assert conn.execute("SELECT count(*) FROM messages").fetchone()[0] == 2


def test_failed_migration_changes_nothing(db_path, monkeypatch):
    make_users("alice")
    monkeypatch.setattr(db, "MIGRATIONS", [*db.MIGRATIONS, ["DROP TABLE users", "SELECT * FROM missing"]])
    with pytest.raises(sqlite3.OperationalError):
        db.init_db()
    assert db.schema_version() == len(db.MIGRATIONS) - 1
    assert db.user_exists("alice")


def test_newer_database_is_refused(db_path):
    with db.connect() as conn:
        conn.execute(f"PRAGMA user_version = {len(db.MIGRATIONS) + 1}")
    with pytest.raises(RuntimeError, match="newer"):
        db.init_db()
