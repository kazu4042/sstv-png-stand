import os
import sys
import numpy as np

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "../../"))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.system_factory import SystemFactory


class TurboPNGAnalyzerService:
    """SSTV Turbo 統合アナライザーサービス（PNG/JPEG プラガブル・完全分離対応）"""

    def __init__(self, mode=None):
        self._target_mode = mode.upper() if mode else None
        self._last_mode = None
        self._ensure_mode_synced()

    @property
    def current_mode(self):
        return self._target_mode or SystemFactory.get_mode()

    def _ensure_mode_synced(self):
        mode_name = self.current_mode
        if getattr(self, '_last_mode', None) == mode_name:
            return

        self._last_mode = mode_name
        self.config = SystemFactory.get_config(mode_name)
        log_dir = getattr(self.config, "TEXT_LOG_DIR", f"data/digital_turbo_{mode_name.lower()}/logs")
        if not os.path.isabs(log_dir):
            self.log_directory = os.path.join(ROOT_DIR, log_dir)
        else:
            self.log_directory = log_dir

        self.aggregator = SystemFactory.get_aggregator(log_dir=self.log_directory, mode=mode_name)
        self.aggregator.load_all_logs()

    def get_available_image_ids(self, user_id=None):
        """DB に存在する画像IDを16進数文字列のリストで返す（現在のモードのみ）"""
        self._ensure_mode_synced()
        image_counts = self.aggregator.db.get_all_image_ids_with_counts(user_id=user_id)
        return sorted([f"{img_id:04X}" for img_id in image_counts.keys()])

    def get_merge_stats(self, target_image_id_hex):
        return {"total_merged": 0, "details": []}

    def get_all_images_summary(self, mode=None):
        """管理画面用: 指定モード（デフォルトは現在稼働中モード）の画像ID詳細一覧を取得"""
        target_mode = (mode or self.current_mode).upper()
        # 一時的に対象モードのアグリゲータを使用
        target_config = SystemFactory.get_config(target_mode)
        log_dir = getattr(target_config, "TEXT_LOG_DIR", f"data/digital_turbo_{target_mode.lower()}/logs")
        if not os.path.isabs(log_dir):
            log_dir_path = os.path.join(ROOT_DIR, log_dir)
        else:
            log_dir_path = log_dir

        target_aggregator = SystemFactory.get_aggregator(log_dir=log_dir_path, mode=target_mode)
        target_aggregator.load_all_logs()

        summaries = target_aggregator.db.get_images_summary()
        total_required = target_config.TILE_COUNT_X * target_config.TILE_COUNT_Y
        static_out = os.path.join(ROOT_DIR, "web_turbo_png", "static", "output")
        target_ext = ".jpg" if target_mode == "JPEG" else ".png"

        for item in summaries:
            img_hex = item["image_id_hex"]
            item["mode"] = target_mode
            item["format"] = target_mode
            item["target_ext"] = target_ext
            item["total_required"] = total_required
            t_count = int(item["tile_count"]) if item.get("tile_count") is not None else 0
            item["restoration_score"] = round((t_count / total_required) * 100, 1) if total_required > 0 else 0.0

            # 画像プレビューパス（指定モードの拡張子のみを厳格に探索）
            img_filename = f"restored_ID_{img_hex}{target_ext}"

            if os.path.exists(os.path.join(static_out, img_filename)):
                item["thumbnail_url"] = f"/static/output/{img_filename}"
            elif target_mode == "JPEG" and os.path.exists(os.path.join(ROOT_DIR, "data", "digital_turbo_jpeg", "images", img_filename)):
                item["thumbnail_url"] = f"/data/digital_turbo_jpeg/images/{img_filename}"
            elif target_mode == "PNG" and os.path.exists(os.path.join(ROOT_DIR, "data", "images", img_filename)):
                item["thumbnail_url"] = f"/data/images/{img_filename}"
            else:
                item["thumbnail_url"] = None

        return summaries

    def delete_images(self, image_ids_hex_list):
        """指定された画像IDのDBレコードおよび画像ファイルを一括削除（PNG/JPEG両方のDBから安全に消去）"""
        if not image_ids_hex_list:
            return {"deleted_packets": 0, "deleted_images": 0, "deleted_files": 0}

        int_ids = []
        for hex_id in image_ids_hex_list:
            try:
                int_ids.append(int(str(hex_id).strip(), 16))
            except (ValueError, TypeError):
                pass

        if not int_ids:
            return {"deleted_packets": 0, "deleted_images": 0, "deleted_files": 0}

        # PNG と JPEG 両方のアグリゲータを取得してDBから削除
        deleted_packets = 0
        for m in ("PNG", "JPEG"):
            try:
                cfg = SystemFactory.get_config(m)
                ldir = getattr(cfg, "TEXT_LOG_DIR", f"data/digital_turbo_{m.lower()}/logs")
                ldir_path = os.path.join(ROOT_DIR, ldir) if not os.path.isabs(ldir) else ldir
                agg = SystemFactory.get_aggregator(log_dir=ldir_path, mode=m)
                deleted_packets += agg.db.delete_images_by_ids(int_ids)
                agg.db.close()
            except Exception as e:
                print(f"Error deleting from {m} db: {e}")

        import glob
        deleted_files_count = 0
        directories_to_clean = [
            os.path.join(ROOT_DIR, "data", "images"),
            os.path.join(ROOT_DIR, "data", "digital_turbo_jpeg", "images"),
            os.path.join(ROOT_DIR, "web_turbo_png", "static", "output")
        ]

        for hex_id in image_ids_hex_list:
            clean_hex = str(hex_id).strip().upper().zfill(4)
            patterns = [
                f"*ID_{clean_hex}*.png",
                f"*ID_{clean_hex}*.jpg",
                f"*ID_{str(hex_id).strip().upper()}*.png",
                f"*ID_{str(hex_id).strip().upper()}*.jpg"
            ]
            for dir_path in directories_to_clean:
                if not os.path.exists(dir_path):
                    continue
                for pat in patterns:
                    for f in glob.glob(os.path.join(dir_path, pat)):
                        try:
                            os.remove(f)
                            deleted_files_count += 1
                        except Exception as e:
                            print(f"Error removing {f}: {e}")

        from web_turbo_png.routes.api_routes import invalidate_analyzer_cache
        invalidate_analyzer_cache()

        return {
            "deleted_packets": deleted_packets,
            "deleted_images": len(int_ids),
            "deleted_files": deleted_files_count
        }

    def clear_all_images(self, mode_only=False):
        """データベース内の全画像・パケットおよび復元ファイルをすべて削除・一掃"""
        deleted_packets = 0
        modes_to_clear = [self.current_mode] if mode_only else ["PNG", "JPEG"]

        for m in modes_to_clear:
            try:
                cfg = SystemFactory.get_config(m)
                ldir = getattr(cfg, "TEXT_LOG_DIR", f"data/digital_turbo_{m.lower()}/logs")
                ldir_path = os.path.join(ROOT_DIR, ldir) if not os.path.isabs(ldir) else ldir
                agg = SystemFactory.get_aggregator(log_dir=ldir_path, mode=m)
                with agg.db.conn:
                    cursor = agg.db.conn.cursor()
                    cursor.execute("DELETE FROM packets")
                    deleted_packets += cursor.rowcount
                agg.db.close()
            except Exception as e:
                print(f"Error clearing packets table ({m}): {e}")

        import glob
        deleted_files_count = 0
        directories_to_clean = [
            os.path.join(ROOT_DIR, "data", "images"),
            os.path.join(ROOT_DIR, "data", "digital_turbo_jpeg", "images"),
            os.path.join(ROOT_DIR, "web_turbo_png", "static", "output")
        ]
        
        target_patterns = ["*.png", "*.jpg", "*.jpeg"]
        if mode_only:
            target_patterns = ["*.jpg", "*.jpeg"] if self.current_mode == "JPEG" else ["*.png"]

        for dir_path in directories_to_clean:
            if not os.path.exists(dir_path):
                continue
            for pat in target_patterns:
                for f in glob.glob(os.path.join(dir_path, pat)):
                    try:
                        os.remove(f)
                        deleted_files_count += 1
                    except Exception as e:
                        print(f"Error removing {f}: {e}")

        from web_turbo_png.routes.api_routes import invalidate_analyzer_cache
        invalidate_analyzer_cache()

        return {
            "deleted_packets": deleted_packets,
            "deleted_images": 0,
            "deleted_files": deleted_files_count
        }

    def get_image_status(self, target_image_id_hex, user_id=None):
        """指定画像の全体復元状況および特定ユーザーの貢献状況を高速取得"""
        config = SystemFactory.get_config()
        try:
            target_id_int = int(target_image_id_hex, 16)
        except (ValueError, TypeError):
            return {
                "image_id": target_image_id_hex,
                "user_has_data": False,
                "user_packet_count": 0,
                "user_matched_count": 0,
                "user_score": 0.0,
                "network_received": 0,
                "network_score": 0.0,
                "total_required": config.TILE_COUNT_X * config.TILE_COUNT_Y
            }

        tile_count_x = config.TILE_COUNT_X
        tile_count_y = config.TILE_COUNT_Y
        total_required = tile_count_x * tile_count_y

        cursor = self.aggregator.db.conn.cursor()

        cursor.execute("""
            SELECT COUNT(DISTINCT tile_y || '_' || tile_x)
            FROM packets
            WHERE image_id = ?
        """, (target_id_int,))
        row_net = cursor.fetchone()
        network_received = row_net[0] if row_net and row_net[0] is not None else 0
        network_score = round((network_received / total_required) * 100, 1) if total_required > 0 else 0.0

        user_has_data = False
        user_packet_count = 0
        user_matched_count = 0
        user_score = 0.0

        if user_id:
            cursor.execute("""
                SELECT COUNT(*), COUNT(DISTINCT tile_y || '_' || tile_x)
                FROM packets
                WHERE image_id = ? AND user_id = ?
            """, (target_id_int, user_id))
            row_user = cursor.fetchone()
            if row_user:
                user_packet_count = row_user[0] or 0
                user_matched_count = row_user[1] or 0
                user_has_data = user_packet_count > 0
                user_score = round((user_matched_count / total_required) * 100, 1) if total_required > 0 else 0.0
                if user_score > 100.0:
                    user_score = 100.0

        static_out = os.path.join(ROOT_DIR, "web_turbo_png", "static", "output")
        mode_name = SystemFactory.get_mode()
        target_ext = ".jpg" if mode_name == "JPEG" else ".png"

        # ユーザー単体画像 (現在のエンジンモードのみ厳格に探索)
        user_img_url = None
        if user_id and user_has_data:
            fname = f"user_{user_id}_ID_{target_image_id_hex}{target_ext}"
            if os.path.exists(os.path.join(static_out, fname)):
                user_img_url = f"/static/output/{fname}"

        # ユーザー累積画像 (現在のエンジンモードのみ厳格に探索)
        user_cumulative_url = None
        if user_id and user_has_data:
            fname = f"user_cumulative_{user_id}_ID_{target_image_id_hex}{target_ext}"
            if os.path.exists(os.path.join(static_out, fname)):
                user_cumulative_url = f"/static/output/{fname}"

        # ネットワーク復元画像 (現在のエンジンモードのみ厳格に探索)
        restored_img_url = None
        fname = f"restored_ID_{target_image_id_hex}{target_ext}"
        if os.path.exists(os.path.join(static_out, fname)):
            restored_img_url = f"/static/output/{fname}"
        elif mode_name == "JPEG" and os.path.exists(os.path.join(ROOT_DIR, "data", "digital_turbo_jpeg", "images", fname)):
            restored_img_url = f"/data/digital_turbo_jpeg/images/{fname}"
        elif mode_name == "PNG" and os.path.exists(os.path.join(ROOT_DIR, "data", "images", fname)):
            restored_img_url = f"/data/images/{fname}"

        return {
            "image_id": target_image_id_hex,
            "user_has_data": user_has_data,
            "user_packet_count": user_packet_count,
            "user_matched_count": user_matched_count,
            "user_score": user_score,
            "network_received": network_received,
            "network_score": network_score,
            "total_required": total_required,
            "user_img_url": user_img_url,
            "user_cumulative_url": user_cumulative_url,
            "restored_img_url": restored_img_url
        }

    def _get_tiles_data(self, target_id_int):
        return self.aggregator.db.get_packets_for_image(target_id_int)

    def find_missing_packets(self, target_image_id_hex, max_limit=2048):
        missing_list = []
        try:
            target_id_int = int(target_image_id_hex, 16)
        except (ValueError, TypeError):
            return missing_list

        config = SystemFactory.get_config()
        tile_count_x = config.TILE_COUNT_X
        tile_count_y = config.TILE_COUNT_Y
        poor_threshold = getattr(config, 'POOR_BLOCK_SNR_THRESHOLD', 5.0)

        cursor = self.aggregator.db.conn.cursor()
        cursor.execute("""
            SELECT tile_y, tile_x, COUNT(*), MAX(snr)
            FROM packets
            WHERE image_id = ?
            GROUP BY tile_y, tile_x
        """, (target_id_int,))

        tile_map = {}
        for row in cursor.fetchall():
            ty, tx, count, max_s = row
            tile_map[(ty, tx)] = {"count": count, "max_snr": max_s or 0.0}

        for ty in range(tile_count_y):
            for tx in range(tile_count_x):
                block_id = ty * tile_count_x + tx
                data = tile_map.get((ty, tx))
                if data is None:
                    missing_list.append({
                        "block_id": block_id,
                        "tile_x": tx,
                        "tile_y": ty,
                        "status": "MISSING",
                        "copies": 0,
                        "max_snr": 0.0
                    })
                elif data["max_snr"] < poor_threshold:
                    missing_list.append({
                        "block_id": block_id,
                        "tile_x": tx,
                        "tile_y": ty,
                        "status": "POOR_QUALITY",
                        "copies": data["count"],
                        "max_snr": round(data["max_snr"], 1)
                    })

                if len(missing_list) >= max_limit:
                    return missing_list

        return missing_list

    def get_snr_analytics(self):
        return self.aggregator.db.get_snr_analytics()

    def get_top_contributors(self, limit=5):
        raw_list = self.aggregator.db.get_top_contributors(limit=limit)
        from web_turbo_png.services.auth_db import get_auth_db
        auth_db = get_auth_db()

        for item in raw_list:
            u_id = item.get("user_id")
            if u_id:
                u_info = auth_db.get_user_by_id(u_id)
                if u_info:
                    item["email"] = u_info.get("email", f"user_{u_id}@example.com")
                    item["display_name"] = u_info.get("display_name") or str(item["email"]).split("@")[0]
                else:
                    item["email"] = f"user_{u_id}@example.com"
                    item["display_name"] = f"局 #{u_id}"
            else:
                item["email"] = "ゲスト"
                item["display_name"] = "ゲスト"
        return raw_list

    def get_hourly_packet_traffic(self, period='today'):
        return self.aggregator.db.get_hourly_packet_traffic(period=period)

    def get_system_health_metrics(self):
        storage = self.aggregator.db.get_system_storage_metrics()
        out_dir = os.path.join(ROOT_DIR, "web_turbo_png", "static", "output")
        img_bytes = 0
        img_count = 0
        if os.path.exists(out_dir):
            for f in os.listdir(out_dir):
                fp = os.path.join(out_dir, f)
                if os.path.isfile(fp):
                    img_bytes += os.path.getsize(fp)
                    img_count += 1
        storage["image_storage_mb"] = round(img_bytes / (1024 * 1024), 2)
        storage["restored_files_count"] = img_count
        return storage

    def get_restoration_overview(self):
        config = SystemFactory.get_config()
        summaries = self.aggregator.db.get_images_summary()
        total_req = config.TILE_COUNT_X * config.TILE_COUNT_Y
        complete = 0
        in_progress = 0
        for s in summaries:
            s_tcount = int(s["tile_count"]) if s.get("tile_count") is not None else 0
            if s_tcount >= total_req:
                complete += 1
            else:
                in_progress += 1
        return {
            "total_images": len(summaries),
            "complete_images": complete,
            "in_progress_images": in_progress
        }

    def optimize_database(self):
        return self.aggregator.db.optimize_database()

    def optimize_db(self):
        return self.optimize_database()
