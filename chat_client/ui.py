import tkinter as tk
from tkinter import messagebox

from chat_client import dialogs

TITLE = "Chat Room!"
LIST_STYLE = {
    "highlightthickness": 0,
    "selectbackground": "deep sky blue",
    "font": ("Arial", 15),
    "bg": "white",
    "fg": "green",
    "bd": 0,
    "activestyle": "none",
    "exportselection": False,
}
FRAME_STYLE = {"font": ("Arial", 15, "bold"), "bg": "white", "fg": "black", "bd": 5}


class MainWindow:
    def __init__(self, root, app):
        self.root = root
        self.app = app
        self.pending_dialog = None

        root.geometry("800x640")
        root.minsize(600, 400)
        root.title(TITLE)
        root.protocol("WM_DELETE_WINDOW", app.exit)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)
        self._build_menu()

        members_frame = tk.LabelFrame(root, text="Members:", **FRAME_STYLE)
        members_frame.grid(row=0, column=0, rowspan=2, sticky=tk.NSEW)
        self.members = self._scrolled_list(members_frame, selectmode=tk.SINGLE, width=14)
        self.members.bind("<<ListboxSelect>>", self._on_member_selected)

        self.chat_frame = tk.LabelFrame(root, text="Chat:", **FRAME_STYLE)
        self.chat_frame.grid(row=0, column=1, sticky=tk.NSEW)
        self.chat = self._scrolled_list(self.chat_frame, selectmode=tk.EXTENDED)

        text_frame = tk.LabelFrame(root, bg="white", bd=5)
        text_frame.grid(row=1, column=1, sticky=tk.EW)
        self.input_text = tk.StringVar()
        self.input = tk.Entry(text_frame, font=("Arial", 15), textvariable=self.input_text)
        self.input.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5, pady=5, ipady=12)
        self.input.bind("<Return>", self._on_send)
        tk.Button(text_frame, text="Send", bg="white", command=self._on_send).pack(
            side=tk.RIGHT, padx=5, pady=5, ipadx=5, ipady=10
        )

        self.status_text = tk.StringVar()
        tk.Label(root, textvariable=self.status_text, anchor=tk.W).grid(
            row=2, column=0, columnspan=2, sticky=tk.EW, padx=5
        )

        self.login_required = tk.Frame(root, bg="grey")
        tk.Label(
            self.login_required, text="Sign in or login required to continue", bg="grey", fg="white", font=("Arial", 20)
        ).place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        self.login_required.place(x=0, y=0, relwidth=1, relheight=1)

    def _build_menu(self):
        menu = tk.Menu(self.root)
        self.root.config(menu=menu)
        for label, items in [
            (
                "File",
                [
                    ("Login", self.app.login),
                    ("Sign up", self.app.signup),
                    ("Sign Out", self.app.sign_out),
                    None,
                    ("Exit", self.app.exit),
                ],
            ),
            ("Friends", [("Find User", self.app.find_user), ("Pending Requests", self.app.open_pending)]),
            ("Info", [("Profile", self.app.show_profile), ("Info", self.app.show_about)]),
        ]:
            submenu = tk.Menu(menu, tearoff=0)
            menu.add_cascade(label=label, menu=submenu)
            for item in items:
                if item is None:
                    submenu.add_separator()
                else:
                    submenu.add_command(label=item[0], command=item[1])

    def _scrolled_list(self, parent, **options):
        scrollbar = tk.Scrollbar(parent, orient=tk.VERTICAL)
        listbox = tk.Listbox(parent, yscrollcommand=scrollbar.set, **LIST_STYLE, **options)
        scrollbar.config(command=listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        return listbox

    def _on_member_selected(self, event):
        selection = self.members.curselection()
        if selection:
            self.app.select_chat(self.members.get(selection[0]))

    def _on_send(self, event=None):
        if self.app.send_message(self.input_text.get()):
            self.input_text.set("")

    def after(self, ms, func, *args):
        self.root.after(ms, func, *args)

    def close(self):
        self.root.destroy()

    def show_error(self, title, text):
        messagebox.showerror(title, text)

    def show_info(self, title, text):
        messagebox.showinfo(title, text)

    def confirm(self, title, text):
        return messagebox.askquestion(title, text, icon="warning") == "yes"

    def ask_credentials(self, title, button_text, on_submit):
        dialogs.ask_credentials(self.root, title, button_text, on_submit)

    def ask_username(self, on_submit):
        dialogs.ask_username(self.root, on_submit)

    def show_pending(self, users, on_respond):
        if self.pending_dialog and self.pending_dialog.is_open():
            self.pending_dialog.lift()
        else:
            self.pending_dialog = dialogs.PendingDialog(self.root, on_respond)
        self.pending_dialog.set_users(users)

    def set_pending(self, users):
        if self.pending_dialog and self.pending_dialog.is_open():
            self.pending_dialog.set_users(users)

    def set_status(self, text):
        self.status_text.set(text)

    def set_logged_in(self, username):
        if username:
            self.root.title(f"{TITLE} - {username}")
            self.login_required.lower()
            self.input.focus_set()
        else:
            self.root.title(TITLE)
            self.login_required.lift()

    def set_members(self, users, selected):
        self.members.delete(0, tk.END)
        for username in users:
            self.members.insert(tk.END, username)
        if selected in users:
            self.members.selection_set(users.index(selected))

    def set_chat(self, username, lines):
        self.chat_frame.config(text=f"Chat: {username}" if username else "Chat:")
        self.chat.delete(0, tk.END)
        for line in lines:
            self.chat.insert(tk.END, line)
        self.chat.yview(tk.END)

    def add_chat_line(self, line):
        self.chat.insert(tk.END, line)
        self.chat.yview(tk.END)
