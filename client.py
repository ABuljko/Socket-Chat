from tkinter import *
from tkinter import messagebox
import socket
import threading

client_socket = None
current_chat = None
members = []
current_user = None
pending_requests_cache = ""

def connect_to_server():
    global client_socket
    try:
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_socket.connect(('127.0.0.1', 5557))
    except Exception as e:
        messagebox.showerror("Connection Error", f"Failed to connect to server: {e}")
        root.destroy()

def clear_app_data():
    global client_socket, current_chat, members, current_user
    current_chat = None
    members.clear()
    current_user = None
    chat.delete(0, END)
    person.delete(0, END)
    root.title("Chat Room!")
    show_login_required()

def show_login_required():
    login_required_frame.lift()

def hide_login_required():
    login_required_frame.lower()

def update_title_with_username(username):
    root.title(f"Chat Room! - {username}")

def show_profile():
    if current_user:
        messagebox.showinfo("Profile", f"Logged in as: {current_user}")

def signup():
    signup_dialog = Toplevel(root)
    signup_dialog.title("Sign Up")

    Label(signup_dialog, text="Username:").grid(row=0, column=0)
    Label(signup_dialog, text="Password:").grid(row=1, column=0)

    username_entry = Entry(signup_dialog)
    password_entry = Entry(signup_dialog, show="*")
    username_entry.grid(row=0, column=1)
    password_entry.grid(row=1, column=1)

    def submit():
        username = username_entry.get()
        password = password_entry.get()
        if username and password:
            signup_user(username, password)
            signup_dialog.destroy()
        else:
            messagebox.showerror("Error", "Username and password cannot be empty.")

    submit_button = Button(signup_dialog, text="Sign up!", command=submit)
    submit_button.grid(row=2, columnspan=2)

def signup_user(username, password):
    global client_socket
    try:
        client_socket.send(f"SIGNUP {username} {password}".encode())
        response = client_socket.recv(1024).decode()
        if response == 'User added successfully.\n':
            login_user(username, password)
            messagebox.showinfo("Sign Up Success", "User added successfully.")
        else:
            messagebox.showerror("Sign Up Failed", response)
    except Exception as e:
        messagebox.showerror("Sign Up Error", f"An error occurred during sign up: {e}")

def login():
    login_dialog = Toplevel(root)
    login_dialog.title("Login")

    Label(login_dialog, text="Username:").grid(row=0, column=0)
    Label(login_dialog, text="Password:").grid(row=1, column=0)

    username_entry = Entry(login_dialog)
    password_entry = Entry(login_dialog, show="*")
    username_entry.grid(row=0, column=1)
    password_entry.grid(row=1, column=1)

    def submit():
        username = username_entry.get()
        password = password_entry.get()
        if username and password:
            login_user(username, password)
            login_dialog.destroy()
        else:
            messagebox.showerror("Error", "Username and password cannot be empty.")

    submit_button = Button(login_dialog, text="Login!", command=submit)
    submit_button.grid(row=2, columnspan=2)

def login_user(username, password):
    global client_socket, current_user, pending_requests_cache
    try:
        client_socket.send(f"LOGIN {username} {password}".encode())
        response = client_socket.recv(1024).decode().strip()
        if response == 'Login successful.':
            current_user = username
            update_title_with_username(username)
            hide_login_required()
            messagebox.showinfo("Login Success", "Login successful.")
            receive_initial_data(client_socket)
            client_socket.send("PENDING".encode())
            pending_response = client_socket.recv(1024).decode().strip()
            if pending_response.startswith("No pending requests"):
                pending_requests_cache = ""
            elif pending_response.startswith("Pending requests:\n"):
                pending_requests_cache = pending_response.split("Pending requests:\n")[1]
            else:
                pending_requests_cache = ""
            receive_thread = threading.Thread(target=receive_messages, args=(client_socket,))
            receive_thread.daemon = True
            receive_thread.start()
        else:
            messagebox.showerror("Login Failed", response)
    except Exception as e:
        messagebox.showerror("Login Error", f"An error occurred during login: {e}")

def receive_initial_data(client_socket):
    try:
        client_socket.send("ACCEPTED".encode())
        accepted_response = client_socket.recv(1024).decode().strip()
        if accepted_response.startswith("Accepted requests:\n"):
            accepted_requests = accepted_response.split("Accepted requests:\n")[1]
            for requestor in accepted_requests.split("\n"):
                if requestor:
                    add_member(requestor)
    except Exception as e:
        print(f"Error in receive_initial_data: {e}")

def add_member(username):
    try:
        if username not in members:
            members.append(username)
            person.insert(END, username)
    except Exception as e:
        print(f"Error in add_member: {e}")

