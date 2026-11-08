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

    def upsert_finalized_tiles(self, image_id: int, tiles_list: List[Tuple[int, int, bytes, str]]):
        """
        確定・ラプター復元済みタイルを finalized_tiles テーブルへ一括保存
        tiles_list: [(tile_x, tile_y, tile_data_bytes, stage), ...]
        """
        if not tiles_list:
            return
        from datetime import datetime
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        rows = [(image_id, tx, ty, tdata, stage, now_str) for tx, ty, tdata, stage in tiles_list]
        with self.conn:
            self.conn.executemany("""
                INSERT OR REPLACE INTO finalized_tiles (image_id, tile_x, tile_y, tile_data, stage, finalized_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, rows)

    def get_finalized_tiles(self, target_image_id: int) -> Dict[Tuple[int, int], Tuple[bytes, str, str]]:
        """
        指定画像IDの確定・ラプター復元済みタイル一覧を取得
        戻り値: {(tile_x, tile_y): (tile_data_bytes, stage, finalized_at)}
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT tile_x, tile_y, tile_data, stage, finalized_at
            FROM finalized_tiles
            WHERE image_id = ?
        """, (target_image_id,))
        res = {}
        for tx, ty, tdata, stage, fin_at in cursor.fetchall():
            res[(int(tx), int(ty))] = (bytes(tdata) if tdata else b"", str(stage or "DIRECT_RS"), str(fin_at or ""))
        return res

    def get_images_summary(self) -> List[Dict[str, Any]]:
        """管理画面用: すべての画像IDごとの統計情報を取得（ラプター復元済みタイル数も考慮）"""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT 
                image_id,
                COUNT(*) as packet_count,
                COUNT(DISTINCT CASE WHEN tile_x < 255 AND tile_y < 255 THEN tile_y || '_' || tile_x ELSE NULL END) as tile_count,
                COUNT(DISTINCT CASE WHEN tile_x = 255 AND tile_y < 255 THEN tile_y ELSE NULL END) as repair_count,
                COUNT(DISTINCT user_id) as user_count,
                MIN(imported_at) as first_received,
                MAX(imported_at) as last_received
            FROM packets
            GROUP BY image_id
            ORDER BY last_received DESC
        """)
        rows = cursor.fetchall()
        results = []
        for row in rows:
            img_id_int, p_count, t_count, rep_count, u_count, first_rec, last_rec = row
            if img_id_int is None:
                continue
            cursor.execute("SELECT COUNT(*) FROM finalized_tiles WHERE image_id = ?", (img_id_int,))
            fin_row = cursor.fetchone()
            fin_count = fin_row[0] if fin_row and fin_row[0] is not None else 0
            effective_tile_count = max(int(t_count or 0), int(fin_count or 0))
            img_id_hex = f"{img_id_int:04X}"
            results.append({
                "image_id_int": img_id_int,
                "image_id_hex": img_id_hex,
                "packet_count": p_count,
                "tile_count": effective_tile_count,
                "repair_count": int(rep_count or 0),
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

    def get_top_contributors(self, limit: int = 5) -> List[Dict[str, Any]]:
        """パケット提供数の多いユーザーランキング"""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT 
                user_id,
                COUNT(*) as packet_count,
                COUNT(DISTINCT image_id) as image_count,
                COUNT(DISTINCT CASE WHEN tile_x < 255 AND tile_y < 255 THEN tile_y || '_' || tile_x ELSE NULL END) as tile_count,
                AVG(snr) as avg_snr,
                MAX(imported_at) as last_seen
            FROM packets
            WHERE user_id IS NOT NULL
            GROUP BY user_id
            ORDER BY packet_count DESC
            LIMIT ?
        """, (limit,))
        results = []
        for row in cursor.fetchall():
            u_id, p_count, img_count, t_count, a_snr, l_seen = row
            results.append({
                "user_id": u_id,
                "packet_count": p_count,
                "image_count": img_count,
                "tile_count": t_count,
                "avg_snr": round(a_snr or 0.0, 1),
                "last_seen": l_seen
            })
        return results

    def get_hourly_packet_traffic(self, period: str = 'today') -> Dict[str, Any]:
        """パケット受信量の時系列推移"""
        from datetime import datetime, timedelta
        cursor = self.conn.cursor()
        labels = []
        traffic_data = []

        try:
            if period == 'today':
                today_str = datetime.now().strftime('%Y-%m-%d')
                cursor.execute("""
                    SELECT strftime('%H', imported_at) as hour,
                           COUNT(*) as packet_count
                    FROM packets
                    WHERE date(imported_at) = ?
                    GROUP BY hour
                """, (today_str,))
                hour_map = {row[0]: row[1] for row in cursor.fetchall()}
                for h in range(24):
                    h_str = f"{h:02d}"
                    labels.append(f"{h_str}:00")
                    traffic_data.append(hour_map.get(h_str, 0))

            elif period == '24h':
                since_time = (datetime.now() - timedelta(hours=23)).strftime('%Y-%m-%d %H:00:00')
                cursor.execute("""
                    SELECT strftime('%Y-%m-%d %H:00', imported_at) as hour_slot,
                           COUNT(*) as packet_count
                    FROM packets
                    WHERE imported_at >= ?
                    GROUP BY hour_slot
                """, (since_time,))
                slot_map = {row[0]: row[1] for row in cursor.fetchall()}
                now = datetime.now()
                for i in range(23, -1, -1):
                    t = now - timedelta(hours=i)
                    slot_key = t.strftime('%Y-%m-%d %H:00')
                    labels.append(t.strftime('%H:00'))
                    traffic_data.append(slot_map.get(slot_key, 0))

            elif period in ['7d', '30d']:
                days = 7 if period == '7d' else 30
                since_date = (datetime.now() - timedelta(days=days - 1)).strftime('%Y-%m-%d 00:00:00')
                cursor.execute("""
                    SELECT date(imported_at) as day,
                           COUNT(*) as packet_count
                    FROM packets
                    WHERE imported_at >= ?
                    GROUP BY day
                """, (since_date,))
                day_map = {row[0]: row[1] for row in cursor.fetchall()}
                now = datetime.now()
                for i in range(days - 1, -1, -1):
                    d = now - timedelta(days=i)
                    day_key = d.strftime('%Y-%m-%d')
                    labels.append(d.strftime('%m/%d'))
                    traffic_data.append(day_map.get(day_key, 0))
        except Exception as e:
            print(f"[PacketDB-FEC] get_hourly_packet_traffic error: {e}")

        return {
            "period": period,
            "labels": labels,
            "packets": traffic_data
        }

    def get_system_storage_metrics(self) -> Dict[str, Any]:
        """ストレージ使用量およびシステム統計"""
        db_size_bytes = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
        db_size_mb = round(db_size_bytes / (1024 * 1024), 2)

        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM imported_files")
        row_files = cursor.fetchone()
        imported_files_count = row_files[0] if row_files else 0

        cursor.execute("SELECT COUNT(*) FROM packets")
        row_pkts = cursor.fetchone()
        total_packets = row_pkts[0] if row_pkts else 0

        return {
            "db_size_mb": db_size_mb,
            "imported_files_count": imported_files_count,
            "total_packets": total_packets
        }

    def optimize_database(self) -> bool:
        """DB の VACUUM 最適化を実行"""
        try:
            self.conn.execute("VACUUM")
            return True
        except Exception as e:
            print(f"[PacketDB-FEC] Optimize error: {e}")
            return False

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass
