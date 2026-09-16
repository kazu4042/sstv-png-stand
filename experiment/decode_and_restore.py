import os
import sys

# UTF-8 出力
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from PIL import Image
import numpy as np
from digital_turbo_jpeg import config_turbo as config
from digital_turbo_jpeg.aggregator_turbo import TurboJPEGAggregator


def decode_and_restore_from_bitstreams(
    bitstream_files,
    output_dir="experiment/output/restored_images",
    original_image_path="data/input/test.jpg"
):
    """digital_turbo_jpeg の本番アグリゲータ (TurboJPEGAggregator) を使用し、
    反転させたビット列テキストから耐ノイズ多数決復元を行ってJPEG画像を復元する。
    """
    os.makedirs(output_dir, exist_ok=True)
    temp_log_dir = os.path.join(output_dir, "temp_logs")
    os.makedirs(temp_log_dir, exist_ok=True)

    print("=" * 70)
    print("🚀 digital_turbo_jpeg 本番デコーダ・アグリゲータによるビット列復元処理")
    print("=" * 70)

    # 1. ログファイルの一時配置 (アグリゲータが読める形式で配置)
    for idx, fpath in enumerate(bitstream_files):
        if not os.path.exists(fpath):
            print(f"[エラー] ビット列ファイルが見つかりません: {fpath}")
            return
        target_name = f"turbo_bitstream_user_{idx+1}_{os.path.basename(fpath)}"
        target_path = os.path.join(temp_log_dir, target_name)
        with open(fpath, "r", encoding="utf-8") as src, open(target_path, "w", encoding="utf-8") as dst:
            dst.write(src.read())

    # 2. アグリゲータの初期化とログ読み込み
    aggregator = TurboJPEGAggregator(log_dir=temp_log_dir)
    print("\n[Step 1] ビット列ログをアグリゲータDBに読み込み中...")
    aggregator.load_all_logs()

    # 3. 多数決 & 耐ノイズJPEG画像復元実行
    print("\n[Step 2] RSTアライメント多数決 & ざらざら感耐ノイズ画像復元を実行中...")
    saved_images = aggregator.process_and_save_images(min_tile_ratio=0.0)

    # 4. 結果の検証と評価
    gt_img_arr = None
    if os.path.exists(original_image_path):
        gt_img = Image.open(original_image_path).convert('RGB').resize((config.WIDTH, config.HEIGHT))
        gt_img_arr = np.array(gt_img)

    print("\n" + "=" * 70)
    print("🎉 復元完了！ 生成された画像一覧:")
    print("=" * 70)

    for img_path in saved_images:
        final_dest = os.path.join(output_dir, os.path.basename(img_path))
        if img_path != final_dest:
            img = Image.open(img_path)
            img.save(final_dest, format="JPEG", quality=95)

        restored_img = Image.open(final_dest)
        arr = np.array(restored_img)

        psnr_str = "N/A"
        if gt_img_arr is not None:
            mse = np.mean((gt_img_arr.astype(float) - arr.astype(float)) ** 2)
            if mse <= 1e-10:
                psnr = 100.0
            else:
                psnr = 20.0 * np.log10(255.0 / np.sqrt(mse))
            psnr_str = f"{psnr:.2f} dB" if psnr < 99 else "100.0 dB (完全一致)"

        print(f"  ★ 復元画像: {final_dest}")
        print(f"     └─ 解像度: {restored_img.size} (JPEG), PSNR: {psnr_str}")

    return saved_images


if __name__ == "__main__":
    # デフォルトで experiment/output/ にあるノイズ入りビット列（A, B, C）を復元
    default_files = [
        "experiment/output/audio_A_noisy_bitstream.txt",
        "experiment/output/audio_B_noisy_bitstream.txt",
        "experiment/output/audio_C_noisy_bitstream.txt"
    ]
    
    # 存在しない場合は、単一の反転テキストを探す
    valid_files = [f for f in default_files if os.path.exists(f)]
    if not valid_files:
        flipped_single = "experiment/output/ground_truth_bitstream_flipped_10.0pct.txt"
        if os.path.exists(flipped_single):
            valid_files = [flipped_single]

    if len(sys.argv) > 1:
        valid_files = sys.argv[1:]

    if valid_files:
        decode_and_restore_from_bitstreams(valid_files)
    else:
        print("[エラー] 復元対象のビット列テキストが見つかりません。")
        print("先に experiment/flip_bits.py または experiment/run_experiment.py を実行してください。")
