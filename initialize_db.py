"""Delete all data and recreate empty tables."""

import chatroom_db as db

if __name__ == "__main__":
    db.reset_db()
    print(f"Database {db.DB_PATH} reset and tables created.")
