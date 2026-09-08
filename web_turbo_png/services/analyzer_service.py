import os
import sys
from typing import Any
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
            item["engine_mode"] = target_mode
            item["target_ext"] = target_ext
            item["total_required"] = total_required
            t_count = int(item["tile_count"]) if item.get("tile_count") is not None else 0
            item["restoration_score"] = round((t_count / total_required) * 100, 1) if total_required > 0 else 0.0

            # 画像プレビューパス（指定モードの拡張子のみを厳格に探索）
            img_filename = f"restored_ID_{img_hex}{target_ext}"
            thumb_path = None
            base_url = None

            if os.path.exists(os.path.join(static_out, img_filename)):
                thumb_path = os.path.join(static_out, img_filename)
                base_url = f"/static/output/{img_filename}"
            elif target_mode == "JPEG" and os.path.exists(os.path.join(ROOT_DIR, "data", "digital_turbo_jpeg", "images", img_filename)):
                thumb_path = os.path.join(ROOT_DIR, "data", "digital_turbo_jpeg", "images", img_filename)
                base_url = f"/data/digital_turbo_jpeg/images/{img_filename}"
            elif target_mode == "PNG" and os.path.exists(os.path.join(ROOT_DIR, "data", "images", img_filename)):
                thumb_path = os.path.join(ROOT_DIR, "data", "images", img_filename)
                base_url = f"/data/images/{img_filename}"

            if base_url and thumb_path and os.path.exists(thumb_path):
                # ブラウザキャッシュによるアイコン不一致（古い画像表示）を防止
                try:
                    v = int(os.path.getmtime(thumb_path))
                    item["thumbnail_url"] = f"{base_url}?v={v}"
                except Exception:
                    item["thumbnail_url"] = base_url
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
            os.path.join(ROOT_DIR, "data", "digital_turbo_png", "images"),
            os.path.join(ROOT_DIR, "data", "digital_turbo_jpeg", "images"),
            os.path.join(ROOT_DIR, "web_turbo_png", "static", "output")
        ]

        for hex_id in image_ids_hex_list:
            clean_hex = str(hex_id).strip().upper().zfill(4)
            raw_hex = str(hex_id).strip().upper()
            patterns = [
                f"*ID_{clean_hex}*.png",
                f"*ID_{clean_hex}*.jpg",
                f"*ID_{raw_hex}*.png",
                f"*ID_{raw_hex}*.jpg",
                f"*{clean_hex}*.png",
                f"*{clean_hex}*.jpg"
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

    def clear_all_images(self, mode_only=False, target_mode=None):
        """データベース内の全画像・パケットおよび復元ファイルをすべて削除・一掃"""
        deleted_packets = 0
        effective_mode = (target_mode or self.current_mode).upper()
        modes_to_clear = [effective_mode] if mode_only else ["PNG", "JPEG"]

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
                    cursor.execute("DELETE FROM imported_files")
                    try:
                        cursor.execute("DELETE FROM finalized_tiles")
                    except Exception:
                        pass
                agg.db.close()
            except Exception as e:
                print(f"Error clearing packets table ({m}): {e}")

        import glob
        deleted_files_count = 0
        directories_to_clean = [
            os.path.join(ROOT_DIR, "data", "images"),
            os.path.join(ROOT_DIR, "data", "digital_turbo_png", "images"),
            os.path.join(ROOT_DIR, "data", "digital_turbo_jpeg", "images"),
            os.path.join(ROOT_DIR, "web_turbo_png", "static", "output")
        ]
        
        target_patterns = ["*.png", "*.jpg", "*.jpeg"]
        if mode_only:
            target_patterns = ["*.jpg", "*.jpeg"] if effective_mode == "JPEG" else ["*.png"]

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

    def detect_image_mode(self, target_image_id_hex):
        """画像IDが PNG / JPEG どちらのDBまたはファイルに存在するかを自動判定"""
        clean_hex = str(target_image_id_hex).strip().upper().zfill(4)
        try:
            target_id_int = int(clean_hex, 16)
        except (ValueError, TypeError):
            return self.current_mode

        # 1. 静的出力ファイルの拡張子をチェック
        static_out = os.path.join(ROOT_DIR, "web_turbo_png", "static", "output")
        jpg_exists = any(os.path.exists(os.path.join(static_out, f"{p}_ID_{clean_hex}.jpg")) for p in ("restored", "user_1", "user_cumulative_1"))
        png_exists = any(os.path.exists(os.path.join(static_out, f"{p}_ID_{clean_hex}.png")) for p in ("restored", "user_1", "user_cumulative_1"))
        if jpg_exists and not png_exists:
            return "JPEG"
        if png_exists and not jpg_exists:
            return "PNG"

        # 2. DB を確認
        for check_mode in (self.current_mode, "JPEG" if self.current_mode == "PNG" else "PNG"):
            try:
                cfg = SystemFactory.get_config(check_mode)
                ldir = getattr(cfg, "TEXT_LOG_DIR", f"data/digital_turbo_{check_mode.lower()}/logs")
                ldir_path = os.path.join(ROOT_DIR, ldir) if not os.path.isabs(ldir) else ldir
                agg = SystemFactory.get_aggregator(log_dir=ldir_path, mode=check_mode)
                cur = agg.db.conn.cursor()
                cur.execute("SELECT 1 FROM packets WHERE image_id = ? LIMIT 1", (target_id_int,))
                if cur.fetchone():
                    return check_mode
            except Exception:
                pass

        return self.current_mode

    def get_image_status(self, target_image_id_hex, user_id=None):
        """指定画像の全体復元状況および特定ユーザーの貢献状況を取得"""
        clean_hex = str(target_image_id_hex).strip().upper().zfill(4)
        effective_mode = self.detect_image_mode(clean_hex)
        config = SystemFactory.get_config(effective_mode)

        try:
            target_id_int = int(clean_hex, 16)
        except (ValueError, TypeError):
            return {
                "image_id": clean_hex,
                "engine_mode": effective_mode,
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

        # 対象モードのアグリゲータを使用
        ldir = getattr(config, "TEXT_LOG_DIR", f"data/digital_turbo_{effective_mode.lower()}/logs")
        ldir_path = os.path.join(ROOT_DIR, ldir) if not os.path.isabs(ldir) else ldir
        agg = SystemFactory.get_aggregator(log_dir=ldir_path, mode=effective_mode)
        cursor = agg.db.conn.cursor()

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
            if row_user and (row_user[0] or 0) > 0:
                user_packet_count = row_user[0] or 0
                user_matched_count = row_user[1] or 0
                user_has_data = True
                user_score = round((user_matched_count / total_required) * 100, 1) if total_required > 0 else 0.0
                if user_score > 100.0:
                    user_score = 100.0

        static_out = os.path.join(ROOT_DIR, "web_turbo_png", "static", "output")
        target_ext = ".jpg" if effective_mode == "JPEG" else ".png"

        # ユーザー単体画像（現在のモードの拡張子のみ）
        user_img_url = None
        target_uid = user_id or 1
        fname_u = f"user_{target_uid}_ID_{clean_hex}{target_ext}"
        if os.path.exists(os.path.join(static_out, fname_u)):
            user_img_url = f"/static/output/{fname_u}"
            user_has_data = True

        # ユーザー累積画像（現在のモードの拡張子のみ）
        user_cumulative_url = None
        fname_c = f"user_cumulative_{target_uid}_ID_{clean_hex}{target_ext}"
        if os.path.exists(os.path.join(static_out, fname_c)):
            user_cumulative_url = f"/static/output/{fname_c}"
            user_has_data = True

        # ネットワーク復元画像（現在のモードの拡張子のみ）
        restored_img_url = None
        fname_r = f"restored_ID_{clean_hex}{target_ext}"
        static_file = os.path.join(static_out, fname_r)
        if os.path.exists(static_file):
            restored_img_url = f"/static/output/{fname_r}"
        else:
            source_dir = os.path.join(ROOT_DIR, "data", f"digital_turbo_{effective_mode.lower()}", "images")
            source_file = os.path.join(source_dir, fname_r)
            if os.path.exists(source_file):
                import shutil
                try:
                    shutil.copy2(source_file, static_file)
                    restored_img_url = f"/static/output/{fname_r}"
                except Exception:
                    restored_img_url = f"/data/digital_turbo_{effective_mode.lower()}/images/{fname_r}"
            elif effective_mode == "PNG" and os.path.exists(os.path.join(ROOT_DIR, "data", "images", fname_r)):
                restored_img_url = f"/data/images/{fname_r}"

        # フォールバック: もし user_has_data かつ user_img_url がまだ無ければ restored_img_url を流用
        if user_has_data and not user_img_url:
            user_img_url = restored_img_url

        return {
            "image_id": clean_hex,
            "engine_mode": effective_mode,
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

    def calculate_reliability_scores(self, target_image_id_hex: str, current_user_id: int | None = None) -> list[dict[str, Any]]:
        """各タイルのSNR重み付き信頼度および貢献度情報を計算して返す"""
        reliability_map: list[dict[str, Any]] = []
        clean_hex = str(target_image_id_hex).strip().upper().zfill(4)
        try:
            target_id_int = int(clean_hex, 16)
        except (ValueError, TypeError):
            return reliability_map

        effective_mode = self.detect_image_mode(clean_hex)
        config = SystemFactory.get_config(effective_mode)
        ldir = getattr(config, "TEXT_LOG_DIR", f"data/digital_turbo_{effective_mode.lower()}/logs")
        ldir_path = os.path.join(ROOT_DIR, ldir) if not os.path.isabs(ldir) else ldir
        agg = SystemFactory.get_aggregator(log_dir=ldir_path, mode=effective_mode)

        tiles_data = agg.db.get_packets_for_image(target_id_int)
        if not tiles_data:
            return reliability_map

        from web_turbo_png.services.auth_db import get_auth_db
        auth_db = get_auth_db()
        user_info_cache: dict[int, dict[str, Any] | None] = {}

        tile_count_x = config.TILE_COUNT_X
        tile_count_y = config.TILE_COUNT_Y

        for ty in range(tile_count_y):
            for tx in range(tile_count_x):
                if ty not in tiles_data or tx not in tiles_data[ty]:
                    continue

                len_dict = tiles_data[ty][tx]
                if not len_dict:
                    continue

                best_plen = None
                max_weight_sum = -1.0
                for plen, pkts in len_dict.items():
                    w_sum = sum(max(0.1, float(p[1]) + 1.0) for p in pkts)
                    if w_sum > max_weight_sum:
                        max_weight_sum = w_sum
                        best_plen = plen

                if not best_plen:
                    continue

                packets = len_dict[best_plen]
                payload_len = best_plen * 8
                total_files = len(packets)
                total_weight_sum = sum(max(0.1, float(p[1]) + 1.0) for p in packets)

                score_0 = np.zeros(payload_len, dtype=float)
                score_1 = np.zeros(payload_len, dtype=float)

                for payload_bits_str, snr, file_name, p_user_id, imported_at in packets:
                    weight = max(0.1, float(snr) + 1.0)
                    if len(payload_bits_str) < payload_len:
                        continue
                    for i, bit_char in enumerate(payload_bits_str[:payload_len]):
                        if bit_char == '1':
                            score_1[i] += weight
                        elif bit_char == '0':
                            score_0[i] += weight

                confidences = []
                for i in range(payload_len):
                    diff = abs(score_1[i] - score_0[i])
                    confidence = min(100.0, (diff / max(1.0, total_weight_sum)) * 100.0)
                    confidences.append(confidence)

                avg_confidence = float(np.mean(confidences)) if confidences else 0.0
                avg_snr = (sum(float(p[1]) for p in packets) / total_files) if total_files > 0 else 0.0

                sources = []
                is_contributed = False
                for payload_bits_str, snr, file_name, p_user_id, imported_at in packets:
                    if current_user_id is not None and p_user_id == current_user_id:
                        is_contributed = True

                    sender_name = "ゲスト"
                    location = "Local"
                    if p_user_id:
                        if p_user_id not in user_info_cache:
                            user_info_cache[p_user_id] = auth_db.get_user_by_id(p_user_id)
                        u_info = user_info_cache[p_user_id]
                        if u_info:
                            callsign = u_info.get("callsign") or ""
                            disp = u_info.get("display_name") or u_info.get("email", "").split("@")[0]
                            sender_name = f"{disp} ({callsign})" if callsign else disp
                            location = u_info.get("location") or "Japan"
                        else:
                            sender_name = f"局 #{p_user_id}"

                    sources.append({
                        "sender": sender_name,
                        "location": location,
                        "received_at": str(imported_at) if imported_at else "Now",
                        "file_name": file_name or f"packet_{len(sources)}",
                        "snr": round(float(snr), 1)
                    })

                reliability_map.append({
                    "line": ty,
                    "block": tx,
                    "score": round(avg_confidence, 1),
                    "avg_snr": round(avg_snr, 1),
                    "samples": total_files,
                    "total_weight": int(total_weight_sum),
                    "is_contributed": is_contributed,
                    "sources": sources
                })

        return reliability_map

    def get_snr_analytics(self) -> dict[str, Any]:
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
