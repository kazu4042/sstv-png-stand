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
from PIL import Image, ImageFile
import io
from collections import defaultdict

# ★ 破損・途切れ・ノイズ混じりJPEGでも例外で捨てずにざらざら描画する設定
setattr(ImageFile, 'LOAD_TRUNCATED_IMAGES', True)

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

from digital_turbo_jpeg import config_turbo as config
from digital_turbo_jpeg.database_turbo import PacketDatabaseTurboJPEG
from core.base_interfaces import BaseAggregator


class TurboJPEGAggregator(BaseAggregator):
    """SSTV Turbo JPEG アグリゲータ (BaseAggregator 準拠)
    ノイズや欠損のあるパケットからでも、JPEG特有のブロック感・ざらつきを残しながら段階的に画像を復元する。
    """
    def __init__(self, log_dir=None):
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
        if log_dir is None:
            self.log_dir = os.path.join(root_dir, getattr(config, "TEXT_LOG_DIR", "data/digital_turbo_jpeg/logs"))
        else:
            self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)
        self.db = PacketDatabaseTurboJPEG(self.log_dir)

    def load_all_logs(self) -> bool:
        log_files = glob.glob(os.path.join(self.log_dir, f"{config.TEXT_LOG_PREFIX}_*.txt"))
        if not log_files:
            return False

        new_files = [f for f in log_files if not self.db.is_file_imported(os.path.basename(f))]
        if not new_files:
            return True

        for file_path in new_files:
            file_name = os.path.basename(file_path)
            # ファイル名から user_id を抽出 (例: turbo_bitstream_user_2_2026...)
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

                    # 0と1のみで構成された純2進数行
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
        """★ NumPy ベクトル化: ビット文字列を一括でバイト配列に変換"""
        n = (len(bits_str) // 8) * 8
        if n == 0:
            return bytearray()
        # '0'/'1' 文字列 → uint8 配列 (0 or 1) に一括変換
        bit_arr = np.frombuffer(bits_str[:n].encode('ascii'), dtype=np.uint8) - ord('0')
        # (N/8, 8) に reshape して [128, 64, 32, 16, 8, 4, 2, 1] との内積でバイト化
        byte_weights = np.array([128, 64, 32, 16, 8, 4, 2, 1], dtype=np.uint8)
        byte_arr = bit_arr.reshape(-1, 8) @ byte_weights
        return bytearray(byte_arr.astype(np.uint8).tobytes())


    def _get_jpeg_header_template(self):
        """16x16 タイル用の正常な JPEG ヘッダテンプレート (SOI〜SOS) をキャッシュ"""
        if not hasattr(self, "_header_template"):
            dummy = Image.new("RGB", (config.TILE_SIZE, config.TILE_SIZE), color=(0, 0, 0))
            bio = io.BytesIO()
            restart_int = getattr(config, "JPEG_RESTART_MARKER", 1)
            dummy.save(bio, format="JPEG", quality=config.JPEG_QUALITY, restart_marker=restart_int)
            raw = bio.getvalue()
            sos_pos = raw.find(b'\xff\xda')
            if sos_pos != -1:
                # SOS マーカー長 (通常 14 バイト) を含めた位置までをヘッダとする
                sos_len = (raw[sos_pos+2] << 8) | raw[sos_pos+3]
                self._header_template = raw[:sos_pos + 2 + sos_len]
                self._sos_offset = sos_pos + 2 + sos_len
            else:
                self._header_template = None
                self._sos_offset = 0
        return self._header_template, self._sos_offset

    def decode_tile_bytes_safely(self, p_bytes):
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

        # 2. ヘッダ修復（SOI: 0xFF 0xD8 の補完）＋ 末尾修復（EOI: 0xFF 0xD9）
        fixed_bytes = bytearray(p_bytes)
        if not (fixed_bytes[0] == 0xFF and fixed_bytes[1] == 0xD8):
            fixed_bytes = bytearray([0xFF, 0xD8]) + fixed_bytes[2:]

        if len(fixed_bytes) >= 2 and not (fixed_bytes[-2] == 0xFF and fixed_bytes[-1] == 0xD9):
            fixed_bytes = fixed_bytes + bytearray([0xFF, 0xD9])

        try:
            tile_img = Image.open(io.BytesIO(fixed_bytes)).convert("RGB")
            tile_img.load()
            return tile_img
        except Exception:
            pass

        # 3. 高度な修復: ヘッダ全壊時、正常な JPEG 構造テンプレート (DQT/DHT/SOF/SOS) で差し替え
        try:
            hdr_tmpl, sos_off = self._get_jpeg_header_template()
            if hdr_tmpl and len(p_bytes) > sos_off:
                # 破損データのスキャンデータ部分を正常ヘッダとドッキング
                repaired_bytes = bytearray(hdr_tmpl + p_bytes[sos_off:])
                if not (repaired_bytes[-2] == 0xFF and repaired_bytes[-1] == 0xD9):
                    repaired_bytes += bytearray([0xFF, 0xD9])
                tile_img = Image.open(io.BytesIO(repaired_bytes)).convert("RGB")
                tile_img.load()
                return tile_img
        except Exception:
            pass

        return None


    def reset_database(self):
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
        db_path = os.path.join(self.log_dir, "sstv_packets_turbo_jpeg.db")
        self.db.close()
        if os.path.exists(db_path):
            os.remove(db_path)
        self.db = PacketDatabaseTurboJPEG(self.log_dir)

    def process_and_save_images(self, min_tile_ratio=0.0, user_id=None) -> list[str]:
        """テキストログをDBに蓄積し、ピクセル領域SNR重み付き平均で画像を復元

        JPEG版の多数決戦略:
          ビット（圧縮データ）の世界で投票するとハフマン符号の構造が壊れるため、
          各パケットをまず個別にJPEGデコードしてピクセルに戻し、
          そのピクセル値に対してSNR重み付き平均を取る（ピクセル領域多数決）。
        """
        self.load_all_logs()

        image_counts = self.db.get_all_image_ids_with_counts(user_id=user_id)
        if not image_counts:
            return []

        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
        output_dir = os.path.join(root_dir, getattr(config, "IMAGE_OUT_DIR", "data/digital_turbo_jpeg/images"))
        os.makedirs(output_dir, exist_ok=True)
        saved_files = []

        total_tiles = config.TILE_COUNT_X * config.TILE_COUNT_Y
        min_packets = max(1, int(total_tiles * min_tile_ratio))

        for main_id, count in sorted(image_counts.items(), key=lambda x: x[1], reverse=True):
            if count < min_packets:
                continue

            tiles_data = self.db.get_packets_for_image(main_id, user_id=user_id)
            canvas = Image.new("RGB", (config.WIDTH, config.HEIGHT), color="black")
            success_tiles = 0

            for ty in range(config.TILE_COUNT_Y):
                for tx in range(config.TILE_COUNT_X):
                    len_dict = tiles_data[ty][tx]
                    if not len_dict:
                        continue

                    # 最も重み合計が大きい payload_length を選択
                    best_plen = max(len_dict.keys(), key=lambda k: sum((p[1] + 1.0) for p in len_dict[k]))
                    packets = len_dict[best_plen]

                    # ===== ピクセル領域 SNR 重み付き多数決 =====
                    # 各パケットを個別にJPEGデコードし、ピクセル値にSNR重み付き平均を取る。
                    # ハフマン符号の構造を壊さず、複数パケットの恩恵を最大限に活かす。
                    tile_h = min(config.TILE_SIZE, config.HEIGHT - ty * config.TILE_SIZE)
                    tile_w = min(config.TILE_SIZE, config.WIDTH - tx * config.TILE_SIZE)

                    # 重み付きピクセル合算用の配列 (float64)
                    pixel_sum = np.zeros((tile_h, tile_w, 3), dtype=np.float64)
                    weight_sum = np.zeros((tile_h, tile_w, 1), dtype=np.float64)
                    decoded_count = 0

                    # SNR降順でデコード試行（高品質パケットを優先）
                    sorted_packets = sorted(packets, key=lambda p: p[1], reverse=True)

                    for row in sorted_packets:
                        payload_bits_str = row[0]
                        snr_val = row[1]
                        payload_bit_len = best_plen * 8

                        if len(payload_bits_str) < payload_bit_len:
                            continue

                        p_bytes = self.bits_to_bytearray(payload_bits_str[:payload_bit_len])
                        tile_img = self.decode_tile_bytes_safely(p_bytes)

                        if tile_img is None:
                            continue

                        # デコード成功: ピクセル配列に変換
                        tile_arr = np.array(tile_img.resize((tile_w, tile_h)), dtype=np.float64)
                        weight = snr_val + 1.0

                        # ノイズによる真っ黒ピクセル (0,0,0) を除外するマスク
                        # （部分デコード時、描画されなかった領域は黒になる）
                        if decoded_count > 0:
                            # 2枚目以降: 全ピクセルが黒 (RGB合計 < 3) のピクセルは除外
                            pixel_brightness = np.sum(tile_arr, axis=2, keepdims=True)  # (H, W, 1)
                            valid_mask = (pixel_brightness > 3.0).astype(np.float64)    # (H, W, 1)
                        else:
                            # 1枚目（最高SNR）: すべてのピクセルを採用
                            valid_mask = np.ones((tile_h, tile_w, 1), dtype=np.float64)

                        pixel_sum += tile_arr * weight * valid_mask
                        weight_sum += weight * valid_mask
                        decoded_count += 1

                    # --- 結果の合成 ---
                    tile_img = None
                    if decoded_count > 0:
                        # 重みが0の箇所（どのパケットでも描画されなかった）は黒のまま
                        safe_weight = np.where(weight_sum > 0, weight_sum, 1.0)
                        averaged_pixels = (pixel_sum / safe_weight).clip(0, 255).astype(np.uint8)
                        tile_img = Image.fromarray(averaged_pixels)

                    # タイル描画
                    if tile_img is not None:
                        tw, th = tile_img.size
                        paste_x = tx * config.TILE_SIZE
                        paste_y = ty * config.TILE_SIZE
                        if paste_x + tw <= config.WIDTH and paste_y + th <= config.HEIGHT:
                            canvas.paste(tile_img, (paste_x, paste_y))
                            success_tiles += 1
                        else:
                            tile_img = tile_img.crop((0, 0, min(tw, config.WIDTH - paste_x), min(th, config.HEIGHT - paste_y)))
                            canvas.paste(tile_img, (paste_x, paste_y))
                            success_tiles += 1


            out_filename = f"restored_ID_{main_id:04X}.jpg" if not user_id else f"user_{user_id}_ID_{main_id:04X}.jpg"
            out_path = os.path.join(output_dir, out_filename)
            canvas.save(out_path, format="JPEG", quality=95)
            saved_files.append(out_path)

            # Webシステム用に static/output にも保存
            static_out = os.path.join(root_dir, "web_turbo_png", "static", "output")
            os.makedirs(static_out, exist_ok=True)
            static_path = os.path.join(static_out, out_filename)
            canvas.save(static_path, format="JPEG", quality=95)

        return saved_files

    def close(self):
        self.db.close()

if __name__ == "__main__":
    try:
        print("=== SSTV Turbo JPEG アグリゲータ (画像復元) 開始 ===")
        aggregator = TurboJPEGAggregator()
        saved_files = aggregator.process_and_save_images()
        if saved_files:
            print(f"🎉 復元完了！ 生成画像:")
            for f in saved_files:
                print(f"  -> {f}")
        else:
            print("⚠️ 復元対象のパケットログが見つかりませんでした。")
            print("   先に decoder_turbo.py を実行してください。")
    except KeyboardInterrupt:
        print("\n[停止] プログラムを終了しました。")

