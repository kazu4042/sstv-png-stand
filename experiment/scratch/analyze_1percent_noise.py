import os
import sys
import io
import numpy as np
from PIL import Image
from typing import Any

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from experiment.bit_flipper import JPEGTextBitFlipper
from digital_turbo_jpeg.aggregator_turbo import TurboJPEGAggregator

def analyze_1pct():
    img_path = os.path.join(ROOT_DIR, "data", "input", "test.jpg")
    img = Image.open(img_path).convert('RGB').resize((256, 256))
    img_arr = np.array(img)

    flipper = JPEGTextBitFlipper(tile_size=16, quality=75)
    gt_packets = flipper.image_to_ground_truth_packets(img_arr)
    aggregator = TurboJPEGAggregator()

    print(f"=== 1% 反転ノイズの徹底画像・ビット解析 ===")
    total_pkts = len(gt_packets)
    total_bits = sum(len(p["payload_bits"]) for p in gt_packets)
    print(f"全パケット数: {total_pkts}, 総ビット数: {total_bits:,}")

    # 1. 各パケットのサイズ分布
    sizes = [len(p["payload_bits"]) for p in gt_packets]
    print(f"パケットサイズ: 最小={min(sizes)} bits, 最大={max(sizes)} bits, 平均={np.mean(sizes):.1f} bits")

    # 2. 1% ランダム反転を注入
    sets = flipper.generate_n_divergent_sets(
        gt_packets,
        num_sets=1,
        error_rates=[1.0],
        mode="random",
        base_seed=42
    )

    pkts_1pct, actual_ber = sets["A"]
    print(f"実効BER: {actual_ber * 100:.3f}%")

    # 3. パケットごとの反転ビット数
    flips_per_pkt = [p["flipped_count"] for p in pkts_1pct]
    zero_flips = sum(1 for c in flips_per_pkt if c == 0)
    print(f"無傷パケット数: {zero_flips} / {total_pkts} ({zero_flips/total_pkts*100:.1f}%)")
    print(f"パケットあたりの反転ビット数: 最小={min(flips_per_pkt)}, 最大={max(flips_per_pkt)}, 平均={np.mean(flips_per_pkt):.1f}")

    # 4. 各タイルを decode_tile_bytes_safely および resolve_tile でデコード
    stage_counts = {}
    valid_pixels_list: list[float] = []
    broken_tiles: list[tuple[int, Any, Any, Any, float, Any]] = []

    for i, p in enumerate(pkts_1pct):
        plen = p["payload_length"]
        bits_str = "".join(str(b) for b in p["payload_bits"])
        tile_img, final_bytes, stage = aggregator.resolve_tile([(bits_str, p["snr_val"])], plen, 16, 16)
        stage_counts[stage] = stage_counts.get(stage, 0) + 1

        if tile_img is not None:
            arr = np.array(tile_img)
            # 有効画素率 (黒落ちしていない割合)
            valid_ratio = float(np.mean(np.sum(arr, axis=2) > 3.0))
            valid_pixels_list.append(valid_ratio)
            if valid_ratio < 0.85:
                broken_tiles.append((i, p["tile_x"], p["tile_y"], stage or "UNKNOWN", valid_ratio, p["flipped_count"]))
        else:
            valid_pixels_list.append(0.0)
            broken_tiles.append((i, p["tile_x"], p["tile_y"], "FAILED", 0.0, p["flipped_count"]))

    print(f"\nタイル復元ステージ内訳: {stage_counts}")
    print(f"平均有効画素率: {np.mean(valid_pixels_list)*100:.1f}%")
    print(f"深刻破損タイル数 (有効画素 < 85%): {len(broken_tiles)} / {total_pkts}")

    print("\n破損タイルのサンプル (先頭5個):")
    for b in broken_tiles[:5]:
        print(f"  Pkt {b[0]} (x={b[1]}, y={b[2]}): Stage={b[3]}, 有効画素率={b[4]*100:.1f}%, 反転ビット数={b[5]}")

    # 5. SOI / DQT / DHT / SOS ヘッダ破損の調査
    header_flips = 0
    scan_flips = 0
    hdr_tmpl, sos_off = aggregator._get_jpeg_header_template(16, 16)
    print(f"\nSOSオフセット (ヘッダ長): {sos_off} bytes ({sos_off * 8} bits)")

    for p in pkts_1pct:
        idxs = p.get("flipped_indices", set())
        h_cnt = sum(1 for idx in idxs if idx < sos_off * 8)
        s_cnt = len(idxs) - h_cnt
        header_flips += h_cnt
        scan_flips += s_cnt

    print(f"全反転ビット内訳: ヘッダ領域={header_flips} bits, スキャンデータ領域={scan_flips} bits")

if __name__ == "__main__":
    analyze_1pct()
