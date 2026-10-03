import logging
import queue
import threading
import time
from datetime import UTC, datetime

log = logging.getLogger("client")

# The server pings every 20 s, so this much silence means the connection is dead.
SERVER_TIMEOUT = 60
RECONNECT_DELAYS = [1, 2, 4, 8, 15, 30]
POLL_MS = 50
ALIVE_CHECK_MS = 5000


def local_time(timestamp):
    try:
        utc = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        # astimezone() fails for very old or far-future dates.
        return utc.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OverflowError, OSError):
        return timestamp


def run_in_thread(func):
    threading.Thread(target=func, daemon=True).start()


class ChatApp:
    """connect(events) must return a connection that isn't reading yet."""

    def __init__(self, make_view, connect, run_in_background=run_in_thread):
        self.connect = connect
        self.run_in_background = run_in_background
        self.events = queue.Queue()
        self.server = None
        self.current_user = None
        self.current_chat = None
        self.members = []
        self.pending = []
        self.signup_credentials = None
        self.login_attempt = None
        # Kept while logged in, to log in again after a dropped connection.
        self.saved_login = None
        # Changes on every reset, so a reconnect that was already running is ignored.
        self.session = 0
        self.reconnect_attempts = 0
        self.handlers = {
            "signup_result": self.on_signup_result,
            "login_result": self.on_login_result,
            "friends": self.on_friends,
            "friend_added": self.on_friend_added,
            "pending": self.on_pending,
            "request_received": self.on_request_received,
            "request_rejected": self.on_request_rejected,
            "history": self.on_history,
            "message": self.on_message,
            "info": self.on_info,
            "error": self.on_error,
            "disconnected": self.on_disconnected,
        }
        self.view = make_view(self)

    def start(self):
        self.view.set_logged_in(None)
        self.view.after(POLL_MS, self.poll_events)
        self.view.after(ALIVE_CHECK_MS, self.check_server_alive)

    def connect_to_server(self):
        if self.server:
            return True
        try:
            self.server = self.connect(self.events)
        except FileNotFoundError as e:
            self.view.show_error(
                "Connection Error", f"Certificate {e.filename} not found. Ask the server owner for it."
            )
            return False
        except OSError as e:
            self.view.show_error("Connection Error", f"Failed to connect to server: {e}")
            return False
        self.server.start()
        return True

    def send(self, type_, /, **fields):
        if not self.server:
            if self.current_user:
                self.view.show_error("Connection Error", "Reconnecting to the server. Try again in a moment.")
            else:
                self.view.show_error("Connection Error", "Not connected to the server.")
            return
        try:
            self.server.send(type_, **fields)
        except OSError as e:
            self.view.show_error("Connection Error", f"Failed to reach the server: {e}")

    def process_events(self):
        while True:
            try:
                conn, message = self.events.get_nowait()
            except queue.Empty:
                return
            try:
                if callable(message):
                    # Work handed over from another thread.
                    message()
                    continue
                # Skip events left over from a closed connection.
                if conn is not self.server:
                    continue
                handler = self.handlers.get(message.get("type"))
                if handler:
                    handler(message)
            except Exception:
                # A bad event must not stop the event loop.
                log.exception("Error handling a server event")

    def poll_events(self):
        self.process_events()
        self.view.after(POLL_MS, self.poll_events)

    def check_server_alive(self):
        if self.server and time.monotonic() - self.server.last_seen > SERVER_TIMEOUT:
            log.warning("No pings from the server, reconnecting")
            # The reading thread then reports the connection as dropped.
            self.server.close()
        self.view.after(ALIVE_CHECK_MS, self.check_server_alive)

    def schedule_reconnect(self):
        delay = RECONNECT_DELAYS[min(self.reconnect_attempts, len(RECONNECT_DELAYS) - 1)]
        self.reconnect_attempts += 1
        self.view.set_status(f"Lost connection. Reconnecting in {delay} s...")
        self.view.after(delay * 1000, self.start_reconnect, self.session)

    def start_reconnect(self, session):
        if session != self.session:
            return
        self.view.set_status("Reconnecting...")

        def connect():
            try:
                conn = self.connect(self.events)
            except OSError as e:
                log.info("Reconnect failed: %s", e)
                conn = None
            self.events.put((None, lambda: self.finish_reconnect(session, conn)))

        # Connecting can take seconds, so keep it off the UI thread.
        self.run_in_background(connect)

    def finish_reconnect(self, session, conn):
        if session != self.session or self.server:
            if conn:
                conn.close()
            return
        if not conn:
            self.schedule_reconnect()
            return
        self.server = conn
        self.server.start()
        self.login_attempt = self.saved_login
        username, password = self.saved_login
        self.send("login", username=username, password=password)

    def reset(self):
        if self.server:
            self.server.close()
            self.server = None
        self.current_user = None
        self.current_chat = None
        self.login_attempt = None
        self.saved_login = None
        self.session += 1
        self.reconnect_attempts = 0
        self.members.clear()
        self.pending.clear()
        self.view.set_pending(self.pending)
        self.view.set_members(self.members, None)
        self.view.set_chat(None, [])
        self.view.set_status("")
        self.view.set_logged_in(None)

    def require_login(self):
        if not self.current_user:
            self.view.show_error("Login Required", "Log in first.")
            return False
        return True

    def signup(self):
        self.view.ask_credentials("Sign Up", "Sign up!", self.signup_user)

    def signup_user(self, username, password):
        if self.current_user:
            self.view.show_error("Sign Up", "Sign out first.")
            return
        if self.connect_to_server():
            self.signup_credentials = (username, password)
            self.send("signup", username=username, password=password)

    def login(self):
        self.view.ask_credentials("Login", "Login!", self.login_user)

    def login_user(self, username, password):
        if self.current_user:
            self.view.show_error("Login", f"Already logged in as {self.current_user}. Sign out first.")
            return
        if self.connect_to_server():
            self.login_attempt = (username, password)
            self.send("login", username=username, password=password)

    def sign_out(self):
        self.reset()

    def exit(self):
        if self.view.confirm("Exit Application", "Are you sure you want to exit the application?"):
            if self.server:
                self.server.close()
            self.view.close()

    def show_profile(self):
        if self.current_user:
            self.view.show_info("Profile", f"Logged in as: {self.current_user}")

    def show_about(self):
        self.view.show_info("Info", "Chat Room\nCreated by Amin Niaziardekani, Swapnaneel Sarkhel and Ajdin Buljko")

    def find_user(self):
        if self.require_login():
            self.view.ask_username(self.send_friend_request)

    def send_friend_request(self, username):
        self.send("friend_request", username=username)

    def open_pending(self):
        if self.require_login():
            self.view.show_pending(self.pending, self.respond_request)
            self.send("pending")

    def respond_request(self, username, accept):
        self.send("respond_request", username=username, accept=accept)
        if username in self.pending:
            self.pending.remove(username)
        self.view.set_pending(self.pending)

    def select_chat(self, username):
        self.current_chat = username
        self.view.set_chat(username, [])
        self.send("history", username=username)

    def send_message(self, text):
        """Return True if the input can be cleared."""
        if not text.strip():
            return False
        if not self.current_chat:
            self.view.show_error("Send Error", "Pick a member to chat with first.")
            return False
        self.send("message", to=self.current_chat, text=text)
        return True

    def format_message(self, message):
        sender = "You" if message["sender"] == self.current_user else message["sender"]
        # List rows can't show line breaks.
        text = " ".join(message["text"].splitlines())
        return f"{local_time(message['timestamp'])} {sender}: {text}"

    def on_signup_result(self, message):
        credentials, self.signup_credentials = self.signup_credentials, None
        if message.get("ok"):
            self.view.show_info("Sign Up Success", "User added successfully.")
            if credentials:
                self.login_user(*credentials)
        else:
            self.view.show_error("Sign Up Failed", message.get("error", "Sign up failed."))

    def on_login_result(self, message):
        credentials, self.login_attempt = self.login_attempt, None
        # Still logged in from before the connection dropped.
        reconnected = self.current_user is not None
        if not message.get("ok"):
            error = message.get("error", "Login failed.")
            if reconnected:
                self.reset()
                self.view.show_error("Disconnected", f"Reconnected, but could not log in again: {error}")
            else:
                self.view.show_error("Login Failed", error)
            return
        self.saved_login = credentials
        self.current_user = message["username"]
        self.reconnect_attempts = 0
        if reconnected:
            self.view.set_status("Reconnected.")
            if self.current_chat:
                self.send("history", username=self.current_chat)
            return
        self.view.set_logged_in(self.current_user)
        self.view.show_info("Login Success", "Login successful.")

    def on_friends(self, message):
        self.members[:] = message.get("users", [])
        self.view.set_members(self.members, self.current_chat)

    def add_member(self, username):
        if username not in self.members:
            self.members.append(username)
            self.view.set_members(self.members, self.current_chat)

    def on_friend_added(self, message):
        username = message["username"]
        self.add_member(username)
        if username in self.pending:
            self.pending.remove(username)
            self.view.set_pending(self.pending)
        self.view.set_status(f"You and {username} are now friends.")

    def on_pending(self, message):
        self.pending[:] = message.get("users", [])
        self.view.set_pending(self.pending)
        if self.pending:
            self.view.set_status(f"Pending requests: {len(self.pending)}")

    def on_request_received(self, message):
        username = message["username"]
        if username not in self.pending:
            self.pending.append(username)
            self.view.set_pending(self.pending)
        self.view.set_status(f"New friend request from {username}. See Friends > Pending Requests.")

    def on_request_rejected(self, message):
        self.view.show_info("Request Rejected", f"Your chat request to {message['username']} was rejected.")

    def on_history(self, message):
        if message.get("username") != self.current_chat:
            return
        self.view.set_chat(self.current_chat, [self.format_message(item) for item in message.get("messages", [])])

    def on_message(self, message):
        other = message["receiver"] if message["sender"] == self.current_user else message["sender"]
        if other == self.current_chat:
            self.view.add_chat_line(self.format_message(message))
        else:
            self.view.set_status(f"New message from {other}.")

    def on_info(self, message):
        self.view.set_status(message.get("text", ""))

    def on_error(self, message):
        self.view.show_error("Error", message.get("text", "Unknown error."))

    def on_disconnected(self, message):
        self.server.close()
        self.server = None
        if self.current_user:
            self.schedule_reconnect()
        else:
            self.reset()
