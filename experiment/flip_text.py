import os
import sys

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

import argparse
from PIL import Image
import numpy as np

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from experiment.bit_flipper import JPEGTextBitFlipper


def main():
    parser = argparse.ArgumentParser(
        description="JPEGテキスト(ビット列)全体に対して任意のパーセンテージでビット反転を行うツール"
    )
    parser.add_argument(
        "--input", "-i",
        default="experiment/output/ground_truth_bitstream.txt",
        help="入力テキストファイルパス (デフォルト: experiment/output/ground_truth_bitstream.txt)"
    )
    parser.add_argument(
        "--image",
        default=None,
        help="画像から直接正解テキストを生成して反転する場合に指定 (例: data/input/test.jpg)"
    )
    parser.add_argument(
        "--percent", "-p",
        type=float,
        default=5.0,
        help="全体に対する反転割合 (%) (例: 1.0, 5.0, 10.0, 20.0 など)"
    )
    parser.add_argument(
        "--mode", "-m",
        choices=["8000_to_4000", "random", "hybrid"],
        default="8000_to_4000",
        help="反転方式: 8000_to_4000 (8000Hz->4000Hz置換), random (ランダム反転), hybrid (複合)"
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="出力テキストファイルパス (指定しない場合は自動命名)"
    )
    parser.add_argument(
        "--protect-header",
        action="store_true",
        default=True,
        help="パケットヘッダ(48bit)を保護しペイロードのみ反転するか (デフォルト: True)"
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="乱数シード (デフォルト: 42)"
    )

    args = parser.parse_args()

    flipper = JPEGTextBitFlipper()

    # 1. 画像が指定された場合はまず正解テキストを生成
    input_text = args.input
    if args.image:
        if not os.path.exists(args.image):
            print(f"[Error] 画像が見つかりません: {args.image}")
            sys.exit(1)
        img = Image.open(args.image).convert('RGB').resize((256, 256))
        gt_pkts = flipper.image_to_ground_truth_packets(np.array(img))
        input_text = "experiment/output/ground_truth_bitstream.txt"
        flipper.save_packets_to_text(gt_pkts, input_text)
        print(f"[Info] 画像から正解テキストを生成しました: {input_text}")

    # 出力ファイル名の決定
    if args.output:
        output_text = args.output
    else:
        out_dir = os.path.dirname(os.path.abspath(input_text))
        base_name = os.path.splitext(os.path.basename(input_text))[0]
        output_text = os.path.join(out_dir, f"{base_name}_flipped_{args.percent:.1f}pct_{args.mode}.txt")

    print("=" * 65)
    print(">> JPEGテキスト(ビットストリーム) 任意パーセント反転処理")
    print("=" * 65)
    print(f"入力テキスト: {input_text}")
    print(f"反転指定率  : {args.percent:.2f}%")
    print(f"反転モード  : {args.mode}")
    print(f"ヘッダ保護  : {args.protect_header}")

    # 反転処理の実行
    result = flipper.flip_text_file(
        input_text_path=input_text,
        output_text_path=output_text,
        percent=args.percent,
        mode=args.mode,
        protect_header=args.protect_header,
        seed=args.seed
    )

    print("-" * 65)
    print(f"出力完了: {result['output_file']}")
    print(f"総パケット数: {result['packet_count']}")
    print(f"反転ビット数: {result['flipped_bits']:,} / {result['total_bits']:,} bits")
    print(f"実効ビット誤り率 (BER): {result['actual_ber']:.2f}%")
    print("=" * 65)


if __name__ == "__main__":
    main()
