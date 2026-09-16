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

def render_fine_noise_levels():
    img_path = os.path.join(ROOT_DIR, "data", "input", "test.jpg")
    img = Image.open(img_path).convert('RGB').resize((256, 256))
    img_arr = np.array(img)

    flipper = JPEGTextBitFlipper(tile_size=16, quality=75)
    gt_packets = flipper.image_to_ground_truth_packets(img_arr)
    aggregator = TurboJPEGAggregator()

    out_dir = os.path.join(ROOT_DIR, "experiment", "output", "debug_images")
    os.makedirs(out_dir, exist_ok=True)

    test_bers = [0.0001, 0.0005, 0.001, 0.002] # 0.01%, 0.05%, 0.1%, 0.2%

    for ber in test_bers:
        canvas = np.zeros((256, 256, 3), dtype=np.uint8)
        rng = random.Random(42)
        total_flips = 0
        total_bits = 0
        damaged_tiles = 0

        for p in gt_packets:
            bits = list(p["payload_bits"])
            flipped = False
            for i in range(len(bits)):
                if rng.random() < ber:
                    bits[i] = 1 - bits[i]
                    total_flips += 1
                    flipped = True
            total_bits += len(bits)
            if flipped:
                damaged_tiles += 1

            bits_str = "".join(str(b) for b in bits)
            t_img, _, _ = aggregator.resolve_tile([(bits_str, 5.0)], p["payload_length"], 16, 16)
            if t_img:
                tx, ty = p["tile_x"], p["tile_y"]
                t_resized = t_img.resize((16, 16))
                canvas[ty*16:(ty+1)*16, tx*16:(tx+1)*16] = np.array(t_resized)

        fn = f"fine_noise_{ber*100:.2f}pct.png"
        Image.fromarray(canvas).save(os.path.join(out_dir, fn))
        print(f"BER = {ber*100:.2f}%: 反転ビット数={total_flips:,}/{total_bits:,}, 破損タイル={damaged_tiles}/256 -> {fn}")

if __name__ == "__main__":
    render_fine_noise_levels()
