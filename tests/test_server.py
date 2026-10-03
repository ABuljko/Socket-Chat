import argparse
import json
import subprocess
import sys
import time

import pytest
from conftest import REPO

import server as chat_server


def friends(server, first="alice", second="bob"):
    a = server().register(first)
    b = server().register(second)
    a.send("friend_request", username=second)
    a.expect("info")
    b.expect("request_received")
    b.send("respond_request", username=first, accept=True)
    b.expect("friend_added")
    b.expect("pending")
    a.expect("friend_added")
    return a, b


def test_signup_then_login_on_same_connection(server):
    c = server()
    c.send("signup", username="alice", password="password1")
    assert c.expect("signup_result") == {"type": "signup_result", "ok": True}
    c.send("signup", username="alice", password="password1")
    assert c.expect("signup_result")["ok"] is False
    c.send("login", username="alice", password="wrong-password")
    assert c.expect("login_result")["ok"] is False
    c.send("login", username="alice", password="password1")
    assert c.expect("login_result") == {"type": "login_result", "ok": True, "username": "alice"}
    assert c.expect("friends")["users"] == []
    assert c.expect("pending")["users"] == []


def test_signup_validation(server):
    c = server()
    for username, password in [
        ("alice", "short"),
        ("bad name", "password1"),
        ("x" * 33, "password1"),
        ("", "password1"),
    ]:
        c.send("signup", username=username, password=password)
        c.expect("error")
    c.send("signup", username="alice")
    assert "password" in c.expect("error")["text"]


def test_guests_cannot_use_chat_commands(server):
    c = server()
    for type_ in ["message", "history", "friend_request", "pending"]:
        c.send(type_, username="bob", to="bob", text="hi")
        assert c.expect("error")["text"] == "Invalid request."


def test_friend_request_accept_and_chat(server):
    a, b = friends(server)
    text = "hello | with pipes\nand a newline"
    a.send("message", to="bob", text=text)
    echoed = a.expect("message")
    delivered = b.expect("message")
    assert echoed == delivered
    assert delivered["sender"] == "alice" and delivered["text"] == text

    b.send("history", username="alice")
    history = b.expect("history")
    assert history["username"] == "alice"
    assert [m["text"] for m in history["messages"]] == [text]


def test_many_messages_arrive_separately_and_in_order(server):
    a, b = friends(server)
    for i in range(50):
        a.send("message", to="bob", text=f"m{i}")
    assert [b.expect("message")["text"] for _ in range(50)] == [f"m{i}" for i in range(50)]


def test_friend_request_rules(server):
    a = server().register("alice")
    b = server().register("bob")
    a.send("friend_request", username="alice")
    a.expect("error")
    a.send("friend_request", username="nobody")
    a.expect("error")
    a.send("respond_request", username="bob", accept=True)
    a.expect("error")
    b.quiet()

    a.send("friend_request", username="bob")
    a.expect("info")
    b.expect("request_received")
    a.send("friend_request", username="bob")
    assert "already" in a.expect("info")["text"]


def test_rejection_is_reported_to_sender(server):
    a = server().register("alice")
    b = server().register("bob")
    a.send("friend_request", username="bob")
    a.expect("info")
    b.expect("request_received")
    b.send("respond_request", username="alice", accept=False)
    assert b.expect("pending")["users"] == []
    assert a.expect("request_rejected")["username"] == "bob"


def test_only_friends_can_chat(server):
    a = server().register("alice")
    server().register("bob")
    a.send("message", to="bob", text="hi")
    a.expect("error")
    a.send("history", username="bob")
    a.expect("error")


def test_message_validation(server):
    a, b = friends(server)
    for text in ["   ", "x" * 2001, 5, None, "bad \ud800"]:
        a.send("message", to="bob", text=text)
        a.expect("error")
    b.quiet()


def test_second_login_replaces_first(server):
    a, b = friends(server)
    again = server()
    again.send("login", username="alice", password="password1")
    again.expect("login_result")
    assert again.expect("friends")["users"] == ["bob"]
    again.expect("pending")
    frames = a.wait_closed()
    assert frames and frames[0]["type"] == "error"

    b.send("message", to="alice", text="still there?")
    b.expect("message")
    assert again.expect("message")["text"] == "still there?"


def test_invalid_json_closes_connection_with_error(server):
    for payload in [
        b"not json\n",
        b"[1, 2]\n",
        b"\xff\xfe{}\n",
        b'{"n": ' + b"9" * 5000 + b"}\n",
        b"[" * 20000 + b"]" * 20000 + b"\n",
    ]:
        c = server()
        c.send_raw(payload)
        frames = c.wait_closed()
        assert frames == [] or frames[0]["type"] == "error", frames


def test_odd_but_valid_input_keeps_connection(server):
    c = server()
    c.send_raw(b'{"type": ["login"]}\n')
    c.expect("error")
    c.send_raw(b"\n\n")
    c.send_raw((json.dumps({"type": "login", "username": None, "password": []}) + "\n").encode())
    c.expect("error")
    c.send("signup", username="alice", password="password1")
    assert c.expect("signup_result")["ok"]


def wrong_logins(c, username, times=chat_server.LOGIN_FREE_FAILURES):
    for _ in range(times):
        c.send("login", username=username, password="wrong-password")
        assert c.expect("login_result")["error"] == "Invalid credentials."


def test_unknown_user_and_wrong_password_look_the_same(server):
    server().register("alice")
    c = server()
    for username in ["alice", "nobody"]:
        c.send("login", username=username, password="wrong-password")
        assert c.expect("login_result") == {"type": "login_result", "ok": False, "error": "Invalid credentials."}


