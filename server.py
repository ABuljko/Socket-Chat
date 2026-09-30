"""Chat server. Each message is one JSON object per line, with a "type" field."""

import argparse
import json
import queue
import re
import socket
import ssl
import sys
import threading
from pathlib import Path

import chatroom_db as db
import tls

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5557
DEFAULT_CERT = Path(__file__).with_name("cert.pem")
DEFAULT_KEY = Path(__file__).with_name("key.pem")
MAX_LINE = 64 * 1024
MAX_QUEUED = 1000
MAX_MESSAGE = 2000
MIN_PASSWORD, MAX_PASSWORD = 8, 128
USERNAME_RE = re.compile(r"[A-Za-z0-9_.-]{1,32}")

clients = {}
clients_lock = threading.Lock()


class ProtocolError(Exception):
    """Drop the connection."""


class BadRequest(Exception):
    """Reply with an error and keep the connection."""


class Connection:
    """Sends from a bounded queue on its own thread, so a slow client can't block others."""

    def __init__(self, sock):
        self.sock = sock
        self.reader = sock.makefile()
        self.outbox = queue.Queue(MAX_QUEUED)
        self.writer = threading.Thread(target=self._write_loop, daemon=True)
        self.writer.start()
        self.username = None

    def send(self, type_, /, **fields):
        data = (json.dumps({"type": type_, **fields}) + "\n").encode()
        try:
            self.outbox.put_nowait(data)
        except queue.Full:
            self.shutdown()

    def _write_loop(self):
        while (data := self.outbox.get()) is not None:
            try:
                self.sock.sendall(data)
            except OSError:
                break
        self.shutdown()

    def finish(self):
        """Close after sending everything already queued."""
        try:
            self.outbox.put_nowait(None)
        except queue.Full:
            self.shutdown()

    def receive(self):
        while True:
            line = self.reader.readline(MAX_LINE)
            if not line:
                return None
            if not line.endswith("\n"):
                if len(line) >= MAX_LINE:
                    raise ProtocolError("line too long")
                return None
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except (ValueError, RecursionError):
                raise ProtocolError("invalid JSON") from None
            if not isinstance(message, dict):
                raise ProtocolError("expected a JSON object")
            return message

    def shutdown(self):
        # Wakes the reading thread, which then closes the connection.
        self.sock.shutdown()

    def close(self):
        self.finish()
        self.writer.join(timeout=5)
        self.shutdown()
        self.reader.close()
        self.sock.close()


def send_to(recipient, type_, /, **fields):
    with clients_lock:
        conn = clients.get(recipient)
    if conn:
        conn.send(type_, **fields)


def text_field(message, name):
    value = message.get(name)
    if not isinstance(value, str):
        raise BadRequest(f"Missing field: {name}.")
    try:
        value.encode()
    except UnicodeEncodeError:
        raise BadRequest(f"Invalid characters in {name}.") from None
    return value


def username_field(message, name="username"):
    username = text_field(message, name)
    if not USERNAME_RE.fullmatch(username):
        raise BadRequest("Usernames are 1 to 32 letters, digits, '.', '_' or '-'.")
    return username


def handle_signup(conn, message):
    username = username_field(message)
    password = text_field(message, "password")
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        raise BadRequest(f"Passwords are {MIN_PASSWORD} to {MAX_PASSWORD} characters.")
    if db.add_user(username, password):
        conn.send("signup_result", ok=True)
    else:
        conn.send("signup_result", ok=False, error="Username already exists.")


def handle_login(conn, message):
    if conn.username:
        raise BadRequest("Already logged in.")
    username = text_field(message, "username")
    password = text_field(message, "password")
    if not db.check_user(username, password):
        conn.send("login_result", ok=False, error="Invalid credentials.")
        return
    conn.username = username
    with clients_lock:
        previous = clients.get(username)
        clients[username] = conn
    if previous:
        previous.send("error", text="You logged in from another window.")
        previous.finish()
    conn.send("login_result", ok=True, username=username)
    conn.send("friends", users=db.get_friends(username))
    conn.send("pending", users=db.get_requests(username))


def handle_friend_request(conn, message):
    target = username_field(message)
    if target == conn.username:
        raise BadRequest("You cannot add yourself.")
    if not db.user_exists(target):
        raise BadRequest(f"User {target} not found.")
    result = db.add_request(conn.username, target)
    if result == "sent":
        conn.send("info", text=f"Request sent to {target}.")
        send_to(target, "request_received", username=conn.username)
    elif result == "pending":
        conn.send("info", text=f"You already sent {target} a request.")
    elif result == "friends":
        conn.send("info", text=f"You and {target} are already friends.")
    else:
        conn.send("friend_added", username=target)
        send_to(target, "friend_added", username=conn.username)


