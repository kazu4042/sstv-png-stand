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

def apply_bandpass_filter_np(data, sample_rate, low_freq, high_freq, transition_width=100.0):
    """
    scipy.signal（DLL依存）を回避し、NumPyのみで動作するゼロ位相バンドパスフィルタ
    """
    n = len(data)
    if n == 0:
        return data
    freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate)
    fft_data = np.fft.rfft(data)

    weight = np.zeros_like(freqs, dtype=np.float32)
    # 通過帯域
    pass_band = (freqs >= low_freq) & (freqs <= high_freq)
    weight[pass_band] = 1.0

    # スムーズな遷移帯域（コサインロールオフ）
    if transition_width > 0:
        low_trans = (freqs >= low_freq - transition_width) & (freqs < low_freq)
        weight[low_trans] = 0.5 * (1.0 + np.cos(np.pi * (low_freq - freqs[low_trans]) / transition_width))

        high_trans = (freqs > high_freq) & (freqs <= high_freq + transition_width)
        weight[high_trans] = 0.5 * (1.0 + np.cos(np.pi * (freqs[high_trans] - high_freq) / transition_width))

    filtered_fft = fft_data * weight
    filtered_data = np.fft.irfft(filtered_fft, n=n)
    return filtered_data.astype(np.float32)

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

