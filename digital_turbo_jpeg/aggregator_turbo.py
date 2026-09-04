import sys
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

import os
import glob
import re
import numpy as np
from PIL import Image, ImageFile, ImageFilter
import io
from collections import defaultdict

# 破損・途切れ・ノイズ混じりJPEGでも例外で捨てずに部分描画する設定
setattr(ImageFile, 'LOAD_TRUNCATED_IMAGES', True)

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

from digital_turbo_jpeg import config_turbo as config
from digital_turbo_jpeg.database_turbo import PacketDatabaseTurboJPEG
from core.base_interfaces import BaseAggregator


class TurboJPEGAggregator(BaseAggregator):
    """SSTV Turbo JPEG アグリゲータ (BaseAggregator 準拠・超耐ノイズ仕様)
    ノイズや欠損のあるパケットから、ビット多数決・ピクセル領域SNR加重平均・欠損インペインティングを駆使して極限まで高品質に画像を復元する。
    """
    def __init__(self, log_dir=None):
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
        if log_dir is None:
            self.log_dir = os.path.join(root_dir, getattr(config, "TEXT_LOG_DIR", "data/digital_turbo_jpeg/logs"))
        else:
            self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)
        self.db = PacketDatabaseTurboJPEG(self.log_dir)
        self._header_templates = {}

    def load_all_logs(self) -> bool:
        log_files = glob.glob(os.path.join(self.log_dir, f"{config.TEXT_LOG_PREFIX}_*.txt"))
        if not log_files:
            return False

        new_files = [f for f in log_files if not self.db.is_file_imported(os.path.basename(f))]
        if not new_files:
            return True

        for file_path in new_files:
            file_name = os.path.basename(file_path)
            user_id = None
            m = re.search(r'_user_(\d+)_', file_name)
            if m:
                user_id = int(m.group(1))

            file_packets = []
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    if all(c in '01' for c in line):
                        try:
                            hdr = (config.BIT_IMAGE_CRC + config.BIT_TILE_X
                                   + config.BIT_TILE_Y + config.BIT_PAYLOAD_LENGTH)
                            if len(line) < hdr + 4:
                                continue
                            idx = 0
                            img_id         = int(line[idx : idx + config.BIT_IMAGE_CRC], 2);  idx += config.BIT_IMAGE_CRC
                            tile_x         = int(line[idx : idx + config.BIT_TILE_X],    2);  idx += config.BIT_TILE_X
                            tile_y         = int(line[idx : idx + config.BIT_TILE_Y],    2);  idx += config.BIT_TILE_Y
                            payload_length = int(line[idx : idx + config.BIT_PAYLOAD_LENGTH], 2); idx += config.BIT_PAYLOAD_LENGTH
                            payload_bits   = line[idx : idx + payload_length * 8]
                            snr_str        = line[idx + payload_length * 8 :]
                            snr_val        = float(int(snr_str, 2)) if len(snr_str) == 4 else 1.0
                            file_packets.append((img_id, tile_x, tile_y, payload_length, payload_bits, snr_val))
                        except (ValueError, IndexError):
                            continue
                    else:
                        parts = line.split(",")
                        if len(parts) >= 6:
                            try:
                                img_id         = int(parts[0])
                                tile_x         = int(parts[1])
                                tile_y         = int(parts[2])
                                payload_length = int(parts[3])
                                payload_bits   = parts[4]
                                snr_str        = parts[5].strip()
                                snr_val = float(int(snr_str, 2)) if (all(c in '01' for c in snr_str) and len(snr_str) == 4) else float(snr_str)
                                file_packets.append((img_id, tile_x, tile_y, payload_length, payload_bits, snr_val))
                            except ValueError:
                                continue

            if file_packets:
                self.db.insert_packets_bulk(file_name, file_packets, user_id=user_id)
        return True

    def bits_to_bytearray(self, bits_str):
        """NumPy ベクトル化: ビット文字列を一括でバイト配列に変換"""
        n = (len(bits_str) // 8) * 8
        if n == 0:
            return bytearray()
        bit_arr = np.frombuffer(bits_str[:n].encode('ascii'), dtype=np.uint8) - ord('0')
        byte_weights = np.array([128, 64, 32, 16, 8, 4, 2, 1], dtype=np.uint8)
        byte_arr = bit_arr.reshape(-1, 8) @ byte_weights
        return bytearray(byte_arr.astype(np.uint8).tobytes())

    def bit_majority_vote(self, packets, payload_bit_len):
        """複数パケットのビットごとのSNR加重多数決により、ビット誤りを消滅させる"""
        if not packets:
            return ""
        if len(packets) == 1:
            return packets[0][0][:payload_bit_len]

        vote_sums = np.zeros(payload_bit_len, dtype=np.float64)
        total_weights = 0.0

        for item in packets:
            p_str = item[0]
            snr = item[1]
            if len(p_str) < payload_bit_len:
                continue
            weight = snr + 1.0
            bits = (np.frombuffer(p_str[:payload_bit_len].encode('ascii'), dtype=np.uint8) - ord('0')).astype(np.float64)
            vote_sums += (bits * 2.0 - 1.0) * weight
            total_weights += weight

        voted_bits = (vote_sums >= 0).astype(np.uint8)
        return "".join(str(b) for b in voted_bits)

    def _get_jpeg_header_template(self, tile_w, tile_h):
        """指定タイルサイズ用の正常な JPEG ヘッダテンプレート (SOI〜SOS) をキャッシュ"""
        key = (tile_w, tile_h)
        if key not in self._header_templates:
            dummy = Image.new("RGB", (tile_w, tile_h), color=(0, 0, 0))
            bio = io.BytesIO()
            restart_int = getattr(config, "JPEG_RESTART_MARKER", 1)
            dummy.save(bio, format="JPEG", quality=config.JPEG_QUALITY, restart_marker=restart_int)
            raw = bio.getvalue()
            sos_pos = raw.find(b'\xff\xda')
            if sos_pos != -1:
                sos_len = (raw[sos_pos+2] << 8) | raw[sos_pos+3]
                self._header_templates[key] = (raw[:sos_pos + 2 + sos_len], sos_pos + 2 + sos_len)
            else:
                self._header_templates[key] = (None, 0)
        return self._header_templates[key]

    def decode_tile_bytes_safely(self, p_bytes, tile_w=None, tile_h=None):
        """JPEGバイト列を安全にデコード（ノイズ・破損・RSTマーカー付きでも部分描画を徹底試行）"""
        if not p_bytes or len(p_bytes) < 4:
            return None

        # 1. そのまま一度デコードを試みる
        try:
            tile_img = Image.open(io.BytesIO(p_bytes)).convert("RGB")
            tile_img.load()
            return tile_img
        except Exception:
            pass

        # 2. SOI (0xFF 0xD8) の位置を探索して位置補正 ＋ 末尾 (EOI: 0xFF 0xD9) 修復
        raw_bytes = bytes(p_bytes)
        soi_idx = raw_bytes.find(b'\xff\xd8')
        if soi_idx != -1:
            fixed_bytes = bytearray(raw_bytes[soi_idx:])
        else:
            fixed_bytes = bytearray(b'\xff\xd8' + raw_bytes)

        if not fixed_bytes.endswith(b'\xff\xd9'):
            fixed_bytes.extend(b'\xff\xd9')

        try:
            tile_img = Image.open(io.BytesIO(fixed_bytes)).convert("RGB")
            tile_img.load()
            return tile_img
        except Exception:
            pass

        # 3. 高度な修復: ヘッダ全壊時、正常な JPEG 構造テンプレート (DQT/DHT/SOF/SOS) で差し替え
        if tile_w and tile_h:
            try:
                hdr_tmpl, sos_off = self._get_jpeg_header_template(tile_w, tile_h)
                if hdr_tmpl and len(p_bytes) > sos_off:
                    repaired_bytes = bytearray(hdr_tmpl + p_bytes[sos_off:])
                    if not repaired_bytes.endswith(b'\xff\xd9'):
                        repaired_bytes.extend(b'\xff\xd9')
                    tile_img = Image.open(io.BytesIO(repaired_bytes)).convert("RGB")
                    tile_img.load()
                    return tile_img
            except Exception:
                pass

        return None

    def is_perfect_jpeg_tile(self, p_bytes, tile_w, tile_h):
        """1箇所の破損もなく完全・無傷に開けたJPEGタイルであるかを厳密に判定"""
        if not p_bytes or len(p_bytes) < 4:
            return None
        raw_bytes = bytes(p_bytes)
        # SOI (0xFFD8) で始まり EOI (0xFFD9) で終わる完全な構造であるか
        if not (raw_bytes.startswith(b'\xff\xd8') and raw_bytes.endswith(b'\xff\xd9')):
            return None

        try:
            tile_img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
            tile_img.load()  # 完全展開
            if tile_img.size != (tile_w, tile_h):
                tile_img = tile_img.resize((tile_w, tile_h))
            # 途中でデータ破損して真っ黒で打ち切られていないか検証
            tile_arr = np.array(tile_img, dtype=np.float32)
            # 画像の最下部8行のピクセルが完全に全画素(0,0,0)なら破損途切れと判定
            bottom_strip = tile_arr[-min(8, tile_h):, :]
            if np.max(bottom_strip) < 3.0 and np.mean(tile_arr) > 10.0:
                return None  # 上部は描画されたが下部が途切れている
            return tile_img
        except Exception:
            return None

    def inpaint_missing_tiles(self, canvas, tile_count_x, tile_count_y, tile_w, tile_h, rendered_mask):
        """受信できなかった欠損タイルを、周囲の正常タイルの色からスマート補間"""
        canvas_arr = np.array(canvas, dtype=np.float64)

        for ty in range(tile_count_y):
            for tx in range(tile_count_x):
                if rendered_mask[ty, tx]:
                    continue  # 正常に描画済み

                # 周囲（上下左右）の正常タイルから平均色を算出
                neighbor_colors = []
                # 上
                if ty > 0 and rendered_mask[ty - 1, tx]:
                    neighbor_colors.append(np.mean(canvas_arr[(ty-1)*tile_h : ty*tile_h, tx*tile_w : (tx+1)*tile_w], axis=(0,1)))
                # 下
                if ty < tile_count_y - 1 and rendered_mask[ty + 1, tx]:
                    neighbor_colors.append(np.mean(canvas_arr[(ty+1)*tile_h : (ty+2)*tile_h, tx*tile_w : (tx+1)*tile_w], axis=(0,1)))
                # 左
                if tx > 0 and rendered_mask[ty, tx - 1]:
                    neighbor_colors.append(np.mean(canvas_arr[ty*tile_h : (ty+1)*tile_h, (tx-1)*tile_w : tx*tile_w], axis=(0,1)))
                # 右
                if tx < tile_count_x - 1 and rendered_mask[ty, tx + 1]:
                    neighbor_colors.append(np.mean(canvas_arr[ty*tile_h : (ty+1)*tile_h, (tx+1)*tile_w : (tx+2)*tile_w], axis=(0,1)))

                if neighbor_colors:
                    fill_color = np.mean(neighbor_colors, axis=0)
                    y1 = ty * tile_h
                    y2 = min(canvas_arr.shape[0], (ty + 1) * tile_h)
                    x1 = tx * tile_w
                    x2 = min(canvas_arr.shape[1], (tx + 1) * tile_w)
                    canvas_arr[y1:y2, x1:x2] = fill_color

        return Image.fromarray(canvas_arr.astype(np.uint8))

    def reset_database(self):
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
        db_path = os.path.join(self.log_dir, "sstv_packets_turbo_jpeg.db")
        self.db.close()
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except Exception:
                pass
        self.db = PacketDatabaseTurboJPEG(self.log_dir)

    def process_and_save_images(self, min_tile_ratio=0.0, user_id=None) -> list[str]:
        """テキストログをDBに蓄積し、完全タイル即時確定 ＋ ビット多数決 ＋ ピクセル領域SNR加重平均 ＋ インペインティングで画像を復元"""
        self.load_all_logs()

        image_counts = self.db.get_all_image_ids_with_counts(user_id=user_id)
        if not image_counts:
            return []

        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
        output_dir = os.path.join(root_dir, getattr(config, "IMAGE_OUT_DIR", "data/digital_turbo_jpeg/images"))
        os.makedirs(output_dir, exist_ok=True)
        saved_files = []

        for main_id, count in sorted(image_counts.items(), key=lambda x: x[1], reverse=True):
            if main_id is None:
                continue

            tiles_data = self.db.get_packets_for_image(main_id, user_id=user_id)
            if not tiles_data:
                continue

            # タイル構造の自動判定
            all_ty = [ty for ty in tiles_data.keys() if ty < 32]
            if not all_ty:
                continue
            max_ty = max(all_ty)
            max_tx = 0
            for ty in all_ty:
                tx_list = [tx for tx in tiles_data[ty].keys() if tx < 32]
                if tx_list:
                    max_tx = max(max_tx, max(tx_list))

            # パケットから実際のタイルサイズを判定
            detected_tile_w: int = int(getattr(config, "TILE_SIZE", 16))
            detected_tile_h: int = int(getattr(config, "TILE_SIZE", 16))
            found_size = False
            for ty in all_ty:
                for tx in tiles_data[ty]:
                    for plen, pkts in tiles_data[ty][tx].items():
                        for p in pkts:
                            p_bits = p[0]
                            if len(p_bits) >= plen * 8:
                                p_bytes = self.bits_to_bytearray(p_bits[:plen * 8])
                                t_img = self.decode_tile_bytes_safely(p_bytes)
                                if t_img:
                                    detected_tile_w, detected_tile_h = int(t_img.size[0]), int(t_img.size[1])
                                    found_size = True
                                    break
                        if found_size:
                            break
                    if found_size:
                        break
                if found_size:
                    break

            canvas_w = int(config.WIDTH)
            canvas_h = int(config.HEIGHT)
            tile_count_x = max(1, canvas_w // detected_tile_w)
            tile_count_y = max(1, canvas_h // detected_tile_h)
            canvas = Image.new("RGB", (canvas_w, canvas_h), color="black")
            rendered_mask = np.zeros((tile_count_y, tile_count_x), dtype=bool)

            for ty in range(tile_count_y):
                for tx in range(tile_count_x):
                    len_dict = tiles_data.get(ty, {}).get(tx, {})
                    if not len_dict:
                        continue

                    best_plen = max(len_dict.keys(), key=lambda k: sum((p[1] + 1.0) for p in len_dict[k]))
                    packets = len_dict[best_plen]

                    cur_tile_w: int = min(detected_tile_w, canvas_w - tx * detected_tile_w)
                    cur_tile_h: int = min(detected_tile_h, canvas_h - ty * detected_tile_h)
                    if cur_tile_w <= 0 or cur_tile_h <= 0:
                        continue

                    perfect_img = None

                    # ★ 最適化 1: 複数パケットがある場合、まずビット多数決で「完全無欠JPEG」を試行
                    if len(packets) > 1:
                        voted_bits_str = self.bit_majority_vote(packets, best_plen * 8)
                        v_bytes = self.bits_to_bytearray(voted_bits_str)
                        perfect_img = self.is_perfect_jpeg_tile(v_bytes, cur_tile_w, cur_tile_h)

                    # ★ 最適化 2: 個別パケットの中にすでに「100%完全な無傷JPEG」があるかチェック
                    if perfect_img is None:
                        sorted_packets = sorted(packets, key=lambda p: p[1], reverse=True)
                        for row in sorted_packets:
                            payload_bits_str = row[0]
                            payload_bit_len = best_plen * 8
                            if len(payload_bits_str) >= payload_bit_len:
                                p_bytes = self.bits_to_bytearray(payload_bits_str[:payload_bit_len])
                                perfect_img = self.is_perfect_jpeg_tile(p_bytes, cur_tile_w, cur_tile_h)
                                if perfect_img is not None:
                                    break

                    # ★ 完全なタイルが見つかった場合は、これ以上多数決・平均化を行わず即座に採用確定！
                    if perfect_img is not None:
                        paste_x = tx * detected_tile_w
                        paste_y = ty * detected_tile_h
                        tw, th = perfect_img.size
                        if paste_x + tw <= canvas_w and paste_y + th <= canvas_h:
                            canvas.paste(perfect_img, (paste_x, paste_y))
                            rendered_mask[ty, tx] = True
                        else:
                            cropped = perfect_img.crop((0, 0, min(tw, canvas_w - paste_x), min(th, canvas_h - paste_y)))
                            canvas.paste(cropped, (paste_x, paste_y))
                            rendered_mask[ty, tx] = True
                        continue

                    # ★ 完全なパケットが1つもなかった場合のみ、破損パケット同士のピクセル領域SNR加重平均を実行
                    pixel_sum = np.zeros((cur_tile_h, cur_tile_w, 3), dtype=np.float64)
                    weight_sum = np.zeros((cur_tile_h, cur_tile_w, 1), dtype=np.float64)
                    decoded_count = 0

                    sorted_packets = sorted(packets, key=lambda p: p[1], reverse=True)

                    for row in sorted_packets:
                        payload_bits_str = row[0]
                        snr_val = row[1]
                        payload_bit_len = best_plen * 8

                        if len(payload_bits_str) < payload_bit_len:
                            continue

                        p_bytes = self.bits_to_bytearray(payload_bits_str[:payload_bit_len])
                        tile_img = self.decode_tile_bytes_safely(p_bytes, cur_tile_w, cur_tile_h)

                        if tile_img is None:
                            continue

                        tile_arr = np.array(tile_img.resize((cur_tile_w, cur_tile_h)), dtype=np.float64)
                        weight = snr_val + 1.0

                        if decoded_count > 0:
                            pixel_brightness = np.sum(tile_arr, axis=2, keepdims=True)
                            valid_mask = (pixel_brightness > 3.0).astype(np.float64)
                        else:
                            valid_mask = np.ones((cur_tile_h, cur_tile_w, 1), dtype=np.float64)

                        pixel_sum += tile_arr * weight * valid_mask
                        weight_sum += weight * valid_mask
                        decoded_count += 1

                    tile_img = None
                    if decoded_count > 0:
                        safe_weight = np.where(weight_sum > 0, weight_sum, 1.0)
                        averaged_pixels = (pixel_sum / safe_weight).clip(0, 255).astype(np.uint8)
                        tile_img = Image.fromarray(averaged_pixels)

                    if tile_img is not None:
                        paste_x = tx * detected_tile_w
                        paste_y = ty * detected_tile_h
                        tw, th = tile_img.size
                        if paste_x + tw <= canvas_w and paste_y + th <= canvas_h:
                            canvas.paste(tile_img, (paste_x, paste_y))
                            rendered_mask[ty, tx] = True
                        else:
                            cropped = tile_img.crop((0, 0, min(tw, canvas_w - paste_x), min(th, canvas_h - paste_y)))
                            canvas.paste(cropped, (paste_x, paste_y))
                            rendered_mask[ty, tx] = True

            # 3. 欠損タイルがある場合、スマートインペインティング（周囲補間）を実施
            if not np.all(rendered_mask):
                canvas = self.inpaint_missing_tiles(canvas, tile_count_x, tile_count_y, detected_tile_w, detected_tile_h, rendered_mask)

            clean_id_str = f"{int(main_id):04X}"
            out_filename = f"restored_ID_{clean_id_str}.jpg" if not user_id else f"user_{user_id}_ID_{clean_id_str}.jpg"
            out_path = os.path.join(output_dir, out_filename)
            canvas.save(out_path, format="JPEG", quality=95)
            saved_files.append(out_path)

            static_out = os.path.join(root_dir, "web_turbo_png", "static", "output")
            os.makedirs(static_out, exist_ok=True)
            static_path = os.path.join(static_out, out_filename)
            canvas.save(static_path, format="JPEG", quality=95)

            # Web用: 全員多数決画像とともに、ユーザー単体・累積画像 (user_1 / user_cumulative_1) も同期生成
            if not user_id:
                for fallback_uid in (1,):
                    u_single = os.path.join(static_out, f"user_{fallback_uid}_ID_{clean_id_str}.jpg")
                    u_cumul = os.path.join(static_out, f"user_cumulative_{fallback_uid}_ID_{clean_id_str}.jpg")
                    canvas.save(u_single, format="JPEG", quality=95)
                    canvas.save(u_cumul, format="JPEG", quality=95)
            else:
                u_cumul = os.path.join(static_out, f"user_cumulative_{user_id}_ID_{clean_id_str}.jpg")
                canvas.save(u_cumul, format="JPEG", quality=95)

        return saved_files

    def close(self):
        self.db.close()

if __name__ == "__main__":
    try:
        print("=== SSTV Turbo JPEG アグリゲータ (画像復元) 開始 ===")
        aggregator = TurboJPEGAggregator()
        saved_files = aggregator.process_and_save_images()
        if saved_files:
            print("🎉 復元完了！ 生成画像:")
            for f in saved_files:
                print(f"  -> {f}")
        else:
            print("⚠️ 復元対象のパケットログが見つかりませんでした。")
            print("   先に decoder_turbo.py を実行してください。")
    except KeyboardInterrupt:
        print("\n[停止] プログラムを終了しました。")
