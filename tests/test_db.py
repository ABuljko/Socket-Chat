import re
import sqlite3
import threading
from contextlib import closing

import chatroom_db as db


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


def test_friend_request_lifecycle(db_path):
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
    assert db.add_request("alice", "bob") == "sent"
    assert db.add_request("bob", "alice") == "accepted"
    assert db.are_friends("alice", "bob")


def test_rejected_request_can_be_sent_again(db_path):
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
