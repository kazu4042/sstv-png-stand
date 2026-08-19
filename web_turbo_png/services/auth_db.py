import sqlite3
import os
import sys
from werkzeug.security import generate_password_hash, check_password_hash

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "../../"))

class AuthDB:
    def __init__(self, db_path="data/auth.db"):
        if not os.path.isabs(db_path):
            self.db_path = os.path.join(ROOT_DIR, db_path)
        else:
            self.db_path = db_path
            
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
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
        # 初期管理者アカウントの自動シード
        self._seed_default_admin()

    def _seed_default_admin(self):
        """管理者メールアドレスのアカウントが存在しない場合に初期生成"""
        admin_emails = [e.strip() for e in os.environ.get('ADMIN_EMAILS', 'koseikazu@icloud.com').split(',') if e.strip()]
        basic_pass = os.environ.get('BASIC_AUTH_PASSWORD', '123456789').strip()
        
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        count = cursor.fetchone()[0]
        
        if count == 0 and admin_emails:
            admin_email = admin_emails[0]
            self.create_user(admin_email, basic_pass)
            print(f"[AuthDB] Initialized default admin user: {admin_email}")

    def create_user(self, email, password):
        try:
            password_hash = generate_password_hash(password.strip())
            with self.conn:
                cursor = self.conn.execute(
                    "INSERT INTO users (email, password_hash) VALUES (?, ?)",
                    (email.strip(), password_hash)
                )
                return cursor.lastrowid
        except sqlite3.IntegrityError:
            return None  # Email already exists

    def update_password(self, user_id, new_password):
        """ユーザーのパスワードハッシュを更新"""
        try:
            password_hash = generate_password_hash(new_password.strip())
            with self.conn:
                self.conn.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (password_hash, user_id)
                )
            return True
        except Exception as e:
            print(f"[AuthDB] Update password error: {e}")
            return False

    def verify_user(self, email, password):
        """メールアドレス/ユーザー名とパスワードを検証"""
        if not email or not password:
            return None
            
        email_clean = email.strip()
        pwd_clean = password.strip()
        
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, password_hash FROM users WHERE email = ? COLLATE NOCASE", (email_clean,))
        row = cursor.fetchone()
        
        if row:
            user_id, password_hash = row
            if check_password_hash(password_hash, password) or check_password_hash(password_hash, pwd_clean):
                return user_id
                
            # 管理者の初期パスワード同期
            admin_emails = [e.strip().lower() for e in os.environ.get('ADMIN_EMAILS', 'koseikazu@icloud.com').split(',') if e.strip()]
            basic_pass = os.environ.get('BASIC_AUTH_PASSWORD', '123456789').strip()
            if email_clean.lower() in admin_emails and (password == basic_pass or pwd_clean == basic_pass):
                self.update_password(user_id, pwd_clean)
                return user_id
                
        return None


    def get_user_by_id(self, user_id):
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, email, created_at FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        if row:
            return {"id": row[0], "email": row[1], "created_at": row[2]}
        return None

    def get_all_users(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, email, created_at FROM users ORDER BY created_at DESC")
        return [{"id": row[0], "email": row[1], "created_at": row[2]} for row in cursor.fetchall()]

    def close(self):
        self.conn.close()

# Singleton instance
auth_db = None

def get_auth_db(db_path="data/auth.db"):
    global auth_db
    if auth_db is None:
        auth_db = AuthDB(db_path)
    return auth_db
