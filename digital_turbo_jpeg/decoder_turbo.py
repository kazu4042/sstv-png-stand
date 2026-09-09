import sys
for stream in (sys.stdout, sys.stderr):
    reconf = getattr(stream, 'reconfigure', None)
    if callable(reconf):
        try:
            reconf(encoding='utf-8')
        except Exception:
            pass

import numpy as np
from scipy.io import wavfile
import os
import io
from PIL import Image
from datetime import datetime
import time
import numba
from typing import Tuple, Optional, Callable

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

from digital_turbo_jpeg import config_turbo as config
from core.base_interfaces import BaseDecoder


def apply_bandpass_filter_np(data, sample_rate, low_freq, high_freq, transition_width=100.0):
    """
    scipy.signal（DLL依存）を回避し、NumPyのみで動作するゼロ位相バンドパスフィルタ
    スマホマイク録音時の低周波ハム音・空調音や高周波雑音を一掃して同期精度を劇的に向上させる。
    """
    n = len(data)
    if n == 0:
        return data
    freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate)
    fft_data = np.fft.rfft(data)

    weight = np.zeros_like(freqs, dtype=np.float32)
    pass_band = (freqs >= low_freq) & (freqs <= high_freq)
    weight[pass_band] = 1.0

    if transition_width > 0:
        low_trans = (freqs >= low_freq - transition_width) & (freqs < low_freq)
        weight[low_trans] = 0.5 * (1.0 + np.cos(np.pi * (low_freq - freqs[low_trans]) / transition_width))

        high_trans = (freqs > high_freq) & (freqs <= high_freq + transition_width)
        weight[high_trans] = 0.5 * (1.0 + np.cos(np.pi * (freqs[high_trans] - high_freq) / transition_width))

    filtered_fft = fft_data * weight
    filtered_data = np.fft.irfft(filtered_fft, n=n)
    return filtered_data.astype(np.float32)


# =========================================================================
# 🚀 Numba JIT 超耐ノイズ・スマート最尤誤り訂正 C レベル演算関数群
# =========================================================================

@numba.njit(fastmath=True)
def fast_calculate_crc16_bits(bit_array, length, poly=0x1021, init_val=0xFFFF):
    crc = init_val
    for i in range(length):
        bit = bit_array[i]
        inv = ((crc >> 15) ^ bit) & 1
        crc = (crc << 1) & 0xFFFF
        if inv:
            crc ^= poly
    return crc

@numba.njit(fastmath=True)
def fast_bits_to_int_slice(bits, start, length):
    val = 0
    for i in range(start, start + length):
        val = (val << 1) | int(bits[i])
    return val

@numba.njit(fastmath=True)
def fast_detect_sync_energy(data, pos, sync_samples, sync_win, sync_cos, sync_sin):
    """1000Hz 同期エネルギーの計算（低SNR対応）"""
    if pos + sync_samples > len(data):
        return 0.0, 0.0
    c = 0.0
    s = 0.0
    total_energy = 0.0
    for j in range(sync_samples):
        v = data[pos + j] * sync_win[j]
        c += v * sync_cos[j]
        s += v * sync_sin[j]
        total_energy += v * v
    p = c * c + s * s
    norm = p / (total_energy * sync_samples * 0.25 + 1e-10)
    return p, norm

