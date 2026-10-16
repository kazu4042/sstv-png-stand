import sys
import os
import random
from PIL import Image
import numpy as np

# コンソール出力エンコーディング設定
for stream in (sys.stdout, sys.stderr):
    reconf = getattr(stream, 'reconfigure', None)
    if callable(reconf):
        try:
            reconf(encoding='utf-8', errors='replace')
        except Exception:
            pass

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# experiment用設定の読み込み
try:
    from experiment import config_experiment as exp_cfg
except ImportError:
    import config_experiment as exp_cfg

from experiment.bit_flipper import JPEGTextBitFlipper


def ensure_ground_truth_text(image_path, output_text_path):
    """画像から正解ビットテキストを生成する"""
    if not os.path.isabs(image_path):
        image_path = os.path.join(ROOT_DIR, image_path)

    if not os.path.exists(image_path):
        raise FileNotFoundError(f"参照画像が見つかりません: {image_path}")

    flipper = JPEGTextBitFlipper(
        width=getattr(exp_cfg, "WIDTH", 256),
        height=getattr(exp_cfg, "HEIGHT", 256),
        tile_size=getattr(exp_cfg, "TILE_SIZE", 16),
        quality=getattr(exp_cfg, "JPEG_QUALITY", 75)
    )
    img = Image.open(image_path).convert('RGB').resize((exp_cfg.WIDTH, exp_cfg.HEIGHT))
    gt_pkts = flipper.image_to_ground_truth_packets(np.array(img))
    flipper.save_packets_to_text(gt_pkts, output_text_path)
    print(f"[Info] 参照画像 ({os.path.basename(image_path)}) から正解テキストを生成しました: {output_text_path}")
    return output_text_path


def flip_text_random(
    input_file=None,
    output_file=None,
    error_percent=None,
    mode=None,
    protect_header=None,
    seed=None
):
    """config_experiment.py の設定をベースにテキストのビット反転を行う"""
    # configからのデフォルト値解決
    if error_percent is None:
        error_percent = getattr(exp_cfg, "ERROR_PERCENT", 10.0)
    if mode is None:
        mode = getattr(exp_cfg, "FLIP_MODE", "random")
    if protect_header is None:
        protect_header = getattr(exp_cfg, "PROTECT_HEADER", True)
    if seed is None:
        seed = getattr(exp_cfg, "SEED", 42)

    out_dir = getattr(exp_cfg, "OUTPUT_DIR", "experiment/output")
    if not os.path.isabs(out_dir):
        out_dir = os.path.join(ROOT_DIR, out_dir)
    os.makedirs(out_dir, exist_ok=True)

    # 入力テキストの決定 (指定がなければ config.INPUT_IMAGE から生成)
    if input_file is None:
        gt_path = os.path.join(out_dir, getattr(exp_cfg, "GROUND_TRUTH_TEXT", "ground_truth_bitstream.txt"))
        input_image = getattr(exp_cfg, "INPUT_IMAGE", "data/input/test.jpg")
        ensure_ground_truth_text(input_image, gt_path)
        input_file = gt_path
    elif not os.path.exists(input_file):
        # 画像パスが直接渡された場合
        if input_file.lower().endswith(('.jpg', '.jpeg', '.png')):
            gt_path = os.path.join(out_dir, getattr(exp_cfg, "GROUND_TRUTH_TEXT", "ground_truth_bitstream.txt"))
            ensure_ground_truth_text(input_file, gt_path)
            input_file = gt_path
        else:
            print(f"[エラー] 入力ファイルが見つかりません: {input_file}")
            return None

    if output_file is None:
        base_name = os.path.splitext(os.path.basename(input_file))[0]
        output_file = os.path.join(out_dir, f"{base_name}_flipped_{error_percent:.1f}pct.txt")

    flipper = JPEGTextBitFlipper()
    result = flipper.flip_text_file(
        input_text_path=input_file,
        output_text_path=output_file,
        percent=error_percent,
        mode=mode,
        protect_header=protect_header,
        seed=seed
    )

    print("=" * 65)
    print(f"★ ランダムビット反転処理 完了 (設定: experiment/config_experiment.py)")
    print(f"入力テキスト : {input_file}")
    print(f"出力テキスト : {output_file}")
    print(f"指定反転率   : {error_percent:.1f}% (モード: {mode})")
    print(f"実効反転率   : {result['actual_ber']:.2f}% ({result['flipped_bits']:,} / {result['total_bits']:,} bits 反転)")
    print("=" * 65)

    return output_file


if __name__ == "__main__":
    # コマンドライン引数があれば引数優先、なければ config_experiment.py の設定を使用
    input_arg = sys.argv[1] if len(sys.argv) > 1 else None
    percent_arg = float(sys.argv[2]) if len(sys.argv) > 2 else None

    flip_text_random(input_file=input_arg, error_percent=percent_arg)
