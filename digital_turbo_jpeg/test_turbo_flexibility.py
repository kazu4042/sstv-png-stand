import sys
for stream in (sys.stdout, sys.stderr):
    reconf = getattr(stream, 'reconfigure', None)
    if callable(reconf):
        try:
            reconf(encoding='utf-8')
        except Exception:
            pass

import time
import os
import glob
import numpy as np
from PIL import Image, ImageDraw

root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from digital_turbo_jpeg import config_turbo as config
from digital_turbo_jpeg.encoder_turbo import DigitalTurboJPEGEncoder
from digital_turbo_jpeg.decoder_turbo import DigitalTurboJPEGDecoder
from digital_turbo_jpeg.aggregator_turbo import TurboJPEGAggregator

def generate_test_image(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img = Image.new('RGB', (256, 256), color='navy')
    draw = ImageDraw.Draw(img)
    for i in range(0, 120, 20):
        draw.rectangle([i, i, 255-i, 255-i], outline=(i*2, 255-i*2, 150))
    draw.text((70, 120), "TURBO JPEG!", fill="white")
    img.save(path, format="JPEG", quality=85)
    print(f"[Create] Test 256x256 image created at: {path}")
    return path

def run_experiment():
    print("==================================================================")
    print("[TURBO] DIGITAL TURBO JPEG - 最速転送＆タイルサイズ自由変更 総合テスト")
    print("==================================================================\n")

    # テスト時の過去ログおよび既存DBの混在誤検知を防止するためのクリーンアップ処理
    log_dir = os.path.join(root_dir, config.TEXT_LOG_DIR)
    if os.path.exists(log_dir):
        for f in glob.glob(os.path.join(log_dir, "*")):
            try:
                if os.path.isfile(f):
                    os.remove(f)
            except Exception:
                pass
        print(f"[Cleanup] 過去のテストログおよびDB ({log_dir}) をクリーンアップしました。")

    test_img_path = os.path.join(root_dir, "data", "input", "test_turbo_sample.jpg")
    generate_test_image(test_img_path)

    encoder = DigitalTurboJPEGEncoder()
    results = []

    # 実験シナリオ表: (名称, タイルサイズ, シンボル時間(ms), Sync時間(ms))
    scenarios = [
        ("1. 旧仕様・標準設定相当", 16, 2.0, 20),
        ("2. タイル中規模 + 高速シンボル", 32, 1.0, 20),
        ("3. タイル大判化 (64x64) + ターボ", 64, 1.0, 20),
        ("4. 超大判タイル (128x128) + 極限最速", 128, 1.0, 20)
    ]

    for name, t_size, sym_ms, sync_ms in scenarios:
        print(f"\n--- [Test] 実験: {name} ---")
        config.update_tile_size(t_size)
        config.update_symbol_speed(sym_ms, sync_ms)
        
        wav_out = os.path.join(root_dir, "data", "digital_turbo_jpeg", "audio", f"test_turbo_size_{t_size}.wav")
        duration_sec, pkt_count, out_wav, img_id = encoder.encode(test_img_path, wav_out)
        results.append((name, t_size, sym_ms, pkt_count, duration_sec, out_wav))

    # パフォーマンス比較結果サマリー
    print("\n==================================================================")
    print(" [Result] タイルサイズ＆周波数最速チューニング 伝送スピード比較結果")
    print("==================================================================")
    print(f"{'実験名':<22} | {'タイル長':<6} | {'速度':<6} | {'パケット数':<6} | {'伝送秒数 (速さ)'}")
    print("-" * 68)
    for name, t_size, sym_ms, pkt_count, duration_sec, _ in results:
        print(f"{name:<22} | {t_size:<6} | {sym_ms} ms | {pkt_count:<6} | {duration_sec:>6.2f} 秒")
    print("==================================================================\n")

    # 極限最速モデル（④）の音声波形を使ってデコード＆画像合成・完全デバッグテストを実施
    fastest_wav = results[-1][5]
    print(f"[Decode] 最速モデル ({results[-1][0]}) のWAV音声ファイルを用いて、シンボル直焦点DFTデコードを実証します...")
    config.update_tile_size(results[-1][1])
    config.update_symbol_speed(results[-1][2], 20)

    decoder = DigitalTurboJPEGDecoder()
    success_pkts, raw_log_path = decoder.run(fastest_wav)

    print("[Aggregate] デコードログからのアグリゲータ（全集約・再構築）処理を実行...")
    aggregator = TurboJPEGAggregator()
    restored_paths = aggregator.process_and_save_images()
    aggregator.close()

    print("\n--- デバッグおよび統合検証完了 ---")
    if success_pkts == results[-1][3] and len(restored_paths) > 0:
        print(f"[SUCCESS] 全 {success_pkts} パケットを 100% 正確に高速DFT復号し、画像再建を達成しました！")
        print(f"[Output] 最終復元画像ファイル: {restored_paths[0]}")
    else:
        print(f"[WARN] 検出パケット: {success_pkts} / {results[-1][3]} | 生成画像数: {len(restored_paths)}")

if __name__ == "__main__":
    run_experiment()
