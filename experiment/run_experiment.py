import os
import sys

# WindowsコンソールのUTF-8文字化け・UnicodeEncodeError防止
for stream in (sys.stdout, sys.stderr):
    reconf = getattr(stream, 'reconfigure', None)
    if callable(reconf):
        try:
            reconf(encoding='utf-8', errors='replace')
        except Exception:
            pass

import argparse
import numpy as np
from PIL import Image

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

try:
    from experiment import config_experiment as exp_cfg
except ImportError:
    from . import config_experiment as exp_cfg

from experiment.bit_flipper import JPEGTextBitFlipper
from experiment.plot_bit_errors import plot_bit_error_distribution, plot_comparison_three_audio
from experiment.plot_majority_recovery import MajorityRecoveryEvaluator


def ensure_test_image(input_path):
    """入力画像が存在しない場合、鮮やかなテスト用グラデーション画像を自動生成する"""
    if not os.path.isabs(input_path):
        input_path = os.path.join(ROOT_DIR, input_path)

    if os.path.exists(input_path):
        return input_path

    os.makedirs(os.path.dirname(os.path.abspath(input_path)), exist_ok=True)
    w, h = 256, 256
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    for y in range(h):
        for x in range(w):
            arr[y, x] = [x % 256, y % 256, (x * 2 + y * 2) % 256]
    Image.fromarray(arr).save(input_path, format="JPEG", quality=90)
    print(f"[Image] テスト用合成画像を生成しました: {input_path}")
    return input_path


