import argparse
import json
import logging
import queue
import socket
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from tkinter import *
from tkinter import messagebox

import tls

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5557
DEFAULT_CAFILE = Path(__file__).with_name("cert.pem")
# The server pings every 20 s, so this much silence means the connection is dead.
SERVER_TIMEOUT = 60
RECONNECT_DELAYS = [1, 2, 4, 8, 15, 30]

log = logging.getLogger("client")


class ServerConnection:
    def __init__(self, host, port, cafile, events):
        context = tls.client_context(cafile)
        sock = socket.create_connection((host, port), timeout=5)
        self.sock = tls.LockedSocket(context.wrap_socket(sock, server_hostname=host))
        self.events = events
        # The reading thread answers pings, so sends come from two threads.
        self.send_lock = threading.Lock()
        self.last_seen = time.monotonic()

    def start(self):
        """Queue each message except pings as (self, message).

        When the connection ends, queue (self, {"type": "disconnected"}).
        """
        threading.Thread(target=self._read_loop, daemon=True).start()

    def send(self, type_, /, **fields):
        data = (json.dumps({"type": type_, **fields}) + "\n").encode()
        with self.send_lock:
            self.sock.sendall(data)

    def _read_loop(self):
        try:
            with self.sock.makefile() as reader:
                for line in reader:
                    self.last_seen = time.monotonic()
                    try:
                        message = json.loads(line)
                    except (ValueError, RecursionError):
                        continue
                    if not isinstance(message, dict):
                        continue
                    if message.get("type") == "ping":
                        self.send("pong")
                    else:
                        self.events.put((self, message))
        except (OSError, ValueError):
            pass
        self.events.put((self, {"type": "disconnected"}))

    def close(self):
        self.sock.close()


server = None
events = queue.Queue()
current_chat = None
current_user = None
members = []
pending = []
pending_list = None
signup_credentials = None
login_attempt = None
# Kept while logged in, to log in again after a dropped connection.
saved_login = None
# Changes in clear_app_data(), so a reconnect that was already running is ignored.
session = 0
reconnect_attempts = 0


def connect_to_server():
    global server
    if server:
        return True
    try:
        server = ServerConnection(args.host, args.port, args.cafile, events)
        server.start()
        return True
    except FileNotFoundError:
        messagebox.showerror("Connection Error", f"Certificate {args.cafile} not found. Ask the server owner for it.")
        return False
    except OSError as e:
        messagebox.showerror("Connection Error", f"Failed to connect to server: {e}")
        return False


def send(type_, /, **fields):
    if not server:
        if current_user:
            messagebox.showerror("Connection Error", "Reconnecting to the server. Try again in a moment.")
        else:
            messagebox.showerror("Connection Error", "Not connected to the server.")
        return
    try:
        server.send(type_, **fields)
    except OSError as e:
        messagebox.showerror("Connection Error", f"Failed to reach the server: {e}")


def poll_events():
    while True:
        try:
            conn, message = events.get_nowait()
        except queue.Empty:
            break
        try:
            if callable(message):
                # Work handed over from another thread.
                message()
                continue
            # Skip events left over from a closed connection.
            if conn is not server:
                continue
            handler = EVENT_HANDLERS.get(message.get("type"))
            if handler:
                handler(message)
        except Exception:
            # A bad event must not stop the event loop.
            log.exception("Error handling a server event")
    root.after(50, poll_events)


def check_server_alive():
    if server and time.monotonic() - server.last_seen > SERVER_TIMEOUT:
        log.warning("No pings from the server, reconnecting")
        # The reading thread then reports the connection as dropped.
        server.close()
    root.after(5000, check_server_alive)


def schedule_reconnect():
    global reconnect_attempts
    delay = RECONNECT_DELAYS[min(reconnect_attempts, len(RECONNECT_DELAYS) - 1)]
    reconnect_attempts += 1
    set_status(f"Lost connection. Reconnecting in {delay} s...")
    root.after(delay * 1000, start_reconnect, session)


def start_reconnect(for_session):
    if for_session != session:
        return
    set_status("Reconnecting...")

    def connect():
        try:
            conn = ServerConnection(args.host, args.port, args.cafile, events)
        except OSError as e:
            log.info("Reconnect failed: %s", e)
            conn = None
        events.put((None, lambda: finish_reconnect(for_session, conn)))

    # Connecting can take seconds, so keep it off the Tk thread.
    threading.Thread(target=connect, daemon=True).start()


def finish_reconnect(for_session, conn):
    global server, login_attempt
    if for_session != session or server:
        if conn:
            conn.close()
        return
    if not conn:
        schedule_reconnect()
        return
    server = conn
    server.start()
    login_attempt = saved_login
    send("login", username=saved_login[0], password=saved_login[1])