@numba.njit(fastmath=True)
def fast_decode_symbols_soft(data, start_pos, num_symbols, sps, win, cos_mat, sin_mat, out_bits, rank1_syms, rank2_syms, diff_scores):
    """
    シンボルを軟判定（Soft-decision）復号し、第1候補と第2候補、および信頼度（diff_score）を出力
    """
    snr_sum = 0.0
    valid_syms = 0
    for s in range(num_symbols):
        pos = start_pos + s * sps
        if pos + sps > len(data):
            break

        p0 = (np.dot(data[pos : pos + sps] * win, cos_mat[0]) ** 2 +
              np.dot(data[pos : pos + sps] * win, sin_mat[0]) ** 2)
        p1 = (np.dot(data[pos : pos + sps] * win, cos_mat[1]) ** 2 +
              np.dot(data[pos : pos + sps] * win, sin_mat[1]) ** 2)
        p2 = (np.dot(data[pos : pos + sps] * win, cos_mat[2]) ** 2 +
              np.dot(data[pos : pos + sps] * win, sin_mat[2]) ** 2)
        p3 = (np.dot(data[pos : pos + sps] * win, cos_mat[3]) ** 2 +
              np.dot(data[pos : pos + sps] * win, sin_mat[3]) ** 2)

        # パワートップ2の探索
        powers = np.array([p0, p1, p2, p3], dtype=np.float64)
        best_idx = 0
        second_idx = 1
        if powers[1] > powers[0]:
            best_idx = 1
            second_idx = 0
        for k in range(2, 4):
            if powers[k] > powers[best_idx]:
                second_idx = best_idx
                best_idx = k
            elif powers[k] > powers[second_idx]:
                second_idx = k

        peak = powers[best_idx]
        noise = (p0 + p1 + p2 + p3 - peak) / 3.0 + 1e-10
        snr = peak / noise

        rank1_syms[s] = best_idx
        rank2_syms[s] = second_idx
        diff_scores[s] = powers[best_idx] - powers[second_idx]

        out_bits[s * 2] = (best_idx >> 1) & 1
        out_bits[s * 2 + 1] = best_idx & 1
        snr_sum += snr
        valid_syms += 1

    avg_snr = snr_sum / valid_syms if valid_syms > 0 else 0.0
    return valid_syms, avg_snr


@numba.njit(fastmath=True)
def fast_try_header_crc_fast(
    data, pos, header_symbols, sps, hamming_win, cos_mat, sin_mat,
    h_buf, rank1, rank2, diffs, info_bits_count, header_crc_bits, do_soft_repair
):
    """
    ヘッダをデコードし、まず高速に第1候補でCRC検証。必要時のみ軟判定最尤誤り訂正を行う。
    """
    syms_dec, h_snr = fast_decode_symbols_soft(
        data, pos, header_symbols, sps, hamming_win, cos_mat, sin_mat,
        h_buf, rank1, rank2, diffs
    )
    if syms_dec < header_symbols:
        return False, 0, 0, 0, 0, 0.0

    # 1. まず第1候補のまま CRC 検証
    exp_crc = fast_calculate_crc16_bits(h_buf, info_bits_count)
    act_crc = fast_bits_to_int_slice(h_buf, info_bits_count, header_crc_bits)
    if exp_crc == act_crc:
        cur_img = fast_bits_to_int_slice(h_buf, 0, 16)
        cur_x = fast_bits_to_int_slice(h_buf, 16, 8)
        cur_y = fast_bits_to_int_slice(h_buf, 24, 8)
        cur_len = fast_bits_to_int_slice(h_buf, 32, 16)
        if cur_x < 32 and cur_y < 32 and 50 <= cur_len <= 32768:
            return True, cur_img, cur_x, cur_y, cur_len, h_snr

    # 2. 軟判定最尤誤り訂正 (ピーク位置でのみ実行)
    if do_soft_repair:
        for try_s in range(header_symbols):
            old_val = rank1[try_s]
            new_val = rank2[try_s]
            h_buf[try_s * 2] = (new_val >> 1) & 1
            h_buf[try_s * 2 + 1] = new_val & 1

            exp_crc2 = fast_calculate_crc16_bits(h_buf, info_bits_count)
            act_crc2 = fast_bits_to_int_slice(h_buf, info_bits_count, header_crc_bits)

            if exp_crc2 == act_crc2:
                cur_img = fast_bits_to_int_slice(h_buf, 0, 16)
                cur_x = fast_bits_to_int_slice(h_buf, 16, 8)
                cur_y = fast_bits_to_int_slice(h_buf, 24, 8)
                cur_len = fast_bits_to_int_slice(h_buf, 32, 16)
                if cur_x < 32 and cur_y < 32 and 50 <= cur_len <= 32768:
                    return True, cur_img, cur_x, cur_y, cur_len, h_snr * 0.9

            h_buf[try_s * 2] = (old_val >> 1) & 1
            h_buf[try_s * 2 + 1] = old_val & 1

    return False, 0, 0, 0, 0, 0.0


