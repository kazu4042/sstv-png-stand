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
        byte_list = []
        for i in range(0, len(bits_str), 8):
            chunk = bits_str[i:i + 8]
            if len(chunk) == 8:
                val = 0
                for b in chunk:
                    val = (val << 1) | (1 if b == '1' else 0)
                byte_list.append(val)
        return bytearray(byte_list)

    def decode_tile_bytes_safely(self, p_bytes):
        """JPEGバイト列を安全にデコード（ノイズがあっても部分描画を試みる）"""
        try:
            tile_img = Image.open(io.BytesIO(p_bytes)).convert("RGB")
            tile_img.load()  # 強制読み込みで破損部をレンダリング
            return tile_img
        except Exception:
            # ヘッダ一部破損時などにSOI/EOIチェックを施して再試行
            try:
                if len(p_bytes) >= 4 and not (p_bytes[0] == 0xFF and p_bytes[1] == 0xD8):
                    p_bytes = bytearray([0xFF, 0xD8]) + p_bytes[2:]
                tile_img = Image.open(io.BytesIO(p_bytes)).convert("RGB")
                tile_img.load()
                return tile_img
            except Exception:
                return None

    def reset_database(self):
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
        db_path = os.path.join(self.log_dir, "sstv_packets_turbo_jpeg.db")
        self.db.close()
        if os.path.exists(db_path):
            os.remove(db_path)
        self.db = PacketDatabaseTurboJPEG(self.log_dir)

    def process_and_save_images(self, min_tile_ratio=0.0, user_id=None) -> list[str]:
        """テキストログをDBに蓄積し、多数決投票・ざらざら感フォールバックで画像を復元"""
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
                    payload_bit_len = best_plen * 8

                    # 1. まずSNR重み付き多数決を実施
                    score_0 = np.zeros(payload_bit_len, dtype=np.float64)
                    score_1 = np.zeros(payload_bit_len, dtype=np.float64)

                    valid_count = 0
                    for row in packets:
                        payload_bits_str = row[0]
                        snr_val = row[1]
                        weight = snr_val + 1.0
                        if len(payload_bits_str) < payload_bit_len:
                            continue
                        valid_count += 1
                        for i, bit_char in enumerate(payload_bits_str[:payload_bit_len]):
                            if bit_char == '1':
                                score_1[i] += weight
                            else:
                                score_0[i] += weight

                    if valid_count == 0:
                        continue

                    voted_payload = "".join(
                        '1' if score_1[i] >= score_0[i] else '0'
                        for i in range(payload_bit_len)
                    )
                    p_bytes = self.bits_to_bytearray(voted_payload)
                    tile_img = self.decode_tile_bytes_safely(p_bytes)

                    # 2. 多数決バイト列でデコード失敗した場合、単体パケット（SNR最高順）でフォールバック
                    if tile_img is None:
                        sorted_pkts = sorted(packets, key=lambda p: p[1], reverse=True)
                        for s_bits, _, _, _, _ in sorted_pkts:
                            fallback_bytes = self.bits_to_bytearray(s_bits)
                            tile_img = self.decode_tile_bytes_safely(fallback_bytes)
                            if tile_img is not None:
                                break

                    # 3. タイル描画
                    if tile_img is not None:
                        tw, th = tile_img.size
                        paste_x = tx * tw
                        paste_y = ty * th
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

        return saved_files

    def close(self):
        self.db.close()
