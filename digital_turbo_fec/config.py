"""SSTV 二重冗長通信システム 統合設定ファイル (digital_turbo_fec/config.py)
【Single Source of Truth】
画素数・解像度、タイルサイズ、JPEG圧縮品質、RSパリティ、4-FSK変調、ラプターパケット数、
伝送路シミュレーション、入出力パスに至るすべてのシステム設定をこのファイル単体で管理・変更可能です。
"""

import math
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_CURRENT_DIR, "../"))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


@dataclass
class SystemConfig:
    """SSTV通信システム 全パラメータ統合設定クラス"""

    # 1. 画像設定 ＆ タイル分割設定
    width: int = 256
    height: int = 256
    tile_size: int = 16

    # 2. JPEG圧縮設定
    jpeg_quality: int = 80
    jpeg_restart_marker: int = 1
    jpeg_subsampling: int = 0

    # 3. 通信パケット構造 ＆ RS符号設定
    enable_header_stripping: bool = True
    TYPE_DATA: int = 0x00
    TYPE_REPAIR: int = 0x01
    rs_header_size: int = 7
    tag_size: int = 5
    rs_payload_size: int = 216
    rs_nsym: int = 32
    magic_size: int = 0
    magic_bytes: bytes = b""

    # 4. ラプター消去訂正符号
    raptor_repair_count: int = 64
    use_global_mds: bool = True
    max_subblock_size: int = 0
    raptor_seed: int = 42
    enable_sandwich_interleave: bool = True
    enable_tile_clustered_shuffle: bool = True
    tile_shuffle_seed: int = 42
    enable_fountain_packets: bool = False
    fountain_packet_count: int = 0
    fec_group_size: int = 0
    enable_bit_voting: bool = True

    # 5. 音響変調 (4-FSK) ＆ 同期信号設定
    sample_rate: int = 44100
    ms_symbol: float = 2.0
    freq_sync: int = 1000
    ms_sync: int = 20
    sync_subdivisions: int = 20
    enable_doppler_compensation: bool = True

    freq_map: Dict[int, int] = field(default_factory=lambda: {
        0b00: 1000,
        0b01: 1400,
        0b10: 1800,
        0b11: 2200
    })

    # 6. 地上局伝送路シミュレーション設定
    station_drop_rates: Dict[str, float] = field(default_factory=lambda: {
        "Station_A": 0.20,
        "Station_B": 0.20,
        "Station_C": 0.20
    })

    station_snr_db: Dict[str, float] = field(default_factory=lambda: {
        "Station_A": 13.0,
        "Station_B": 11.5,
        "Station_C": 12.0
    })

    # 7. ディレクトリ・ファイルパス設定
    project_root: str = field(default_factory=lambda: _PROJECT_ROOT)
    header_size: int = 13
    payload_size: int = 256
    num_data_packets: int = 868
    num_fec_groups: int = 217

    @property
    def data_dir(self) -> str:
        return os.path.join(self.project_root, "data", "digital_turbo_fec")

    @property
    def input_dir(self) -> str:
        return os.path.join(self.project_root, "data", "input")

    @property
    def output_dir(self) -> str:
        return os.path.join(self.project_root, "data", "output")

    @property
    def output_dir_raptor(self) -> str:
        return os.path.join(self.data_dir)

    @property
    def output_dir_rs(self) -> str:
        return os.path.join(self.data_dir, "rs")

    # 8. 自動計算プロパティ
    @property
    def tile_count_x(self) -> int:
        return self.width // self.tile_size

    @property
    def tile_count_y(self) -> int:
        return self.height // self.tile_size

    @property
    def total_tiles(self) -> int:
        return self.tile_count_x * self.tile_count_y

    @property
    def rs_k(self) -> int:
        return self.rs_header_size + self.rs_payload_size

    @property
    def rs_n(self) -> int:
        return self.rs_k + self.rs_nsym

    @property
    def rs_protected_size(self) -> int:
        return self.rs_n

    @property
    def packet_total_size(self) -> int:
        return self.magic_size + self.rs_n

    @property
    def rs_data_chunk_size(self) -> int:
        return self.rs_payload_size - self.tag_size

    @property
    def rs_max_correctable_bytes(self) -> int:
        return self.rs_nsym // 2

    @property
    def symbol_size(self) -> int:
        return self.rs_payload_size

    @property
    def expected_data_packets(self) -> int:
        if self.enable_header_stripping:
            return self.total_tiles
        return self.total_tiles * 4

    @property
    def expected_total_packets(self) -> int:
        return self.expected_data_packets + self.raptor_repair_count

    @property
    def expected_transmission_seconds(self) -> float:
        pkt_duration = (self.packet_total_size * 4 * self.ms_symbol + self.ms_sync) / 1000.0
        return self.expected_total_packets * pkt_duration

    @property
    def nominal_1ms_samples(self) -> float:
        return self.sample_rate / 1000.0


# グローバル共有インスタンス
CONFIG = SystemConfig()
RAPTOR_CFG = CONFIG
RS_CFG = CONFIG
CFG = CONFIG
RaptorConfig = SystemConfig
RSConfig = SystemConfig
SSTVConfig = SystemConfig


def update_config(
    width: Optional[int] = None,
    height: Optional[int] = None,
    tile_size: Optional[int] = None,
    jpeg_quality: Optional[int] = None,
    jpeg_restart_marker: Optional[int] = None,
    repair_count: Optional[int] = None,
    use_global_mds: Optional[bool] = None,
    enable_sandwich: Optional[bool] = None,
    enable_header_stripping: Optional[bool] = None,
    rs_nsym: Optional[int] = None,
    seed: Optional[int] = None,
    sample_rate: Optional[int] = None,
    ms_symbol: Optional[float] = None
):
    if width is not None:
        CONFIG.width = width
    if height is not None:
        CONFIG.height = height
    if tile_size is not None:
        CONFIG.tile_size = tile_size
    if jpeg_quality is not None:
        CONFIG.jpeg_quality = jpeg_quality
    if jpeg_restart_marker is not None:
        CONFIG.jpeg_restart_marker = jpeg_restart_marker
    if repair_count is not None:
        CONFIG.raptor_repair_count = repair_count
    if use_global_mds is not None:
        CONFIG.use_global_mds = use_global_mds
    if enable_sandwich is not None:
        CONFIG.enable_sandwich_interleave = enable_sandwich
    if enable_header_stripping is not None:
        CONFIG.enable_header_stripping = enable_header_stripping
    if rs_nsym is not None:
        CONFIG.rs_nsym = rs_nsym
    if seed is not None:
        CONFIG.raptor_seed = seed
        CONFIG.tile_shuffle_seed = seed
    if sample_rate is not None:
        CONFIG.sample_rate = sample_rate
    if ms_symbol is not None:
        CONFIG.ms_symbol = ms_symbol


update_raptor_params = update_config
update_rs_params = update_config