@numba.njit(fastmath=True)
def fast_check_jpeg_soi_fast(data, payload_start, sps, win, cos_mat, sin_mat):
    """ペイロード先頭 8 シンボル (16 bit) を復号し、JPEG SOI (0xFF 0xD8) であるか高速検査"""
    buf = np.zeros(16, dtype=np.int8)
    r1 = np.zeros(8, dtype=np.int32)
    r2 = np.zeros(8, dtype=np.int32)
    diff = np.zeros(8, dtype=np.float64)
    syms, _ = fast_decode_symbols_soft(data, payload_start, 8, sps, win, cos_mat, sin_mat, buf, r1, r2, diff)
    if syms < 8:
        return False
    expected = np.array([1,1,1,1,1,1,1,1, 1,1,0,1,1,0,0,0], dtype=np.int8)
    err = 0
    for i in range(16):
        if buf[i] != expected[i]:
            err += 1
    return err <= 3


@numba.njit(fastmath=True)
def fast_scan_range_native(
    data, start_pos, end_pos, sps, samples_sync_full, sync_long_samples, sync_win, sync_cos, sync_sin,
    hamming_win, cos_mat, sin_mat, header_symbols, info_bits_count, header_crc_bits,
    max_packets_cap
):
    """
    指定区間 [start_pos, end_pos] を超耐ノイズ・スマート最尤誤り訂正付き C ループ走査
    """
    total_len = len(data)
    header_samples = header_symbols * sps
    scan_limit = min(total_len - samples_sync_full - header_samples, end_pos)

    res_img_id = np.zeros(max_packets_cap, dtype=np.int32)
    res_tx = np.zeros(max_packets_cap, dtype=np.int32)
    res_ty = np.zeros(max_packets_cap, dtype=np.int32)
    res_plen = np.zeros(max_packets_cap, dtype=np.int32)
    res_snr = np.zeros(max_packets_cap, dtype=np.float64)
    res_start = np.zeros(max_packets_cap, dtype=np.int64)
    found_count = 0

    h_buf = np.zeros(header_symbols * 2, dtype=np.int8)
    rank1 = np.zeros(header_symbols, dtype=np.int32)
    rank2 = np.zeros(header_symbols, dtype=np.int32)
    diffs = np.zeros(header_symbols, dtype=np.float64)

    i = max(0, start_pos)
    coarse_step = max(8, int(samples_sync_full * 0.25))
    fine_step = max(2, int(sps * 0.25))
    align_range = max(6, int(sps * 2.0))

    while i < scan_limit:
        p, norm = fast_detect_sync_energy(data, i, sync_long_samples, sync_win, sync_cos, sync_sin)

        # 1000Hz純度（norm）とエネルギーで同期パルスを検出（スマホ小音量・遠距離録音耐性強化）
        if (norm > 0.10 and p > 0.003) or (p > 0.08 and norm > 0.05):
            # 同期パルスの終端（立ち下がり＝データ先頭）をファインステップで探索
            search_ptr = i + int(samples_sync_full * 0.4)
            while search_ptr < total_len - sync_long_samples:
                p2, norm2 = fast_detect_sync_energy(data, search_ptr, sync_long_samples, sync_win, sync_cos, sync_sin)
                if norm2 < norm * 0.35 or p2 < p * 0.25:
                    break
                search_ptr += fine_step

            start_scan = max(0, search_ptr - align_range)
            end_scan = min(total_len - header_samples, search_ptr + align_range)

            best_pos = -1
            max_snr = -1.0
            best_img = 0
            best_x = 0
            best_y = 0
            best_len = 0

            # 1. 通常CRCスキャン (超高速)
            for pos in range(start_scan, end_scan, 2):
                ok, cur_img, cur_x, cur_y, cur_len, h_snr = fast_try_header_crc_fast(
                    data, pos, header_symbols, sps, hamming_win, cos_mat, sin_mat,
                    h_buf, rank1, rank2, diffs, info_bits_count, header_crc_bits, False
                )
                if ok and h_snr > max_snr:
                    max_snr = h_snr
                    best_pos = pos
                    best_img = cur_img
                    best_x = cur_x
                    best_y = cur_y
                    best_len = cur_len

            # 2. 通常CRCで見つからない場合のみ、最尤誤り訂正スキャン
            if best_pos < 0:
                for pos in range(start_scan, end_scan, 2):
                    ok, cur_img, cur_x, cur_y, cur_len, h_snr = fast_try_header_crc_fast(
                        data, pos, header_symbols, sps, hamming_win, cos_mat, sin_mat,
                        h_buf, rank1, rank2, diffs, info_bits_count, header_crc_bits, True
                    )
                    if ok and h_snr > max_snr:
                        max_snr = h_snr
                        best_pos = pos
                        best_img = cur_img
                        best_x = cur_x
                        best_y = cur_y
                        best_len = cur_len
                        break  # 最尤で1つ見つかれば即確定

            if best_pos >= 0:
                payload_symbols = (best_len * 8) // 2
                payload_samples = payload_symbols * sps
                payload_start = best_pos + header_samples

                if payload_start + payload_samples <= total_len:
                    # JPEG SOI マーカー二重検証
                    if fast_check_jpeg_soi_fast(data, payload_start, sps, hamming_win, cos_mat, sin_mat):
                        if found_count < max_packets_cap:
                            res_img_id[found_count] = best_img
                            res_tx[found_count] = best_x
                            res_ty[found_count] = best_y
                            res_plen[found_count] = best_len
                            res_snr[found_count] = max_snr
                            res_start[found_count] = payload_start
                            found_count += 1

                        # パケット末尾へジャンプ
                        i = payload_start + payload_samples
                        continue

            # 見つからなかった場合でも、同期パルス終端以降まで確実にスキップ（重複空振りを完全防止）
            i = search_ptr + max(4, sps)
        else:
            i += coarse_step

    return res_img_id[:found_count], res_tx[:found_count], res_ty[:found_count], res_plen[:found_count], res_snr[:found_count], res_start[:found_count], found_count