def clear_app_data():
    global server, current_chat, current_user, login_attempt, saved_login, session, reconnect_attempts
    if server:
        server.close()
        server = None
    current_chat = None
    current_user = None
    login_attempt = None
    saved_login = None
    session += 1
    reconnect_attempts = 0
    members.clear()
    pending.clear()
    refresh_pending_list()
    chat.delete(0, END)
    person.delete(0, END)
    chatframe.config(text="Chat:")
    root.title("Chat Room!")
    set_status("")
    show_login_required()


def show_login_required():
    login_required_frame.lift()


def hide_login_required():
    login_required_frame.lower()


def set_status(text):
    status_text.set(text)


def require_login():
    if not current_user:
        messagebox.showerror("Login Required", "Log in first.")
        return False
    return True


def show_profile():
    if current_user:
        messagebox.showinfo("Profile", f"Logged in as: {current_user}")


def credentials_dialog(title, button_text, on_submit):
    dialog = Toplevel(root)
    dialog.title(title)
    dialog.transient(root)
    dialog.resizable(False, False)

    Label(dialog, text="Username:").grid(row=0, column=0, padx=5, pady=5, sticky=E)
    Label(dialog, text="Password:").grid(row=1, column=0, padx=5, pady=5, sticky=E)

    username_entry = Entry(dialog)
    password_entry = Entry(dialog, show="*")
    username_entry.grid(row=0, column=1, padx=5, pady=5)
    password_entry.grid(row=1, column=1, padx=5, pady=5)
    username_entry.focus_set()

    def submit(event=None):
        username = username_entry.get().strip()
        password = password_entry.get()
        if not username or not password:
            messagebox.showerror("Error", "Username and password cannot be empty.", parent=dialog)
            return
        dialog.destroy()
        on_submit(username, password)

    Button(dialog, text=button_text, command=submit).grid(row=2, columnspan=2, pady=5)
    dialog.bind("<Return>", submit)


def signup():
    credentials_dialog("Sign Up", "Sign up!", signup_user)


def signup_user(username, password):
    global signup_credentials
    if current_user:
        messagebox.showerror("Sign Up", "Sign out first.")
        return
    if connect_to_server():
        signup_credentials = (username, password)
        send("signup", username=username, password=password)


def on_signup_result(message):
    global signup_credentials
    credentials, signup_credentials = signup_credentials, None
    if message.get("ok"):
        messagebox.showinfo("Sign Up Success", "User added successfully.")
        if credentials:
            login_user(*credentials)
    else:
        messagebox.showerror("Sign Up Failed", message.get("error", "Sign up failed."))


def login():
    credentials_dialog("Login", "Login!", login_user)


def login_user(username, password):
    global login_attempt
    if current_user:
        messagebox.showerror("Login", f"Already logged in as {current_user}. Sign out first.")
        return
    if connect_to_server():
        login_attempt = (username, password)
        send("login", username=username, password=password)


def on_login_result(message):
    global current_user, login_attempt, saved_login, reconnect_attempts
    credentials, login_attempt = login_attempt, None
    # Still logged in from before the connection dropped.
    reconnected = current_user is not None
    if not message.get("ok"):
        error = message.get("error", "Login failed.")
        if reconnected:
            clear_app_data()
            messagebox.showerror("Disconnected", f"Reconnected, but could not log in again: {error}")
        else:
            messagebox.showerror("Login Failed", error)
        return
    saved_login = credentials
    current_user = message["username"]
    reconnect_attempts = 0
    if reconnected:
        set_status("Reconnected.")
        if current_chat:
            send("history", username=current_chat)
        return
    root.title(f"Chat Room! - {current_user}")
    hide_login_required()
    text_input.focus_set()
    messagebox.showinfo("Login Success", "Login successful.")


def on_friends(message):
    members.clear()
    person.delete(0, END)
    for username in message.get("users", []):
        add_member(username)
    if current_chat in members:
        person.selection_set(members.index(current_chat))


def add_member(username):
    if username not in members:
        members.append(username)
        person.insert(END, username)


def on_friend_added(message):
    username = message["username"]
    add_member(username)
    if username in pending:
        pending.remove(username)
        refresh_pending_list()
    set_status(f"You and {username} are now friends.")


def on_pending(message):
    pending[:] = message.get("users", [])
    refresh_pending_list()
    if pending:
        set_status(f"Pending requests: {len(pending)}")


def on_request_received(message):
    username = message["username"]
    if username not in pending:
        pending.append(username)
        refresh_pending_list()
    set_status(f"New friend request from {username}. See Friends > Pending Requests.")


def on_request_rejected(message):
    messagebox.showinfo("Request Rejected", f"Your chat request to {message['username']} was rejected.")


def local_time(timestamp):
    try:
        utc = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        # astimezone() fails for very old or far-future dates.
        return utc.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OverflowError, OSError):
        return timestamp


def format_message(message):
    sender = "You" if message["sender"] == current_user else message["sender"]
    # Listbox rows can't show line breaks.
    text = " ".join(message["text"].splitlines())
    return f"{local_time(message['timestamp'])} {sender}: {text}"


def on_history(message):
    if message.get("username") != current_chat:
        return
    chat.delete(0, END)
    for item in message.get("messages", []):
        chat.insert(END, format_message(item))
    chat.yview(END)


def on_message(message):
    other = message["receiver"] if message["sender"] == current_user else message["sender"]
    if other == current_chat:
        chat.insert(END, format_message(message))
        chat.yview(END)
    else:
        set_status(f"New message from {other}.")


def on_info(message):
    set_status(message.get("text", ""))


def on_error(message):
    messagebox.showerror("Error", message.get("text", "Unknown error."))


def on_disconnected(message):
    global server
    server.close()
    server = None
    if current_user:
        schedule_reconnect()
    else:
        clear_app_data()


EVENT_HANDLERS = {
    "signup_result": on_signup_result,
    "login_result": on_login_result,
    "friends": on_friends,
    "friend_added": on_friend_added,
    "pending": on_pending,
    "request_received": on_request_received,
    "request_rejected": on_request_rejected,
    "history": on_history,
    "message": on_message,
    "info": on_info,
    "error": on_error,
    "disconnected": on_disconnected,
}


def select_member(event):
    global current_chat
    selection = person.curselection()
    if not selection:
        return
    current_chat = person.get(selection[0])
    chatframe.config(text=f"Chat: {current_chat}")
    chat.delete(0, END)
    send("history", username=current_chat)


def send_message(event=None):
    text = input_text.get()
    if not text.strip():
        return
    if not current_chat:
        messagebox.showerror("Send Error", "Pick a member to chat with first.")
        return
    send("message", to=current_chat, text=text)
    input_text.set("")


def find_user():
    if not require_login():
        return
    dialog = Toplevel(root)
    dialog.title("Find User")
    dialog.transient(root)
    dialog.resizable(False, False)

    Label(dialog, text="Username:").grid(row=0, column=0, padx=5, pady=5)
    username_entry = Entry(dialog)
    username_entry.grid(row=0, column=1, padx=5, pady=5)
    username_entry.focus_set()

    def submit(event=None):
        username = username_entry.get().strip()
        if not username:
            messagebox.showerror("Error", "Username cannot be empty.", parent=dialog)
            return
        dialog.destroy()
        send("friend_request", username=username)

    Button(dialog, text="Send request", command=submit).grid(row=1, columnspan=2, pady=5)
    dialog.bind("<Return>", submit)


def refresh_pending_list():
    if pending_list and pending_list.winfo_exists():
        pending_list.delete(0, END)
        for username in pending:
            pending_list.insert(END, username)


def pending_requests():
    global pending_list
    if not require_login():
        return
    if pending_list and pending_list.winfo_exists():
        pending_list.winfo_toplevel().lift()
        return

    dialog = Toplevel(root)
    dialog.title("Pending Requests")
    dialog.geometry("300x300")
    dialog.transient(root)

    Label(dialog, text="Pending Requests:").pack(pady=10)

    frame = Frame(dialog)
    frame.pack(fill=BOTH, expand=True, padx=10)
    scrollbar = Scrollbar(frame, orient=VERTICAL)
    pending_list = Listbox(frame, selectmode=SINGLE, exportselection=False, yscrollcommand=scrollbar.set)
    scrollbar.config(command=pending_list.yview)
    scrollbar.pack(side=RIGHT, fill=Y)
    pending_list.pack(side=LEFT, fill=BOTH, expand=True)

    def respond(accept):
        selection = pending_list.curselection()
        if not selection:
            messagebox.showerror("Error", "No request selected.", parent=dialog)
            return
        username = pending_list.get(selection[0])
        send("respond_request", username=username, accept=accept)
        if username in pending:
            pending.remove(username)
        refresh_pending_list()

    buttons = Frame(dialog)
    Button(buttons, text="Accept", command=lambda: respond(True), bg="green", fg="white", width=6).pack(
        side=LEFT, padx=5
    )
    Button(buttons, text="Reject", command=lambda: respond(False), bg="red", fg="white", width=6).pack(
        side=RIGHT, padx=5
    )
    buttons.pack(pady=10)

    refresh_pending_list()
    send("pending")


