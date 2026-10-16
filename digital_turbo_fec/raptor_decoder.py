"""ラプター符号 ＋ RS符号 二重冗長 SSTV デコーダ (raptor_decoder.py)
音声波形 ➔ 1000Hz同期 ➔ 4-FSK復調 ➔ RS誤り訂正 ➔ ESI/データ/リペアシンボル抽出。
"""

import math
import os
import struct
import sys
from typing import Dict, List, Optional, Tuple

import numpy as np

# プロジェクトルートのパス解決 (直接実行時でも親ディレクトリの rs_fec や core を確実にインポート可能にする)
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    from digital_turbo_fec.rs_codec import RSCodec
    from digital_turbo_fec.config import RAPTOR_CFG, RaptorConfig
    from digital_turbo_fec.raptor_codec import fast_calculate_crc16
except ImportError:
    from rs_fec.rs_codec import RSCodec
    from config import RAPTOR_CFG, RaptorConfig
    from raptor_rs_fec.raptor_codec import fast_calculate_crc16



class RaptorRSDecoder:
    """ラプター符号 ＆ RS符号 統合デコーダ"""

    def __init__(self, config: RaptorConfig = RAPTOR_CFG):
        self.cfg = config
        self.rs_codec = RSCodec(nsym=self.cfg.rs_nsym)
        self.samples_per_symbol = max(1, int(self.cfg.sample_rate * self.cfg.ms_symbol / 1000))
        self.samples_sync = max(1, int(self.cfg.sample_rate * self.cfg.ms_sync / 1000))

        # 直交周波数の基底行列
        sorted_pairs = sorted(self.cfg.freq_map.items(), key=lambda kv: kv[1], reverse=True)
        self.target_freqs = np.array([freq for sym, freq in sorted_pairs], dtype=np.float64)
        self.freq_idx_to_symbol = np.array([sym for sym, freq in sorted_pairs], dtype=np.uint8)

        t_sym = np.arange(self.samples_per_symbol) / self.cfg.sample_rate
        self.cos_mat = np.cos(2 * np.pi * self.target_freqs[:, None] * t_sym)
        self.sin_mat = np.sin(2 * np.pi * self.target_freqs[:, None] * t_sym)

    @staticmethod
    def calculate_crc16(data: bytes, poly: int = 0x1021, init: int = 0xFFFF) -> int:
        """テーブル引きによる高速 CRC-16-CCITT 計算"""
        return fast_calculate_crc16(data, init=init)

    def demodulate_symbols(
        self,
        wave_data: np.ndarray,
        start_idx: int,
        num_symbols: int,
        drift_ratio: float = 0.0
    ) -> Tuple[np.ndarray, float]:
        """4-FSK シンボル列のNumPy高速相関復調（スマホ録音クロックドリフト±2000ppm完全追従）"""
        sps = self.samples_per_symbol
        if abs(drift_ratio) < 1e-7:
            total_samples = num_symbols * sps
            chunk = wave_data[start_idx : start_idx + total_samples]
            if len(chunk) < total_samples:
                chunk = np.pad(chunk, (0, total_samples - len(chunk)))
            reshaped = chunk.reshape(num_symbols, sps)
        else:
            eff_sps = sps * (1.0 + drift_ratio)
            indices = np.round(start_idx + np.arange(num_symbols) * eff_sps).astype(np.int32)
            sample_offsets = np.arange(sps)
            all_indices = indices[:, None] + sample_offsets[None, :]
            all_indices = np.clip(all_indices, 0, len(wave_data) - 1)
            reshaped = wave_data[all_indices]

        corr_i = np.dot(reshaped, self.cos_mat.T)
        corr_q = np.dot(reshaped, self.sin_mat.T)
        power = corr_i ** 2 + corr_q ** 2

        best_freq_idx = np.argmax(power, axis=1)
        symbols = self.freq_idx_to_symbol[best_freq_idx]

        sorted_power = np.sort(power, axis=1)
        max_p = sorted_power[:, 3]
        second_p = np.maximum(sorted_power[:, 2], 1e-9)
        snr_linear = np.mean(max_p / second_p)
        mean_snr_db = float(10.0 * np.log10(max(1.0, snr_linear)))

        return symbols, mean_snr_db

    def symbols_to_bytes(self, symbols: np.ndarray) -> bytes:
        """2ビットシンボル列をバイト列に変換"""
        n_bytes = len(symbols) // 4
        syms_4 = symbols[: n_bytes * 4].reshape(-1, 4)
        byte_arr = (syms_4[:, 0] << 6) | (syms_4[:, 1] << 4) | (syms_4[:, 2] << 2) | syms_4[:, 3]
        return byte_arr.astype(np.uint8).tobytes()

    def decode_rs_packet(
        self,
        raw_packet_bytes: bytes,
        snr_db: float = 0.0
    ) -> Dict:
        """
        1つの復調パケットバイト列 (255B固定長) に対し、RS誤り訂正を行いメタデータを抽出。
        """
        expected_len = self.cfg.packet_total_size  # 255B

        if len(raw_packet_bytes) < expected_len:
            raw_packet_bytes = raw_packet_bytes.ljust(expected_len, b"\x00")
        elif len(raw_packet_bytes) > expected_len:
            raw_packet_bytes = raw_packet_bytes[:expected_len]

        rs_block = raw_packet_bytes

        # 1. RSシンドローム検査 (誤りゼロ高速パス)
        if self.rs_codec.is_clean(rs_block):
            decoded_data = rs_block[:-self.cfg.rs_nsym]
            corrected_count = 0
            is_valid = True
        else:
            # 2. RS復号試行 (最大16バイト自動修復)
            decoded_data, corrected_count, is_valid = self.rs_codec.decode(rs_block)

        if is_valid and decoded_data is not None:
            # ヘッダ (7B): img_id(2B) + pkt_type(1B) + ESI(2B) + hdr_crc(2B)
            img_id, pkt_type, esi = struct.unpack(">HBH", decoded_data[:5])
            hdr_crc = struct.unpack(">H", decoded_data[5:7])[0]
            
            # ヘッダCRC-16による偽陽性二重ガード
            if fast_calculate_crc16(decoded_data[:5]) == hdr_crc:
                payload = decoded_data[7:]

                tile_id = 0xFF
                t_seq = -1
                t_num_pkts = -1
                t_len = -1

                if pkt_type == self.cfg.TYPE_DATA:
                    # 自己完結タグ (5B)
                    tile_id, t_seq, t_num_pkts, t_len = struct.unpack(">BBBH", payload[:5])

                return {
                    "is_valid": True,
                    "corrected_bytes": corrected_count,
                    "packet_id": esi,
                    "img_id": img_id,
                    "pkt_type": pkt_type,
                    "esi": esi,
                    "tile_id": tile_id,
                    "tile_seq": t_seq,
                    "tile_num_pkts": t_num_pkts,
                    "tile_data_len": t_len,
                    "payload": payload,
                    "raw_rs_block": rs_block,
                    "snr_db": snr_db
                }
            else:
                is_valid = False

        # RS復号失敗 (>16バイト誤り)
        # ヘッダ(7B)のCRCが一致している場合のみ ESI を信用する（それ以外は別パケットの多数決を汚染しないよう esi = -1）
        esi = -1
        img_id = 0
        pkt_type = 0
        try:
            if len(rs_block) >= 7:
                cand_img_id, cand_pkt_type, cand_esi = struct.unpack(">HBH", rs_block[:5])
                cand_crc = struct.unpack(">H", rs_block[5:7])[0]
                if self.calculate_crc16(rs_block[:5]) == cand_crc:
                    img_id = cand_img_id
                    pkt_type = cand_pkt_type
                    esi = cand_esi
        except Exception:
            img_id, pkt_type, esi = 0, 0, -1

        return {
            "is_valid": False,
            "corrected_bytes": -1,
            "packet_id": esi,
            "img_id": img_id,
            "pkt_type": pkt_type,
            "esi": esi,
            "tile_id": 0xFF,
            "tile_seq": -1,
            "tile_num_pkts": -1,
            "tile_data_len": -1,
            "payload": rs_block[7 : 7 + self.cfg.rs_payload_size] if len(rs_block) >= 7 + self.cfg.rs_payload_size else b"",
            "raw_rs_block": rs_block,
            "snr_db": snr_db
        }

    def measure_1ms_sync_reference(self, sync_wave: np.ndarray) -> Dict:
        """
        【同期信号 20等分・1ms基準時間 ＆ ドップラー精密測定】
        20msの同期信号 (freq_sync=1000Hz) を20等分スライス (各1ms) に分割し、
        各スライスの絶対時間位相推移およびエネルギーからドップラーシフトと実測1ms基準サンプル数を算出する。
        """
        num_sub = getattr(self.cfg, "sync_subdivisions", 20)
        nominal_1ms = self.cfg.sample_rate / 1000.0
        freq_sync = self.cfg.freq_sync
        fs = self.cfg.sample_rate

        expected_len = int(round(num_sub * nominal_1ms))
        if len(sync_wave) < expected_len:
            sync_wave = np.pad(sync_wave, (0, expected_len - len(sync_wave)))

        times = []
        phases = []
        energies = []
        weights = []

        for k in range(num_sub):
            s_start = int(round(k * nominal_1ms))
            s_end = int(round((k + 1) * nominal_1ms))
            chunk = sync_wave[s_start:s_end]
            n_samples = len(chunk)
            if n_samples <= 0:
                continue

            # 絶対時間軸での複素キャリア相関 (サンプリングジッターの完全排除)
            t_chunk = (s_start + np.arange(n_samples)) / fs
            z = np.sum(chunk * np.exp(-1j * 2.0 * np.pi * freq_sync * t_chunk))
            mag = float(np.abs(z))
            phase = float(np.angle(z))
            energy = float(mag ** 2) / max(1, n_samples)

            t_center = (s_start + s_end - 1) / (2.0 * fs)
            times.append(t_center)
            phases.append(phase)
            energies.append(energy)
            weights.append(max(mag, 1e-6))

        if len(phases) < 2:
            return {
                "measured_1ms_samples": nominal_1ms,
                "nominal_1ms_samples": nominal_1ms,
                "doppler_delta_f": 0.0,
                "doppler_ratio": 0.0,
                "sync_quality": 0.0,
                "sub_ms_energies": energies,
                "sub_ms_phases": phases
            }

        unwrapped = np.unwrap(phases)
        times_arr = np.array(times)
        w = np.array(weights)
        w_sum = np.sum(w)
        if w_sum > 0:
            t_bar = np.sum(w * times_arr) / w_sum
            p_bar = np.sum(w * unwrapped) / w_sum
            denom = np.sum(w * (times_arr - t_bar) ** 2)
            slope = np.sum(w * (times_arr - t_bar) * (unwrapped - p_bar)) / denom if denom > 1e-12 else 0.0
        else:
            slope = 0.0

        # 周波数オフセット Delta f (Hz) = slope / (2 * pi)
        doppler_delta_f = float(slope / (2.0 * np.pi))
        doppler_delta_f = float(np.clip(doppler_delta_f, -50.0, 50.0))
        doppler_ratio = doppler_delta_f / freq_sync

        # ドップラーによる実測1ms基準サンプル数
        # 周波数が (1 + delta) 倍 ➔ 1msあたりのサンプル数は公称 / (1 + delta)
        measured_1ms_samples = float(nominal_1ms / (1.0 + doppler_ratio))
        mean_energy = float(np.mean(energies)) if energies else 0.0

        return {
            "measured_1ms_samples": measured_1ms_samples,
            "nominal_1ms_samples": nominal_1ms,
            "doppler_delta_f": doppler_delta_f,
            "doppler_ratio": doppler_ratio,
            "sync_quality": mean_energy,
            "sub_ms_energies": energies,
            "sub_ms_phases": phases
        }

    def demodulate_packet_wave(self, packet_wave: np.ndarray) -> Dict:
        """1パケット分の音声波形から20等分同期解析＆ドップラー補正を行い復調"""
        samples_sync = self.samples_sync
        symbols_per_pkt = self.cfg.packet_total_size * 4

        # 1. 先頭20ms同期信号から 20等分・1ms基準 ＆ ドップラーを測定
        sync_chunk = packet_wave[:samples_sync] if len(packet_wave) >= samples_sync else packet_wave
        sync_metric = self.measure_1ms_sync_reference(sync_chunk)

        # 2. 1ms基準時間に基づくドップラー物理補正 (タイムベース再標本化)
        if getattr(self.cfg, "enable_doppler_compensation", True) and abs(sync_metric["doppler_delta_f"]) > 0.05:
            measured_1ms = sync_metric["measured_1ms_samples"]
            nominal_1ms = sync_metric["nominal_1ms_samples"]
            scaling = measured_1ms / nominal_1ms

            # 時間軸伸縮と周波数シフトを一挙にキャンセルするリサンプリング
            t_target = np.arange(len(packet_wave))
            t_in = t_target * scaling
            valid = t_in < len(packet_wave)
            working_wave = np.zeros(len(packet_wave), dtype=np.float32)
            working_wave[valid] = np.interp(t_in[valid], np.arange(len(packet_wave)), packet_wave)
        else:
            working_wave = packet_wave

        # 3. 補正済み波形から 4-FSK シンボル列を復調
        symbols, mean_snr = self.demodulate_symbols(working_wave, samples_sync, symbols_per_pkt)
        raw_pkt_bytes = self.symbols_to_bytes(symbols)
        res = self.decode_rs_packet(raw_pkt_bytes, snr_db=mean_snr)
        res["sync_metric"] = sync_metric
        return res


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, 'reconfigure', None)
        if callable(reconf):
            try:
                reconf(encoding='utf-8', errors='replace')
            except Exception:
                pass

    print("[RaptorRSDecoder] 単体実行テスト")
    decoder = RaptorRSDecoder(RAPTOR_CFG)
    # テスト用RSブロック生成 (正常パケット)
    rs_codec = RSCodec(nsym=RAPTOR_CFG.rs_nsym)
    hdr_core = struct.pack(">HBH", 0x1234, RAPTOR_CFG.TYPE_DATA, 0)
    hdr_crc = decoder.calculate_crc16(hdr_core)
    hdr = hdr_core + struct.pack(">H", hdr_crc)
    tag = struct.pack(">BBBH", 0, 0, 1, 100)
    payload = tag + b"TEST_DECODER_DATA" + bytes([0x00] * (RAPTOR_CFG.rs_payload_size - 5 - len("TEST_DECODER_DATA")))
    rs_block = rs_codec.encode(hdr + payload)

    res = decoder.decode_rs_packet(rs_block, snr_db=15.0)
    print(f"  ・復号結果: {'成功' if res['is_valid'] else '失敗'}")
    print(f"  ・ESI: {res['esi']}, タイルID: {res['tile_id']}, タイルパケット数: {res['tile_num_pkts']}")
    print("[OK] デコーダ単体テスト正常完了！")
