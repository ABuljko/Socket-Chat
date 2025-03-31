import socket
import threading
import sqlite3
from chatroom_db import (
    check_user, add_user, add_request, get_requests,
    get_accepted_requests, add_message, get_messages, get_chat_history
)

clients = {}

def update_request(sender, receiver, status):
    conn = sqlite3.connect('chatroom.db')
    c = conn.cursor()
    c.execute('UPDATE requests SET status = ? WHERE sender = ? AND receiver = ?', (status, sender, receiver))
    conn.commit()

    if status == "accepted":
        notify_membership_change(sender, receiver)

def notify_membership_change(sender, receiver):
    if sender in clients:
        clients[sender].send(f"ADD_MEMBER {receiver}".encode())
    if receiver in clients:
        clients[receiver].send(f"ADD_MEMBER {sender}".encode())

def send_initial_data(client_socket, username):
    accepted_requests = get_accepted_requests(username)
    if accepted_requests:
        accepted_users_str = "\n".join(accepted_requests)
        client_socket.send(f"Accepted requests:\n{accepted_users_str}".encode())
    else:
        client_socket.send(b"No accepted requests.\n")

def handle_client(client_socket):
    username = None
    try:
        request = client_socket.recv(1024).decode().strip()
        if request.startswith("SIGNUP"):
            _, username, password = request.split()
            if add_user(username, password):
                client_socket.send(b'User added successfully.\n')
            else:
                client_socket.send(b'Username already exists.\n')
            client_socket.close()
            return
        elif request.startswith("LOGIN"):
            _, username, password = request.split()
            if not check_user(username, password):
                client_socket.send(b'Invalid credentials.\n')
                client_socket.close()
                return
            client_socket.send(b'Login successful.\n')
            clients[username] = client_socket
            send_initial_data(client_socket, username)
            handle_messages(client_socket, username)
        else:
            client_socket.send(b'Invalid request.\n')
            client_socket.close()
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if username and username in clients:
            del clients[username]
        client_socket.close()

def handle_messages(client_socket, username):
    while True:
        try:
            message = client_socket.recv(1024).decode().strip()
            if message.startswith("SEARCH"):
                _, target_username = message.split()
                add_request(username, target_username)
                client_socket.send(b'Request sent.\n')
            elif message.startswith("REQUEST_ACCEPTED"):
                _, requestor_username = message.split()
                update_request(requestor_username, username, "accepted")
                client_socket.send(b'Request accepted.\n')
            elif message.startswith("REQUEST_REJECTED"):
                _, requestor_username = message.split()
                update_request(requestor_username, username, "rejected")
                if requestor_username in clients:
                    clients[requestor_username].send(f"REQUEST_REJECTED {username}".encode())
                client_socket.send(b'Request rejected.\n')
            elif message.startswith("PENDING"):
                requests = get_requests(username)
                if requests:
                    requests_str = "\n".join(requests)
                    client_socket.send(f"Pending requests:\n{requests_str}".encode())
                else:
                    client_socket.send(b'No pending requests.\n')
            elif message.startswith("ACCEPTED"):
                accepted_requests = get_accepted_requests(username)
                if accepted_requests:
                    requests_str = "\n".join(accepted_requests)
                    client_socket.send(f"Accepted requests:\n{requests_str}".encode())
                else:
                    client_socket.send(b'No accepted requests.\n')
            elif message.startswith("ACCEPT"):
                _, requestor_username = message.split()
                update_request(requestor_username, username, "accepted")
                client_socket.send(b'Request accepted.\n')
                notify_membership_change(requestor_username, username)
            elif message.startswith("REJECT"):
                _, requestor_username = message.split()
                update_request(requestor_username, username, "rejected")
                client_socket.send(b'Request rejected.\n')
                if requestor_username in clients:
                    clients[requestor_username].send(f"REQUEST_REJECTED {username}".encode())
            elif message.startswith("HISTORY"):
                _, target_username = message.split(maxsplit=1)
                chat_history = get_chat_history(username, target_username)
                if chat_history:
                    history_messages = "\n".join(
                        [f"CHAT_HISTORY|{record[0]}|{record[1]}|{record[2]}|{record[3]}" for record in chat_history]
                    )
                    client_socket.send(history_messages.encode())
                else:
                    client_socket.send(b'No messages.\n')
            else:
                target_username, chat_message = message.split(' ', 1)
                add_message(username, target_username, chat_message)
                if target_username in clients:
                    clients[target_username].send(f"{username}: {chat_message}".encode())
        except Exception as e:
            print(f"Error: {e}")
            break

def start_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(('127.0.0.1', 5557))
    server.listen(5)
    print('Server listening on port 5556')

    while True:
        client_socket, addr = server.accept()
        print(f'Connection from {addr}')
        client_handler = threading.Thread(target=handle_client, args=(client_socket,))
        client_handler.start()

if __name__ == '__main__':
    start_server()