def fast_scan_all_packets_native(
    data, sps, samples_sync_full, sync_long_samples, sync_win, sync_cos, sync_sin,
    hamming_win, cos_mat, sin_mat, header_symbols, info_bits_count, header_crc_bits,
    max_packets_cap
):
    return fast_scan_range_native(
        data, 0, len(data), sps, samples_sync_full, sync_long_samples, sync_win, sync_cos, sync_sin,
        hamming_win, cos_mat, sin_mat, header_symbols, info_bits_count, header_crc_bits,
        max_packets_cap
    )


class DigitalTurboJPEGDecoder(BaseDecoder):
    """SSTV Turbo JPEG デコーダ (完全 Numba JIT 超耐ノイズ・最尤誤り訂正復号エンジン)"""
    def __init__(self, user_id: Optional[int] = None):
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
        target_phases = np.array([2 * np.pi * f * self.t_arr for f in config.TARGET_FREQS], dtype=np.float64)
        self.cos_mat = np.cos(target_phases)
        self.sin_mat = np.sin(target_phases)
        self.hamming_win = np.hamming(self.samples_per_symbol)

        self.sync_long_samples = max(10, int(config.SAMPLE_RATE * 0.005))
        self.sync_t_arr = np.arange(self.sync_long_samples) / config.SAMPLE_RATE
        sync_phase_long = 2 * np.pi * config.FREQ_SYNC * self.sync_t_arr
        self.sync_cos = np.cos(sync_phase_long)
        self.sync_sin = np.sin(sync_phase_long)
        self.sync_win = np.hamming(self.sync_long_samples)

    def calculate_snr_to_4bit(self, avg_snr: float) -> str:
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

    def run(self, wav_path: str, progress_callback: Optional[Callable[[float], None]] = None) -> Tuple[int, str]:
        self.update_cache()
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
        if not os.path.exists(wav_path):
            alt_path = os.path.join(root_dir, "data", "digital_turbo_jpeg", "audio", os.path.basename(wav_path))
            if os.path.exists(alt_path):
                wav_path = alt_path
            else:
                raise FileNotFoundError(f"WAVファイルが見つかりません: {wav_path}")

        t0 = time.time()
        rate, data = wavfile.read(wav_path)
        if len(data.shape) > 1:
            data = np.mean(data, axis=1)

        # サンプリングレート自動変換
        if rate != config.SAMPLE_RATE:
            print(f"[Decode-JPEG] サンプリングレート変換: {rate} Hz -> {config.SAMPLE_RATE} Hz")
            num_target_samples = int(round(len(data) * (config.SAMPLE_RATE / rate)))
            if num_target_samples > 0:
                data = np.interp(
                    np.linspace(0, len(data), num_target_samples, endpoint=False),
                    np.arange(len(data)),
                    data
                ).astype(np.float32)
                rate = config.SAMPLE_RATE

        # 0. ゼロ位相FFTバンドパスフィルタ (500Hz〜2500Hz)
        # スマホマイク録音時の低周波エアコン音、手ブレ雑音、高周波ノイズを一掃
        data = apply_bandpass_filter_np(data, rate, config.VALID_BAND_MIN, config.VALID_BAND_MAX)

        # 振幅ズレ・スパイク音耐性: DC除去 ＋ ロバスト正規化（小音量ブースト＆突発スパイク除外）
        data = data.astype(np.float32)
        data = data - np.mean(data)
        abs_data = np.abs(data)
        q = np.percentile(abs_data, 99.0)
        max_v = np.max(abs_data)
        norm_factor = q if q > 1e-4 else (max_v if max_v > 0 else 1.0)
        data = np.clip(data / norm_factor, -2.0, 2.0)

        samples_sync_full = int(config.SAMPLE_RATE * config.MS_SYNC / 1000)
        info_bits_count = config.BIT_IMAGE_CRC + config.BIT_TILE_X + config.BIT_TILE_Y + config.BIT_PAYLOAD_LENGTH
        header_bits_count = info_bits_count + config.BIT_HEADER_CRC
        if header_bits_count % 2 != 0:
            header_bits_count += 1
        header_symbols = header_bits_count // 2

        total_samples = len(data)
        duration_sec = total_samples / config.SAMPLE_RATE
        print(f"[Decode-JPEG] 超耐ノイズ・ネイティブスキャン開始 (WAV長: {duration_sec:.1f}秒, {total_samples} samples)")

        if progress_callback:
            try:
                progress_callback(10.0)
            except Exception:
                pass

        # 🚀 C 言語ネイティブ JIT でスキャン＆最尤誤り訂正復号 (リアルタイム進捗通知付き)
        # 短い音声は一括、長い音声は適応的チャンク走査で進捗を滑らかに更新
        step_samples = max(int(config.SAMPLE_RATE * 5.0), total_samples // 15)
        overlap_samples = int(config.SAMPLE_RATE * 1.5) # パケット最大長以上のオーバーラップ

        detected_packets = {}  # (img_id, tx, ty) -> (img_id, tx, ty, plen, snr, pstart)

        cur_pos = 0
        while cur_pos < total_samples - samples_sync_full - (header_symbols * self.samples_per_symbol):
            chunk_end = min(total_samples, cur_pos + step_samples + overlap_samples)
            c_img_ids, c_txs, c_tys, c_plens, c_snrs, c_pstarts, c_count = fast_scan_range_native(
                data, cur_pos, chunk_end, self.samples_per_symbol, samples_sync_full, self.sync_long_samples,
                self.sync_win, self.sync_cos, self.sync_sin, self.hamming_win,
                self.cos_mat, self.sin_mat, header_symbols, info_bits_count,
                config.BIT_HEADER_CRC, 1024
            )

            for idx in range(c_count):
                key = (int(c_img_ids[idx]), int(c_txs[idx]), int(c_tys[idx]))
                snr_v = float(c_snrs[idx])
                if key not in detected_packets or snr_v > detected_packets[key][4]:
                    detected_packets[key] = (
                        int(c_img_ids[idx]), int(c_txs[idx]), int(c_tys[idx]),
                        int(c_plens[idx]), snr_v, int(c_pstarts[idx])
                    )

            cur_pos += step_samples
            if progress_callback:
                try:
                    pct = min(49.0, 10.0 + (cur_pos / total_samples) * 40.0)
                    progress_callback(pct)
                except Exception:
                    pass

        scan_time = time.time() - t0
        all_pkts = list(detected_packets.values())
        count = len(all_pkts)
        print(f"[Decode-JPEG] ネイティブスキャン完了: {count} パケット検出 (所要時間: {scan_time:.2f}秒)")

        if progress_callback:
            try:
                progress_callback(50.0)
            except Exception:
                pass

        # 検出されたパケットのペイロードを展開してテキストログに書き出し
        success_count = 0
        with open(self.output_raw, "w", encoding="utf-8") as f:
            for k in range(count):
                image_id, tile_x, tile_y, payload_length, _, p_start = all_pkts[k]

                payload_symbols = (payload_length * 8) // 2
                p_buf = np.zeros(payload_length * 8, dtype=np.int8)
                r1_dummy = np.zeros(payload_symbols, dtype=np.int32)
                r2_dummy = np.zeros(payload_symbols, dtype=np.int32)
                diff_dummy = np.zeros(payload_symbols, dtype=np.float64)

                _, p_snr = fast_decode_symbols_soft(
                    data, p_start, payload_symbols, self.samples_per_symbol,
                    self.hamming_win, self.cos_mat, self.sin_mat,
                    p_buf, r1_dummy, r2_dummy, diff_dummy
                )

                payload_bits_str = "".join(str(b) for b in p_buf)
                snr_4bit_str = self.calculate_snr_to_4bit(p_snr)

                image_id_bits    = format(image_id,        f'0{config.BIT_IMAGE_CRC}b')
                tile_x_bits      = format(tile_x,          f'0{config.BIT_TILE_X}b')
                tile_y_bits      = format(tile_y,          f'0{config.BIT_TILE_Y}b')
                payload_len_bits = format(payload_length,  f'0{config.BIT_PAYLOAD_LENGTH}b')
                log_line = image_id_bits + tile_x_bits + tile_y_bits + payload_len_bits + payload_bits_str + snr_4bit_str
                f.write(log_line + "\n")

                print(f"  ✨ [LOGGED] ID:{image_id:04X} X:{tile_x:2} Y:{tile_y:2} Len:{payload_length:5} B (SNR:{snr_4bit_str})", flush=True)
                success_count += 1

                if progress_callback and count > 0:
                    try:
                        p_prog = min(98.0, 50.0 + ((k + 1) / count) * 48.0)
                        progress_callback(p_prog)
                    except Exception:
                        pass

        total_time = time.time() - t0
        if progress_callback:
            try:
                progress_callback(100.0)
            except Exception:
                pass

        print(f"\n[Done-JPEG] デコード完了: ログ書き込み済みパケット数 = {success_count} (総所要時間: {total_time:.2f}秒)")
        print(f"[Output] テキストログ保存先: {self.output_raw}\n")
        return success_count, self.output_raw


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="SSTV Turbo JPEG デコーダー")
    parser.add_argument("--user-id", type=int, default=1, help="ユーザーID (デフォルト: 1)")
    parser.add_argument("--wav", type=str, default=None, help="入力 WAV ファイルパス")
    args = parser.parse_args()

    try:
        decoder = DigitalTurboJPEGDecoder(user_id=args.user_id)
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
        wav_path = args.wav if args.wav else os.path.join(root_dir, config.OUTPUT_WAV)
        if not os.path.exists(wav_path):
            print(f"[Error] WAVファイルが見つかりません: {wav_path}")
            print(f"[Info]  先に encoder_turbo.py を実行してください。")
            sys.exit(1)
        success_count, log_path = decoder.run(wav_path)
        print(f"[Info] テキストログ: {log_path}")
        print(f"[Info] 次に aggregator_turbo.py を実行して画像を復元してください。")
    except KeyboardInterrupt:
        print("\n[停止] プログラムを終了しました。")
