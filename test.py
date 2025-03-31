from tkinter import *
from tkinter import messagebox
import socket
import threading

# Global variable to store the client socket
client_socket = None
current_chat = None
members = []

# Connect to the server at the beginning
def connect_to_server():
    global client_socket
    try:
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_socket.connect(('127.0.0.1', 5555))
    except Exception as e:
        messagebox.showerror("Connection Error", f"Failed to connect to server: {e}")
        root.destroy()

root = Tk()
root.geometry("800x600")
root.resizable(0, 0)
root.title("Chat room!")
menu = Menu(root)
root.config(menu=menu)

# Sign up function
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

# Login function
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
    global client_socket
    try:
        client_socket.send(f"LOGIN {username} {password}".encode())
        response = client_socket.recv(1024).decode()
        if response == 'Login successful.\n':
            messagebox.showinfo("Login Success", "Login successful.")
            receive_thread = threading.Thread(target=receive_messages, args=(client_socket,))
            receive_thread.daemon = True
            receive_thread.start()
        else:
            messagebox.showerror("Login Failed", response)
    except Exception as e:
        messagebox.showerror("Login Error", f"An error occurred during login: {e}")

def receive_messages(client_socket):
    while True:
        try:
            message = client_socket.recv(1024).decode()
            if message.startswith("REQUEST"):
                _, from_user = message.split()
                response = messagebox.askquestion("Chat Request", f"{from_user} wants to chat with you. Accept?")
                if response == 'yes':
                    client_socket.send(f"ACCEPT {from_user}".encode())
                    add_member(from_user)
                else:
                    client_socket.send(f"REJECT {from_user}".encode())
            elif message.startswith("REQUEST_ACCEPTED"):
                _, accepted_user = message.split()
                add_member(accepted_user)
            elif message:
                chat.insert(END, message)
                chat.yview(END)
            else:
                break
        except Exception as e:
            print(f"Error: {e}")
            break

def send_message():
    global client_socket, current_chat
    message = input_text.get()
    if message and current_chat:
        try:
            client_socket.send(f"{current_chat} {message}".encode())
            chat.insert(END, f"You: {message}")
            input_text.set("")  # Clear the input field
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
        response = client_socket.recv(1024).decode()
        if response == 'User is online.\n':
            messagebox.showinfo("User Found", f"{username} is online. Chat request sent.")
        else:
            messagebox.showinfo("User Found", f"{username} is offline. Chat request saved.")
    except Exception as e:
        messagebox.showerror("Search Error", f"An error occurred during search: {e}")

def pending_requests():
    global client_socket
    try:
        client_socket.send("PENDING".encode())
        response = client_socket.recv(1024).decode()
        if response == 'No pending requests.\n':
            messagebox.showinfo("Pending Requests", "No pending requests.")
        else:
            show_pending_requests(response)
    except Exception as e:
        messagebox.showerror("Pending Requests Error", f"An error occurred while fetching pending requests: {e}")

def show_pending_requests(requests):
    pending_dialog = Toplevel(root)
    pending_dialog.title("Pending Requests")

    Label(pending_dialog, text="Pending Requests:").grid(row=0, column=0)

    pending_list = Listbox(pending_dialog)
    pending_list.grid(row=1, column=0)

    requests = requests.split("\n")
    for request in requests:
        pending_list.insert(END, request)

    def accept_request():
        selected_request = pending_list.get(ACTIVE)
        if selected_request:
            accept_chat_request(selected_request)
            pending_dialog.destroy()
        else:
            messagebox.showerror("Error", "No request selected.")

    accept_button = Button(pending_dialog, text="Accept", command=accept_request)
    accept_button.grid(row=2, column=0)

def accept_chat_request(requestor_username):
    global client_socket
    try:
        client_socket.send(f"ACCEPT {requestor_username}".encode())
        response = client_socket.recv(1024).decode()
        if response == 'Request accepted.\n':
            messagebox.showinfo("Request Accepted", f"You accepted the chat request from {requestor_username}.")
            add_member(requestor_username)
        else:
            messagebox.showerror("Request Error", response)
    except Exception as e:
        messagebox.showerror("Accept Error", f"An error occurred while accepting the request: {e}")

def add_member(username):
    if username not in members:
        members.append(username)
        person.insert(END, username)

def select_member(event):
    global current_chat
    selected_member = person.get(ACTIVE)
    current_chat = selected_member
    load_chat_history(selected_member)

def load_chat_history(username):
    global client_socket
    chat.delete(0, END)
    try:
        client_socket.send(f"HISTORY {username}".encode())
        response = client_socket.recv(1024).decode()
        messages = response.split("\n")
        for message in messages:
            chat.insert(END, message)
    except Exception as e:
        messagebox.showerror("Load Error", f"Failed to load chat history: {e}")

def exit_application():
    msg_box = messagebox.askquestion("Exit Application", "Are you sure you want to exit the application?", icon="warning")
    if msg_box == "yes":
        root.destroy()
    else:
        pass

def info():
    messagebox.showinfo("info", " Chatroom \n Created by Amin Niaziardekani \n Swapnaneel Sarkhel \n Ajdin Buljko \n Computer Network Project", )

# UI setup
filemenu = Menu(menu)
menu.add_cascade(label="File", menu=filemenu)
filemenu.add_command(label="Login", command=login)
filemenu.add_command(label="Sign up", command=signup)
filemenu.add_command(label="Pending", command=pending_requests)
filemenu.add_command(label="Clear Chats!")
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
infomenu.add_command(label="Profile")
infomenu.add_command(label="Info", command=info)

# Chatroom
chatframe = LabelFrame(root, text="Chat :", font=("Arial", 15, "bold"), bg="white", fg='black', bd=5)
chatframe.place(x=200, y=0, width=600, height=550)

scroll1_y = Scrollbar(chatframe, orient=VERTICAL)

chat = Listbox(chatframe, highlightthickness=0, selectbackground="deep sky blue", selectmode=EXTENDED, font=("Arial", 15), bg="white",
               fg="green", bd=0, relief=None, activestyle="none", yscrollcommand=scroll1_y.set)
chat.place(x=20, y=0, width=340, height=250)

scroll1_y.config(command=chat.yview)
scroll1_y.pack(side=RIGHT, fill=BOTH)

# Text input
textframe = LabelFrame(root, font=("Arial", 15, "bold"), bg="white", fg='black', bd=5)
textframe.place(x=200, y=550, width=600, height=80)

input_text = StringVar()
text_input = Entry(root, font=("Arial", 15), textvariable=input_text)
text_input.place(x=210, y=560, width=500, height=60)

# Members list
personframe = LabelFrame(root, text="Member :", font=("Arial", 15, "bold"), bg="white", fg='black', bd=5)
personframe.place(x=0, y=0, width=200, height=630)

scroll2_y = Scrollbar(personframe, orient=VERTICAL)

person = Listbox(personframe, highlightthickness=0, selectbackground="deep sky blue", selectmode=EXTENDED, font=("Arial", 15), bg="white",
                 fg="green", bd=0, relief=None, activestyle="none", yscrollcommand=scroll2_y.set)
person.place(x=10, y=10, width=180, height=610)

scroll2_y.config(command=person.yview)
scroll2_y.pack(side=LEFT, fill=BOTH)

# Bind event to select member
person.bind('<<ListboxSelect>>', select_member)

# Send button
send = Button(root, text="Send", bg="white", command=send_message)
send.place(x=730, y=565, width=50, height=50)

# Connect to the server at the beginning
connect_to_server()

root.mainloop()
