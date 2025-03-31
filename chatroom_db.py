import sqlite3

def get_chat_history(user1, user2):
    conn = sqlite3.connect('chatroom.db')
    c = conn.cursor()
    c.execute('''
        SELECT sender, receiver, message, timestamp
        FROM messages
        WHERE (sender = ? AND receiver = ?) OR (sender = ? AND receiver = ?)
        ORDER BY timestamp
    ''', (user1, user2, user2, user1))
    chat_history = c.fetchall()
    conn.close()
    return chat_history

def check_user(username, password):
    conn = sqlite3.connect('chatroom.db')
    c = conn.cursor()
    c.execute('SELECT 1 FROM users WHERE username = ? AND password = ?', (username, password))
    return c.fetchone() is not None

def add_user(username, password):
    conn = sqlite3.connect('chatroom.db')
    c = conn.cursor()
    try:
        c.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username, password))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False

def get_messages(username):
    conn = sqlite3.connect('chatroom.db')
    c = conn.cursor()
    c.execute(
        'SELECT sender, receiver, message, timestamp FROM messages WHERE sender = ? OR receiver = ? ORDER BY timestamp',
        (username, username)
    )
    return c.fetchall()

def add_message(sender, receiver, message):
    conn = sqlite3.connect('chatroom.db')
    c = conn.cursor()
    c.execute('INSERT INTO messages (sender, receiver, message) VALUES (?, ?, ?)', (sender, receiver, message))
    conn.commit()
    conn.close()

def add_request(sender, receiver):
    conn = sqlite3.connect('chatroom.db')
    c = conn.cursor()
    c.execute('INSERT INTO requests (sender, receiver, status) VALUES (?, ?, "pending")', (sender, receiver))
    conn.commit()

def get_requests(username):
    try:
        conn = sqlite3.connect('chatroom.db')
        c = conn.cursor()
        c.execute('SELECT sender FROM requests WHERE receiver = ? AND status = "pending"', (username,))
        requests = c.fetchall()
        conn.close()
        return [req[0] for req in requests]
    except Exception as e:
        print(f"Error retrieving requests for {username}: {e}")
        return []

def get_accepted_requests(username):
    conn = sqlite3.connect('chatroom.db')
    c = conn.cursor()
    c.execute('''
        SELECT sender, receiver FROM requests
        WHERE (sender = ? OR receiver = ?) AND status = "accepted"
    ''', (username, username))
    requests = c.fetchall()
    conn.close()
    accepted_users = set()
    for sender, receiver in requests:
        if sender != username:
            accepted_users.add(sender)
        if receiver != username:
            accepted_users.add(receiver)
    return list(accepted_users)