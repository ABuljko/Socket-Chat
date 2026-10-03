import time

import pytest

from chat_client import app as client_app
from chat_client.app import ChatApp


class FakeView:
    def __init__(self):
        self.scheduled = []
        self.errors = []
        self.infos = []
        self.status = ""
        self.logged_in = None
        self.members = []
        self.selected = None
        self.chat = (None, [])
        self.pending = None
        self.credentials_prompt = None
        self.confirm_answer = True
        self.closed = False

    def run_scheduled(self, name):
        """Run the scheduled calls to the method with this name, and return their delays."""
        due = [item for item in self.scheduled if item[1].__name__ == name]
        for item in due:
            self.scheduled.remove(item)
        for _, func, args in due:
            func(*args)
        return [ms for ms, _, _ in due]

    def after(self, ms, func, *args):
        self.scheduled.append((ms, func, args))

    def close(self):
        self.closed = True

    def show_error(self, title, text):
        self.errors.append((title, text))

    def show_info(self, title, text):
        self.infos.append((title, text))

    def confirm(self, title, text):
        return self.confirm_answer

    def ask_credentials(self, title, button_text, on_submit):
        self.credentials_prompt = (title, on_submit)

    def ask_username(self, on_submit):
        on_submit("bob")

    def show_pending(self, users, on_respond):
        self.pending = list(users)

    def set_pending(self, users):
        if self.pending is not None:
            self.pending = list(users)

    def set_status(self, text):
        self.status = text

    def set_logged_in(self, username):
        self.logged_in = username

    def set_members(self, users, selected):
        self.members = list(users)
        self.selected = selected if selected in users else None

    def set_chat(self, username, lines):
        self.chat = (username, list(lines))

    def add_chat_line(self, line):
        self.chat[1].append(line)


class FakeConnection:
    def __init__(self, events):
        self.events = events
        self.sent = []
        self.started = False
        self.closed = False
        self.last_seen = time.monotonic()

    def start(self):
        self.started = True

    def send(self, type_, /, **fields):
        if self.closed:
            raise OSError("connection closed")
        self.sent.append({"type": type_, **fields})

    def close(self):
        self.closed = True

    def receive(self, type_, /, **fields):
        self.events.put((self, {"type": type_, **fields}))

    def drop(self):
        self.events.put((self, {"type": "disconnected"}))


class Connector:
    def __init__(self):
        self.connections = []
        self.error = None

    def __call__(self, events):
        if self.error:
            raise self.error
        conn = FakeConnection(events)
        self.connections.append(conn)
        return conn


@pytest.fixture
def connector():
    return Connector()


@pytest.fixture
def app(connector):
    chat = ChatApp(lambda app: FakeView(), connector, run_in_background=lambda func: func())
    chat.start()
    return chat


def log_in(app, connector, username="alice", friends=("bob",)):
    app.login_user(username, "password1")
    conn = connector.connections[-1]
    conn.receive("login_result", ok=True, username=username)
    conn.receive("friends", users=list(friends))
    conn.receive("pending", users=[])
    app.process_events()
    return conn


def test_login(app, connector):
    app.login()
    assert app.view.credentials_prompt[0] == "Login"
    app.view.credentials_prompt[1]("alice", "password1")
    conn = connector.connections[0]
    assert conn.started
    assert conn.sent == [{"type": "login", "username": "alice", "password": "password1"}]
    conn.receive("login_result", ok=True, username="alice")
    conn.receive("friends", users=["bob", "carol"])
    app.process_events()
    assert app.view.logged_in == "alice"
    assert app.view.members == ["bob", "carol"]
    assert app.view.infos == [("Login Success", "Login successful.")]


def test_failed_login_stays_logged_out(app, connector):
    app.login_user("alice", "wrong-password")
    connector.connections[0].receive("login_result", ok=False, error="Invalid credentials.")
    app.process_events()
    assert app.view.logged_in is None
    assert app.view.errors == [("Login Failed", "Invalid credentials.")]
    assert app.saved_login is None


def test_signup_then_logs_in(app, connector):
    app.signup_user("alice", "password1")
    conn = connector.connections[0]
    conn.receive("signup_result", ok=True)
    app.process_events()
    assert conn.sent[-1] == {"type": "login", "username": "alice", "password": "password1"}


@pytest.mark.parametrize(
    "error, text",
    [
        (FileNotFoundError(2, "certificate not found", "cert.pem"), "Certificate cert.pem not found."),
        (ConnectionRefusedError("refused"), "Failed to connect to server: refused"),
    ],
)
def test_connect_errors_are_shown(app, connector, error, text):
    connector.error = error
    app.login_user("alice", "password1")
    assert app.view.errors[0][1].startswith(text)
    assert app.server is None


def test_chat_history_and_messages(app, connector):
    conn = log_in(app, connector)
    app.select_chat("bob")
    assert conn.sent[-1] == {"type": "history", "username": "bob"}
    message = {"sender": "alice", "receiver": "bob", "text": "hi\nthere", "timestamp": "not a time"}
    conn.receive("history", username="bob", messages=[message])
    conn.receive("message", sender="bob", receiver="alice", text="yo", timestamp="t")
    conn.receive("message", sender="carol", receiver="alice", text="psst", timestamp="t")
    app.process_events()
    assert app.view.chat == ("bob", ["not a time You: hi there", "t bob: yo"])
    assert app.view.status == "New message from carol."


def test_send_message(app, connector):
    conn = log_in(app, connector)
    assert not app.send_message("   ")
    assert not app.send_message("hi")
    assert app.view.errors == [("Send Error", "Pick a member to chat with first.")]
    app.select_chat("bob")
    assert app.send_message("hi")
    assert conn.sent[-1] == {"type": "message", "to": "bob", "text": "hi"}


