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
import io
from PIL import Image
from datetime import datetime
import numba
from scipy.signal import butter, lfilter

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

from digital_turbo_png import config_turbo as config

@numba.njit
def fast_detect_sync_long_dft(audio_segment, sync_long_samples, sync_win, sync_phase_long_cos, sync_phase_long_sin):
    if len(audio_segment) < sync_long_samples:
        return 0.0
    c = 0.0
    s = 0.0
    for i in range(sync_long_samples):
        val = audio_segment[i] * sync_win[i]
        c += val * sync_phase_long_cos[i]
        s += val * sync_phase_long_sin[i]
    return c*c + s*s

@numba.njit
def fast_detect_symbol_dft(audio_segment, samples_per_symbol, hamming_win, target_phases_cos, target_phases_sin):
    if len(audio_segment) < samples_per_symbol:
        return 0, 0.0

    num_phases = target_phases_cos.shape[0]
    powers = np.zeros(num_phases, dtype=np.float64)

    for p in range(num_phases):
        c = 0.0
        s = 0.0
        for i in range(samples_per_symbol):
            val = audio_segment[i] * hamming_win[i]
            c += val * target_phases_cos[p, i]
            s += val * target_phases_sin[p, i]
        powers[p] = c*c + s*s

    best_idx = 0
    peak_power = powers[0]
    for p in range(1, num_phases):
        if powers[p] > peak_power:
            best_idx = p
            peak_power = powers[p]

    noise_sum = 0.0
    for p in range(num_phases):
        if p != best_idx:
            noise_sum += powers[p]
            
    noise_power = (noise_sum / (num_phases - 1)) + 1e-10
    snr = peak_power / noise_power

    return best_idx, snr

@numba.njit
def fast_decode_symbols_exact(audio_data, start_idx, num_symbols, samples_per_symbol, hamming_win, target_phases_cos, target_phases_sin):
    all_bits = np.zeros(num_symbols * 2, dtype=np.int32)
    snr_sum = 0.0
    
    bits_map_0 = np.array([0, 0, 1, 1], dtype=np.int32)
    bits_map_1 = np.array([0, 1, 0, 1], dtype=np.int32)

    valid_symbols = 0
    for s in range(num_symbols):
        pos = start_idx + s * samples_per_symbol
        if pos + samples_per_symbol > len(audio_data):
            break
        
        segment = audio_data[pos : pos + samples_per_symbol]
        best_idx, snr = fast_detect_symbol_dft(segment, samples_per_symbol, hamming_win, target_phases_cos, target_phases_sin)
        
        all_bits[s*2] = bits_map_0[best_idx]
        all_bits[s*2+1] = bits_map_1[best_idx]
        snr_sum += snr
        valid_symbols += 1

    avg_snr = snr_sum / valid_symbols if valid_symbols > 0 else 0.0
    return all_bits[:valid_symbols*2], avg_snr

@numba.njit
def fast_calculate_crc16_bits(bit_array, poly=0x1021, init_val=0xFFFF):
    crc = init_val
    for bit in bit_array:
        inv = ((crc >> 15) ^ bit) & 1
        crc = (crc << 1) & 0xFFFF
        if inv:
            crc ^= poly
    return crc

@numba.njit
def fast_bits_to_int(bits):
    val = 0
    for b in bits:
        val = (val << 1) | b
    return val

@numba.njit
def fast_find_header_alignment(data, start_scan, end_scan, header_symbols, samples_per_symbol, hamming_win, target_phases_cos, target_phases_sin, info_bits_count, header_crc_bits):
    best_pos = -1
    best_snr = -1.0
    best_bits = np.zeros(header_symbols * 2, dtype=np.int32)
    
    for pos in range(start_scan, end_scan):
        h_bits, h_snr = fast_decode_symbols_exact(data, pos, header_symbols, samples_per_symbol, hamming_win, target_phases_cos, target_phases_sin)
        if len(h_bits) < info_bits_count + header_crc_bits:
            continue
        info_bits = h_bits[:info_bits_count]
        crc_bits = h_bits[info_bits_count : info_bits_count + header_crc_bits]
        
        expected_crc = fast_calculate_crc16_bits(info_bits)
        actual_crc = fast_bits_to_int(crc_bits)
        
        if expected_crc == actual_crc:
            if h_snr > best_snr:
                best_snr = h_snr
                best_pos = pos
                best_bits = h_bits.copy()
                
    return best_pos, best_snr, best_bits