def handle_respond_request(conn, message):
    requester = username_field(message)
    accept = message.get("accept")
    if not isinstance(accept, bool):
        raise BadRequest("Missing field: accept.")
    if not db.respond_to_request(requester, conn.username, accept):
        raise BadRequest(f"No pending request from {requester}.")
    if accept:
        conn.send("friend_added", username=requester)
        send_to(requester, "friend_added", username=conn.username)
    else:
        send_to(requester, "request_rejected", username=conn.username)
    conn.send("pending", users=db.get_requests(conn.username))


def handle_pending(conn, message):
    conn.send("pending", users=db.get_requests(conn.username))


def require_friend(conn, other):
    if not db.are_friends(conn.username, other):
        raise BadRequest(f"You and {other} are not friends yet.")


def handle_history(conn, message):
    other = username_field(message)
    require_friend(conn, other)
    history = [
        {"sender": sender, "receiver": receiver, "text": text, "timestamp": timestamp}
        for sender, receiver, text, timestamp in db.get_chat_history(conn.username, other)
    ]
    conn.send("history", username=other, messages=history)


def handle_message(conn, message):
    receiver = username_field(message, "to")
    text = text_field(message, "text")
    if not text.strip():
        raise BadRequest("Message is empty.")
    if len(text) > MAX_MESSAGE:
        raise BadRequest(f"Messages are at most {MAX_MESSAGE} characters.")
    require_friend(conn, receiver)
    timestamp = db.add_message(conn.username, receiver, text)
    fields = {"sender": conn.username, "receiver": receiver, "text": text, "timestamp": timestamp}
    conn.send("message", **fields)
    if receiver != conn.username:
        send_to(receiver, "message", **fields)


GUEST_HANDLERS = {
    "signup": handle_signup,
    "login": handle_login,
}

USER_HANDLERS = {
    "friend_request": handle_friend_request,
    "respond_request": handle_respond_request,
    "pending": handle_pending,
    "history": handle_history,
    "message": handle_message,
}


def handle_client(sock, addr, context):
    try:
        sock.settimeout(tls.HANDSHAKE_TIMEOUT)
        sock = context.wrap_socket(sock, server_side=True)
    except OSError as e:
        print(f"TLS handshake with {addr} failed: {e}")
        sock.close()
        return
    conn = Connection(tls.LockedSocket(sock))
    try:
        while (message := conn.receive()) is not None:
            handlers = USER_HANDLERS if conn.username else GUEST_HANDLERS
            type_ = message.get("type")
            handler = handlers.get(type_) if isinstance(type_, str) else None
            if handler is None:
                conn.send("error", text="Invalid request.")
                continue
            try:
                handler(conn, message)
            except BadRequest as e:
                conn.send("error", text=str(e))
    except ProtocolError as e:
        conn.send("error", text=f"Protocol error: {e}.")
        print(f"Dropping {addr}: {e}")
    except OSError:
        pass
    except Exception as e:
        print(f"Error handling {addr}: {e!r}")
    finally:
        if conn.username:
            with clients_lock:
                if clients.get(conn.username) is conn:
                    del clients[conn.username]
        conn.close()
        print(f"Disconnected {addr}")


def start_server(context, host=DEFAULT_HOST, port=DEFAULT_PORT):
    db.init_db()
    with socket.create_server((host, port)) as server:
        print(f"Server listening on {host}:{port} (TLS)")
        while True:
            client_socket, addr = server.accept()
            print(f"Connection from {addr}")
            threading.Thread(target=handle_client, args=(client_socket, addr, context), daemon=True).start()


def main():
    parser = argparse.ArgumentParser(description="Run the chat server.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--db", help="SQLite database file (default: chatroom.db next to this script)")
    parser.add_argument("--cert", default=DEFAULT_CERT, help="TLS certificate (default: cert.pem next to this script)")
    parser.add_argument("--key", default=DEFAULT_KEY, help="TLS private key (default: key.pem next to this script)")
    args = parser.parse_args()
    if args.db:
        db.DB_PATH = args.db
    for path in (args.cert, args.key):
        if not Path(path).is_file():
            sys.exit(f"{path} not found. Run python make_cert.py to make a certificate.")
    try:
        context = tls.server_context(args.cert, args.key)
    except ssl.SSLError as e:
        sys.exit(f"Could not load the certificate: {e}")
    try:
        start_server(context, args.host, args.port)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