def receive_messages(client_socket):
    global pending_requests_cache
    global current_chat
    global current_user
    while True:
        try:
            message = client_socket.recv(1024).decode().strip()
            if message.startswith("REQUEST_ACCEPTED"):
                parts = message.split()
                if len(parts) == 2:
                    _, accepted_user = parts
                    add_member(accepted_user)
            elif message.startswith("ADD_MEMBER"):
                parts = message.split()
                if len(parts) == 2:
                    _, member = parts
                    add_member(member)
            elif message.startswith("CHAT_HISTORY"):
                lines = message.split('\n')
                for line in lines:
                    parts = line.split(' ', 4)
                    if len(parts) == 5:
                        _, sender, receiver, msg, timestamp = parts
                        if current_chat == sender or current_chat == receiver:
                            chat.insert(END, f"{timestamp} {sender}: {msg}")
            elif message.startswith("REQUEST_REJECTED"):
                parts = message.split()
                if len(parts) == 2:
                    _, rejected_user = parts
                    messagebox.showinfo("Request Rejected", f"Your chat request to {rejected_user} was rejected.")
            elif message.startswith("Pending requests"):
                pending_requests_cache = message.split("Pending requests:\n")[1]
            elif message:
                chat.insert(END, message)
                chat.yview(END)
            else:
                break
        except Exception as e:
            print(f"Error in receive_messages: {e}")
            break


def select_member(event):
    global current_chat
    try:
        selected_member = person.get(ACTIVE)
        current_chat = selected_member
    except Exception as e:
        messagebox.showerror("Selection Error", f"Failed to select member: {e}")

def open_chat():
    global current_chat
    username = current_chat
    if current_chat:
        load_chat_history(username)

def send_message(event=None):
    global client_socket, current_chat
    message = input_text.get()
    if message and current_chat:
        try:
            client_socket.send(f"{current_chat} {message}".encode())
            chat.insert(END, f"You: {message}")
            input_text.set("")
        except Exception as e:
            messagebox.showerror("Send Error", f"Failed to send message: {e}")

def find_user():
    find_dialog = Toplevel(root)
    find_dialog.title("Find User")

    Label(find_dialog, text="Username:").grid(row=0, column=0)

    username_entry = Entry(find_dialog)
    username_entry.grid(row=0, column=1)

    def submit():
        username = username_entry.get()
        if username:
            send_chat_request(username)
            find_dialog.destroy()
        else:
            messagebox.showerror("Error", "Username cannot be empty.")

    submit_button = Button(find_dialog, text="Find", command=submit)
    submit_button.grid(row=1, columnspan=2)

def send_chat_request(username):
    global client_socket
    try:
        client_socket.send(f"SEARCH {username}".encode())
        messagebox.showinfo("Request Sent", "Chat request sent.")
    except Exception as e:
        messagebox.showerror("Search Error", f"An error occurred during search: {e}")

def show_and_fetch_pending_requests(initial_requests=None):
    pending_dialog = Toplevel(root)
    pending_dialog.title("Pending Requests")
    pending_dialog.geometry("300x300")

    Label(pending_dialog, text="Pending Requests:").grid(row=0, column=0, columnspan=2, pady=10)

    frame = Frame(pending_dialog)
    frame.grid(row=1, column=0, columnspan=2, padx=10, pady=10)

    scrollbar = Scrollbar(frame, orient=VERTICAL)
    pending_list = Listbox(frame, selectmode=SINGLE, yscrollcommand=scrollbar.set)
    scrollbar.config(command=pending_list.yview)
    scrollbar.pack(side=RIGHT, fill=Y)
    pending_list.pack(side=LEFT, fill=BOTH, expand=1)

    def accept_request():
        selected_request = pending_list.get(ACTIVE)
        if selected_request:
            client_socket.send(f"ACCEPT {selected_request}".encode())
            pending_list.delete(ACTIVE)
        else:
            messagebox.showerror("Error", "No request selected.")

    def reject_request():
        selected_request = pending_list.get(ACTIVE)
        if selected_request:
            client_socket.send(f"REJECT {selected_request}".encode())
            pending_list.delete(ACTIVE)
        else:
            messagebox.showerror("Error", "No request selected.")

    frame_buttons = Frame(pending_dialog)
    accept_button = Button(frame_buttons, text="Accept", command=accept_request, bg="green", fg="white", width=6)
    reject_button = Button(frame_buttons, text="Reject", command=reject_request, bg="red", fg="white", width=6)
    accept_button.pack(side=LEFT, padx=5, pady=5)
    reject_button.pack(side=RIGHT, padx=5, pady=5)
    frame_buttons.grid(row=2, column=0, columnspan=2, pady=10)

    if initial_requests:
        request_list = initial_requests.split("\n")
        for request in request_list:
            if request:
                pending_list.insert(END, request)

    elif initial_requests is None:
        try:
            client_socket.send("PENDING".encode())
            response = client_socket.recv(1024).decode()
            if response.startswith("No pending requests"):
                pass
            elif response.startswith("Pending requests:\n"):
                requests = response.split("Pending requests:\n")[1]
                request_list = requests.split("\n")
                for request in request_list:
                    if request:
                        pending_list.insert(END, request)
            else:
                messagebox.showerror("Pending Requests Error", "Unexpected response from server.")
        except Exception as e:
            messagebox.showerror("Pending Requests Error", f"An error occurred while fetching pending requests: {e}")

