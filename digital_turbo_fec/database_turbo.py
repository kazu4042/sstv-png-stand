"""SSTV Turbo FEC (Raptor + RS) 専用 SQLite データベース (database_turbo.py)
【sstv-png-stand 互換】
受信パケット、取り込み済みログファイル、画像復元状況を WAL モードで高速に管理。
"""

import os
import re
import sqlite3
import sys
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if _CURRENT_DIR not in sys.path:
    sys.path.insert(0, _CURRENT_DIR)

try:
    from digital_turbo_fec import config_turbo as config
except ImportError:
    from raptor_rs_fec import config_turbo as config



class PacketDatabaseRaptorFEC:
    """SSTV Turbo FEC 用パケットデータベース (SQLite)"""

    def __init__(self, log_dir: str):
        os.makedirs(log_dir, exist_ok=True)
        self.db_path = os.path.join(log_dir, "sstv_packets_turbo_fec.db")
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
        self.conn.execute("PRAGMA journal_mode=WAL")  # 高速WALアクセス
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
                    esi INTEGER DEFAULT NULL,
                    pkt_type INTEGER DEFAULT 0,
                    imported_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS finalized_tiles (
                    image_id INTEGER,
                    tile_x INTEGER,
                    tile_y INTEGER,
                    tile_data BLOB,
                    stage TEXT,
                    finalized_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (image_id, tile_x, tile_y)
                )
            """)
            # カラム追加のフォールバック (既存DB対策)
            for col, col_type in [("user_id", "INTEGER DEFAULT NULL"), ("esi", "INTEGER DEFAULT NULL"), ("pkt_type", "INTEGER DEFAULT 0")]:
                try:
                    self.conn.execute(f"ALTER TABLE packets ADD COLUMN {col} {col_type}")
                except sqlite3.OperationalError:
                    pass

            # インデックスの生成
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_turbo_fec_packets_image_tile ON packets (image_id, tile_x, tile_y, payload_length)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_turbo_fec_packets_image_id ON packets (image_id)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_turbo_fec_packets_user_id ON packets (user_id)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_turbo_fec_packets_image_user ON packets (image_id, user_id)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_turbo_fec_packets_tile_group ON packets (image_id, tile_y, tile_x)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_turbo_fec_packets_esi ON packets (image_id, esi)")

    def is_file_imported(self, file_name: str) -> bool:
        cursor = self.conn.cursor()
        cursor.execute("SELECT 1 FROM imported_files WHERE file_name = ?", (file_name,))
        return cursor.fetchone() is not None

    def mark_file_imported(self, file_name: str):
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO imported_files (file_name) VALUES (?)", (file_name,))

    def delete_imported_file(self, file_name: str):
        with self.conn:
            self.conn.execute("DELETE FROM imported_files WHERE file_name = ?", (file_name,))

    def clear_imported_files(self):
        with self.conn:
            self.conn.execute("DELETE FROM imported_files")

    def insert_packets_bulk(self, file_name: str, packets_list: List[Tuple], user_id: Optional[int] = None):
        """
        packets_list: [(image_id, tile_x, tile_y, payload_length, payload_bits, snr, esi, pkt_type), ...]
        または 6 要素タプル: (image_id, tile_x, tile_y, payload_length, payload_bits, snr)
        """
        from datetime import datetime
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        rows_to_insert = []
        for p in packets_list:
            img_id = p[0]
            tx = p[1]
            ty = p[2]
            plen = p[3]
            pbits = p[4]
            snr_val = p[5]
            esi_val = p[6] if len(p) > 6 else None
            ptype_val = p[7] if len(p) > 7 else 0
            rows_to_insert.append((img_id, tx, ty, plen, pbits, snr_val, file_name, user_id, esi_val, ptype_val, now_str))

        with self.conn:
            self.conn.executemany("""
                INSERT INTO packets (image_id, tile_x, tile_y, payload_length, payload_bits, snr, file_name, user_id, esi, pkt_type, imported_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows_to_insert)
            self.mark_file_imported(file_name)

    def get_all_image_ids_with_counts(self, user_id: Optional[int] = None) -> Dict[int, int]:
        cursor = self.conn.cursor()
        if user_id:
            cursor.execute("SELECT image_id, COUNT(*) FROM packets WHERE user_id = ? GROUP BY image_id", (user_id,))
        else:
            cursor.execute("SELECT image_id, COUNT(*) FROM packets GROUP BY image_id")
        return {row[0]: row[1] for row in cursor.fetchall() if row[0] is not None}

    def get_packets_for_image(self, target_image_id: int, user_id: Optional[int] = None) -> Dict:
        """
        指定された画像IDのパケットを取得。
        戻り値構造: data[tile_y][tile_x][payload_length] = [(payload_bits, snr, file_name, user_id, imported_at, esi, pkt_type), ...]
        """
        cursor = self.conn.cursor()
        if user_id:
            cursor.execute("""
                SELECT tile_x, tile_y, payload_length, payload_bits, snr, file_name, user_id, imported_at, esi, pkt_type
                FROM packets
                WHERE image_id = ? AND user_id = ?
                ORDER BY tile_y, tile_x
            """, (target_image_id, user_id))
        else:
            cursor.execute("""
                SELECT tile_x, tile_y, payload_length, payload_bits, snr, file_name, user_id, imported_at, esi, pkt_type
                FROM packets
                WHERE image_id = ?
                ORDER BY tile_y, tile_x
            """, (target_image_id,))

        data = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        for row in cursor.fetchall():
            tile_x, tile_y, payload_length, payload_bits, snr, file_name, p_user_id, imported_at, esi, pkt_type = row
            data[tile_y][tile_x][payload_length].append(
                (payload_bits, snr, file_name, p_user_id, imported_at, esi, pkt_type)
            )

        return data

    def get_images_summary(self) -> List[Dict[str, Any]]:
        """管理画面用: すべての画像IDごとの統計情報を取得"""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT 
                image_id,
                COUNT(*) as packet_count,
                COUNT(DISTINCT CASE WHEN tile_x != 255 AND tile_y != 255 THEN tile_y || '_' || tile_x ELSE NULL END) as tile_count,
                COUNT(DISTINCT user_id) as user_count,
                MIN(imported_at) as first_received,
                MAX(imported_at) as last_received
            FROM packets
            GROUP BY image_id
            ORDER BY last_received DESC
        """)
        results = []
        for row in cursor.fetchall():
            img_id_int, p_count, t_count, u_count, first_rec, last_rec = row
            if img_id_int is None:
                continue
            img_id_hex = f"{img_id_int:04X}"
            results.append({
                "image_id_int": img_id_int,
                "image_id_hex": img_id_hex,
                "packet_count": p_count,
                "tile_count": t_count,
                "user_count": u_count,
                "first_received": first_rec,
                "last_received": last_rec
            })
        return results

    def delete_images_by_ids(self, image_ids_int_list: List[int]) -> int:
        """指定された画像IDのパケットをDBから完全削除"""
        if not image_ids_int_list:
            return 0
        with self.conn:
            placeholders = ",".join("?" for _ in image_ids_int_list)
            cursor = self.conn.cursor()
            cursor.execute(f"DELETE FROM packets WHERE image_id IN ({placeholders})", image_ids_int_list)
            deleted_count = cursor.rowcount
            cursor.execute(f"DELETE FROM finalized_tiles WHERE image_id IN ({placeholders})", image_ids_int_list)
        return deleted_count

    def get_snr_analytics(self) -> Dict[str, Any]:
        """電波品質（SNR: Signal to Noise Ratio）の統計と分布を取得"""
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_packets,
                    AVG(snr) as avg_snr,
                    MAX(snr) as max_snr,
                    MIN(snr) as min_snr,
                    COALESCE(SUM(CASE WHEN snr >= 15.0 THEN 1 ELSE 0 END), 0) as count_high,
                    COALESCE(SUM(CASE WHEN snr >= 8.0 AND snr < 15.0 THEN 1 ELSE 0 END), 0) as count_mid,
                    COALESCE(SUM(CASE WHEN snr < 8.0 THEN 1 ELSE 0 END), 0) as count_low
                FROM packets
            """)
            row = cursor.fetchone()
            if not row or row[0] == 0:
                return {
                    "total_packets": 0,
                    "avg_snr": 0.0,
                    "max_snr": 0.0,
                    "min_snr": 0.0,
                    "count_high": 0,
                    "count_mid": 0,
                    "count_low": 0,
                    "condition": "NO_DATA",
                    "condition_label": "データ待機中"
                }

            total, avg_s, max_s, min_s, c_high, c_mid, c_low = row
            avg_s = avg_s or 0.0
            max_s = max_s or 0.0
            min_s = min_s or 0.0

            if avg_s >= 14.0:
                condition = "EXCELLENT"
                condition_label = "極めて良好 (High SNR)"
            elif avg_s >= 9.0:
                condition = "GOOD"
                condition_label = "良好・安定 (Clear)"
            elif avg_s >= 5.0:
                condition = "MODERATE"
                condition_label = "普通・ややノイズあり"
            else:
                condition = "POOR"
                condition_label = "混信・弱電界 (Noisy)"

            return {
                "total_packets": total or 0,
                "avg_snr": round(avg_s, 1),
                "max_snr": round(max_s, 1),
                "min_snr": round(min_s, 1),
                "count_high": c_high or 0,
                "count_mid": c_mid or 0,
                "count_low": c_low or 0,
                "condition": condition,
                "condition_label": condition_label
            }
        except Exception:
            return {
                "total_packets": 0,
                "avg_snr": 0.0,
                "max_snr": 0.0,
                "min_snr": 0.0,
                "count_high": 0,
                "count_mid": 0,
                "count_low": 0,
                "condition": "ERROR",
                "condition_label": "取得エラー"
            }

    def get_user_history(self, user_id: int) -> List[Dict[str, Any]]:
        """指定したユーザーのアップロード履歴を取得"""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT file_name, MAX(imported_at) as imported_at, MAX(image_id) as image_id
            FROM packets
            WHERE user_id = ?
            GROUP BY file_name
            ORDER BY imported_at DESC
        """, (user_id,))

        history = []
        for row in cursor.fetchall():
            file_name, imported_at, image_id = row
            m = re.search(r'_(\d{8})_(\d{6})', file_name)
            if m:
                d_str = m.group(1)
                t_str = m.group(2)
                timestamp = f"{d_str[:4]}-{d_str[4:6]}-{d_str[6:8]} {t_str[:2]}:{t_str[2:4]}:{t_str[4:6]}"
            else:
                timestamp = imported_at

            history.append({
                "file_name": file_name,
                "timestamp": timestamp,
                "image_id": f"{image_id:04X}" if image_id is not None else "UNKNOWN"
            })
        return history

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass
