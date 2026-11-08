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
    ゼロ位相IIRバンドパスフィルタ (Butterworth SOS)
    巨大FFT配列によるメモリ枯渇(OOM)を防止し、スマホマイク録音時の低周波ハム音や高周波雑音を高速・低メモリで除去。
    """
    n = len(data)
    if n == 0:
        return data

    try:
        from scipy.signal import butter, sosfiltfilt
        nyq = sample_rate / 2.0
        low = max(10.0, min(low_freq, nyq - 100.0))
        high = max(low + 100.0, min(high_freq, nyq - 10.0))
        sos = butter(4, [low, high], btype='bandpass', fs=sample_rate, output='sos')
        return sosfiltfilt(sos, data).astype(np.float32)
    except Exception:
        # SciPyがない場合のフォールバック（小ブロック向け）
        freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate)
        fft_data = np.fft.rfft(data)
        weight = np.zeros_like(freqs, dtype=np.float32)
        pass_band = (freqs >= low_freq) & (freqs <= high_freq)
        weight[pass_band] = 1.0
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

        # 1000Hz 同期パルス参照信号 (全体用 ＆ 5ms×4サブウィンドウ非コヒーレント合成用)
        t_sync = np.arange(self.sync_samples) / float(self.cfg.sample_rate)
        self.sync_win = np.hanning(self.sync_samples).astype(np.float32)
        self.sync_cos = (np.cos(2 * np.pi * self.cfg.freq_sync * t_sync) * self.sync_win).astype(np.float32)
        self.sync_sin = (np.sin(2 * np.pi * self.cfg.freq_sync * t_sync) * self.sync_win).astype(np.float32)

        self.sub_count = 4
        self.sub_len = self.sync_samples // self.sub_count
        t_sub = np.arange(self.sub_len) / float(self.cfg.sample_rate)
        sub_win = np.hamming(self.sub_len).astype(np.float32)
        self.sub_cos = (np.cos(2 * np.pi * self.cfg.freq_sync * t_sub) * sub_win).astype(np.float32)
        self.sub_sin = (np.sin(2 * np.pi * self.cfg.freq_sync * t_sub) * sub_win).astype(np.float32)

    def _bytes_to_bits_str(self, b_data: bytes) -> str:
        """バイト列を '0' と '1' の文字列に変換"""
        return "".join(f"{b:08b}" for b in b_data)

    def _detect_sync_energy(self, chunk: np.ndarray) -> Tuple[float, float]:
        """
        1000Hz同期パルスの相関エネルギーと純度比 (norm) を算出。
        スマホ手持ち録音時のドップラーシフトや室内残響による位相干渉を防ぐため、
        20msコヒーレント相関と5ms×4サブウィンドウ電力合成の最大値を採用する。
        """
        if len(chunk) < self.sync_samples:
            return 0.0, 0.0
        v = chunk[:self.sync_samples]
        total_energy = float(np.sum(v * v))
        if total_energy < 1e-9:
            return 0.0, 0.0

        # 1. 20msコヒーレント相関
        c = float(np.dot(v, self.sync_cos))
        s = float(np.dot(v, self.sync_sin))
        p_full = c * c + s * s
        norm_full = p_full / (total_energy * self.sync_samples * 0.25 + 1e-10)

        # 2. 5ms×4サブウィンドウ電力合成 (残響・ドップラー耐性)
        v_sub = v[: self.sub_count * self.sub_len].reshape(self.sub_count, self.sub_len)
        c_sub = np.dot(v_sub, self.sub_cos)
        s_sub = np.dot(v_sub, self.sub_sin)
        p_sub_arr = c_sub * c_sub + s_sub * s_sub
        p_sub = float(np.sum(p_sub_arr)) * float(self.sub_count)
        norm_sub = p_sub / (total_energy * self.sync_samples * 0.25 + 1e-10)

        # 全4サブウィンドウにある程度エネルギーが分布していることを確認 (単発パルス雑音の排除)
        min_sub_ratio = float(np.min(p_sub_arr)) / (float(np.max(p_sub_arr)) + 1e-10)
        if min_sub_ratio < 0.03:
            norm_sub *= 0.5

        p_best = max(p_full, p_sub)
        norm_best = max(norm_full, norm_sub)
        return p_best, norm_best

    def _verify_or_recover_header(
        self,
        h_syms: np.ndarray,
        h_power: np.ndarray,
        locked_img_id: Optional[int] = None,
        allow_soft_flip: bool = False
    ) -> Optional[bytes]:
        """
        28シンボル(7B)ヘッダのCRC-16検証、およびスマホ録音ノイズ時の軟判定1〜2シンボル反転修復。
        """
        import struct
        max_esi = self.cfg.total_tiles + self.cfg.raptor_repair_count + 64

        def _check_hdr(b_arr: bytes) -> bool:
            if len(b_arr) < 7:
                return False
            img_id, pkt_type, esi, hdr_crc = struct.unpack(">HBHH", b_arr[:7])
            if pkt_type not in (self.cfg.TYPE_DATA, self.cfg.TYPE_REPAIR):
                return False
            if esi >= max_esi:
                return False
            if locked_img_id is not None and img_id != locked_img_id:
                return False
            return fast_calculate_crc16(b_arr[:5]) == hdr_crc

        h_bytes = self.raptor_decoder.symbols_to_bytes(h_syms)
        if _check_hdr(h_bytes):
            return h_bytes[:7]

        if not allow_soft_flip or len(h_power) < 28:
            return None

        # 軟判定修復: 1位と2位の電力差が小さい（確信度が低い）上位4シンボルを特定
        order = np.argsort(h_power[:28], axis=1)
        second_freq_idx = order[:, 2]
        second_syms = self.raptor_decoder.freq_idx_to_symbol[second_freq_idx]
        max_p = h_power[np.arange(28), order[:, 3]]
        sec_p = h_power[np.arange(28), order[:, 2]]
        margin = (max_p - sec_p) / (max_p + 1e-9)
        weak_indices = np.argsort(margin)[:4]

        # 1シンボル反転試行
        for idx1 in weak_indices:
            cand_syms = h_syms.copy()
            cand_syms[idx1] = second_syms[idx1]
            cand_bytes = self.raptor_decoder.symbols_to_bytes(cand_syms)
            if _check_hdr(cand_bytes):
                return cand_bytes[:7]

        # 2シンボル反転試行
        for i_a in range(len(weak_indices)):
            for i_b in range(i_a + 1, len(weak_indices)):
                idx1 = weak_indices[i_a]
                idx2 = weak_indices[i_b]
                cand_syms = h_syms.copy()
                cand_syms[idx1] = second_syms[idx1]
                cand_syms[idx2] = second_syms[idx2]
                cand_bytes = self.raptor_decoder.symbols_to_bytes(cand_syms)
                if _check_hdr(cand_bytes):
                    return cand_bytes[:7]

        return None

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

        step_size = max(16, int(self.sps * 2))  # 約2ms (88サンプル) ステップ
        align_range = max(24, int(self.sps * 3.5))  # ±3.5ms アライメント探索窓

        base_drifts = [round(d * 0.0002, 5) for d in range(-10, 11)]  # -2000ppm 〜 +2000ppm (200ppm刻み)
        cached_drift = 0.0
        locked_img_id: Optional[int] = None

        decoded_packets = []
        i = 0

        # スキャンループ
        while i <= total_samples - packet_payload_samples:
            if progress_callback and total_samples > 0:
                prog = min(99.0, (i / total_samples) * 100.0)
                progress_callback(prog)

            if i + self.sync_samples > total_samples:
                break

            p, norm = self._detect_sync_energy(data[i : i + self.sync_samples])

            # 同期検出: 1000Hz純度比 (norm) または相関電力で検出
            if (norm > 0.06 and p > 0.002) or (p > 0.03 and norm > 0.035):
                # 1. ローカルピーク全域探索: 同期パルス長(sync_samples)全域＋マージン内で真のエネルギー最大位置を特定
                search_start = max(0, i - step_size)
                search_end = min(total_samples - self.sync_samples, i + self.sync_samples + step_size)
                best_p = p
                peak_pos = i
                for test_pos in range(search_start, search_end + 1, 16):
                    p_cand, _ = self._detect_sync_energy(data[test_pos : test_pos + self.sync_samples])
                    if p_cand > best_p:
                        best_p = p_cand
                        peak_pos = test_pos

                # ピーク周辺±16サンプルを4サンプル刻みで精密化
                fine_p_start = max(0, peak_pos - 16)
                fine_p_end = min(total_samples - self.sync_samples, peak_pos + 16)
                for test_pos in range(fine_p_start, fine_p_end + 1, 4):
                    p_cand, _ = self._detect_sync_energy(data[test_pos : test_pos + self.sync_samples])
                    if p_cand > best_p:
                        best_p = p_cand
                        peak_pos = test_pos

                # 2. 20等分同期パルスからのドップラー初期推定 ＆ パケット局所イコライザ算出
                center_ptr = peak_pos + self.sync_samples
                if center_ptr + packet_payload_samples > total_samples:
                    i += step_size
                    continue

                local_eq = self.raptor_decoder.estimate_eq_weights(
                    data[center_ptr : center_ptr + packet_payload_samples]
                )

                sync_metric = self.raptor_decoder.measure_1ms_sync_reference(
                    data[peak_pos : peak_pos + self.sync_samples]
                )
                est_drift = float(np.clip(-sync_metric.get("doppler_ratio", 0.0), -0.0025, 0.0025))

                # 優先順に並べたクロックドリフト候補リストを構築
                drift_candidates = []
                for d_c in [cached_drift, 0.0, round(est_drift, 5)] + base_drifts:
                    if all(abs(d_c - existing) > 1e-5 for existing in drift_candidates):
                        drift_candidates.append(d_c)

                start_scan = max(0, center_ptr - align_range)
                end_scan = min(total_samples - packet_payload_samples, center_ptr + align_range)

                # ヘッダ候補リスト: [(h_snr, pos, d_cand, hdr_bytes), ...]
                header_locks = []
                hdr_drifts = [cached_drift] if abs(cached_drift) < 1e-6 else [cached_drift, 0.0]

                # 第1パス: 高速・無訂正ヘッダCRC一致探索 (2サンプル刻み)
                for pos in range(start_scan, end_scan, 2):
                    for d_cand in hdr_drifts:
                        h_syms, h_snr, h_pow = self.raptor_decoder.demodulate_symbols(
                            data, pos, header_symbols, drift_ratio=d_cand, eq_weights=local_eq, return_power=True
                        )
                        hdr_ok = self._verify_or_recover_header(
                            h_syms, h_pow, locked_img_id=locked_img_id, allow_soft_flip=False
                        )
                        if hdr_ok is not None:
                            header_locks.append((h_snr, pos, d_cand, hdr_ok))

                # 第1パスで見つかった場合、最良位置の周辺±4サンプルを1サンプル刻みで追加探索
                if header_locks:
                    header_locks.sort(key=lambda x: x[0], reverse=True)
                    top_pos = header_locks[0][1]
                    fine_start = max(start_scan, top_pos - 4)
                    fine_end = min(end_scan, top_pos + 5)
                    for pos in range(fine_start, fine_end):
                        for d_cand in hdr_drifts:
                            h_syms, h_snr, h_pow = self.raptor_decoder.demodulate_symbols(
                                data, pos, header_symbols, drift_ratio=d_cand, eq_weights=local_eq, return_power=True
                            )
                            hdr_ok = self._verify_or_recover_header(
                                h_syms, h_pow, locked_img_id=locked_img_id, allow_soft_flip=False
                            )
                            if hdr_ok is not None:
                                header_locks.append((h_snr, pos, d_cand, hdr_ok))

                # 第2パス: スマホ強ノイズ・強ドリフト時の軟判定ヘッダ修復 (1〜2シンボル反転でCRC一致探索)
                if not header_locks:
                    ext_hdr_drifts = list(hdr_drifts)
                    for d_ext in [round(est_drift, 4), -0.0006, 0.0006, -0.0012, 0.0012, -0.0018, 0.0018]:
                        if all(abs(d_ext - ex) > 1e-4 for ex in ext_hdr_drifts):
                            ext_hdr_drifts.append(d_ext)
                    for pos in range(start_scan, end_scan, 3):
                        for d_cand in ext_hdr_drifts:
                            h_syms, h_snr, h_pow = self.raptor_decoder.demodulate_symbols(
                                data, pos, header_symbols, drift_ratio=d_cand, eq_weights=local_eq, return_power=True
                            )
                            hdr_ok = self._verify_or_recover_header(
                                h_syms, h_pow, locked_img_id=locked_img_id, allow_soft_flip=True
                            )
                            if hdr_ok is not None:
                                header_locks.append((h_snr, pos, d_cand, hdr_ok))

                # 位置クラスタリング（近接位置±6サンプル内で最高SNRの代表位置を最大4個抽出）
                header_locks.sort(key=lambda x: x[0], reverse=True)
                distinct_locks = []
                for item in header_locks:
                    if all(abs(item[1] - existing[1]) > 6 for existing in distinct_locks):
                        distinct_locks.append(item)
                        if len(distinct_locks) >= 4:
                            break

                # 4. 各ヘッダロック候補位置に対して全パケット(1020シンボル)復調＆RS復号を試行
                pkt_res = None
                locked_pos_final = -1

                for _, best_pos, best_drift, best_hdr_bytes in distinct_locks:
                    # パケット後半(末尾160シンボル: 860〜1020)のトーン純度(SNR)から最適クロックドリフトを100ppm精度で高速特定
                    tail_sym_offset = packet_symbols - 160
                    tail_num_syms = 160
                    coarse_scores = []
                    for d_c in base_drifts:
                        tail_start = int(round(best_pos + tail_sym_offset * self.sps * (1.0 + d_c)))
                        if tail_start < 0 or tail_start + tail_num_syms * self.sps > total_samples:
                            continue
                        _, t_snr = self.raptor_decoder.demodulate_symbols(
                            data, tail_start, tail_num_syms, drift_ratio=d_c, eq_weights=local_eq, return_power=False
                        )
                        coarse_scores.append((t_snr, d_c))

                    coarse_scores.sort(key=lambda x: x[0], reverse=True)
                    refined_drifts = []
                    for _, top_d in coarse_scores[:3]:
                        for delta_d in (0.0, -0.0001, 0.0001):
                            cand_d = round(top_d + delta_d, 5)
                            if all(abs(cand_d - ex) > 1e-5 for ex in refined_drifts):
                                refined_drifts.append(cand_d)

                    test_drifts = []
                    for d_c in [cached_drift, best_drift, 0.0] + refined_drifts + drift_candidates:
                        if all(abs(d_c - ex) > 1e-5 for ex in test_drifts):
                            test_drifts.append(d_c)

                    demod_trials = []

                    # Step A: 通常RS復号 (最大16B誤り訂正、偽陽性ゼロ)
                    for pos_offset in (0, -2, 2):
                        cur_pos = best_pos + pos_offset
                        if cur_pos < 0 or cur_pos + packet_payload_samples > total_samples:
                            continue
                        for d_val in test_drifts[:10]:
                            symbols, avg_snr_db, power = self.raptor_decoder.demodulate_symbols(
                                data, cur_pos, packet_symbols, drift_ratio=d_val, eq_weights=local_eq, return_power=True
                            )
                            raw_bytes = self.raptor_decoder.symbols_to_bytes(symbols)
                            res = self.raptor_decoder.decode_rs_packet(
                                raw_bytes, snr_db=avg_snr_db, byte_conf=None, known_header=best_hdr_bytes, allow_erasures=False
                            )
                            if res.get("is_valid"):
                                pkt_res = res
                                locked_pos_final = cur_pos
                                if res.get("corrected_bytes", 16) <= 8:
                                    cached_drift = d_val
                                locked_img_id = int(res["img_id"])
                                break
                            demod_trials.append((avg_snr_db, raw_bytes, power, d_val, cur_pos))
                        if pkt_res is not None:
                            break

                    # Step B: 軟判定信頼度によるRS消失(Erasure)訂正 (SNR上位候補から試行)
                    if pkt_res is None and demod_trials:
                        demod_trials.sort(key=lambda x: x[0], reverse=True)
                        for avg_snr_db, raw_bytes, power, d_val, cur_pos in demod_trials[:4]:
                            byte_conf = self.raptor_decoder.compute_byte_confidences(power)
                            res = self.raptor_decoder.decode_rs_packet(
                                raw_bytes, snr_db=avg_snr_db, byte_conf=byte_conf, known_header=best_hdr_bytes, allow_erasures=True
                            )
                            if res.get("is_valid"):
                                pkt_res = res
                                locked_pos_final = cur_pos
                                locked_img_id = int(res["img_id"])
                                break

                    # Step C: 上位複数候補間のビット多数決＋票割れErasure RS復号フォールバック
                    if pkt_res is None and len(demod_trials) >= 3:
                        try:
                            from digital_turbo_fec.fec_engine import fast_bit_majority_vote_with_erasures
                            top_blocks = [
                                (best_hdr_bytes + t[1][7:]) if (best_hdr_bytes is not None and len(best_hdr_bytes) == 7) else t[1]
                                for t in demod_trials[:5]
                            ]
                            top_weights = [max(0.5, t[0]) for t in demod_trials[:5]]
                            voted_block, erase_pos = fast_bit_majority_vote_with_erasures(top_blocks, weights=top_weights)
                            if erase_pos:
                                conf_arr = np.ones(len(voted_block), dtype=np.float64)
                                for ep in erase_pos:
                                    if 0 <= ep < len(conf_arr):
                                        conf_arr[ep] = 0.01
                            else:
                                conf_arr = None
                            res_v = self.raptor_decoder.decode_rs_packet(
                                voted_block,
                                snr_db=float(max(top_weights)),
                                byte_conf=conf_arr,
                                known_header=best_hdr_bytes,
                                allow_erasures=True
                            )
                            if res_v.get("is_valid"):
                                pkt_res = res_v
                                locked_pos_final = best_pos
                                locked_img_id = int(res_v["img_id"])
                        except Exception:
                            pass

                    if pkt_res is not None:
                        break

                if pkt_res and pkt_res.get("is_valid"):
                    decoded_packets.append(pkt_res)
                    # パケット全体を一気にスキップして次へ (末尾の微小マージンを残す)
                    i = max(best_pos, locked_pos_final) + packet_payload_samples - self.sps * 2
                    continue

                # 有効なパケットが見つからなかった場合は、本物のピークを飛び越さないよう小幅に進める
                i = max(i + step_size, peak_pos + step_size * 2)
                continue

            i += step_size

        # 5. テキストログファイルへの出力 (統一された純2進数ビットストリーム行形式)
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
                    # リペアパケット時は tile_x = 0xFF (255), tile_y にリペア番号 (esi - TOTAL_TILES: 0..63) を格納して ESI を完全保持
                    tile_x = 0xFF
                    rep_idx = int(pkt.get("esi", config.TOTAL_TILES)) - config.TOTAL_TILES
                    tile_y = max(0, min(254, rep_idx))

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
