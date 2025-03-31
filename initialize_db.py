import sqlite3

def initialize_db():
    conn = sqlite3.connect('chatroom.db')
    c = conn.cursor()

    # Drop existing tables if you want to override
    c.execute('DROP TABLE IF EXISTS users')
    c.execute('DROP TABLE IF EXISTS messages')
    c.execute('DROP TABLE IF EXISTS requests')

    # Create users table
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password TEXT
        )
    ''')

    # Create messages table
    c.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            sender TEXT,
            receiver TEXT,
            message TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Create requests table
    c.execute('''
        CREATE TABLE IF NOT EXISTS requests (
            sender TEXT,
            receiver TEXT,
            status TEXT
        )
    ''')

    conn.commit()
    conn.close()
    print("Database initialized and tables created.")

# Initialize the database
if __name__ == '__main__':
    initialize_db()