@numba.njit(fastmath=True)
def fast_scan_all_packets_png_native(
    data, samples_per_symbol, samples_sync_full, sync_long_samples,
    sync_win, sync_cos, sync_sin, hamming_win,
    target_phases_cos, target_phases_sin, header_symbols, info_bits_count,
    header_crc_bits, max_packets_cap
):
    res_img_id = np.zeros(max_packets_cap, dtype=np.int32)
    res_tx = np.zeros(max_packets_cap, dtype=np.int32)
    res_ty = np.zeros(max_packets_cap, dtype=np.int32)
    res_plen = np.zeros(max_packets_cap, dtype=np.int32)
    res_start = np.zeros(max_packets_cap, dtype=np.int32)
    found_count = 0

    total_samples = len(data)
    header_samples = header_symbols * samples_per_symbol
    step_size = max(1, int(samples_per_symbol * 0.5))
    fine_step = max(1, int(samples_per_symbol * 0.15))
    align_range = max(5, int(samples_per_symbol * 2.0))

    i = 0
    while i < total_samples - samples_sync_full - header_samples:
        c = 0.0
        s = 0.0
        tot_e = 1e-9
        for j in range(sync_long_samples):
            val = data[i + j] * sync_win[j]
            c += val * sync_cos[j]
            s += val * sync_sin[j]
            tot_e += val * val
        sync_power = c * c + s * s
        sync_norm = sync_power / (tot_e * sync_long_samples)

        # 1000Hz純度（norm）とエネルギーで同期パルスを検出（スマホ小音量・遠距離録音耐性強化）
        if (sync_norm > 0.10 and sync_power > 0.003) or (sync_power > 0.04 and sync_norm > 0.05):
            search_ptr = i + int(samples_sync_full * 0.5)
            while search_ptr < total_samples - sync_long_samples:
                c2 = 0.0
                s2 = 0.0
                for j in range(sync_long_samples):
                    val = data[search_ptr + j] * sync_win[j]
                    c2 += val * sync_cos[j]
                    s2 += val * sync_sin[j]
                p2 = c2 * c2 + s2 * s2
                if p2 < sync_power * 0.25:
                    break
                search_ptr += fine_step

            start_scan = max(0, search_ptr - align_range)
            end_scan = min(total_samples - header_samples, search_ptr + align_range)

            best_pos = -1
            best_snr = -1.0
            best_img = 0
            best_x = 0
            best_y = 0
            best_len = 0

            for pos in range(start_scan, end_scan):
                h_bits, h_snr = fast_decode_symbols_exact(
                    data, pos, header_symbols, samples_per_symbol,
                    hamming_win, target_phases_cos, target_phases_sin
                )
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
                        best_img = fast_bits_to_int(h_bits[0 : 16])
                        best_x = fast_bits_to_int(h_bits[16 : 24])
                        best_y = fast_bits_to_int(h_bits[24 : 32])
                        best_len = fast_bits_to_int(h_bits[32 : 48])

            if best_pos >= 0 and best_len > 0:
                payload_symbols = (best_len * 8) // 2
                payload_samples = payload_symbols * samples_per_symbol
                payload_start = best_pos + header_samples

                if payload_start + payload_samples <= total_samples:
                    if found_count < max_packets_cap:
                        res_img_id[found_count] = best_img
                        res_tx[found_count] = best_x
                        res_ty[found_count] = best_y
                        res_plen[found_count] = best_len
                        res_start[found_count] = payload_start
                        found_count += 1

                    # パケットの末尾へ一気にジャンプ！
                    i = payload_start + payload_samples
                    continue

            # 見つからなかった場合、同期信号の半分スキップして高速化
            i += max(step_size, samples_sync_full // 2)
        else:
            i += step_size

    return res_img_id[:found_count], res_tx[:found_count], res_ty[:found_count], res_plen[:found_count], res_start[:found_count], found_count


from core.base_interfaces import BaseDecoder


class DigitalTurboPNGDecoder(BaseDecoder):
    def __init__(self, user_id=None):
        super().__init__(user_id=user_id)
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

        # --- スマホ録音等の異なるサンプリングレート（48kHz, 16kHz等）を自動リサンプリング ---
        if rate != config.SAMPLE_RATE:
            print(f"[Decode] サンプリングレート変換: {rate} Hz -> {config.SAMPLE_RATE} Hz")
            num_target_samples = int(round(len(data) * (config.SAMPLE_RATE / rate)))
            if num_target_samples > 0:
                data = np.interp(
                    np.linspace(0, len(data), num_target_samples, endpoint=False),
                    np.arange(len(data)),
                    data
                ).astype(np.float32)
                rate = config.SAMPLE_RATE

        # 振幅ズレ・スパイク音耐性: DC除去 ＋ ロバスト正規化（小音量ブースト＆突発スパイク除外）
        data = data.astype(np.float32)
        data = data - np.mean(data)
        abs_data = np.abs(data)
        q = np.percentile(abs_data, 99.0)
        max_v = np.max(abs_data)
        norm_factor = q if q > 1e-4 else (max_v if max_v > 0 else 1.0)
        data = np.clip(data / norm_factor, -2.0, 2.0)

        # --- ノイズ耐性向上: バンドパスフィルタ ---
        if getattr(config, "BANDPASS_ENABLE", False):
            print(f"[Decode] バンドパスフィルタ適用 ({config.VALID_BAND_MIN}Hz - {config.VALID_BAND_MAX}Hz)")
            data = apply_bandpass_filter_np(data, rate, config.VALID_BAND_MIN, config.VALID_BAND_MAX)
            # フィルタ後の再正規化
            q2 = np.percentile(np.abs(data), 99.5)
            if q2 > 1e-5:
                data = np.clip(data / q2, -1.5, 1.5)

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
        print(f"[Decode] 超高速ネイティブデコード開始 (サンプル間隔: {self.samples_per_symbol} samples/sym, WAV長: {duration_sec:.1f}秒)")

        if progress_callback:
            try:
                progress_callback(10.0)
            except Exception:
                pass

        # 🚀 C言語ネイティブ JIT で一括スキャン＆ヘッダ検出
        img_ids, txs, tys, plens, pstarts, count = fast_scan_all_packets_png_native(
            data, self.samples_per_symbol, samples_sync_full, self.sync_long_samples,
            self.sync_win, self.sync_phase_long_cos, self.sync_phase_long_sin,
            self.hamming_win, self.target_phases_cos, self.target_phases_sin,
            header_symbols, info_bits_count, config.BIT_HEADER_CRC, 4096
        )

        if progress_callback:
            try:
                progress_callback(50.0)
            except Exception:
                pass

        # 検出されたパケットのペイロードをデコードしてテキストログに書き出し
        success_count = 0
        with open(self.output_raw, "w", encoding="utf-8") as f:
            for k in range(count):
                image_id = int(img_ids[k])
                tile_x = int(txs[k])
                tile_y = int(tys[k])
                payload_length = int(plens[k])
                payload_start = int(pstarts[k])
                payload_symbols = (payload_length * 8) // 2

                if payload_start + payload_symbols * self.samples_per_symbol > len(data):
                    continue

                p_bits, p_snr = fast_decode_symbols_exact(
                    data, payload_start, payload_symbols, self.samples_per_symbol,
                    self.hamming_win, self.target_phases_cos, self.target_phases_sin
                )
                if p_snr < 1.0:
                    continue

                payload_bits_str = "".join(str(b) for b in p_bits[:payload_length * 8])
                snr_4bit_str = self.calculate_snr_to_4bit(p_snr)

                image_id_bits    = format(image_id,        f'0{config.BIT_IMAGE_CRC}b')
                tile_x_bits      = format(tile_x,          f'0{config.BIT_TILE_X}b')
                tile_y_bits      = format(tile_y,          f'0{config.BIT_TILE_Y}b')
                payload_len_bits = format(payload_length,  f'0{config.BIT_PAYLOAD_LENGTH}b')
                log_line = image_id_bits + tile_x_bits + tile_y_bits + payload_len_bits + payload_bits_str + snr_4bit_str
                f.write(log_line + "\n")

                print(f"  ✨ [LOGGED] ID:{image_id:04X} X:{tile_x:2} Y:{tile_y:2} Len:{payload_length:5} B (SNR:{snr_4bit_str})")
                success_count += 1

                if progress_callback and count > 0:
                    prog_pct = 50.0 + (k / count) * 45.0
                    try:
                        progress_callback(prog_pct)
                    except Exception:
                        pass

        if progress_callback:
            try:
                progress_callback(100.0)
            except Exception:
                pass

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