def exit_application():
    if (
        messagebox.askquestion("Exit Application", "Are you sure you want to exit the application?", icon="warning")
        == "yes"
    ):
        if server:
            server.close()
        root.destroy()


def sign_out():
    clear_app_data()


def info():
    messagebox.showinfo("Info", "Chat Room\nCreated by Amin Niaziardekani, Swapnaneel Sarkhel and Ajdin Buljko")


parser = argparse.ArgumentParser(description="Run the chat client.")
parser.add_argument("--host", default=DEFAULT_HOST)
parser.add_argument("--port", type=int, default=DEFAULT_PORT)
parser.add_argument(
    "--cafile", default=DEFAULT_CAFILE, help="server certificate to trust (default: cert.pem next to this script)"
)
args = parser.parse_args()
logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s")

root = Tk()
root.geometry("800x640")
root.minsize(600, 400)
root.title("Chat Room!")
root.protocol("WM_DELETE_WINDOW", exit_application)
root.columnconfigure(1, weight=1)
root.rowconfigure(0, weight=1)

menu = Menu(root)
root.config(menu=menu)

filemenu = Menu(menu, tearoff=0)
menu.add_cascade(label="File", menu=filemenu)
filemenu.add_command(label="Login", command=login)
filemenu.add_command(label="Sign up", command=signup)
filemenu.add_command(label="Sign Out", command=sign_out)
filemenu.add_separator()
filemenu.add_command(label="Exit", command=exit_application)

friendsmenu = Menu(menu, tearoff=0)
menu.add_cascade(label="Friends", menu=friendsmenu)
friendsmenu.add_command(label="Find User", command=find_user)
friendsmenu.add_command(label="Pending Requests", command=pending_requests)

infomenu = Menu(menu, tearoff=0)
menu.add_cascade(label="Info", menu=infomenu)
infomenu.add_command(label="Profile", command=show_profile)
infomenu.add_command(label="Info", command=info)

personframe = LabelFrame(root, text="Members:", font=("Arial", 15, "bold"), bg="white", fg="black", bd=5)
personframe.grid(row=0, column=0, rowspan=2, sticky=NSEW)

scroll2_y = Scrollbar(personframe, orient=VERTICAL)
person = Listbox(
    personframe,
    highlightthickness=0,
    selectbackground="deep sky blue",
    selectmode=SINGLE,
    font=("Arial", 15),
    bg="white",
    fg="green",
    bd=0,
    activestyle="none",
    exportselection=False,
    width=14,
    yscrollcommand=scroll2_y.set,
)
scroll2_y.config(command=person.yview)
scroll2_y.pack(side=RIGHT, fill=Y)
person.pack(side=LEFT, fill=BOTH, expand=True, padx=5, pady=5)
person.bind("<<ListboxSelect>>", select_member)

chatframe = LabelFrame(root, text="Chat:", font=("Arial", 15, "bold"), bg="white", fg="black", bd=5)
chatframe.grid(row=0, column=1, sticky=NSEW)

scroll1_y = Scrollbar(chatframe, orient=VERTICAL)
chat = Listbox(
    chatframe,
    highlightthickness=0,
    selectbackground="deep sky blue",
    selectmode=EXTENDED,
    font=("Arial", 15),
    bg="white",
    fg="green",
    bd=0,
    activestyle="none",
    exportselection=False,
    yscrollcommand=scroll1_y.set,
)
scroll1_y.config(command=chat.yview)
scroll1_y.pack(side=RIGHT, fill=Y)
chat.pack(side=LEFT, fill=BOTH, expand=True, padx=5, pady=5)

textframe = LabelFrame(root, bg="white", bd=5)
textframe.grid(row=1, column=1, sticky=EW)

input_text = StringVar()
text_input = Entry(textframe, font=("Arial", 15), textvariable=input_text)
text_input.pack(side=LEFT, fill=X, expand=True, padx=5, pady=5, ipady=12)
text_input.bind("<Return>", send_message)

send_button = Button(textframe, text="Send", bg="white", command=send_message)
send_button.pack(side=RIGHT, padx=5, pady=5, ipadx=5, ipady=10)

status_text = StringVar()
Label(root, textvariable=status_text, anchor=W).grid(row=2, column=0, columnspan=2, sticky=EW, padx=5)

login_required_frame = Frame(root, bg="grey")
Label(
    login_required_frame, text="Sign in or login required to continue", bg="grey", fg="white", font=("Arial", 20)
).place(relx=0.5, rely=0.5, anchor=CENTER)
login_required_frame.place(x=0, y=0, relwidth=1, relheight=1)

show_login_required()
root.after(50, poll_events)
root.after(5000, check_server_alive)
root.mainloop()
