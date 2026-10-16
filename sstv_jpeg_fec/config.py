"""SSTV JPEG FEC システム設定ファイル (config.py)
固定長パケット化・消失訂正(FEC)・即時タイル描画を実現する新アーキテクチャの設定。
後々の実験や拡張でパラメータを自由に変更できるよう、モジュール化および動的更新関数を完備。
"""

import math
import os
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class SSTVConfig:
    # ========================================================
    # 1. 画像・タイル分割設定
    # ========================================================
    width: int = 256
    height: int = 256
    tile_size: int = 16  # 16, 32, 64 等に変更可能

    # ========================================================
    # 2. JPEG圧縮設定
    # ========================================================
    jpeg_quality: int = 75       # 0〜100
    jpeg_restart_marker: int = 1  # 1 MCUごとに同期復帰マーカー挿入 (耐ノイズ)
    jpeg_subsampling: int = 0    # 4:4:4 高画質サンプリング

    # ========================================================
    # 3. 固定長パケット & FEC (消失訂正パリティ) 設定 ★超重要★
    # ========================================================
    # 1パケットあたりの固定データ長 (バイト数: 128, 256, 512 等に変更可能)
    payload_size: int = 256

    # 何パケットごとに1つの冗長パケット(R)を作るか (K値)
    # 例: 4なら 4+1方式 (冗長率20%), 8なら 8+1方式 (冗長率11.1%)
    fec_group_size: int = 4

    # 系統的ファウンテン符号（噴水パケット）を付与するかどうか
    enable_fountain_packets: bool = True
    fountain_packet_count: int = 10  # 予備の噴水パケット数

    # ========================================================
    # 4. 音響変調 (4-FSK) 設定
    # ========================================================
    sample_rate: int = 44100
    ms_sync: int = 20    # 同期信号時間 (ミリ秒)
    ms_symbol: int = 2   # 1シンボル長 (2ミリ秒 = 1000 bps)

    freq_sync: int = 1000
    freq_map: Dict[int, int] = field(default_factory=lambda: {
        0b00: 8000,
        0b01: 6000,
        0b10: 4000,
        0b11: 2000
    })

    # ========================================================
    # 5. ディレクトリ・パス設定
    # ========================================================
    project_root: str = field(default_factory=lambda: os.path.dirname(os.path.abspath(__file__)))

    @property
    def tile_count_x(self) -> int:
        return math.ceil(self.width / self.tile_size)

    @property
    def tile_count_y(self) -> int:
        return math.ceil(self.height / self.tile_size)

    @property
    def total_tiles(self) -> int:
        return self.tile_count_x * self.tile_count_y

    @property
    def data_dir(self) -> str:
        return os.path.join(self.project_root, "data")

    @property
    def input_dir(self) -> str:
        return os.path.join(self.data_dir, "input")

    @property
    def output_dir(self) -> str:
        return os.path.join(self.data_dir, "output")


# グローバル共有インスタンス
CFG = SSTVConfig()


# ========================================================
# 🔧 パラメータ動的更新関数 (後から自由に呼び出し変更可能)
# ========================================================
def update_tile_size(new_tile_size: int):
    """タイルサイズを変更 (16, 32, 64 等)"""
    CFG.tile_size = new_tile_size


def update_payload_size(new_payload_size: int):
    """固定長パケットのバイト長を変更 (128, 256, 512 等)"""
    CFG.payload_size = new_payload_size


def update_fec_ratio(new_group_size: int, enable_fountain: bool = None, fountain_count: int = None):
    """パリティ間隔を変更 (例: 4なら4+1方式、8なら8+1方式)"""
    CFG.fec_group_size = new_group_size
    if enable_fountain is not None:
        CFG.enable_fountain_packets = enable_fountain
    if fountain_count is not None:
        CFG.fountain_packet_count = fountain_count


def update_speed(new_ms_symbol: int, new_ms_sync: int = 20):
    """通信速度を変更 (1シンボルのミリ秒数)"""
    CFG.ms_symbol = new_ms_symbol
    CFG.ms_sync = new_ms_sync


def ensure_directories():
    """入出力ディレクトリの作成"""
    os.makedirs(CFG.input_dir, exist_ok=True)
    os.makedirs(CFG.output_dir, exist_ok=True)