def test_failed_logins_lock_the_username(server):
    server().register("alice")
    c = server()
    wrong_logins(c, "alice")
    c.send("login", username="alice", password="password1")
    result = c.expect("login_result")
    assert result["ok"] is False and result["error"].startswith("Too many failed logins.")


def test_failed_logins_for_any_names_lock_the_ip(server):
    server().register("alice")
    c = server()
    for i in range(chat_server.LOGIN_FREE_FAILURES):
        wrong_logins(c, f"nobody{i}", times=1)
    c.send("login", username="alice", password="password1")
    assert c.expect("login_result")["error"].startswith("Too many failed logins.")


def test_successful_logins_do_not_lock_the_ip(server):
    server().register("alice")
    for _ in range(chat_server.LOGIN_FREE_FAILURES + 2):
        c = server()
        c.send("login", username="alice", password="password1")
        assert c.expect("login_result")["ok"]
        c.close()


def test_login_validation(server):
    c = server()
    for username, password in [("bad name", "password1"), ("x" * 33, "password1"), ("alice", "x" * 129)]:
        c.send("login", username=username, password=password)
        c.expect("error")


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def throttle(clock):
    return chat_server.LoginThrottle(clock)


def fail(throttle, host, username, times=1):
    for _ in range(times):
        assert throttle.begin(host, username) == 0


def test_throttle_wait_doubles_up_to_the_cap(throttle, clock):
    fail(throttle, "1.1.1.1", "alice", chat_server.LOGIN_FREE_FAILURES)
    waits = []
    for _ in range(10):
        waits.append(throttle.begin("1.1.1.1", "alice"))
        clock.now += waits[-1]
        fail(throttle, "1.1.1.1", "alice")
    assert waits == [1, 2, 4, 8, 16, 32, 60, 60, 60, 60]


def test_throttle_locks_a_username_across_ips(throttle):
    for i in range(chat_server.LOGIN_FREE_FAILURES):
        fail(throttle, f"10.0.0.{i}", "alice")
    assert throttle.begin("10.0.0.99", "alice") > 0
    assert throttle.begin("10.0.0.99", "bob") == 0


def test_throttle_counts_attempts_still_in_progress(throttle):
    for _ in range(chat_server.LOGIN_FREE_FAILURES):
        assert throttle.begin("1.1.1.1", "alice") == 0
    assert throttle.begin("1.1.1.1", "alice") > 0


def test_throttle_success_clears_the_username_but_not_other_ip_failures(throttle):
    fail(throttle, "1.1.1.1", "nobody", chat_server.LOGIN_FREE_FAILURES - 1)
    fail(throttle, "1.1.1.1", "alice")
    throttle.succeeded("1.1.1.1", "alice")
    assert ("user", "alice") not in throttle.failures
    fail(throttle, "1.1.1.1", "bob")
    assert throttle.begin("1.1.1.1", "carol") > 0


def test_throttle_forgets_old_failures(throttle, clock):
    fail(throttle, "1.1.1.1", "alice", chat_server.LOGIN_FREE_FAILURES)
    clock.now += chat_server.LOGIN_FORGET
    assert throttle.begin("1.1.1.1", "alice") == 0


def test_throttle_prunes_stale_entries(throttle, clock, monkeypatch):
    monkeypatch.setattr(chat_server, "LOGIN_MAX_TRACKED", 4)
    for i in range(3):
        fail(throttle, "1.1.1.1", f"user{i}")
    clock.now += chat_server.LOGIN_FORGET
    fail(throttle, "2.2.2.2", "fresh")
    fail(throttle, "2.2.2.2", "fresh")
    assert set(throttle.failures) == {("ip", "2.2.2.2"), ("user", "fresh")}


FAST_HEARTBEAT = pytest.mark.server_args("--ping-interval", "0.2", "--idle-timeout", "1")


@FAST_HEARTBEAT
def test_server_sends_pings(server):
    c = server()
    c.answer_pings = False
    c.expect("ping", timeout=2)


@FAST_HEARTBEAT
def test_silent_client_is_dropped(server):
    a = server().register("alice")
    a.answer_pings = False
    start = time.monotonic()
    a.wait_closed(timeout=5)
    assert time.monotonic() - start < 3
    again = server()
    again.send("login", username="alice", password="password1")
    assert again.expect("login_result")["ok"]


@FAST_HEARTBEAT
def test_client_that_answers_pings_stays_connected(server):
    c = server().register("alice")
    c.quiet(timeout=2)
    c.send("pending")
    assert c.expect("pending")["users"] == []


@FAST_HEARTBEAT
def test_half_sent_line_does_not_count_as_alive(server):
    c = server()
    c.answer_pings = False
    deadline = time.monotonic() + 3
    try:
        while time.monotonic() < deadline:
            c.send_raw(b" ")
            time.sleep(0.1)
    except OSError:
        pass
    assert all(frame["type"] == "ping" for frame in c.wait_closed(timeout=5))


@pytest.mark.parametrize("text", ["-1", "0", "nan", "inf", "soon"])
def test_heartbeat_settings_must_be_positive_numbers(text):
    with pytest.raises((argparse.ArgumentTypeError, ValueError)):
        chat_server.seconds(text)


@pytest.mark.parametrize(
    "args", [["--ping-interval", "61", "--idle-timeout", "100"], ["--ping-interval", "5", "--idle-timeout", "2"]]
)
def test_server_refuses_heartbeat_settings_that_cannot_work(args):
    result = subprocess.run(
        [sys.executable, str(REPO / "server.py"), *args], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 2, result.stderr
