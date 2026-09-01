import sys
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

import numpy as np
from scipy.io import wavfile
import os
from datetime import datetime
import time

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

from digital_turbo_jpeg import config_turbo as config
from core.base_interfaces import BaseDecoder


class DigitalTurboJPEGDecoder(BaseDecoder):
    """SSTV Turbo JPEG デコーダ (BaseDecoder 準拠)"""
    def __init__(self, user_id=None):
        super().__init__(user_id=user_id)
        import uuid
        timestamp = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:6]}"
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))

        log_dir = os.path.join(root_dir, getattr(config, "TEXT_LOG_DIR", "data/digital_turbo_jpeg/logs"))
        os.makedirs(log_dir, exist_ok=True)
        
        if user_id:
            self.output_raw = os.path.join(log_dir, f"{config.TEXT_LOG_PREFIX}_user_{user_id}_{timestamp}.txt")
        else:
            self.output_raw = os.path.join(log_dir, f"{config.TEXT_LOG_PREFIX}_{timestamp}.txt")

        self.update_cache()

    def update_cache(self):
        self.samples_per_symbol = max(1, int(config.SAMPLE_RATE * config.MS_SYMBOL / 1000))
        self.t_arr = np.arange(self.samples_per_symbol) / config.SAMPLE_RATE
        self.target_phases = [2 * np.pi * f * self.t_arr for f in config.TARGET_FREQS]
        
        # 誤検出回避用の5msロング Sync 判定用
        self.sync_long_samples = max(10, int(config.SAMPLE_RATE * 0.005))
        self.sync_t_arr = np.arange(self.sync_long_samples) / config.SAMPLE_RATE
        self.sync_phase_long = 2 * np.pi * config.FREQ_SYNC * self.sync_t_arr
        self.sync_win = np.hamming(self.sync_long_samples)

    def detect_sync_long_dft(self, audio_segment):
        if len(audio_segment) < self.sync_long_samples:
            return 0.0
        segment = audio_segment[:self.sync_long_samples] * self.sync_win
        c = np.sum(segment * np.cos(self.sync_phase_long))
        s = np.sum(segment * np.sin(self.sync_phase_long))
        return float(c * c + s * s)

    def detect_symbol_dft(self, audio_segment):
        if len(audio_segment) < self.samples_per_symbol:
            return 0, 0.0
            
        segment = audio_segment[:self.samples_per_symbol] * np.hamming(self.samples_per_symbol)
        
        powers = []
        for phase in self.target_phases:
            c = np.sum(segment * np.cos(phase))
            s = np.sum(segment * np.sin(phase))
            powers.append(float(c * c + s * s))
        
        best_idx = int(np.argmax(powers))
        peak_power = powers[best_idx]
        
        other_powers = [p for i, p in enumerate(powers) if i != best_idx]
        noise_power = np.mean(other_powers) + 1e-10
        snr = float(peak_power / noise_power)
        
        return best_idx, snr

    def decode_symbols_exact(self, audio_data, start_idx, num_symbols):
        """正確な間隔でのシンボル切り出し・復号"""
        all_bits = []
        snr_list = []
        bits_map = {0: [0, 0], 1: [0, 1], 2: [1, 0], 3: [1, 1]}
        
        for s in range(num_symbols):
            pos = start_idx + s * self.samples_per_symbol
            segment = audio_data[pos : pos + self.samples_per_symbol]
            if len(segment) < self.samples_per_symbol:
                break
            best_idx, snr = self.detect_symbol_dft(segment)
            all_bits.extend(bits_map[best_idx])
            snr_list.append(snr)
            
        avg_snr = np.mean(snr_list) if snr_list else 0.0
        return all_bits, avg_snr

    def calculate_snr_to_4bit(self, avg_snr):
        snr_max = getattr(config, "SNR_MAX_THRESH", 15.0)
        snr_min = getattr(config, "SNR_MIN_THRESH", 2.0)
        if avg_snr >= snr_max:
            score = 15
        elif avg_snr <= snr_min:
            score = 0
        else:
            ratio = (avg_snr - snr_min) / (snr_max - snr_min)
            score = int(np.round(ratio * 15))
        return bin(score)[2:].zfill(4)

    def bits_to_int(self, bits):
        val = 0
        for b in bits:
            val = (val << 1) | b
        return val

    def bits_to_bytearray(self, bits):
        byte_list = []
        for i in range(0, len(bits), 8):
            chunk = bits[i:i + 8]
            if len(chunk) == 8:
                byte_list.append(self.bits_to_int(chunk))
        return bytearray(byte_list)

    @staticmethod
    def calculate_crc16_bits(bit_list, poly=0x1021, init_val=0xFFFF):
        crc = init_val
        for bit in bit_list:
            inv = ((crc >> 15) ^ bit) & 1
            crc = (crc << 1) & 0xFFFF
            if inv:
                crc ^= poly
        return crc

    def run(self, wav_path, progress_callback=None):
        self.update_cache()
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
        if not os.path.exists(wav_path):
            alt_path = os.path.join(root_dir, "data", "digital_turbo_jpeg", "audio", os.path.basename(wav_path))
            if os.path.exists(alt_path):
                wav_path = alt_path
            else:
                raise FileNotFoundError(f"WAVファイルが見つかりません: {wav_path}")

        rate, data = wavfile.read(wav_path)
        if len(data.shape) > 1:
            data = np.mean(data, axis=1)

        max_val = np.max(np.abs(data))
        if max_val > 0:
            data = data.astype(np.float32) / max_val

        samples_sync_full = int(config.SAMPLE_RATE * config.MS_SYNC / 1000)
        
        info_bits_count = config.BIT_IMAGE_CRC + config.BIT_TILE_X + config.BIT_TILE_Y + config.BIT_PAYLOAD_LENGTH
        header_bits_count = info_bits_count + config.BIT_HEADER_CRC
        if header_bits_count % 2 != 0:
            header_bits_count += 1
        header_symbols = header_bits_count // 2
        header_samples = header_symbols * self.samples_per_symbol

        total_samples = len(data)
        duration_sec = total_samples / config.SAMPLE_RATE
        print(f"[Decode-JPEG] デコード開始 (サンプル間隔: {self.samples_per_symbol} samples/sym)")
        print(f"[Decode-JPEG] Header: {header_symbols} symbols ({header_bits_count} bits)")
        print(f"[Decode-JPEG] WAV長: {duration_sec:.1f} 秒")
        success_count = 0
        last_progress_time = time.time()

        try:
            with open(self.output_raw, "w", encoding="utf-8") as f:
                i = 0
                step_size = max(1, int(config.SAMPLE_RATE * 0.002))
                while i < total_samples - samples_sync_full - header_samples:
                    now = time.time()
                    if now - last_progress_time >= 0.2:
                        pct = min(99.0, 100.0 * i / total_samples)
                        if progress_callback:
                            try:
                                progress_callback(pct)
                            except Exception:
                                pass
                        last_progress_time = now

                    sync_power = self.detect_sync_long_dft(data[i : i + self.sync_long_samples])

                    if sync_power > 10.0:
                        search_ptr = i + int(samples_sync_full * 0.5)
                        fine_step = max(1, int(config.SAMPLE_RATE * 0.0005))
                        while search_ptr < len(data) - self.sync_long_samples:
                            p = self.detect_sync_long_dft(data[search_ptr : search_ptr + self.sync_long_samples])
                            if p < sync_power * 0.3:
                                break
                            search_ptr += fine_step

                        align_range = max(5, int(config.SAMPLE_RATE * 0.003))
                        start_scan = max(0, search_ptr - align_range)
                        end_scan = min(len(data) - header_samples, search_ptr + align_range)

                        best_match = None
                        max_snr = -1.0

                        for pos in range(start_scan, end_scan, 1):
                            h_bits, h_snr = self.decode_symbols_exact(data, pos, header_symbols)
                            info_bits = h_bits[:info_bits_count]
                            crc_bits = h_bits[info_bits_count : info_bits_count + config.BIT_HEADER_CRC]

                            expected_crc = self.calculate_crc16_bits(info_bits)
                            actual_crc = self.bits_to_int(crc_bits)

                            if expected_crc == actual_crc:
                                if h_snr > max_snr:
                                    max_snr = h_snr
                                    best_match = (pos, h_bits)

                        if best_match is not None:
                            best_pos, h_bits = best_match
                            idx = 0
                            image_id = self.bits_to_int(h_bits[idx : idx + config.BIT_IMAGE_CRC])
                            idx += config.BIT_IMAGE_CRC
                            tile_x = self.bits_to_int(h_bits[idx : idx + config.BIT_TILE_X])
                            idx += config.BIT_TILE_X
                            tile_y = self.bits_to_int(h_bits[idx : idx + config.BIT_TILE_Y])
                            idx += config.BIT_TILE_Y
                            payload_length = self.bits_to_int(h_bits[idx : idx + config.BIT_PAYLOAD_LENGTH])

                            payload_symbols = (payload_length * 8) // 2
                            payload_samples = payload_symbols * self.samples_per_symbol
                            payload_start = best_pos + header_samples

                            if payload_start + payload_samples <= len(data):
                                p_bits, p_snr = self.decode_symbols_exact(data, payload_start, payload_symbols)

                                payload_bits_str = "".join(str(b) for b in p_bits[:payload_length * 8])
                                snr_4bit_str = self.calculate_snr_to_4bit(p_snr)

                                image_id_bits    = format(image_id,        f'0{config.BIT_IMAGE_CRC}b')
                                tile_x_bits      = format(tile_x,          f'0{config.BIT_TILE_X}b')
                                tile_y_bits      = format(tile_y,          f'0{config.BIT_TILE_Y}b')
                                payload_len_bits = format(payload_length,  f'0{config.BIT_PAYLOAD_LENGTH}b')
                                log_line = image_id_bits + tile_x_bits + tile_y_bits + payload_len_bits + payload_bits_str + snr_4bit_str
                                f.write(log_line + "\n")

                                success_count += 1
                                i = payload_start + payload_samples
                                continue

                        i += step_size
                    else:
                        i += step_size

            if progress_callback:
                progress_callback(100.0)

        except KeyboardInterrupt:
            print(f"\n[停止] 中断されました: 検出済み {success_count} パケット保存")

        print(f"\n[Done-JPEG] デコード完了: ログ書き込み済みパケット数 = {success_count}")
        return success_count, self.output_raw
