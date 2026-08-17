import math
import os

# ========================================================
# ⚙️ 自由調整パラメータエリア (タイルサイズや速度変更用)
# ========================================================
# --- 画像・分割設定 ---
WIDTH = 256
HEIGHT = 256

# ★ タイルサイズ（自由に 16, 32, 64, 128, 256 等に変更可能！）
TILE_SIZE = 16
TILE_COUNT_X = math.ceil(WIDTH / TILE_SIZE)
TILE_COUNT_Y = math.ceil(HEIGHT / TILE_SIZE)

# --- PNG圧縮設定 ---
PNG_COMPRESS = 6  # PNG圧縮レベル (0=無圧縮, 9=最高圧縮, 6=バランス)

# --- 音響速度・最速設定 ---
SAMPLE_RATE = 44100
MS_SYNC = 20        # 同期信号時間を 100ms → 20ms へ劇的短縮
MS_SYMBOL = 2       # 1シンボル＝2ミリ秒 (2000 bps)

# ========================================================
# 📡 変調・ヘッダ仕様
# ========================================================
BIT_IMAGE_CRC = 16      # 画像全体ID
BIT_TILE_X = 8          # タイルX座標
BIT_TILE_Y = 8          # タイルY座標
BIT_PAYLOAD_LENGTH = 16 # PNGデータ長 (最大65,535バイト)
BIT_HEADER_CRC = 16     # ヘッダーCRCを16bit化

# --- 周波数マップ (4-FSK 直交周波数) ---
FREQ_SYNC = 1000
FREQ_MAP = {0b00: 8000, 0b01: 6000, 0b10: 4000, 0b11: 2000}
TARGET_FREQS = [8000, 6000, 4000, 2000]

# --- 有効通信帯域 ---
VALID_BAND_MIN = 500
VALID_BAND_MAX = 9000

# --- ファイル・パス設定 ---
TEXT_LOG_DIR = "data/logs"
TEXT_LOG_PREFIX = "turbo_png_bitstream"
IMAGE_OUT_DIR = "data/images"
IMAGE_OUT_NAME = "decoded_result_turbo.png"
INPUT_IMAGE = "data/input/test2.jpeg"
OUTPUT_WAV = "data/audio/turbo_png_256_256.wav"

# --- テスト用ノイズ設定 ---
NOISE_LEVEL = 0.0

def update_tile_size(new_size):
    global TILE_SIZE, TILE_COUNT_X, TILE_COUNT_Y
    TILE_SIZE = new_size
    TILE_COUNT_X = math.ceil(WIDTH / TILE_SIZE)
    TILE_COUNT_Y = math.ceil(HEIGHT / TILE_SIZE)

def update_symbol_speed(new_ms_symbol, new_ms_sync=20):
    global MS_SYMBOL, MS_SYNC
    MS_SYMBOL = new_ms_symbol
    MS_SYNC = new_ms_sync
