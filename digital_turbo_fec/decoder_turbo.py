"""SSTV Turbo FEC (Raptor + RS) スマホ録音最適化デコーダ (decoder_turbo.py)
【sstv-png-stand BaseDecoder 準拠】
スマホマイク録音（環境ノイズ・エアコン音・反響・小音量・高域減衰・48k/44.1kサンプリング差）
に完全適応した超耐ノイズ・ゼロ位相バンドパス・AGC・高感度1000Hz同期・NumPy超高速ベクトル4-FSK復調デコーダ。
"""

import math
import os
import sys
import uuid
from datetime import datetime
from typing import Callable, Optional, Tuple

import numpy as np
from scipy.io import wavfile

_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_CURRENT_DIR, "../"))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from core.base_interfaces import BaseDecoder
from digital_turbo_fec import config_turbo as config
from digital_turbo_fec.config import RAPTOR_CFG
from digital_turbo_fec.raptor_codec import fast_calculate_crc16
from digital_turbo_fec.raptor_decoder import RaptorRSDecoder


def apply_bandpass_filter_np(
    data: np.ndarray,
    sample_rate: float,
    low_freq: float = 400.0,
    high_freq: float = 9500.0,
    transition_width: float = 100.0
) -> np.ndarray:
    """
    NumPyのみで動作するゼロ位相バンドパスフィルタ
    スマホマイク録音時の低周波ハム音・エアコン音（50/60/120Hz）や高周波雑音を一掃して同期精度を劇的に向上させる。
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


class DigitalTurboFECDecoder(BaseDecoder):
    """SSTV Turbo FEC デコーダ (スマホ録音最適化 ＆ BaseDecoder 準拠)"""

    def __init__(self, user_id: Optional[int] = None):
        super().__init__(user_id=user_id)
        self.cfg = RAPTOR_CFG
        self.raptor_decoder = RaptorRSDecoder(self.cfg)

        timestamp = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        log_dir = getattr(config, "TEXT_LOG_DIR", "data/digital_turbo_fec/logs")
        if not os.path.isabs(log_dir):
            log_dir_path = os.path.join(_PROJECT_ROOT, log_dir)
        else:
            log_dir_path = log_dir
        os.makedirs(log_dir_path, exist_ok=True)

        user_tag = f"_user_{user_id}" if user_id is not None else ""
        self.log_filename = f"{config.TEXT_LOG_PREFIX}_{timestamp}{user_tag}.txt"
        self.log_path = os.path.join(log_dir_path, self.log_filename)

        self.sps = self.raptor_decoder.samples_per_symbol
        self.sync_samples = self.raptor_decoder.samples_sync

        # 1000Hz 同期パルス参照信号
        t_sync = np.arange(self.sync_samples) / float(self.cfg.sample_rate)
        self.sync_win = np.hanning(self.sync_samples).astype(np.float32)
        self.sync_cos = (np.cos(2 * np.pi * self.cfg.freq_sync * t_sync) * self.sync_win).astype(np.float32)
        self.sync_sin = (np.sin(2 * np.pi * self.cfg.freq_sync * t_sync) * self.sync_win).astype(np.float32)

    def _bytes_to_bits_str(self, b_data: bytes) -> str:
        """バイト列を '0' と '1' の文字列に変換"""
        return "".join(f"{b:08b}" for b in b_data)

    def _detect_sync_energy(self, chunk: np.ndarray) -> Tuple[float, float]:
        """
        1000Hz同期パルスの相関エネルギーと純度比 (norm) を算出
        """
        if len(chunk) < self.sync_samples:
            return 0.0, 0.0
        v = chunk[:self.sync_samples]
        c = float(np.dot(v, self.sync_cos))
        s = float(np.dot(v, self.sync_sin))
        p = c * c + s * s
        total_energy = float(np.sum(v * v))
        norm = p / (total_energy * self.sync_samples * 0.25 + 1e-10)
        return p, norm

    def run(
        self,
        wav_path: str,
        progress_callback: Optional[Callable[[float], None]] = None
    ) -> Tuple[int, str]:
        """
        スマホ録音 WAV ファイルから高精度にパケットを復調・誤り訂正し、ビットストリームログを出力
        戻り値: (success_count, log_path)
        """
        if not os.path.exists(wav_path):
            raise FileNotFoundError(f"WAVファイルが見つかりません: {wav_path}")

        sr, raw_data = wavfile.read(wav_path)
        if raw_data.ndim > 1:
            raw_data = np.mean(raw_data, axis=1)

        # 1. 44.1kHz への高精度リサンプリング
        target_sr = self.cfg.sample_rate
        if sr != target_sr:
            try:
                from scipy.signal import resample_poly
                gcd_val = math.gcd(int(target_sr), int(sr))
                up = int(target_sr) // gcd_val
                down = int(sr) // gcd_val
                data = resample_poly(raw_data, up, down).astype(np.float32)
            except Exception:
                num_target = int(round(len(raw_data) * (target_sr / sr)))
                data = np.interp(
                    np.linspace(0, len(raw_data), num_target, endpoint=False),
                    np.arange(len(raw_data)),
                    raw_data
                ).astype(np.float32)
        else:
            data = raw_data.astype(np.float32)

        # 2. DCオフセット除去 ＆ 動的音量正規化 (AGC)
        data = data - np.mean(data)
        max_v = np.max(np.abs(data))
        if max_v > 1e-4:
            data = data / max_v

        # 3. ゼロ位相バンドパスフィルタ
        if getattr(config, "BANDPASS_ENABLE", True):
            data = apply_bandpass_filter_np(
                data,
                sample_rate=target_sr,
                low_freq=config.VALID_BAND_MIN,
                high_freq=config.VALID_BAND_MAX,
                transition_width=100.0
            )

        import struct

        total_samples = len(data)
        packet_symbols = self.cfg.packet_total_size * 4  # 255B * 4 sym/B = 1020 sym
        packet_payload_samples = packet_symbols * self.sps
        header_symbols = 28  # ヘッダ7B (28シンボル)

        step_size = max(16, int(self.sync_samples * 0.3))
        fine_step = max(4, self.sps // 2)
        align_range = max(16, int(self.sps * 2.5))

        decoded_packets = []
        i = 0

        # スキャンループ
        while i < total_samples - self.sync_samples - packet_payload_samples:
            if progress_callback and total_samples > 0:
                prog = min(99.0, (i / total_samples) * 100.0)
                progress_callback(prog)

            p, norm = self._detect_sync_energy(data[i : i + self.sync_samples])

            # 同期検出: 1000Hz純度比 (norm) または相関電力で検出
            if (norm > 0.08 and p > 0.005) or (p > 0.05 and norm > 0.04):
                # 1. 同期パルスの立ち下がりエッジを探索（パルスの終端位置）
                search_ptr = i + int(self.sync_samples * 0.5)
                while search_ptr < total_samples - self.sync_samples:
                    p2, _ = self._detect_sync_energy(data[search_ptr : search_ptr + self.sync_samples])
                    if p2 < p * 0.30:
                        break
                    search_ptr += fine_step

                # 2. 立ち下がりエッジ周辺でヘッダCRC-16整合性による高精度シンボルアライメント＆クロック偏差探索
                start_scan = max(0, search_ptr - align_range)
                end_scan = min(total_samples - packet_payload_samples, search_ptr + align_range)

                best_pos = -1
                best_snr = -1.0
                best_drift = 0.0
                # スマホとPCの水晶発振子クロック偏差 (最大±1200ppm) 候補
                drift_candidates = [0.0, -0.0005, 0.0005, -0.0003, 0.0003, -0.0008, 0.0008]

                for pos in range(start_scan, end_scan, 2):
                    for d_cand in drift_candidates:
                        h_syms, h_snr = self.raptor_decoder.demodulate_symbols(data, pos, header_symbols, drift_ratio=d_cand)
                        h_bytes = self.raptor_decoder.symbols_to_bytes(h_syms)
                        if len(h_bytes) < 7:
                            continue
                        hdr_core = h_bytes[:5]
                        hdr_crc = struct.unpack(">H", h_bytes[5:7])[0]
                        if fast_calculate_crc16(hdr_core) == hdr_crc:
                            if h_snr > best_snr:
                                best_snr = h_snr
                                best_pos = pos
                                best_drift = d_cand

                # 3. ヘッダがロックオンされた場合、全パケットをドリフト補正付きで一括復調＆RS誤り訂正
                if best_pos >= 0:
                    pkt_res = None
                    test_drifts = [best_drift] + [d for d in drift_candidates if d != best_drift]
                    for d_val in test_drifts:
                        symbols, avg_snr_db = self.raptor_decoder.demodulate_symbols(data, best_pos, packet_symbols, drift_ratio=d_val)
                        raw_bytes = self.raptor_decoder.symbols_to_bytes(symbols)
                        res = self.raptor_decoder.decode_rs_packet(raw_bytes, snr_db=avg_snr_db)
                        if res.get("is_valid"):
                            pkt_res = res
                            break

                    if pkt_res and pkt_res.get("is_valid"):
                        decoded_packets.append(pkt_res)
                        # パケット全体を一気にスキップして次へ
                        i = best_pos + packet_payload_samples
                        continue

            i += step_size

        # 4. テキストログファイルへの出力 (統一された純2進数ビットストリーム行形式)
        success_count = 0
        with open(self.log_path, "w", encoding="utf-8") as f:
            for pkt in decoded_packets:
                img_id = pkt["img_id"]
                pkt_type = pkt["pkt_type"]
                snr_db = pkt["snr_db"]
                payload = pkt["payload"]

                if pkt_type == self.cfg.TYPE_DATA:
                    tile_id = pkt["tile_id"]
                    tile_x = tile_id % config.TILE_COUNT_X
                    tile_y = tile_id // config.TILE_COUNT_X
                else:
                    # リペアパケット時は 0xFF (255)
                    tile_x = 0xFF
                    tile_y = 0xFF

                payload_len = len(payload)
                payload_bits = self._bytes_to_bits_str(payload)

                snr_int = min(15, max(0, int(round(snr_db))))
                snr_bits = f"{snr_int:04b}"

                header_bits = (
                    f"{img_id:016b}"
                    f"{tile_x:08b}"
                    f"{tile_y:08b}"
                    f"{payload_len:016b}"
                )
                line = f"{header_bits}{payload_bits}{snr_bits}\n"
                f.write(line)
                success_count += 1

        if progress_callback:
            progress_callback(100.0)

        return success_count, self.log_path
