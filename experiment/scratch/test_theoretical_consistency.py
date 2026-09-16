import os
import sys
import numpy as np

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from experiment.bit_flipper import JPEGTextBitFlipper
from experiment.plot_majority_recovery import MajorityRecoveryEvaluator

def test_all_modes():
    print("=================================================================")
    print("理論的整合性・数理モデル厳密検証テスト")
    print("=================================================================")
    
    # 256x256 テスト画像
    img_arr = np.full((256, 256, 3), 128, dtype=np.uint8)
    for y in range(256):
        for x in range(256):
            img_arr[y, x] = [x, y, (x * 2 + y * 2) % 256]

    flipper = JPEGTextBitFlipper(tile_size=16, quality=75)
    gt_packets = flipper.image_to_ground_truth_packets(img_arr)
    total_bits = sum(len(p["payload_bits"]) for p in gt_packets)
    print(f"[*] 正解パケット数: {len(gt_packets)}, 総ペイロードビット数: {total_bits:,} bits")

    target_rates = [25.0, 15.0, 10.0]
    evaluator = MajorityRecoveryEvaluator(tile_size=16)

    modes = ["random", "8000_to_4000", "packet_burst"]

    for mode in modes:
        print(f"\n==================== [検証モード: {mode}] ====================")
        sets = flipper.generate_n_divergent_sets(
            gt_packets,
            num_sets=3,
            error_rates=target_rates,
            mode=mode,
            base_seed=42
        )

        for name, (pkts, ber) in sets.items():
            idx = ord(name) - 65
            expected = target_rates[idx]
            actual = ber * 100.0
            print(f"  データセット [{name}]: 目標反転率={expected:5.1f}% | 実効BER={actual:5.2f}%")
            if mode == "random":
                diff = abs(expected - actual)
                assert diff < 0.2, f"random モードの実効BERが目標と一致していません (diff={diff})"

        progression = evaluator.evaluate_progression(gt_packets, img_arr, sets)
        print("  多数決による復元推移:")
        prev_tile_rate = -1.0
        prev_bit_acc = -1.0
        for d in progression:
            stage_str = ", ".join(f"{k}:{v}" for k, v in d["stage_counts"].items())
            print(f"    Stage {d['count']} [{d['label']:11s}]: TileRec={d['tile_rate']:5.1f}% | BitAcc={d['bit_acc']:6.2f}% | PSNR={d['psnr']:5.1f}dB | ({stage_str})")
            # 多数決で音声数が増えるほどビット正解率・復元率は維持または向上すること
            assert d["tile_rate"] >= prev_tile_rate - 1e-5, "音声数増加に伴いタイル復元率が低下しています"
            assert d["bit_acc"] >= prev_bit_acc - 1e-5, "音声数増加に伴いビット正解率が低下しています"
            prev_tile_rate = d["tile_rate"]
            prev_bit_acc = d["bit_acc"]

        print(f"  ✓ {mode} モードの検証成功！")

    print("\n=================================================================")
    print(">> すべての理論的整合性・数理テストに合格しました！")
    print("=================================================================")

if __name__ == "__main__":
    test_all_modes()