def pending_requests():
    show_and_fetch_pending_requests(pending_requests_cache)

def load_chat_history(username):
    global client_socket
    chat.delete(0, END)
    try:
        client_socket.send(f"HISTORY {username}".encode())
    except Exception as e:
        messagebox.showerror("Load Error", f"Failed to load chat history: {e}")

def exit_application():
    msg_box = messagebox.askquestion("Exit Application", "Are you sure you want to exit the application?", icon="warning")
    if msg_box == "yes":
        root.destroy()
    else:
        pass

def sign_out():
    global client_socket
    if client_socket:
        client_socket.close()
    clear_app_data()
    connect_to_server()

def info():
    messagebox.showinfo("info", "Chat Room \n Created by Your Team")

root = Tk()
root.geometry("800x600")
root.resizable(0, 0)
root.title("Chat Room!")
menu = Menu(root)
root.config(menu=menu)

filemenu = Menu(menu)
menu.add_cascade(label="File", menu=filemenu)
filemenu.add_command(label="Login", command=login)
filemenu.add_command(label="Sign up", command=signup)
filemenu.add_command(label="Pending", command=pending_requests)
filemenu.add_command(label="Sign Out", command=sign_out)
filemenu.add_command(label="Exit", command=exit_application)

editmenu = Menu(menu)
menu.add_cascade(label="Edit", menu=editmenu)
editmenu.add_command(label="Find User", command=find_user)
editmenu.add_command(label="Edit")
editmenu.add_command(label="Copy")
editmenu.add_command(label="Paste")
editmenu.add_command(label="Select all")

infomenu = Menu(menu)
menu.add_cascade(label="Info", menu=infomenu)
infomenu.add_command(label="Profile", command=show_profile)
infomenu.add_command(label="Info", command=info)

chatframe = LabelFrame(root, text="Chat:", font=("Arial", 15, "bold"), bg="white", fg='black', bd=5)
chatframe.place(x=200, y=0, width=600, height=550)

scroll1_y = Scrollbar(chatframe, orient=VERTICAL)

chat = Listbox(chatframe, highlightthickness=0, selectbackground="deep sky blue", selectmode=EXTENDED, font=("Arial", 15), bg="white",
               fg="green", bd=0, relief=None, activestyle="none", yscrollcommand=scroll1_y.set)
chat.place(x=20, y=0, width=340, height=250)

scroll1_y.config(command=chat.yview)
scroll1_y.pack(side=RIGHT, fill=BOTH)

textframe = LabelFrame(root, font=("Arial", 15, "bold"), bg="white", fg='black', bd=5)
textframe.place(x=200, y=550, width=600, height=80)

input_text = StringVar()
text_input = Entry(root, font=("Arial", 15), textvariable=input_text)
text_input.place(x=210, y=560, width=500, height=60)
text_input.bind("<Return>", send_message)

personframe = LabelFrame(root, text="Members:", font=("Arial", 15, "bold"), bg="white", fg='black', bd=5)
personframe.place(x=0, y=0, width=200, height=630)

scroll2_y = Scrollbar(personframe, orient=VERTICAL)

person = Listbox(personframe, highlightthickness=0, selectbackground="deep sky blue", selectmode=SINGLE, font=("Arial", 15), bg="white",
                 fg="green", bd=0, relief=None, activestyle="none", yscrollcommand=scroll2_y.set)
person.place(x=10, y=10, width=180, height=570)

scroll2_y.config(command=person.yview)
scroll2_y.pack(side=LEFT, fill=BOTH)

person.bind('<<ListboxSelect>>', select_member)

open_button = Button(root, text="Open", command=open_chat)
open_button.place(x=70, y=580, width=60, height=30)

send = Button(root, text="Send", bg="white", command=send_message)
send.place(x=730, y=565, width=50, height=50)

login_required_frame = Frame(root, bg="grey", width=800, height=600)
login_required_label = Label(login_required_frame, text="Sign in or login required to continue", bg="grey", fg="white", font=("Arial", 20))
login_required_label.place(relx=0.5, rely=0.5, anchor=CENTER)
login_required_frame.place(relx=0.5, rely=0.5, anchor=CENTER)

connect_to_server()
show_login_required()

root.mainloop()