class DigitalTurboPNGDecoder:
    def __init__(self, user_id=None):
        import uuid
        timestamp = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:6]}"
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))

        # テキストログのみ出力（タイル画像はアグリゲータが生成する）
        log_dir = os.path.join(root_dir, config.TEXT_LOG_DIR)
        os.makedirs(log_dir, exist_ok=True)
        
        if user_id:
            self.output_raw = os.path.join(log_dir, f"{config.TEXT_LOG_PREFIX}_user_{user_id}_{timestamp}.txt")
        else:
            self.output_raw = os.path.join(log_dir, f"{config.TEXT_LOG_PREFIX}_{timestamp}.txt")

        self.update_cache()

    def update_cache(self):
        # 累積サンプリング誤差をシャットアウトするため、エンコーダと完全一致させる
        self.samples_per_symbol = max(1, int(config.SAMPLE_RATE * config.MS_SYMBOL / 1000))
        self.t_arr = np.arange(self.samples_per_symbol) / config.SAMPLE_RATE
        target_phases = np.array([2 * np.pi * f * self.t_arr for f in config.TARGET_FREQS], dtype=np.float64)
        self.target_phases_cos = np.cos(target_phases)
        self.target_phases_sin = np.sin(target_phases)
        self.hamming_win = np.hamming(self.samples_per_symbol)

        # 誤検出回避用の5msロング Sync 判定用
        self.sync_long_samples = max(10, int(config.SAMPLE_RATE * 0.005))
        self.sync_t_arr = np.arange(self.sync_long_samples) / config.SAMPLE_RATE
        sync_phase_long = 2 * np.pi * config.FREQ_SYNC * self.sync_t_arr
        self.sync_phase_long_cos = np.cos(sync_phase_long)
        self.sync_phase_long_sin = np.sin(sync_phase_long)
        self.sync_win = np.hamming(self.sync_long_samples)

    @classmethod
    def warmup_jit(cls):
        """Numba JIT関数をダミーデータで事前コンパイルし、初回デコード遅延を防ぐ"""
        try:
            dummy_samples = 441
            dummy_audio = np.zeros(dummy_samples, dtype=np.float32)
            dummy_win = np.hamming(dummy_samples)
            dummy_target_phases = np.zeros((4, dummy_samples), dtype=np.float64)
            dummy_cos = np.cos(dummy_target_phases)
            dummy_sin = np.sin(dummy_target_phases)
            dummy_sync_cos = np.cos(np.zeros(dummy_samples))
            dummy_sync_sin = np.sin(np.zeros(dummy_samples))
            dummy_bits = np.array([0, 1, 0, 1], dtype=np.int32)

            fast_detect_sync_long_dft(dummy_audio, dummy_samples, dummy_win, dummy_sync_cos, dummy_sync_sin)
            fast_detect_symbol_dft(dummy_audio, dummy_samples, dummy_win, dummy_cos, dummy_sin)
            fast_decode_symbols_exact(dummy_audio, 0, 2, dummy_samples // 2, np.hamming(dummy_samples // 2), np.cos(np.zeros((4, dummy_samples // 2))), np.sin(np.zeros((4, dummy_samples // 2))))
            fast_calculate_crc16_bits(dummy_bits)
            fast_bits_to_int(dummy_bits)
            fast_find_header_alignment(dummy_audio, 0, 2, 2, dummy_samples // 2, np.hamming(dummy_samples // 2), np.cos(np.zeros((4, dummy_samples // 2))), np.sin(np.zeros((4, dummy_samples // 2))), 4, 16)
        except Exception:
            pass

    def detect_sync_long_dft(self, audio_segment):
        return fast_detect_sync_long_dft(audio_segment, self.sync_long_samples, self.sync_win, self.sync_phase_long_cos, self.sync_phase_long_sin)

    def detect_symbol_dft(self, audio_segment):
        return fast_detect_symbol_dft(audio_segment, self.samples_per_symbol, self.hamming_win, self.target_phases_cos, self.target_phases_sin)

    def decode_symbols_exact(self, audio_data, start_idx, num_symbols):
        return fast_decode_symbols_exact(audio_data, start_idx, num_symbols, self.samples_per_symbol, self.hamming_win, self.target_phases_cos, self.target_phases_sin)

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
        return fast_bits_to_int(bits)

    def bits_to_bytearray(self, bits):
        byte_list = []
        for i in range(0, len(bits), 8):
            chunk = bits[i:i+8]
            if len(chunk) == 8:
                byte_list.append(fast_bits_to_int(chunk))
        return bytearray(byte_list)

    @staticmethod
    def calculate_crc16_bits(bit_list, poly=0x1021, init_val=0xFFFF):
        return fast_calculate_crc16_bits(bit_list, poly, init_val)

    def run(self, wav_path, progress_callback=None):
        self.update_cache()
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
        if not os.path.exists(wav_path):
            alt_path = os.path.join(root_dir, "data", "digital_turbo_png", "audio", os.path.basename(wav_path))
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

        # --- ノイズ耐性向上: バンドパスフィルタ ---
        if getattr(config, "BANDPASS_ENABLE", False):
            print(f"[Decode] バンドパスフィルタ適用 ({config.VALID_BAND_MIN}Hz - {config.VALID_BAND_MAX}Hz)")
            nyq = 0.5 * rate
            low = config.VALID_BAND_MIN / nyq
            high = config.VALID_BAND_MAX / nyq
            b, a = butter(4, [low, high], btype='band')
            data = lfilter(b, a, data)
            # フィルタ後の再正規化
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

        import time
        total_samples = len(data)
        duration_sec = total_samples / config.SAMPLE_RATE
        print(f"[Decode] デコード開始 (サンプル間隔: {self.samples_per_symbol} samples/sym)")
        print(f"[Decode] Header: {header_symbols} symbols ({header_bits_count} bits)")
        print(f"[Decode] WAV長: {duration_sec:.1f} 秒  (Ctrl+C で途中停止・部分保存が可能)")
        success_count = 0
        decoded_packets = []
        last_progress_time = time.time()

        try:
            with open(self.output_raw, "w", encoding="utf-8") as f:
                i = 0
                step_size = max(1, int(config.SAMPLE_RATE * 0.002))
                while i < total_samples - samples_sync_full - header_samples:

                    # ===== 進捗を表示・コールバック =====
                    now = time.time()
                    if now - last_progress_time >= 2.0:
                        pct = 100.0 * i / total_samples
                        pos_sec = i / config.SAMPLE_RATE
                        remain_sec = max(0, duration_sec - pos_sec)
                        print(f"  [進捗] {pct:5.1f}% ({pos_sec:.0f}/{duration_sec:.0f}秒) | パケット検出: {success_count} | 残り約 {remain_sec:.0f}秒", flush=True)
                        if progress_callback:
                            progress_callback(pct)
                        last_progress_time = now

                    sync_power = self.detect_sync_long_dft(data[i : i + self.sync_long_samples])

                    # 同期検出の固定閾値を下げ、微弱な信号も拾いやすくする（ノイズ判定は後続のCRCで弾く）
                    if sync_power > 1.5:
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

                        best_pos, max_snr, h_bits = fast_find_header_alignment(
                            data, start_scan, end_scan, header_symbols,
                            self.samples_per_symbol, self.hamming_win,
                            self.target_phases_cos, self.target_phases_sin,
                            info_bits_count, config.BIT_HEADER_CRC
                        )

                        if best_pos >= 0:
                            idx = 0
                            image_id = self.bits_to_int(h_bits[idx : idx+config.BIT_IMAGE_CRC])
                            idx += config.BIT_IMAGE_CRC
                            tile_x = self.bits_to_int(h_bits[idx : idx+config.BIT_TILE_X])
                            idx += config.BIT_TILE_X
                            tile_y = self.bits_to_int(h_bits[idx : idx+config.BIT_TILE_Y])
                            idx += config.BIT_TILE_Y
                            payload_length = self.bits_to_int(h_bits[idx : idx+config.BIT_PAYLOAD_LENGTH])

                            payload_symbols = (payload_length * 8) // 2
                            payload_samples = payload_symbols * self.samples_per_symbol
                            payload_start = best_pos + header_samples

                            if payload_start + payload_samples <= len(data):
                                p_bits, p_snr = self.decode_symbols_exact(data, payload_start, payload_symbols)

                                # --- 堅牢性（Robustness）の向上 ---
                                # CRC16を偶然通過したノイズ（約1/65536の確率）を確実に排除するため、
                                # ペイロード全体のSNRを評価し、基準値未満ならノイズとみなして破棄する
                                if p_snr < 1.5:
                                    continue

                                payload_bits_str = "".join(str(b) for b in p_bits[:payload_length*8])
                                snr_4bit_str = self.calculate_snr_to_4bit(p_snr)

                                # 全フィールドを2進数に変換して書き込む（0と1のみ）
                                # 形式: [image_id:16bit][tile_x:8bit][tile_y:8bit][payload_len:16bit][payload_bits][snr:4bit]
                                image_id_bits    = format(image_id,        f'0{config.BIT_IMAGE_CRC}b')
                                tile_x_bits      = format(tile_x,          f'0{config.BIT_TILE_X}b')
                                tile_y_bits      = format(tile_y,          f'0{config.BIT_TILE_Y}b')
                                payload_len_bits = format(payload_length,  f'0{config.BIT_PAYLOAD_LENGTH}b')
                                log_line = image_id_bits + tile_x_bits + tile_y_bits + payload_len_bits + payload_bits_str + snr_4bit_str
                                f.write(log_line + "\n")

                                decoded_packets.append((image_id, tile_x, tile_y, payload_length, payload_bits_str, snr_4bit_str))

                                print(f"  ✨ [LOGGED] ID:{image_id:04X} X:{tile_x:2} Y:{tile_y:2} Len:{payload_length:5} B (SNR:{snr_4bit_str})")
                                success_count += 1
                                i = payload_start + payload_samples
                                continue

                        i += step_size
                    else:
                        i += step_size

        except KeyboardInterrupt:
            print(f"\n[停止] Ctrl+C を検出しました。途中まで検出した {success_count} パケットをログに保存済みです。")
            print(f"[Info] 部分ログ: {self.output_raw}")
            print(f"[Info] aggregator_turbo.py を実行すると、部分データから復元を試みます。")

        print(f"\n[Done] デコード完了: ログ書き込み済みパケット数 = {success_count}")
        print(f"[Output] テキストログ保存先: {self.output_raw}\n")
        return success_count, self.output_raw

if __name__ == "__main__":
    try:
        decoder = DigitalTurboPNGDecoder()
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
        wav_path = os.path.join(root_dir, config.OUTPUT_WAV)
        if not os.path.exists(wav_path):
            print(f"[Error] WAVファイルが見つかりません: {wav_path}")
            print(f"[Info]  先に encoder_turbo.py を実行してください。")
            sys.exit(1)
        success_count, log_path = decoder.run(wav_path)
        print(f"[Info] テキストログ: {log_path}")
        print(f"[Info] 次に aggregator_turbo.py を実行して画像を復元してください。")
    except KeyboardInterrupt:
        print("\n[停止] プログラムを終了しました。")
