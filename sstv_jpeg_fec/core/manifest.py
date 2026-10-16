"""タイル目録マニフェスト (manifest.py)
マスターバイナリストリーム内における各タイルの開始・終了バイトオフセットを管理し、
受信側で「どのタイルがデコード可能か」を即座に判定できるようにするモジュール。
"""

import struct
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional


@dataclass
class TileEntry:
    tile_x: int
    tile_y: int
    offset_start: int
    offset_end: int
    byte_length: int

    @property
    def is_valid(self) -> bool:
        return self.byte_length > 0 and (self.offset_end - self.offset_start == self.byte_length)


class TileManifest:
    """タイルの座標とバイナリオフセットの対応関係を管理するクラス"""

    def __init__(self, width: int = 256, height: int = 256, tile_size: int = 16):
        self.width = width
        self.height = height
        self.tile_size = tile_size
        self.entries: Dict[Tuple[int, int], TileEntry] = {}
        self.total_payload_bytes = 0

    def add_tile(self, tx: int, ty: int, jpeg_bytes: bytes) -> Tuple[int, int]:
        """タイルデータを追加し、そのオフセット範囲を返す"""
        start = self.total_payload_bytes
        length = len(jpeg_bytes)
        end = start + length
        entry = TileEntry(tile_x=tx, tile_y=ty, offset_start=start, offset_end=end, byte_length=length)
        self.entries[(tx, ty)] = entry
        self.total_payload_bytes = end
        return start, end

    def get_tile_range(self, tx: int, ty: int) -> Optional[Tuple[int, int]]:
        entry = self.entries.get((tx, ty))
        if entry:
            return entry.offset_start, entry.offset_end
        return None

    def serialize_header(self) -> bytes:
        """
        マニフェスト情報をコンパクトなバイナリヘッダとしてエンコード。
        フォーマット:
          - Magic: b"TM01" (4B)
          - Total Tiles: uint16 (2B)
          - 各タイルの長さ: uint16 (2B) × total_tiles
        256タイルの場合、わずか 4 + 2 + 512 = 518 バイト！
        """
        tile_count_x = (self.width + self.tile_size - 1) // self.tile_size
        tile_count_y = (self.height + self.tile_size - 1) // self.tile_size
        total = tile_count_x * tile_count_y

        header = bytearray(b"TM01")
        header.extend(struct.pack(">H", total))

        for ty in range(tile_count_y):
            for tx in range(tile_count_x):
                entry = self.entries.get((tx, ty))
                length = entry.byte_length if entry else 0
                header.extend(struct.pack(">H", length))

        return bytes(header)

    @classmethod
    def deserialize_header(cls, header_bytes: bytes, width: int = 256, height: int = 256, tile_size: int = 16) -> "TileManifest":
        """バイナリヘッダからマニフェストを復元"""
        if len(header_bytes) < 6 or header_bytes[:4] != b"TM01":
            raise ValueError("不正なマニフェストヘッダです")

        total = struct.unpack(">H", header_bytes[4:6])[0]
        manifest = cls(width=width, height=height, tile_size=tile_size)

        tile_count_x = (width + tile_size - 1) // tile_size
        tile_count_y = (height + tile_size - 1) // tile_size

        idx = 6
        current_offset = 0
        for ty in range(tile_count_y):
            for tx in range(tile_count_x):
                if idx + 2 > len(header_bytes):
                    break
                length = struct.unpack(">H", header_bytes[idx:idx+2])[0]
                idx += 2
                start = current_offset
                end = start + length
                current_offset = end
                manifest.entries[(tx, ty)] = TileEntry(
                    tile_x=tx, tile_y=ty, offset_start=start, offset_end=end, byte_length=length
                )

        manifest.total_payload_bytes = current_offset
        return manifest

    def check_tile_ready(self, tx: int, ty: int, received_mask: List[bool]) -> bool:
        """
        受信バッファのビット/バイトマスクから、指定タイルがデコード可能か判定。
        [offset_start, offset_end) の全バイトが揃っていれば True
        """
        entry = self.entries.get((tx, ty))
        if not entry or entry.byte_length == 0:
            return False
        if len(received_mask) < entry.offset_end:
            return False
        return all(received_mask[entry.offset_start:entry.offset_end])
