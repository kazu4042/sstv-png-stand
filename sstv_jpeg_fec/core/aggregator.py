"""SSTV JPEG FEC アグリゲータ (aggregator.py)
ランダムに届く固定長パケット・冗長パケットから、
1. 消失パケットのXOR逆算復元 (FEC)
2. バイト揃い判定によるタイルの即時JPEGデコード
3. キャンバスへの逐次プログレッシブ描画
を実行するコア復元エンジン。
"""

import io
import struct
import numpy as np
from PIL import Image, ImageFile
from typing import Dict, List, Set, Tuple, Optional

# 破損・途切れJPEGも安全に読み込める設定
setattr(ImageFile, 'LOAD_TRUNCATED_IMAGES', True)

from sstv_jpeg_fec.config import CFG
from sstv_jpeg_fec.core.manifest import TileManifest
from sstv_jpeg_fec.core.fec_engine import FECEngine, FECPacket


class SSTVJPEGFAggregator:
    """即時タイル描画 ＋ 消失訂正FEC 統合アグリゲータ"""

    def __init__(self, config=CFG, expected_master_length: Optional[int] = None):
        self.cfg = config
        self.fec_engine = FECEngine(
            payload_size=self.cfg.payload_size,
            group_size=self.cfg.fec_group_size
        )
        self.expected_master_length = expected_master_length

        # キャンバス (RGB)
        self.canvas = np.zeros((self.cfg.height, self.cfg.width, 3), dtype=np.uint8)
        self.rendered_tiles: Set[Tuple[int, int]] = set()

        # パケット保管庫
        self.received_packets: Dict[int, FECPacket] = {}  # {packet_id: FECPacket}
        self.recovered_fec_ids: Set[int] = set()

        # バイナリストリームバッファ
        self.stream_buffer = bytearray()
        self.stream_mask: List[bool] = []
        self.manifest: Optional[TileManifest] = None

        if self.expected_master_length:
            self._init_buffer(self.expected_master_length)

    def _init_buffer(self, length: int):
        self.stream_buffer = bytearray(b"\x00" * length)
        self.stream_mask = [False] * length

    @staticmethod
    def calculate_crc16(data: bytes, poly: int = 0x1021, init: int = 0xFFFF) -> int:
        crc = init
        for b in data:
            crc ^= (b << 8)
            for _ in range(8):
                if crc & 0x8000:
                    crc = ((crc << 1) ^ poly) & 0xFFFF
                else:
                    crc = (crc << 1) & 0xFFFF
        return crc

    def parse_raw_packet(self, raw_bytes: bytes) -> Optional[FECPacket]:
        """バイナリパケットからヘッダ検証とFECPacket展開"""
        if len(raw_bytes) < 11:
            return None
        magic, img_id, p_type, g_id, p_id = struct.unpack(">2sHBH H", raw_bytes[:9])
        if magic != b"SF":
            return None

        hdr_crc = struct.unpack(">H", raw_bytes[9:11])[0]
        calc_crc = self.calculate_crc16(raw_bytes[:9])
        if hdr_crc != calc_crc:
            return None  # ヘッダ破損

        payload = raw_bytes[11:]
        return FECPacket(packet_id=p_id, packet_type=p_type, group_id=g_id, payload=payload)

    def feed_packet(self, pkt: FECPacket) -> List[Tuple[int, int]]:
        """
        パケットを1個投入し、
        1. バッファ書き込み
        2. FEC消失逆算
        3. 新しくデコード可能になったタイルを描画
        戻り値: 今回新しく描画完了したタイルの座標リスト [(tx, ty), ...]
        """
        if pkt.packet_id in self.received_packets:
            return []  # 重複

        self.received_packets[pkt.packet_id] = pkt

        # データパケットならバッファに直接書き込み
        if pkt.is_data:
            self._write_packet_to_buffer(pkt.packet_id, pkt.payload)

        # Peeling Decoder (剥ぎ取り法) による連鎖的消失逆算を実行
        recovered_dict, new_ids = self.fec_engine.recover_with_peeling_decoder(
            list(self.received_packets.values())
        )

        for pid in new_ids:
            if pid not in self.recovered_fec_ids:
                self.recovered_fec_ids.add(pid)
                payload = recovered_dict[pid]
                recovered_pkt = FECPacket(
                    packet_id=pid,
                    packet_type=FECPacket.TYPE_DATA,
                    group_id=pid // self.cfg.fec_group_size,
                    payload=payload,
                    source_ids={pid}
                )
                self.received_packets[pid] = recovered_pkt
                self._write_packet_to_buffer(pid, payload)

        # マニフェストの解釈 (まだ未解決の場合)
        if self.manifest is None:
            self._try_load_manifest()

        # 新しく揃ったタイルを即座にデコード
        newly_rendered = self._try_render_tiles()
        return newly_rendered

    def _write_packet_to_buffer(self, pid: int, payload: bytes):
        start = pid * self.cfg.payload_size
        end = start + len(payload)

        if len(self.stream_buffer) < end:
            ext = end - len(self.stream_buffer)
            self.stream_buffer.extend(b"\x00" * ext)
            self.stream_mask.extend([False] * ext)

        self.stream_buffer[start:end] = payload
        for i in range(start, end):
            self.stream_mask[i] = True

    def _try_load_manifest(self):
        """マニフェストヘッダ領域 (先頭518バイト) が揃ったら展開"""
        tile_cnt_x = self.cfg.tile_count_x
        tile_cnt_y = self.cfg.tile_count_y
        total_tiles = tile_cnt_x * tile_cnt_y
        req_header_len = 6 + total_tiles * 2

        if len(self.stream_mask) >= req_header_len and all(self.stream_mask[:req_header_len]):
            try:
                self.manifest = TileManifest.deserialize_header(
                    bytes(self.stream_buffer[:req_header_len]),
                    width=self.cfg.width,
                    height=self.cfg.height,
                    tile_size=self.cfg.tile_size
                )
            except Exception:
                pass

    def _try_render_tiles(self) -> List[Tuple[int, int]]:
        """揃ったタイルを即座にJPEGデコードしてキャンバスに反映"""
        if self.manifest is None:
            return []

        tile_cnt_x = self.cfg.tile_count_x
        tile_cnt_y = self.cfg.tile_count_y
        newly_rendered = []

        manifest_header_len = 6 + (tile_cnt_x * tile_cnt_y) * 2

        for ty in range(tile_cnt_y):
            for tx in range(tile_cnt_x):
                if (tx, ty) in self.rendered_tiles:
                    continue

                entry = self.manifest.entries.get((tx, ty))
                if not entry or entry.byte_length == 0:
                    continue

                # マスターストリーム内での絶対オフセット
                abs_start = manifest_header_len + entry.offset_start
                abs_end = manifest_header_len + entry.offset_end

                if len(self.stream_mask) >= abs_end and all(self.stream_mask[abs_start:abs_end]):
                    # 完全に揃っている！
                    jpeg_bytes = bytes(self.stream_buffer[abs_start:abs_end])
                    try:
                        t_img = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
                        t_arr = np.array(t_img.resize((self.cfg.tile_size, self.cfg.tile_size)))

                        y0 = ty * self.cfg.tile_size
                        y1 = min(y0 + self.cfg.tile_size, self.cfg.height)
                        x0 = tx * self.cfg.tile_size
                        x1 = min(x0 + self.cfg.tile_size, self.cfg.width)

                        self.canvas[y0:y1, x0:x1] = t_arr
                        self.rendered_tiles.add((tx, ty))
                        newly_rendered.append((tx, ty))
                    except Exception:
                        pass

        return newly_rendered

    @property
    def tile_recovery_rate(self) -> float:
        return (len(self.rendered_tiles) / self.cfg.total_tiles) * 100.0

    @property
    def is_fully_recovered(self) -> bool:
        return len(self.rendered_tiles) == self.cfg.total_tiles
