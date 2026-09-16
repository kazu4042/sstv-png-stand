import os
import sys
import io
import random
import numpy as np
from PIL import Image

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from experiment.bit_flipper import JPEGTextBitFlipper
from digital_turbo_jpeg.aggregator_turbo import TurboJPEGAggregator

def test_noise_levels():
    img_path = os.path.join(ROOT_DIR, "data", "input", "test.jpg")
    img = Image.open(img_path).convert('RGB').resize((256, 256))
    img_arr = np.array(img)

    flipper = JPEGTextBitFlipper(tile_size=16, quality=75)
    gt_packets = flipper.image_to_ground_truth_packets(img_arr)
    aggregator = TurboJPEGAggregator()

    print("=== 各種ノイズ付加方式における画像破壊度の比較検証 ===")

    # 1. 1つのパケットでヘッダ位置を特定
    sample_pkt = gt_packets[0]
    raw_bytes = sample_pkt["payload_bytes"]
    sos_pos = raw_bytes.find(b'\xff\xda')
    sos_len = (raw_bytes[sos_pos+2] << 8) | raw_bytes[sos_pos+3]
    header_end_byte = sos_pos + 2 + sos_len
    header_end_bit = header_end_byte * 8
    print(f"サンプルパケット: 総バイト数={len(raw_bytes)}, SOSヘッダ終端={header_end_byte} bytes ({header_end_bit} bits)")

    # 2. 様々なビット誤り率での挙動テスト
    test_bers = [0.0005, 0.001, 0.005, 0.01, 0.05] # 0.05%, 0.1%, 0.5%, 1.0%, 5.0%

    for ber in test_bers:
        print(f"\n--- [BER = {ber*100:.2f}%] ---")

        # パターンA: ヘッダ含む全ビット一様反転
        rng_a = random.Random(42)
        total_flips_a = 0
        total_bits_a = 0
        tiles_ok_a = 0

        for p in gt_packets:
            bits = list(p["payload_bits"])
            for i in range(len(bits)):
                if rng_a.random() < ber:
                    bits[i] = 1 - bits[i]
                    total_flips_a += 1
            total_bits_a += len(bits)

            # デコード試行
            bits_str = "".join(str(b) for b in bits)
            t_img, _, stage = aggregator.resolve_tile([(bits_str, 10.0)], p["payload_length"], 16, 16)
            if t_img is not None:
                arr = np.array(t_img)
                if np.mean(np.sum(arr, axis=2) > 3.0) > 0.85:
                    tiles_ok_a += 1

        print(f"  パターンA (ヘッダ含む全域): 実効BER={total_flips_a/total_bits_a*100:.2f}%, 有効タイル={tiles_ok_a}/256 ({tiles_ok_a/256*100:.1f}%)")

        # パターンB: ヘッダ保護 (スキャンデータのみ反転)
        rng_b = random.Random(42)
        total_flips_b = 0
        total_bits_b = 0
        tiles_ok_b = 0

        for p in gt_packets:
            bits = list(p["payload_bits"])
            # パケット固有のSOS位置を探索
            p_bytes = p["payload_bytes"]
            s_pos = p_bytes.find(b'\xff\xda')
            s_len = (p_bytes[s_pos+2] << 8) | p_bytes[s_pos+3]
            scan_start_bit = (s_pos + 2 + s_len) * 8

            for i in range(len(bits)):
                if i >= scan_start_bit:  # スキャンデータのみ反転
                    if rng_b.random() < ber:
                        bits[i] = 1 - bits[i]
                        total_flips_b += 1
                total_bits_b += 1

            bits_str = "".join(str(b) for b in bits)
            t_img, _, stage = aggregator.resolve_tile([(bits_str, 10.0)], p["payload_length"], 16, 16)
            if t_img is not None:
                arr = np.array(t_img)
                if np.mean(np.sum(arr, axis=2) > 3.0) > 0.85:
                    tiles_ok_b += 1

        print(f"  パターンB (スキャンデータのみ): 実効BER={total_flips_b/total_bits_b*100:.2f}%, 有効タイル={tiles_ok_b}/256 ({tiles_ok_b/256*100:.1f}%)")

if __name__ == "__main__":
    test_noise_levels()
