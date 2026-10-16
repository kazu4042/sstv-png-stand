"""ラプター符号 ＋ RS符号 二重冗長 SSTV エンコーダ (raptor_encoder.py)
タイル単位グルーピング ＋ パケット内RS(255, 223) ＋ ラプターリペア生成 ＋ 均等サンドイッチシャッフル ＋ NumPy高速4-FSK変調。
"""

import io
import math
import os
import random
import struct
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image
from scipy.io import wavfile

# プロジェクトルートのパス解決 (直接実行時でも親ディレクトリの rs_fec や core を確実にインポート可能にする)
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    from digital_turbo_fec.rs_codec import RSCodec
    from digital_turbo_fec.config import RAPTOR_CFG, RaptorConfig
    from digital_turbo_fec.raptor_codec import RaptorCodec, fast_calculate_crc16
except ImportError:
    from rs_fec.rs_codec import RSCodec
    from config import RAPTOR_CFG, RaptorConfig
    from raptor_rs_fec.raptor_codec import RaptorCodec, fast_calculate_crc16



@dataclass
class RaptorRSPacket:
    """ラプター符号 ＋ RS符号 ハイブリッドパケット構造体 (255B固定長)"""
    pkt_id: int
    img_id: int
    pkt_type: int        # TYPE_DATA (0x00) or TYPE_REPAIR (0x01)
    esi: int             # 0〜K-1: データ, K〜: リペア
    tile_id: int         # リペアパケット時は 0xFF
    tile_seq: int
    tile_num_pkts: int
    tile_data_len: int
    hdr_crc: int
    payload: bytes       # 216B
    rs_block: bytes      # 255B
    raw_packet: bytes    # 255B


