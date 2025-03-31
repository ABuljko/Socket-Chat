# add_initial_users.py
import sqlite3


def add_user(username, password):
    conn = sqlite3.connect('chatroom.db')
    c = conn.cursor()

    try:
        c.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username, password))
        conn.commit()
        print(f"User {username} added successfully.")
    except sqlite3.IntegrityError:
        print(f"Username {username} already exists.")

    conn.close()


if __name__ == '__main__':
    # Add initial users
    add_user('admin', 'admin')
