import os
import sqlite3
import sys
from collections import defaultdict

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

from digital_turbo_png import config_turbo as config

class PacketDatabaseTurboPNG:
    def __init__(self, log_dir):
        os.makedirs(log_dir, exist_ok=True)
        self.db_path = os.path.join(log_dir, "sstv_packets_turbo_png.db")
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")  # 高速アクセス
        self._init_db()

    def _init_db(self):
        with self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS imported_files (
                    file_name TEXT PRIMARY KEY,
                    imported_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS packets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    image_id INTEGER,
                    tile_x INTEGER,
                    tile_y INTEGER,
                    payload_length INTEGER,
                    payload_bits TEXT,
                    snr REAL,
                    file_name TEXT,
                    user_id INTEGER,
                    imported_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            try:
                self.conn.execute("ALTER TABLE packets ADD COLUMN user_id INTEGER DEFAULT NULL")
            except sqlite3.OperationalError:
                pass # Already exists
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_turbo_png_packets_image_tile ON packets (image_id, tile_x, tile_y, payload_length)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_turbo_png_packets_image_id ON packets (image_id)")

    def is_file_imported(self, file_name):
        cursor = self.conn.cursor()
        cursor.execute("SELECT 1 FROM imported_files WHERE file_name = ?", (file_name,))
        return cursor.fetchone() is not None

    def get_user_history(self, user_id):
        """指定したユーザーのアップロード履歴を返す。各アップロード(ファイル単位)のタイムスタンプと画像IDを取得"""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT file_name, MAX(imported_at) as imported_at, MAX(image_id) as image_id
            FROM packets
            WHERE user_id = ?
            GROUP BY file_name
            ORDER BY imported_at DESC
        """, (user_id,))
        
        import re
        history = []
        for row in cursor.fetchall():
            file_name, imported_at, image_id = row
            
            # Extract local time from file_name (e.g. turbo_png_bitstream_20260815_211712...)
            m = re.search(r'_(\d{8})_(\d{6})', file_name)
            if m:
                d_str = m.group(1)
                t_str = m.group(2)
                timestamp = f"{d_str[:4]}-{d_str[4:6]}-{d_str[6:8]} {t_str[:2]}:{t_str[2:4]}:{t_str[4:6]}"
            else:
                # Fallback to database time if format doesn't match
                timestamp = imported_at
                
            history.append({
                "file_name": file_name,
                "timestamp": timestamp,
                "image_id": f"{image_id:04X}" if image_id is not None else "UNKNOWN"
            })
        return history

    def mark_file_imported(self, file_name):
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO imported_files (file_name) VALUES (?)", (file_name,))

    def insert_packets_bulk(self, file_name, packets_list, user_id=None):
        """
        packets_list: [(image_id, tile_x, tile_y, payload_length, payload_bits, snr), ...]
        """
        with self.conn:
            self.conn.executemany("""
                INSERT INTO packets (image_id, tile_x, tile_y, payload_length, payload_bits, snr, file_name, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, [(p[0], p[1], p[2], p[3], p[4], p[5], file_name, user_id) for p in packets_list])
            self.mark_file_imported(file_name)

    def get_all_image_ids_with_counts(self, user_id=None):
        cursor = self.conn.cursor()
        if user_id:
            cursor.execute("SELECT image_id, COUNT(*) FROM packets WHERE user_id = ? GROUP BY image_id", (user_id,))
        else:
            cursor.execute("SELECT image_id, COUNT(*) FROM packets GROUP BY image_id")
        return {row[0]: row[1] for row in cursor.fetchall()}

    def get_packets_for_image(self, target_image_id, user_id=None):
        cursor = self.conn.cursor()
        if user_id:
            cursor.execute("""
                SELECT tile_x, tile_y, payload_length, payload_bits, snr, file_name, user_id, imported_at
                FROM packets
                WHERE image_id = ? AND user_id = ?
                ORDER BY tile_y, tile_x
            """, (target_image_id, user_id))
        else:
            cursor.execute("""
                SELECT tile_x, tile_y, payload_length, payload_bits, snr, file_name, user_id, imported_at
                FROM packets
                WHERE image_id = ?
                ORDER BY tile_y, tile_x
            """, (target_image_id,))

        # 構造: data[tile_y][tile_x][payload_length] = [(payload_bits, snr, file_name, user_id, imported_at), ...]
        data = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        for row in cursor.fetchall():
            tile_x, tile_y, payload_length, payload_bits, snr, file_name, p_user_id, imported_at = row
            data[tile_y][tile_x][payload_length].append((payload_bits, snr, file_name, p_user_id, imported_at))

        return data

    def close(self):
        self.conn.close()
