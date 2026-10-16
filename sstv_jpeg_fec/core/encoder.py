"""SSTV JPEG FEC エンコーダ (encoder.py)
画像をJPEGタイル化し、マスターバイナリ化 ➔ 固定長パケット化 ➔ パリティ付与 ➔ ランダムシャッフル ➔ 4-FSK変調。
"""

import io
import os
import random
import struct
import numpy as np
from PIL import Image
from scipy.io import wavfile
from typing import List, Tuple, Dict

from sstv_jpeg_fec.config import CFG
from sstv_jpeg_fec.core.manifest import TileManifest
from sstv_jpeg_fec.core.fec_engine import FECEngine, FECPacket


class SSTVJPEGEncoder:
    """固定長 ＋ FEC (消失訂正) 対応 SSTV エンコーダ"""

    def __init__(self, config=CFG):
        self.cfg = config
        self.fec_engine = FECEngine(
            payload_size=self.cfg.payload_size,
            group_size=self.cfg.fec_group_size
        )
        self.current_phase = 0.0

    @staticmethod
    def calculate_crc16(data: bytes, poly: int = 0x1021, init: int = 0xFFFF) -> int:
        """CRC-16-CCITT 計算"""
        crc = init
        for b in data:
            crc ^= (b << 8)
            for _ in range(8):
                if crc & 0x8000:
                    crc = ((crc << 1) ^ poly) & 0xFFFF
                else:
                    crc = (crc << 1) & 0xFFFF
        return crc

    def build_master_binary(self, image_path: str) -> Tuple[bytes, TileManifest, np.ndarray]:
        """
        画像を16x16タイルごとにJPEG圧縮し、
        [マニフェストヘッダ] + [全タイルJPEGデータ] の1本化バイナリを生成。
        """
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"画像が見つかりません: {image_path}")

        img = Image.open(image_path).convert("RGB").resize((self.cfg.width, self.cfg.height))
        img_arr = np.array(img)

        manifest = TileManifest(width=self.cfg.width, height=self.cfg.height, tile_size=self.cfg.tile_size)
        jpeg_chunks = []

        tile_cnt_x = self.cfg.tile_count_x
        tile_cnt_y = self.cfg.tile_count_y

        for ty in range(tile_cnt_y):
            for tx in range(tile_cnt_x):
                y0 = ty * self.cfg.tile_size
                y1 = min(y0 + self.cfg.tile_size, self.cfg.height)
                x0 = tx * self.cfg.tile_size
                x1 = min(x0 + self.cfg.tile_size, self.cfg.width)

                tile_pixels = img_arr[y0:y1, x0:x1]
                tile_pil = Image.fromarray(tile_pixels)

                with io.BytesIO() as bio:
                    tile_pil.save(
                        bio,
                        format="JPEG",
                        quality=self.cfg.jpeg_quality,
                        restart_marker_blocks=self.cfg.jpeg_restart_marker,
                        subsampling=self.cfg.jpeg_subsampling
                    )
                    j_data = bio.getvalue()

                manifest.add_tile(tx, ty, j_data)
                jpeg_chunks.append(j_data)

        # マニフェストヘッダとJPEGデータを結合
        manifest_header = manifest.serialize_header()
        all_jpegs = b"".join(jpeg_chunks)

        master_stream = manifest_header + all_jpegs
        return master_stream, manifest, img_arr

    def packetize_and_fec(self, master_stream: bytes, image_id: int = 0x1234) -> Tuple[List[bytes], List[FECPacket], int]:
        """
        マスターバイナリを固定長スライスし、XORグループパリティおよび噴水パリティを生成。
        マニフェスト（目録）は最重要メタデータのためカルーセル送出 (リピート) を付加。
        """
        data_packets, orig_len = self.fec_engine.slice_to_data_packets(master_stream)
        parity_packets = self.fec_engine.generate_group_parities(data_packets)

        # 噴水パリティ (ランダム結合) の生成 (設定で有効な場合)
        fountain_packets = []
        if getattr(self.cfg, "enable_fountain_packets", True):
            count = getattr(self.cfg, "fountain_packet_count", 25)
            fountain_packets = self.fec_engine.generate_fountain_parities(data_packets, count=count)

        # マニフェスト保護: 先頭3パケット (P0, P1, P2) はカルーセル (多重) 送信
        # 宇宙通信や放送規格 (DVB/ISDB) の定石: 目録は定期的に再送する
        manifest_packets_count = min(3, len(data_packets))
        carousel_packets = [
            FECPacket(
                packet_id=p.packet_id,
                packet_type=p.packet_type,
                group_id=p.group_id,
                payload=p.payload,
                source_ids=set(p.source_ids)
            )
            for p in data_packets[:manifest_packets_count]
        ]

        all_fec_packets = data_packets + parity_packets + fountain_packets + carousel_packets
        binary_packets = []

        for pkt in all_fec_packets:
            hdr_no_crc = struct.pack(">2sHBH H", b"SF", image_id, pkt.packet_type, pkt.group_id, pkt.packet_id)
            crc_val = self.calculate_crc16(hdr_no_crc)
            full_header = hdr_no_crc + struct.pack(">H", crc_val)

            raw_pkt = full_header + pkt.payload
            binary_packets.append(raw_pkt)

        return binary_packets, all_fec_packets, orig_len

    # --- 4-FSK 音声変調 ---
    def get_tone_samples(self, freq: float, num_samples: int) -> np.ndarray:
        if num_samples <= 0:
            return np.array([], dtype=np.float32)
        t = np.arange(num_samples) / self.cfg.sample_rate
        phase = 2 * np.pi * freq * t + self.current_phase
        wave = np.sin(phase)
        self.current_phase = phase[-1] % (2 * np.pi)
        return wave.astype(np.float32)

    def packet_to_fsk_wave(self, raw_packet: bytes) -> np.ndarray:
        """1パケットを4-FSK音声波形に変換"""
        samples_per_symbol = max(1, int(self.cfg.sample_rate * self.cfg.ms_symbol / 1000))
        sync_samples = max(1, int(self.cfg.sample_rate * self.cfg.ms_sync / 1000))

        sync_wave = self.get_tone_samples(self.cfg.freq_sync, sync_samples)

        # 各バイトを2ビットずつシンボル化 (4-FSK: 1バイト = 4シンボル)
        wave_segments = [sync_wave]
        for b in raw_packet:
            syms = [(b >> 6) & 0b11, (b >> 4) & 0b11, (b >> 2) & 0b11, b & 0b11]
            for s in syms:
                freq = self.cfg.freq_map[s]
                wave_segments.append(self.get_tone_samples(freq, samples_per_symbol))

        return np.concatenate(wave_segments)

    def encode_to_wav(self, image_path: str, output_wav_path: str = None, shuffle: bool = True) -> Dict:
        """完全エンコード処理 (画像 ➔ パケット ➔ 音声WAV保存)"""
        master_stream, manifest, _ = self.build_master_binary(image_path)
        binary_pkts, fec_pkts, orig_len = self.packetize_and_fec(master_stream)

        # ランダムシャッフル
        indices = list(range(len(binary_pkts)))
        if shuffle:
            random.seed(42)
            random.shuffle(indices)

        shuffled_binary_pkts = [binary_pkts[i] for i in indices]
        shuffled_fec_pkts = [fec_pkts[i] for i in indices]

        waves = [self.packet_to_fsk_wave(pkt) for pkt in shuffled_binary_pkts]
        final_wave = np.concatenate(waves)
        final_wave = final_wave / np.max(np.abs(final_wave)) * 0.85

        if output_wav_path:
            os.makedirs(os.path.dirname(os.path.abspath(output_wav_path)), exist_ok=True)
            wavfile.write(output_wav_path, self.cfg.sample_rate, (final_wave * 32767).astype(np.int16))

        data_count = sum(1 for p in fec_pkts if p.is_data)
        parity_count = sum(1 for p in fec_pkts if p.is_parity)

        return {
            "master_length": orig_len,
            "data_packets": data_count,
            "parity_packets": parity_count,
            "total_packets": len(binary_pkts),
            "binary_packets": binary_pkts,
            "fec_packets": fec_pkts,
            "shuffled_fec_packets": shuffled_fec_pkts,
            "manifest": manifest,
            "wav_path": output_wav_path,
            "duration_sec": len(final_wave) / self.cfg.sample_rate
        }