class RaptorRSEncoder:
    """ラプター符号 ＆ RS符号 統合エンコーダ"""

    def __init__(self, config: RaptorConfig = RAPTOR_CFG):
        self.cfg = config
        self.rs_codec = RSCodec(nsym=self.cfg.rs_nsym)
        self.current_phase = 0.0

        # 4-FSK周波数のNumPyルックアップ配列 (高速ベクトル変調用)
        self.freq_lut_arr = np.array([
            self.cfg.freq_map[0b00],
            self.cfg.freq_map[0b01],
            self.cfg.freq_map[0b10],
            self.cfg.freq_map[0b11]
        ], dtype=np.float32)

    @staticmethod
    def calculate_crc16(data: bytes, poly: int = 0x1021, init: int = 0xFFFF) -> int:
        """テーブル引きによる高速 CRC-16-CCITT 計算"""
        return fast_calculate_crc16(data, init=init)

    @staticmethod
    def extract_tile_entropy(tile_jpeg: bytes) -> Tuple[bytes, bytes]:
        """
        JPEGバイナリから固定ヘッダ (SOI〜SOS末尾) とエントロピー符号化データ (SOS末尾以降) を分離。
        戻り値: (header_bytes, entropy_bytes)
        """
        soi_idx = tile_jpeg.find(b'\xff\xd8')
        sos_idx = tile_jpeg.find(b'\xff\xda', (soi_idx + 2) if soi_idx != -1 else 0)
        if soi_idx != -1 and sos_idx != -1 and sos_idx + 3 < len(tile_jpeg):
            sos_len = (tile_jpeg[sos_idx + 2] << 8) | tile_jpeg[sos_idx + 3]
            entropy_start = sos_idx + 2 + sos_len
            if entropy_start <= len(tile_jpeg):
                return tile_jpeg[:entropy_start], tile_jpeg[entropy_start:]
        return b"", tile_jpeg

    def build_tile_jpegs(self, image_path: str) -> Tuple[List[bytes], np.ndarray]:
        """画像をタイルサイズごとに個別にJPEG圧縮し、JPEGバイト列リストを生成"""
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"画像が見つかりません: {image_path}")

        img = Image.open(image_path).convert("RGB").resize((self.cfg.width, self.cfg.height))
        img_arr = np.array(img)

        tile_cnt_x = self.cfg.tile_count_x
        tile_cnt_y = self.cfg.tile_count_y
        tile_jpegs: List[bytes] = []

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

                tile_jpegs.append(j_data)

        return tile_jpegs, img_arr

    def packetize(
        self,
        tile_jpegs: List[bytes],
        image_id: int = 0x1234,
        shuffle_tiles: bool = True,
        seed: Optional[int] = 42
    ) -> Tuple[List[bytes], List[RaptorRSPacket], int]:
        """
        【パケット化 ＆ ラプター生成 ＆ 均等サンドイッチシャッフル】
        1. タイル毎にデータパケット群 (ESI: 0〜K-1) を生成 (enable_header_stripping時は1タイル1パケット化！)
        2. 全データパケットから Raptor リペアパケット (ESI: K〜K+R-1) を生成
        3. 全パケットに RS(255, 223) パリティ(32B) を付加
        4. タイル単位グルーピングを崩さず、タイルの合間にラプターパケットを均等にサンドイッチ配置！
        """
        chunk_size = self.cfg.rs_data_chunk_size
        packets_by_tile: Dict[int, List[RaptorRSPacket]] = {}
        all_data_packets: List[RaptorRSPacket] = []
        esi_counter = 0
        total_jpeg_bytes = sum(len(j) for j in tile_jpegs)

        strip_header = getattr(self.cfg, "enable_header_stripping", False)

        # 1. データパケット群の生成 (ESI = 0 〜 K-1)
        for tile_id, j_data in enumerate(tile_jpegs):
            if strip_header:
                # 共通固定ヘッダ(629B)を省略し、エントロピー符号化データのみパケット化
                _, send_data = self.extract_tile_entropy(j_data)
            else:
                send_data = j_data

            t_len = len(send_data)
            num_pkts = max(1, (t_len + chunk_size - 1) // chunk_size)
            tile_pkt_list: List[RaptorRSPacket] = []

            for seq in range(num_pkts):
                start = seq * chunk_size
                end = min(start + chunk_size, t_len)
                chunk = send_data[start:end]

                if len(chunk) < chunk_size:
                    chunk = chunk.ljust(chunk_size, b"\x00")

                # 自己完結タグ (5B): tile_id, seq, num_pkts, t_len
                tag = struct.pack(">BBBH", tile_id, seq, num_pkts, t_len)
                payload = tag + chunk  # 216B

                # ヘッダメタデータ (7B): img_id(2B) + pkt_type(1B: DATA=0) + ESI(2B) + hdr_crc(2B)
                hdr_core = struct.pack(">HBH", image_id, self.cfg.TYPE_DATA, esi_counter)
                hdr_crc_val = self.calculate_crc16(hdr_core)
                hdr_full_meta = hdr_core + struct.pack(">H", hdr_crc_val)

                # RS符号化: 223B -> 255B
                rs_input = hdr_full_meta + payload
                rs_block = self.rs_codec.encode(rs_input)

                pkt_obj = RaptorRSPacket(
                    pkt_id=esi_counter,
                    img_id=image_id,
                    pkt_type=self.cfg.TYPE_DATA,
                    esi=esi_counter,
                    tile_id=tile_id,
                    tile_seq=seq,
                    tile_num_pkts=num_pkts,
                    tile_data_len=t_len,
                    hdr_crc=hdr_crc_val,
                    payload=payload,
                    rs_block=rs_block,
                    raw_packet=rs_block
                )
                tile_pkt_list.append(pkt_obj)
                all_data_packets.append(pkt_obj)
                esi_counter += 1

            packets_by_tile[tile_id] = tile_pkt_list

        k_source = len(all_data_packets)

        # 2. ラプターリペアシンボルの生成
        num_repair = self.cfg.raptor_repair_count
        raptor_codec = RaptorCodec(
            num_source_symbols=k_source,
            symbol_size=self.cfg.symbol_size,
            seed=self.cfg.raptor_seed,
            max_subblock_size=getattr(self.cfg, "max_subblock_size", 0),
            use_global_mds=getattr(self.cfg, "use_global_mds", True)
        )

        source_payloads = [p.payload for p in all_data_packets]
        repair_symbols = raptor_codec.encode_repair_symbols(source_payloads, num_repair)

        repair_packets: List[RaptorRSPacket] = []
        for rep_esi, rep_payload in repair_symbols:
            # リペアヘッダ: img_id(2B) + pkt_type(1B: REPAIR=1) + ESI(2B) + hdr_crc(2B)
            hdr_core = struct.pack(">HBH", image_id, self.cfg.TYPE_REPAIR, rep_esi)
            hdr_crc_val = self.calculate_crc16(hdr_core)
            hdr_full_meta = hdr_core + struct.pack(">H", hdr_crc_val)

            # リペアパケットにもRS(255, 223)を付与 (二重保護)
            rs_input = hdr_full_meta + rep_payload
            rs_block = self.rs_codec.encode(rs_input)

            rep_obj = RaptorRSPacket(
                pkt_id=rep_esi,
                img_id=image_id,
                pkt_type=self.cfg.TYPE_REPAIR,
                esi=rep_esi,
                tile_id=0xFF,  # リペアパケットは全タイル共通
                tile_seq=-1,
                tile_num_pkts=-1,
                tile_data_len=-1,
                hdr_crc=hdr_crc_val,
                payload=rep_payload,
                rs_block=rs_block,
                raw_packet=rs_block
            )
            repair_packets.append(rep_obj)

        # 3. 均等サンドイッチ・シャッフル (インターリーブ配置)
        tile_ids = list(packets_by_tile.keys())
        if shuffle_tiles:
            rng = random.Random(seed) if seed is not None else random.Random()
            rng.shuffle(tile_ids)

        ordered_packets: List[RaptorRSPacket] = []
        ordered_raw_packets: List[bytes] = []

        if self.cfg.enable_sandwich_interleave and repair_packets:
            # 何タイルごとに1つのラプターリペアパケットを挟むか
            interval = max(1, len(tile_ids) // len(repair_packets))
            repair_iter = iter(repair_packets)

            for i, t_id in enumerate(tile_ids):
                # タイル内のパケット (seq 0, 1, 2) を連続して配置 ➔ 即時描画UXを完全維持！
                for p in packets_by_tile[t_id]:
                    ordered_packets.append(p)
                    ordered_raw_packets.append(p.raw_packet)

                # 一定間隔ごとにラプターリペアパケットを1個均等にサンドイッチ
                if (i + 1) % interval == 0:
                    try:
                        rp = next(repair_iter)
                        ordered_packets.append(rp)
                        ordered_raw_packets.append(rp.raw_packet)
                    except StopIteration:
                        pass

            # 余ったリペアパケットがあれば末尾に追加
            for rp in repair_iter:
                ordered_packets.append(rp)
                ordered_raw_packets.append(rp.raw_packet)
        else:
            # サンドイッチ無効時は順次配置
            for t_id in tile_ids:
                for p in packets_by_tile[t_id]:
                    ordered_packets.append(p)
                    ordered_raw_packets.append(p.raw_packet)
            for rp in repair_packets:
                ordered_packets.append(rp)
                ordered_raw_packets.append(rp.raw_packet)

        return ordered_raw_packets, ordered_packets, total_jpeg_bytes

    def packet_to_fsk_wave(self, raw_pkt: bytes) -> np.ndarray:
        """1つのパケットを4-FSK音声波形に変換 (NumPyベクトル演算)"""
        sample_rate = self.cfg.sample_rate
        sps = max(1, int(sample_rate * self.cfg.ms_symbol / 1000))
        samples_sync = max(1, int(sample_rate * self.cfg.ms_sync / 1000))

        # 同期信号 (1000Hz, 20ms)
        t_sync = np.arange(samples_sync) / sample_rate
        sync_wave = np.sin(2 * np.pi * self.cfg.freq_sync * t_sync).astype(np.float32)

        raw_arr = np.frombuffer(raw_pkt, dtype=np.uint8)
        s0 = (raw_arr >> 6) & 0b11
        s1 = (raw_arr >> 4) & 0b11
        s2 = (raw_arr >> 2) & 0b11
        s3 = raw_arr & 0b11
        symbols = np.stack([s0, s1, s2, s3], axis=1).reshape(-1)

        freqs = self.freq_lut_arr[symbols]
        sample_freqs = np.repeat(freqs, sps)

        phase_increments = (2.0 * np.pi * sample_freqs / sample_rate)
        cum_phase = np.cumsum(phase_increments) + self.current_phase
        sym_wave = np.sin(cum_phase).astype(np.float32)

        self.current_phase = float(cum_phase[-1] % (2.0 * np.pi))
        return np.concatenate([sync_wave, sym_wave])

    def modulate_4fsk(self, binary_packets: List[bytes]) -> np.ndarray:
        """全バイナリパケットをNumPyベクトル演算で一括4-FSK変調"""
        packet_waves = [self.packet_to_fsk_wave(pkt) for pkt in binary_packets]
        full_wave = np.concatenate(packet_waves)
        max_val = np.max(np.abs(full_wave))
        if max_val > 0:
            full_wave /= max_val
        return full_wave


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, 'reconfigure', None)
        if callable(reconf):
            try:
                reconf(encoding='utf-8', errors='replace')
            except Exception:
                pass

    print("[RaptorRSEncoder] 単体実行テスト")
    encoder = RaptorRSEncoder(RAPTOR_CFG)
    test_img = np.zeros((RAPTOR_CFG.height, RAPTOR_CFG.width, 3), dtype=np.uint8)
    test_img[:] = [100, 150, 200]
    test_path = os.path.join(RAPTOR_CFG.output_dir_raptor, "temp_encoder_test.png")
    os.makedirs(os.path.dirname(test_path), exist_ok=True)
    Image.fromarray(test_img).save(test_path)

    tile_jpegs, _ = encoder.build_tile_jpegs(test_path)
    raw_pkts, pkt_objs, total_bytes = encoder.packetize(tile_jpegs)
    data_pkts = [p for p in pkt_objs if p.pkt_type == RAPTOR_CFG.TYPE_DATA]
    rep_pkts = [p for p in pkt_objs if p.pkt_type == RAPTOR_CFG.TYPE_REPAIR]

    print(f"  ・生成タイル数: {len(tile_jpegs)} 個")
    print(f"  ・データパケット数: {len(data_pkts)} 個 (ヘッダ省略1タイル1パケット化: {'有効' if RAPTOR_CFG.enable_header_stripping else '無効'})")
    print(f"  ・リペアパケット数: {len(rep_pkts)} 個")
    print(f"  ・総パケット数: {len(raw_pkts)} 個 (各255B固定長)")
    print("[OK] エンコーダ単体テスト正常完了！")
