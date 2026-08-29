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
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS access_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    session_id TEXT,
                    ip_address TEXT,
                    endpoint TEXT,
                    method TEXT DEFAULT 'GET',
                    user_agent TEXT,
                    created_at DATETIME DEFAULT (datetime('now', 'localtime')),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
                )
            """)
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_access_logs_created_at ON access_logs(created_at)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_access_logs_user_id ON access_logs(user_id)")
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

    def delete_user(self, user_id):
        try:
            with self.conn:
                self.conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            return True
        except Exception as e:
            print(f"[AuthDB] Delete user error: {e}")
            return False

    def log_access(self, user_id=None, session_id=None, ip_address=None, endpoint=None, method='GET', user_agent=None):
        """アクセスログを記録"""
        try:
            with self.conn:
                self.conn.execute("""
                    INSERT INTO access_logs (user_id, session_id, ip_address, endpoint, method, user_agent)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (user_id, session_id, ip_address, endpoint, method, user_agent))
            return True
        except Exception as e:
            print(f"[AuthDB] Log access error: {e}")
            return False

    def get_realtime_active_count(self, window_minutes=5):
        """直近N分以内のアクティブユーザー数（ユニーク）を取得"""
        try:
            cursor = self.conn.cursor()
            # ログインユーザーまたはIP/Sessionでユニーク訪問者数をカウント
            cursor.execute("""
                SELECT COUNT(DISTINCT COALESCE(CAST(user_id AS TEXT), session_id, ip_address))
                FROM access_logs
                WHERE created_at >= datetime('now', 'localtime', '-' || ? || ' minutes')
            """, (window_minutes,))
            row = cursor.fetchone()
            return row[0] if row else 0
        except Exception as e:
            print(f"[AuthDB] get_realtime_active_count error: {e}")
            return 0

    def get_today_overview_stats(self):
        """本日のアクセス概要（PV数、ユニーク訪問者数、ログインユーザー数）を取得"""
        try:
            cursor = self.conn.cursor()
            # 本日の総PV
            cursor.execute("""
                SELECT COUNT(*) FROM access_logs 
                WHERE date(created_at) = date('now', 'localtime')
            """)
            today_pv = cursor.fetchone()[0]

            # 本日のユニーク訪問者数
            cursor.execute("""
                SELECT COUNT(DISTINCT COALESCE(CAST(user_id AS TEXT), session_id, ip_address)) 
                FROM access_logs 
                WHERE date(created_at) = date('now', 'localtime')
            """)
            today_uu = cursor.fetchone()[0]

            # 本日のログインユーザー数
            cursor.execute("""
                SELECT COUNT(DISTINCT user_id) 
                FROM access_logs 
                WHERE date(created_at) = date('now', 'localtime') AND user_id IS NOT NULL
            """)
            today_logged_in_users = cursor.fetchone()[0]

            return {
                "today_pv": today_pv,
                "today_uu": today_uu,
                "today_logged_in_users": today_logged_in_users,
                "active_now": self.get_realtime_active_count(window_minutes=5)
            }
        except Exception as e:
            print(f"[AuthDB] get_today_overview_stats error: {e}")
            return {"today_pv": 0, "today_uu": 0, "today_logged_in_users": 0, "active_now": 0}

    def get_activity_graph_data(self, period='today'):
        """
        Chart.js 描画用の集計データを生成
        period: 'today' (今日の0~23時), '24h' (過去24時間), '7d' (過去7日間), '30d' (過去30日間)
        """
        cursor = self.conn.cursor()
        labels = []
        pv_data = []
        uu_data = []

        try:
            if period == 'today':
                # 今日の00:00 〜 23:00（各時間帯の集計）
                cursor.execute("""
                    SELECT strftime('%H', created_at) as hour,
                           COUNT(*) as pv,
                           COUNT(DISTINCT COALESCE(CAST(user_id AS TEXT), session_id, ip_address)) as uu
                    FROM access_logs
                    WHERE date(created_at) = date('now', 'localtime')
                    GROUP BY hour
                """)
                hour_map = {row[0]: (row[1], row[2]) for row in cursor.fetchall()}

                for h in range(24):
                    h_str = f"{h:02d}"
                    labels.append(f"{h_str}:00")
                    pv, uu = hour_map.get(h_str, (0, 0))
                    pv_data.append(pv)
                    uu_data.append(uu)

            elif period == '24h':
                # 過去24時間（1時間刻み）
                cursor.execute("""
                    SELECT strftime('%Y-%m-%d %H:00', created_at) as hour_slot,
                           COUNT(*) as pv,
                           COUNT(DISTINCT COALESCE(CAST(user_id AS TEXT), session_id, ip_address)) as uu
                    FROM access_logs
                    WHERE created_at >= datetime('now', 'localtime', '-23 hours')
                    GROUP BY hour_slot
                """)
                slot_map = {row[0]: (row[1], row[2]) for row in cursor.fetchall()}

                from datetime import datetime, timedelta
                now = datetime.now()
                for i in range(23, -1, -1):
                    t = now - timedelta(hours=i)
                    slot_key = t.strftime('%Y-%m-%d %H:00')
                    label_str = t.strftime('%H:00')
                    labels.append(label_str)
                    pv, uu = slot_map.get(slot_key, (0, 0))
                    pv_data.append(pv)
                    uu_data.append(uu)

            elif period in ['7d', '30d']:
                days = 7 if period == '7d' else 30
                cursor.execute(f"""
                    SELECT date(created_at) as day,
                           COUNT(*) as pv,
                           COUNT(DISTINCT COALESCE(CAST(user_id AS TEXT), session_id, ip_address)) as uu
                    FROM access_logs
                    WHERE created_at >= datetime('now', 'localtime', '-{days-1} days')
                    GROUP BY day
                """)
                day_map = {row[0]: (row[1], row[2]) for row in cursor.fetchall()}

                from datetime import datetime, timedelta
                now = datetime.now()
                for i in range(days - 1, -1, -1):
                    d = now - timedelta(days=i)
                    day_key = d.strftime('%Y-%m-%d')
                    labels.append(d.strftime('%m/%d'))
                    pv, uu = day_map.get(day_key, (0, 0))
                    pv_data.append(pv)
                    uu_data.append(uu)

        except Exception as e:
            print(f"[AuthDB] get_activity_graph_data error: {e}")

        return {
            "period": period,
            "labels": labels,
            "pv": pv_data,
            "uu": uu_data
        }

    def get_top_pages_stats(self, days=7, limit=6):
        """よく見られているページのアクセスランキング"""
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT endpoint, COUNT(*) as pv,
                       COUNT(DISTINCT COALESCE(CAST(user_id AS TEXT), session_id, ip_address)) as uu
                FROM access_logs
                WHERE created_at >= datetime('now', 'localtime', '-' || ? || ' days')
                GROUP BY endpoint
                ORDER BY pv DESC
                LIMIT ?
            """, (days, limit))
            rows = cursor.fetchall()
            return [{"endpoint": r[0], "pv": r[1], "uu": r[2]} for r in rows]
        except Exception as e:
            print(f"[AuthDB] get_top_pages_stats error: {e}")
            return []

    def get_recent_access_logs(self, limit=20):
        """直近のアクセス履歴を取得"""
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT l.id, l.user_id, u.email, l.ip_address, l.endpoint, l.method, l.created_at
                FROM access_logs l
                LEFT JOIN users u ON l.user_id = u.id
                ORDER BY l.created_at DESC
                LIMIT ?
            """, (limit,))
            rows = cursor.fetchall()
            return [{
                "id": r[0],
                "user_id": r[1],
                "user_name": r[2] if r[2] else "ゲスト",
                "ip_address": r[3],
                "endpoint": r[4],
                "method": r[5],
                "created_at": r[6]
            } for r in rows]
        except Exception as e:
            print(f"[AuthDB] get_recent_access_logs error: {e}")
            return []

    def close(self):
        self.conn.close()

# Singleton instance
auth_db = None

def get_auth_db(db_path="data/auth.db"):
    global auth_db
    if auth_db is None:
        auth_db = AuthDB(db_path)
    return auth_db
