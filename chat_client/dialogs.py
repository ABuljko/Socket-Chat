import tkinter as tk
from tkinter import messagebox


def _dialog(parent, title):
    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.transient(parent)
    dialog.resizable(False, False)
    return dialog


def ask_credentials(parent, title, button_text, on_submit):
    dialog = _dialog(parent, title)
    tk.Label(dialog, text="Username:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.E)
    tk.Label(dialog, text="Password:").grid(row=1, column=0, padx=5, pady=5, sticky=tk.E)
    username_entry = tk.Entry(dialog)
    password_entry = tk.Entry(dialog, show="*")
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

    tk.Button(dialog, text=button_text, command=submit).grid(row=2, columnspan=2, pady=5)
    dialog.bind("<Return>", submit)


def ask_username(parent, on_submit):
    dialog = _dialog(parent, "Find User")
    tk.Label(dialog, text="Username:").grid(row=0, column=0, padx=5, pady=5)
    username_entry = tk.Entry(dialog)
    username_entry.grid(row=0, column=1, padx=5, pady=5)
    username_entry.focus_set()

    def submit(event=None):
        username = username_entry.get().strip()
        if not username:
            messagebox.showerror("Error", "Username cannot be empty.", parent=dialog)
            return
        dialog.destroy()
        on_submit(username)

    tk.Button(dialog, text="Send request", command=submit).grid(row=1, columnspan=2, pady=5)
    dialog.bind("<Return>", submit)


class PendingDialog:
    """Lists friend requests. on_respond(username, accept) is called for Accept and Reject."""

    def __init__(self, parent, on_respond):
        self.on_respond = on_respond
        self.window = tk.Toplevel(parent)
        self.window.title("Pending Requests")
        self.window.geometry("300x300")
        self.window.transient(parent)

        tk.Label(self.window, text="Pending Requests:").pack(pady=10)
        frame = tk.Frame(self.window)
        frame.pack(fill=tk.BOTH, expand=True, padx=10)
        scrollbar = tk.Scrollbar(frame, orient=tk.VERTICAL)
        self.list = tk.Listbox(frame, selectmode=tk.SINGLE, exportselection=False, yscrollcommand=scrollbar.set)
        scrollbar.config(command=self.list.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        buttons = tk.Frame(self.window)
        tk.Button(buttons, text="Accept", command=lambda: self.respond(True), bg="green", fg="white", width=6).pack(
            side=tk.LEFT, padx=5
        )
        tk.Button(buttons, text="Reject", command=lambda: self.respond(False), bg="red", fg="white", width=6).pack(
            side=tk.RIGHT, padx=5
        )
        buttons.pack(pady=10)

    def is_open(self):
        return bool(self.window.winfo_exists())

    def lift(self):
        self.window.lift()

    def set_users(self, users):
        self.list.delete(0, tk.END)
        for username in users:
            self.list.insert(tk.END, username)

    def respond(self, accept):
        selection = self.list.curselection()
        if not selection:
            messagebox.showerror("Error", "No request selected.", parent=self.window)
            return
        self.on_respond(self.list.get(selection[0]), accept)
