"""SSTV Turbo FEC (Raptor + RS) Webプラットフォーム統合設定 (config_turbo.py)
【sstv-png-stand SystemFactory 互換】
管理者ダッシュボード、API、デコーダ、アグリゲータが共通参照する設定定数とパラメータ群。
"""

import math
import os

# ========================================================
# ⚙️ 自由調整パラメータエリア (タイルサイズや速度変更用)
# ========================================================
# --- 画像・分割設定 ---
WIDTH = 256
HEIGHT = 256

# タイルサイズ (16x16, 256タイル)
TILE_SIZE = 16
TILE_COUNT_X = math.ceil(WIDTH / TILE_SIZE)
TILE_COUNT_Y = math.ceil(HEIGHT / TILE_SIZE)
TOTAL_TILES = TILE_COUNT_X * TILE_COUNT_Y

# --- JPEG圧縮設定 (Raptor+RS 最適化) ---
JPEG_QUALITY = 80          # パケット実データ枠(211B)に全256タイルが1パケットで収まる最高品質設定
JPEG_RESTART_MARKER = 1   # 1 MCUごとに同期復帰マーカーを挿入し、ハフマン同期崩壊を防止
JPEG_SUBSAMPLING = 0      # 0: 4:4:4 高画質非間引き

# --- 音響速度・変調設定 (JPEGモードと完全一致) ---
SAMPLE_RATE = 44100
MS_SYNC = 20              # 同期信号時間 (20ms 高速同期)
MS_SYMBOL = 2             # 1シンボル＝2ミリ秒 (1000 bps, JPEGモードと完全統一)

# ========================================================
# 📡 変調・ヘッダ仕様 (sstv-png-stand 互換)
# ========================================================
BIT_IMAGE_CRC = 16        # 画像全体ID (16bit)
BIT_TILE_X = 8            # タイルX座標 (8bit, リペア時は 0xFF)
BIT_TILE_Y = 8            # タイルY座標 (8bit, リペア時は 0xFF)
BIT_PAYLOAD_LENGTH = 16   # ペイロードデータ長 (16bit)
BIT_HEADER_CRC = 16       # ヘッダーCRC (16bit)

# --- 周波数マップ (4-FSK 直交周波数: JPEGモードと完全同一) ---
FREQ_SYNC = 1000
FREQ_MAP = {0b00: 8000, 0b01: 6000, 0b10: 4000, 0b11: 2000}
TARGET_FREQS = [8000, 6000, 4000, 2000]


# --- 有効通信帯域 ---
VALID_BAND_MIN = 500
VALID_BAND_MAX = 9000
BANDPASS_ENABLE = True    # 受信時のノイズフィルタを有効にするか

# ========================================================
# 🛡️ Raptor ＋ RS 二重冗長 FEC 特有パラメータ
# ========================================================
RS_NSYM = 32                      # RS(255, 223) パリティバイト数 (最大16B自動誤り訂正、イレイジャ時最大32B)
RS_TOTAL_SIZE = 255               # パケット総サイズ (255B固定長)
RS_PAYLOAD_SIZE = 216             # RSペイロード長 (タグ5B + 実データ211B)
TAG_SIZE = 5                      # 自己完結タグ長 (tile_id, seq, num_pkts, data_len)
RAPTOR_REPAIR_COUNT = 64          # 万能ラプター消去リペアパケット数 (R=64, 20%欠落でも100%全再生)
USE_GLOBAL_MDS = True             # 大域 Cauchy MDS (特異点ゼロ・ブロック分割撤廃)
MAX_SUBBLOCK_SIZE = 0             # 0: 分割なし (大域MDS)
RAPTOR_SEED = 42
ENABLE_HEADER_STRIPPING = True    # 共通固定ヘッダ送信省略による1タイル1パケット化
ENABLE_SANDWICH_INTERLEAVE = True # 均等サンドイッチシャッフル (即時描画と穴埋め両立)
ENABLE_BIT_VOTING = True          # 複数局ビット多数決

# パケットタイプ定数
TYPE_DATA = 0x00
TYPE_REPAIR = 0x01

# ========================================================
# 📁 ファイル・パス設定 (FEC専用ディレクトリへ統一)
# ========================================================
TEXT_LOG_DIR = "data/digital_turbo_fec/logs"
TEXT_LOG_PREFIX = "turbo_fec_bitstream"
IMAGE_OUT_DIR = "data/digital_turbo_fec/images"
IMAGE_OUT_NAME = "decoded_result_fec.jpg"
INPUT_IMAGE = "data/input/test.jpg"
OUTPUT_WAV = "data/digital_turbo_fec/audio/turbo_fec_256_256.wav"

# --- テスト用ノイズ設定 ---
NOISE_LEVEL = 0.0


def update_tile_size(new_size: int):
    """タイルサイズを動的に変更し、関連カウンタを再計算"""
    global TILE_SIZE, TILE_COUNT_X, TILE_COUNT_Y, TOTAL_TILES
    TILE_SIZE = new_size
    TILE_COUNT_X = math.ceil(WIDTH / TILE_SIZE)
    TILE_COUNT_Y = math.ceil(HEIGHT / TILE_SIZE)
    TOTAL_TILES = TILE_COUNT_X * TILE_COUNT_Y


def update_symbol_speed(new_ms_symbol: int, new_ms_sync: int = 20):
    """通信シンボル速度を動的に変更"""
    global MS_SYMBOL, MS_SYNC
    MS_SYMBOL = new_ms_symbol
    MS_SYNC = new_ms_sync
