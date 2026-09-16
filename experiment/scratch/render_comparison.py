import os
import sys
import random
import numpy as np
from PIL import Image

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from experiment.bit_flipper import JPEGTextBitFlipper
from digital_turbo_jpeg.aggregator_turbo import TurboJPEGAggregator

def render_comparison_images():
    img_path = os.path.join(ROOT_DIR, "data", "input", "test.jpg")
    img = Image.open(img_path).convert('RGB').resize((256, 256))
    img_arr = np.array(img)

    flipper = JPEGTextBitFlipper(tile_size=16, quality=75)
    gt_packets = flipper.image_to_ground_truth_packets(img_arr)
    aggregator = TurboJPEGAggregator()

    out_dir = os.path.join(ROOT_DIR, "experiment", "output", "debug_images")
    os.makedirs(out_dir, exist_ok=True)

    # 1. 1% BER 全域反転
    canvas_a = np.zeros((256, 256, 3), dtype=np.uint8)
    rng_a = random.Random(42)
    for p in gt_packets:
        bits = list(p["payload_bits"])
        for i in range(len(bits)):
            if rng_a.random() < 0.01:
                bits[i] = 1 - bits[i]
        bits_str = "".join(str(b) for b in bits)
        t_img, _, _ = aggregator.resolve_tile([(bits_str, 5.0)], p["payload_length"], 16, 16)
        if t_img:
            tx, ty = p["tile_x"], p["tile_y"]
            canvas_a[ty*16:(ty+1)*16, tx*16:(tx+1)*16] = np.array(t_img)

    Image.fromarray(canvas_a).save(os.path.join(out_dir, "pattern_A_1pct_all.png"))

    # 2. 1% BER スキャンデータのみ反転
    canvas_b = np.zeros((256, 256, 3), dtype=np.uint8)
    rng_b = random.Random(42)
    for p in gt_packets:
        bits = list(p["payload_bits"])
        raw = p["payload_bytes"]
        s_pos = raw.find(b'\xff\xda')
        s_len = (raw[s_pos+2] << 8) | raw[s_pos+3]
        sos_end = (s_pos + 2 + s_len) * 8
        for i in range(len(bits)):
            if i >= sos_end:
                if rng_b.random() < 0.01:
                    bits[i] = 1 - bits[i]
        bits_str = "".join(str(b) for b in bits)
        t_img, _, _ = aggregator.resolve_tile([(bits_str, 5.0)], p["payload_length"], 16, 16)
        if t_img:
            tx, ty = p["tile_x"], p["tile_y"]
            canvas_b[ty*16:(ty+1)*16, tx*16:(tx+1)*16] = np.array(t_img)

    Image.fromarray(canvas_b).save(os.path.join(out_dir, "pattern_B_1pct_scan_only.png"))

    print("画像保存完了:")
    print("  pattern_A_1pct_all.png (全域1%反転)")
    print("  pattern_B_1pct_scan_only.png (スキャンデータ1%反転)")

if __name__ == "__main__":
    render_comparison_images()
