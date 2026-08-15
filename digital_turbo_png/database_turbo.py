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
                    imported_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_turbo_png_packets_image_tile ON packets (image_id, tile_x, tile_y, payload_length)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_turbo_png_packets_image_id ON packets (image_id)")

    def is_file_imported(self, file_name):
        cursor = self.conn.cursor()
        cursor.execute("SELECT 1 FROM imported_files WHERE file_name = ?", (file_name,))
        return cursor.fetchone() is not None

    def mark_file_imported(self, file_name):
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO imported_files (file_name) VALUES (?)", (file_name,))

    def insert_packets_bulk(self, file_name, packets_list):
        """
        packets_list: [(image_id, tile_x, tile_y, payload_length, payload_bits, snr), ...]
        """
        with self.conn:
            self.conn.executemany("""
                INSERT INTO packets (image_id, tile_x, tile_y, payload_length, payload_bits, snr, file_name)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, [(p[0], p[1], p[2], p[3], p[4], p[5], file_name) for p in packets_list])
            self.mark_file_imported(file_name)

    def get_all_image_ids_with_counts(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT image_id, COUNT(*) FROM packets GROUP BY image_id")
        return {row[0]: row[1] for row in cursor.fetchall()}

    def get_packets_for_image(self, target_image_id):
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT tile_x, tile_y, payload_length, payload_bits, snr, file_name
            FROM packets
            WHERE image_id = ?
            ORDER BY tile_y, tile_x
        """, (target_image_id,))

        # 構造: data[tile_y][tile_x][payload_length] = [(payload_bits, snr, file_name), ...]
        data = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        for row in cursor.fetchall():
            tile_x, tile_y, payload_length, payload_bits, snr, file_name = row
            data[tile_y][tile_x][payload_length].append((payload_bits, snr, file_name))

        return data

    def close(self):
        self.conn.close()
