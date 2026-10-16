import os
import sys

for stream in (sys.stdout, sys.stderr):
    reconf = getattr(stream, 'reconfigure', None)
    if callable(reconf):
        try:
            reconf(encoding='utf-8', errors='replace')
        except Exception:
            pass

import math
import io
import random
import numpy as np
from PIL import Image
from scipy.io import wavfile

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from digital_turbo_jpeg import config_turbo as config


class JPEGTextBitFlipper:
    """JPEG画像からテキスト(ビットストリーム)を生成し、
    指定した割合(%)やシンボル周波数置換(8000Hz↔4000Hz)でビット反転を施し、
    音声(WAV)やログテキストを生成するクラス。
    """

    def __init__(self, width=None, height=None, tile_size=None, quality=None):
        self.width = width or config.WIDTH
        self.height = height or config.HEIGHT
        self.tile_size = tile_size or config.TILE_SIZE
        self.quality = quality or config.JPEG_QUALITY
        self.tile_count_x = math.ceil(self.width / self.tile_size)
        self.tile_count_y = math.ceil(self.height / self.tile_size)
        self.current_phase = 0.0
        self.samples_per_symbol = max(1, int(config.SAMPLE_RATE * config.MS_SYMBOL / 1000))

    # -------------------------------------------------------------
    # 1. 画像 → 正解パケット・テキスト（ビット列）生成
    # -------------------------------------------------------------
    def image_to_ground_truth_packets(self, image_path_or_array):
        """画像から各タイルのJPEGデータを生成し、正解パケット情報(辞書のリスト)を返す。"""
        if isinstance(image_path_or_array, str):
            if not os.path.exists(image_path_or_array):
                raise FileNotFoundError(f"画像が見つかりません: {image_path_or_array}")
            img = Image.open(image_path_or_array).convert('RGB').resize((self.width, self.height))
            pixels = np.array(img)
        else:
            pixels = np.array(image_path_or_array)

        all_pixel_bits = self._bytearray_to_bitstream(pixels.tobytes())
        image_id_val = self._calculate_crc16_bits(all_pixel_bits)
        image_id_bits = self._bytes_to_bitstream([image_id_val], [config.BIT_IMAGE_CRC])

        packets = []
        for ty in range(self.tile_count_y):
            for tx in range(self.tile_count_x):
                y_start = ty * self.tile_size
                y_end = min(y_start + self.tile_size, self.height)
                x_start = tx * self.tile_size
                x_end = min(x_start + self.tile_size, self.width)
                tile_pixels = pixels[y_start:y_end, x_start:x_end]

                tile_img = Image.fromarray(tile_pixels)
                with io.BytesIO() as bio:
                    restart_int = getattr(config, "JPEG_RESTART_MARKER", 1)
                    tile_img.save(
                        bio,
                        format="JPEG",
                        quality=self.quality,
                        restart_marker_blocks=restart_int,
                        subsampling=0
                    )
                    jpeg_data = bio.getvalue()

                payload_length = len(jpeg_data)
                payload_bits = self._bytearray_to_bitstream(jpeg_data)

                # ヘッダ情報 (ImageID, TileX, TileY, Length)
                header_info_bits = image_id_bits + self._bytes_to_bitstream(
                    [tx, ty, payload_length],
                    [config.BIT_TILE_X, config.BIT_TILE_Y, config.BIT_PAYLOAD_LENGTH]
                )
                header_crc_val = self._calculate_crc16_bits(header_info_bits)
                header_crc_bits = self._bytes_to_bitstream([header_crc_val], [config.BIT_HEADER_CRC])
                full_header_bits = header_info_bits + header_crc_bits

                packets.append({
                    "tile_x": tx,
                    "tile_y": ty,
                    "image_id": image_id_val,
                    "payload_length": payload_length,
                    "header_info_bits": header_info_bits,
                    "header_crc_bits": header_crc_bits,
                    "full_header_bits": full_header_bits,
                    "payload_bits": payload_bits,
                    "payload_bytes": jpeg_data,
                    "snr_bits": [1, 1, 1, 1],  # SNR = 15
                    "snr_val": 15.0
                })

        return packets

    def get_ground_truth_jpeg_image(self, gt_packets):
        """正解JPEGパケット群をそのまま展開した、送信側理想JPEG画像 (RGB配列) を生成"""
        canvas = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        for p in gt_packets:
            tx = p["tile_x"]
            ty = p["tile_y"]
            y_start = ty * self.tile_size
            y_end = min(y_start + self.tile_size, self.height)
            x_start = tx * self.tile_size
            x_end = min(x_start + self.tile_size, self.width)
            try:
                t_img = Image.open(io.BytesIO(p["payload_bytes"])).convert("RGB")
                canvas[y_start:y_end, x_start:x_end] = np.array(t_img.resize((x_end - x_start, y_end - y_start)))
            except Exception:
                pass
        return canvas

    # -------------------------------------------------------------
    # 2. ビット反転処理 (完全一様ランダム / 物理層歪み / パケット分散)
    # -------------------------------------------------------------
    def generate_n_divergent_sets(
        self,
        gt_packets,
        num_sets=3,
        error_rates=None,
        mode="random",
        base_seed=42
    ):
        """N個 (例: 3個, 5個, 7個など) の独立なノイズデータセット (A, B, C, D, ...) を生成する。
        各データセット全体を通して1本の独立乱数ストリーム（メルセンヌ・ツイスタ）を生成し、
        シード初期化による偏りを完全に排除した真のランダムビット反転を実現。
        """
        num_pkts = len(gt_packets)

        if error_rates is None:
            error_rates = [0.05, 0.02, 0.01]
        if len(error_rates) < num_sets:
            last = error_rates[-1]
            error_rates = list(error_rates) + [max(0.005, last * 0.5) for _ in range(num_sets - len(error_rates))]

        # 各セットの名前 (A, B, C, D, E, ...)
        labels = [chr(65 + i) if i < 26 else f"Src_{i+1}" for i in range(num_sets)]

        results = {}
        for s_idx in range(num_sets):
            name = labels[s_idx]
            user_rate_pct = float(error_rates[s_idx])
            prob = user_rate_pct / 100.0  # 反転確率 (例: 0.05% -> 0.0005)

            # データセットごとに独立な乱数生成器を作成（全パケットを通して連続して進行）
            set_rng = random.Random(base_seed + (s_idx + 1) * 7777)

            noisy_list = []
            total_bits = 0
            total_flipped = 0

            if mode in ("random", "all"):
                # =========================================================
                # 1. 完全一様ランダム反転 (BSC伝搬路モデル)
                # 全ビットストリームに対して独立にベルヌーイ試行を行い、完全なランダム性を保証
                # =========================================================
                for p_idx, pkt in enumerate(gt_packets):
                    p_copy = dict(pkt)
                    payload = list(pkt["payload_bits"])
                    flipped_indices = set()
                    count = 0

                    for b_idx in range(len(payload)):
                        if set_rng.random() < prob:
                            payload[b_idx] = 1 - payload[b_idx]
                            flipped_indices.add(b_idx)
                            count += 1

                    p_copy["payload_bits"] = payload
                    p_copy["flipped_indices"] = flipped_indices
                    p_copy["flipped_count"] = count

                    # ビット誤り率に応じた理論的SNR (0〜15)
                    # 誤り率が低いほど高SNR、高いほど低SNR
                    snr_calc = max(1.0, min(15.0, 15.0 * (1.0 - min(1.0, prob * 20.0))))
                    p_copy["snr_val"] = snr_calc
                    snr_int = round(snr_calc)
                    p_copy["snr_bits"] = [
                        (snr_int >> 3) & 1,
                        (snr_int >> 2) & 1,
                        (snr_int >> 1) & 1,
                        snr_int & 1
                    ]

                    total_flipped += count
                    total_bits += len(payload)
                    noisy_list.append(p_copy)

            elif mode == "8000_to_4000":
                # =========================================================
                # 2. 4-FSK周波数置換 (8000Hz(00) -> 4000Hz(10))
                # =========================================================
                for p_idx, pkt in enumerate(gt_packets):
                    p_copy = dict(pkt)
                    payload = list(pkt["payload_bits"])
                    flipped_indices = set()
                    count = 0

                    for i in range(0, len(payload) - 1, 2):
                        if payload[i] == 0 and payload[i + 1] == 0:  # 8000Hz
                            if set_rng.random() < prob:
                                payload[i] = 1  # 4000Hz
                                flipped_indices.add(i)
                                count += 1

                    p_copy["payload_bits"] = payload
                    p_copy["flipped_indices"] = flipped_indices
                    p_copy["flipped_count"] = count

                    snr_calc = max(1.0, min(15.0, 15.0 * (1.0 - min(1.0, prob * 15.0))))
                    p_copy["snr_val"] = snr_calc
                    snr_int = round(snr_calc)
                    p_copy["snr_bits"] = [
                        (snr_int >> 3) & 1,
                        (snr_int >> 2) & 1,
                        (snr_int >> 1) & 1,
                        snr_int & 1
                    ]

                    total_flipped += count
                    total_bits += len(payload)
                    noisy_list.append(p_copy)

            else:  # "packet_burst"
                # =========================================================
                # 3. パケット分散バーストフェージング (局所フェージング)
                # 指定したエラー率に基づき破損パケット数を決定し、各音声間で破損位置を独立分散
                # =========================================================
                burst_bit_rate = 0.05  # 壊れたパケット内のビット誤り率 5%
                target_damaged_pkts = max(1, int(num_pkts * (prob / burst_bit_rate)))
                target_damaged_pkts = min(num_pkts, target_damaged_pkts)

                # ランダムシャッフルで破損パケットを決定（周期バイアスを排除）
                all_idx = list(range(num_pkts))
                set_rng.shuffle(all_idx)
                damaged_set = set(all_idx[:target_damaged_pkts])

                for p_idx, pkt in enumerate(gt_packets):
                    p_copy = dict(pkt)
                    payload = list(pkt["payload_bits"])
                    if p_idx in damaged_set:
                        flipped_indices = set()
                        count = 0
                        for b_idx in range(len(payload)):
                            if set_rng.random() < burst_bit_rate:
                                payload[b_idx] = 1 - payload[b_idx]
                                flipped_indices.add(b_idx)
                                count += 1
                        p_copy["payload_bits"] = payload
                        p_copy["flipped_indices"] = flipped_indices
                        p_copy["flipped_count"] = count
                        p_copy["snr_val"] = 2.0
                        p_copy["snr_bits"] = [0, 0, 1, 0]
                        total_flipped += count
                    else:
                        p_copy["payload_bits"] = payload
                        p_copy["flipped_indices"] = set()
                        p_copy["flipped_count"] = 0
                        p_copy["snr_val"] = 15.0
                        p_copy["snr_bits"] = [1, 1, 1, 1]

                    total_bits += len(payload)
                    noisy_list.append(p_copy)

            actual_ber = total_flipped / max(1, total_bits)
            results[name] = (noisy_list, actual_ber)

        return results

    # -------------------------------------------------------------
    # 3. テキストファイル保存
    # -------------------------------------------------------------
    def save_packets_to_text(self, packets, output_text_path):
        """ログテキストファイル出力"""
        os.makedirs(os.path.dirname(os.path.abspath(output_text_path)), exist_ok=True)
        with open(output_text_path, "w", encoding="utf-8") as f:
            for p in packets:
                header_info_str = "".join(str(b) for b in p["header_info_bits"])
                payload_str = "".join(str(b) for b in p["payload_bits"])
                snr_str = "".join(str(b) for b in p.get("snr_bits", [1, 1, 1, 1]))
                line = header_info_str + payload_str + snr_str
                f.write(line + "\n")
        return output_text_path

    def flip_text_file(
        self,
        input_text_path,
        output_text_path,
        percent=5.0,
        mode="8000_to_4000",
        protect_header=True,
        seed=42
    ):
        """保存されたパケットテキストファイル(1行=1パケット)に対し、
        指定割合・モードでビット反転を施して新規テキストファイルに出力する。
        """
        if not os.path.exists(input_text_path):
            raise FileNotFoundError(f"入力テキストファイルが見つかりません: {input_text_path}")

        os.makedirs(os.path.dirname(os.path.abspath(output_text_path)), exist_ok=True)
        set_rng = random.Random(seed)
        prob = float(percent) / 100.0

        header_len = 48 if protect_header else 0

        with open(input_text_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]

        output_lines = []
        total_target_bits = 0
        total_flipped = 0

        for line in lines:
            bits = [int(c) for c in line if c in ('0', '1')]
            total_len = len(bits)
            if total_len <= header_len:
                output_lines.append("".join(str(b) for b in bits))
                continue

            # 末尾4bitはSNRビット (存在する場合)
            end_idx = total_len - 4 if total_len > (header_len + 4) else total_len
            target_slice_len = end_idx - header_len
            total_target_bits += target_slice_len

            if mode in ("random", "all"):
                for idx in range(header_len, end_idx):
                    if set_rng.random() < prob:
                        bits[idx] = 1 - bits[idx]
                        total_flipped += 1
            elif mode == "8000_to_4000":
                # 2bit単位で00(8000Hz)を10(4000Hz)へ置換
                for idx in range(header_len, end_idx - 1, 2):
                    if bits[idx] == 0 and bits[idx + 1] == 0:
                        if set_rng.random() < prob:
                            bits[idx] = 1
                            total_flipped += 1
            elif mode == "hybrid":
                # 8000->4000置換 + ランダムノイズ
                half_prob = prob * 0.5
                for idx in range(header_len, end_idx - 1, 2):
                    if bits[idx] == 0 and bits[idx + 1] == 0:
                        if set_rng.random() < half_prob:
                            bits[idx] = 1
                            total_flipped += 1
                for idx in range(header_len, end_idx):
                    if set_rng.random() < half_prob:
                        bits[idx] = 1 - bits[idx]
                        total_flipped += 1
            else:
                for idx in range(header_len, end_idx):
                    if set_rng.random() < prob:
                        bits[idx] = 1 - bits[idx]
                        total_flipped += 1

            output_lines.append("".join(str(b) for b in bits))

        with open(output_text_path, "w", encoding="utf-8") as f:
            for out_line in output_lines:
                f.write(out_line + "\n")

        actual_ber = (total_flipped / total_target_bits * 100.0) if total_target_bits > 0 else 0.0

        return {
            "output_file": output_text_path,
            "packet_count": len(lines),
            "flipped_bits": total_flipped,
            "total_bits": total_target_bits,
            "actual_ber": actual_ber
        }

    # -------------------------------------------------------------
    # 4. 4-FSK音声 (WAV) 生成
    # -------------------------------------------------------------
    def get_tone_samples(self, freq, num_samples):
        if num_samples <= 0:
            return np.array([], dtype=np.float32)
        phase_increment = 2.0 * math.pi * freq / config.SAMPLE_RATE
        phases = self.current_phase + phase_increment * np.arange(num_samples)
        self.current_phase = (self.current_phase + phase_increment * num_samples) % (2.0 * math.pi)
        return np.sin(phases).astype(np.float32)

    def packets_to_wav(self, packets, output_wav_path, seed=42):
        """パケット群を4-FSK音声波形 (WAV) に変調保存"""
        os.makedirs(os.path.dirname(os.path.abspath(output_wav_path)), exist_ok=True)
        self.current_phase = 0.0

        freq_map = {
            (0, 0): 8000.0,
            (0, 1): 6000.0,
            (1, 0): 4000.0,
            (1, 1): 2000.0
        }

        all_samples = []
        leader_samples = int(config.SAMPLE_RATE * 0.1)
        all_samples.append(self.get_tone_samples(8000.0, leader_samples))

        for p in packets:
            bits = p["full_header_bits"] + p["payload_bits"] + p.get("snr_bits", [1, 1, 1, 1])
            for i in range(0, len(bits) - 1, 2):
                sym = (bits[i], bits[i + 1])
                freq = freq_map.get(sym, 8000.0)
                all_samples.append(self.get_tone_samples(freq, self.samples_per_symbol))

        trailer_samples = int(config.SAMPLE_RATE * 0.1)
        all_samples.append(self.get_tone_samples(2000.0, trailer_samples))

        full_wave = np.concatenate(all_samples)
        full_wave = full_wave / max(1.0, np.max(np.abs(full_wave)))
        wav_int16 = (full_wave * 32767.0).astype(np.int16)
        wavfile.write(output_wav_path, config.SAMPLE_RATE, wav_int16)
        return output_wav_path

    # -------------------------------------------------------------
    # 5. 内部ヘルパー
    # -------------------------------------------------------------
    @staticmethod
    def _bytearray_to_bitstream(data_bytes):
        bits = []
        for b in data_bytes:
            for shift in range(7, -1, -1):
                bits.append((b >> shift) & 1)
        return bits

    @staticmethod
    def _bytes_to_bitstream(values, bit_lengths):
        bits = []
        for val, length in zip(values, bit_lengths):
            for shift in range(length - 1, -1, -1):
                bits.append((val >> shift) & 1)
        return bits

    @staticmethod
    def _calculate_crc16_bits(bits_list):
        crc = 0xFFFF
        cur_byte = 0
        bit_cnt = 0
        for b in bits_list:
            cur_byte = (cur_byte << 1) | b
            bit_cnt += 1
            if bit_cnt == 8:
                crc ^= (cur_byte << 8)
                for _ in range(8):
                    if crc & 0x8000:
                        crc = ((crc << 1) ^ 0x1021) & 0xFFFF
                    else:
                        crc = (crc << 1) & 0xFFFF
                cur_byte = 0
                bit_cnt = 0
        if bit_cnt > 0:
            cur_byte <<= (8 - bit_cnt)
            crc ^= (cur_byte << 8)
            for _ in range(8):
                if crc & 0x8000:
                    crc = ((crc << 1) ^ 0x1021) & 0xFFFF
                else:
                    crc = (crc << 1) & 0xFFFF
        return crc
