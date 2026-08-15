import sqlite3
import os
from werkzeug.security import generate_password_hash, check_password_hash

class AuthDB:
    def __init__(self, db_path="data/auth.db"):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_db()

    def _init_db(self):
        with self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

    def create_user(self, email, password):
        try:
            password_hash = generate_password_hash(password)
            with self.conn:
                cursor = self.conn.execute(
                    "INSERT INTO users (email, password_hash) VALUES (?, ?)",
                    (email, password_hash)
                )
                return cursor.lastrowid
        except sqlite3.IntegrityError:
            return None  # Email already exists

    def verify_user(self, email, password):
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, password_hash FROM users WHERE email = ?", (email,))
        row = cursor.fetchone()
        if row:
            user_id, password_hash = row
            if check_password_hash(password_hash, password):
                return user_id
        return None

    def get_user_by_id(self, user_id):
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, email, created_at FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        if row:
            return {"id": row[0], "email": row[1], "created_at": row[2]}
        return None

    def close(self):
        self.conn.close()

# Singleton instance
auth_db = None

def get_auth_db(db_path="data/auth.db"):
    global auth_db
    if auth_db is None:
        auth_db = AuthDB(db_path)
    return auth_db