def main():
    parser = argparse.ArgumentParser(
        description="JPEGテキスト段階でのビット反転・多数決効果実効性検証・グラフ化統合実験スクリプト"
    )
    parser.add_argument(
        "--input",
        default=getattr(exp_cfg, "INPUT_IMAGE", "data/input/test.jpg"),
        help="入力画像パス (デフォルト: config_experiment.py の INPUT_IMAGE)"
    )
    parser.add_argument(
        "--mode",
        choices=["random", "8000_to_4000", "packet_burst", "hybrid"],
        default=getattr(exp_cfg, "FLIP_MODE", "random"),
        help="ビット反転方式: random (均一ランダム), 8000_to_4000 (4-FSK周波数歪み), packet_burst (パケット分散バースト)"
    )
    parser.add_argument(
        "--num-sources", "-n",
        type=int,
        default=getattr(exp_cfg, "NUM_SOURCES", 3),
        help="多数決に参加させるデータ(音声)の個数 (例: 3, 5, 7 など)"
    )
    parser.add_argument(
        "--bit-flip-rate",
        type=float,
        default=getattr(exp_cfg, "ERROR_PERCENT", 10.0) / 100.0,
        help="ノイズパケット内のビット反転率"
    )
    parser.add_argument(
        "--tile-size",
        type=int,
        default=getattr(exp_cfg, "TILE_SIZE", 16),
        help="JPEGタイルサイズ"
    )
    parser.add_argument(
        "--quality",
        type=int,
        default=getattr(exp_cfg, "JPEG_QUALITY", 75),
        help="JPEG圧縮品質"
    )
    parser.add_argument(
        "--web",
        action="store_true",
        default=False,
        help="ブラウザUI (Webスタジオ) を起動してインタラクティブに実験を行う"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5005,
        help="Webスタジオのポート番号 (デフォルト: 5005)"
    )
    parser.add_argument(
        "--generate-wav",
        action="store_true",
        default=False,
        help="4-FSK音声ファイル(WAV)も生成する場合に指定"
    )
    parser.add_argument(
        "--out-dir",
        default=getattr(exp_cfg, "OUTPUT_DIR", "experiment/output"),
        help="実験結果出力ディレクトリ"
    )

    args = parser.parse_args()

    if args.web:
        from experiment.app_experiment import start_server
        start_server(port=args.port, open_browser=True)
        return

    out_dir = os.path.abspath(args.out_dir)
    os.makedirs(out_dir, exist_ok=True)

    print("=" * 70)
    print(">> SSTV Turbo JPEG テキストビット反転 & 多数決復元実証 実験開始")
    print("=" * 70)
    print(f"モード設定: {args.mode}")
    print(f"多数決データ数: {args.num_sources} 個")
    print(f"ノイズパケット内反転率: {args.bit_flip_rate * 100:.1f}%")

    # 1. 入力画像の準備
    input_path = ensure_test_image(args.input)
    gt_img = Image.open(input_path).convert('RGB').resize((256, 256))
    gt_img_arr = np.array(gt_img)

    # 2. 正解テキスト（ビットストリーム）生成
    flipper = JPEGTextBitFlipper(tile_size=args.tile_size, quality=args.quality)
    print("\n[Step 1] 画像から正解パケット・テキスト（Ground Truth Bitstream）を抽出中...")
    gt_packets = flipper.image_to_ground_truth_packets(gt_img_arr)
    total_payload_bits = sum(len(p["payload_bits"]) for p in gt_packets)
    print(f"  -> 全 {len(gt_packets)} パケット生成完了 (総ペイロードビット数: {total_payload_bits:,} bits)")

    # 正解テキストファイルの保存
    gt_text_path = os.path.join(out_dir, "ground_truth_bitstream.txt")
    flipper.save_packets_to_text(gt_packets, gt_text_path)
    print(f"  -> 正解ビットテキスト保存: {gt_text_path}")

    print(f"\n[Step 2] テキスト段階でビット反転を注入 (全 {args.num_sources} 個のデータセット)...")
    base_rate = args.bit_flip_rate * 100.0
    user_rates = [base_rate]
    for i in range(1, args.num_sources):
        user_rates.append(max(2.0, user_rates[-1] * 0.7))
    sets = flipper.generate_n_divergent_sets(
        gt_packets,
        num_sets=args.num_sources,
        error_rates=user_rates,
        mode=args.mode,
        base_seed=getattr(exp_cfg, "SEED", 100)
    )

    for name, (pkts, ber) in sets.items():
        text_path = os.path.join(out_dir, f"audio_{name}_noisy_bitstream.txt")
        flipper.save_packets_to_text(pkts, text_path)
        print(f"  -> 音声{name} テキスト保存 (実効BER: {ber * 100:.2f}%): {text_path}")

        # 音声(WAV)の生成 (Webアップロードやマイク実験用)
        if args.generate_wav:
            wav_path = os.path.join(out_dir, f"audio_{name}_noisy.wav")
            flipper.packets_to_wav(pkts, wav_path, seed=42)
            print(f"     └─ 4-FSK音声(WAV)生成完了: {wav_path}")

    # 4. グラフ①: ビット反転・エラー分布グラフの生成
    print("\n[Step 3] ビット反転分布・BERの可視化グラフを生成中...")
    for name, (pkts, ber) in sets.items():
        plot_path = os.path.join(out_dir, f"graph1_bit_error_distribution_{name}.png")
        plot_bit_error_distribution(gt_packets, pkts, dataset_name=f"Audio {name}", save_path=plot_path)

    comp_plot_path = os.path.join(out_dir, "graph1_comparison_three_audio_ber.png")
    plot_comparison_three_audio(gt_packets, sets, save_path=comp_plot_path)

    # 5. 多数決処理シミュレーション & 画像復元評価
    print("\n[Step 4] 多数決処理 (Bit Majority Vote) と画像復元の評価中...")
    evaluator = MajorityRecoveryEvaluator(tile_size=args.tile_size)
    progression = evaluator.evaluate_progression(gt_packets, gt_img_arr, sets)

    print("\n--- 多数決による復元率推移結果 ---")
    for d in progression:
        psnr_str = f"{d['psnr']:.2f} dB" if d['psnr'] < 99 else "100.0 dB (完全復元)"
        print(f"  * [{d['label']}] => タイル復元率: {d['tile_rate']:.1f}% | ビット正解率: {d['bit_acc']:.2f}% | PSNR: {psnr_str} | SSIM: {d['ssim']:.4f}")

    # 6. グラフ②: 学会発表用 復元率推移グラフ & ギャラリーの生成
    print("\n[Step 5] 学会発表用 高解像度復元率推移グラフを生成中...")
    benchmark_graph_path = os.path.join(out_dir, "graph2_majority_recovery_benchmark_300dpi.png")
    evaluator.plot_recovery_progression(progression, save_path=benchmark_graph_path, dpi=300)

    gallery_path = os.path.join(out_dir, "graph2_visual_recovery_gallery.png")
    evaluator.plot_visual_comparison_gallery(gt_img_arr, progression, save_path=gallery_path, dpi=300)

    print("\n" + "=" * 70)
    print(">> 実験およびすべてのグラフ生成が正常に完了しました！")
    print(f"出力先フォルダ: {out_dir}")
    print("=" * 70)


if __name__ == "__main__":
    main()