def test_friend_requests(app, connector):
    conn = log_in(app, connector, friends=())
    app.find_user()
    assert conn.sent[-1] == {"type": "friend_request", "username": "bob"}
    conn.receive("request_received", username="carol")
    conn.receive("request_received", username="dave")
    app.process_events()
    app.open_pending()
    assert app.view.pending == ["carol", "dave"]
    app.respond_request("carol", True)
    assert conn.sent[-1] == {"type": "respond_request", "username": "carol", "accept": True}
    assert app.view.pending == ["dave"]
    conn.receive("friend_added", username="dave")
    app.process_events()
    assert app.view.pending == []
    assert app.view.members == ["dave"]


def test_guest_commands_need_login(app):
    app.find_user()
    app.open_pending()
    assert app.view.errors == [("Login Required", "Log in first.")] * 2


def test_events_from_an_old_connection_are_ignored(app, connector):
    old = log_in(app, connector)
    app.sign_out()
    old.receive("message", sender="bob", receiver="alice", text="late", timestamp="t")
    app.process_events()
    assert app.view.status == ""


def test_a_bad_event_does_not_stop_the_rest(app, connector):
    conn = log_in(app, connector)
    conn.receive("message", text="no sender")
    conn.receive("info", text="still working")
    app.process_events()
    assert app.view.status == "still working"


def test_reconnects_and_logs_in_again(app, connector):
    old = log_in(app, connector)
    app.select_chat("bob")
    old.drop()
    app.process_events()
    assert old.closed and app.server is None
    assert app.view.logged_in == "alice"
    assert app.view.status == "Lost connection. Reconnecting in 1 s..."
    assert app.send_message("lost?") is False
    assert app.view.errors[-1] == ("Connection Error", "Reconnecting to the server. Try again in a moment.")

    assert app.view.run_scheduled("start_reconnect") == [1000]
    app.process_events()
    new = connector.connections[-1]
    assert new is not old and new.started
    assert new.sent == [{"type": "login", "username": "alice", "password": "password1"}]
    new.receive("login_result", ok=True, username="alice")
    new.receive("friends", users=["bob"])
    app.process_events()
    assert app.view.status == "Reconnected."
    assert new.sent[-1] == {"type": "history", "username": "bob"}
    assert app.view.selected == "bob"
    assert app.view.infos == [("Login Success", "Login successful.")]
    assert app.reconnect_attempts == 0


def test_reconnect_backs_off(app, connector):
    log_in(app, connector).drop()
    app.process_events()
    connector.error = ConnectionRefusedError("refused")
    delays = []
    for _ in range(8):
        delays += app.view.run_scheduled("start_reconnect")
        app.process_events()
    assert delays == [ms * 1000 for ms in client_app.RECONNECT_DELAYS] + [30_000, 30_000]


def test_sign_out_stops_reconnecting(app, connector):
    log_in(app, connector).drop()
    app.process_events()
    app.sign_out()
    count = len(connector.connections)
    app.view.run_scheduled("start_reconnect")
    app.process_events()
    assert len(connector.connections) == count
    assert app.server is None


def test_sign_out_while_connecting_drops_the_new_connection(app, connector):
    log_in(app, connector).drop()
    app.process_events()
    app.view.run_scheduled("start_reconnect")
    app.sign_out()
    app.process_events()
    assert connector.connections[-1].closed
    assert app.server is None


def test_failed_login_after_reconnect_signs_out(app, connector):
    log_in(app, connector).drop()
    app.process_events()
    app.view.run_scheduled("start_reconnect")
    app.process_events()
    connector.connections[-1].receive("login_result", ok=False, error="Too many failed logins.")
    app.process_events()
    assert app.view.logged_in is None and app.current_user is None
    assert app.view.errors[-1] == ("Disconnected", "Reconnected, but could not log in again: Too many failed logins.")


def test_guest_disconnect_does_not_reconnect(app, connector):
    app.login_user("alice", "password1")
    connector.connections[0].drop()
    app.process_events()
    assert app.server is None
    assert not app.view.run_scheduled("start_reconnect")


def test_silent_server_is_dropped(app, connector):
    conn = log_in(app, connector)
    app.view.run_scheduled("check_server_alive")
    assert not conn.closed
    conn.last_seen -= client_app.SERVER_TIMEOUT + 1
    app.view.run_scheduled("check_server_alive")
    assert conn.closed


def test_exit_asks_first(app, connector):
    conn = log_in(app, connector)
    app.view.confirm_answer = False
    app.exit()
    assert not app.view.closed
    app.view.confirm_answer = True
    app.exit()
    assert app.view.closed and conn.closed


def test_second_login_waits_for_the_first(app, connector):
    app.login_user("alice", "alice-password")
    app.login_user("bob", "bob-password")
    app.signup_user("carol", "carol-password")
    assert app.view.errors == [("Please Wait", "Still waiting for the server to answer.")] * 2
    conn = connector.connections[0]
    assert [m["type"] for m in conn.sent] == ["login"]
    conn.receive("login_result", ok=True, username="alice")
    app.process_events()
    assert app.current_user == "alice"
    assert app.saved_login == ("alice", "alice-password")


def test_login_result_nobody_asked_for_is_ignored(app, connector):
    app.signup_user("alice", "password1")
    connector.connections[0].receive("login_result", ok=True, username="mallory")
    app.process_events()
    assert app.current_user is None


def test_drop_while_logging_in_is_reported(app, connector):
    app.login_user("alice", "password1")
    connector.connections[0].drop()
    app.process_events()
    assert app.view.errors == [("Disconnected", "Lost connection to the server.")]
    app.login_user("alice", "password1")
    assert len(connector.connections) == 2
