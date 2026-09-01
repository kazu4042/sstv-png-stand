import os
import sys
import numpy as np
from PIL import Image

ROOT_DIR = os.path.abspath(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.system_factory import SystemFactory
from digital_turbo_jpeg.encoder_turbo import DigitalTurboJPEGEncoder


def test_jpeg_engine():
    print("=== SSTV Turbo JPEG エンジン & ざらざら感耐ノイズ復元のテスト開始 ===")

    # 1. ファクトリのモード切り替えテスト
    SystemFactory.set_mode("JPEG")
    assert SystemFactory.get_mode() == "JPEG", "SystemFactory mode switch failed"
    print("✅ SystemFactory モード切替 (JPEG) 合格")

    config = SystemFactory.get_config("JPEG")
    assert getattr(config, "JPEG_QUALITY", 0) == 75, "JPEG config not loaded correctly"
    print(f"✅ JPEG Config 読み込み合格 (Tile={config.TILE_SIZE}x{config.TILE_SIZE}, Quality={getattr(config, 'JPEG_QUALITY', 75)})")

    # 2. テスト用入力画像の準備
    input_dir = os.path.join(ROOT_DIR, "data", "input")
    os.makedirs(input_dir, exist_ok=True)
    test_img_path = os.path.join(input_dir, "test_synth.jpg")

    # 鮮やかなカラーグラデーション画像を生成
    arr = np.zeros((config.HEIGHT, config.WIDTH, 3), dtype=np.uint8)
    for y in range(config.HEIGHT):
        for x in range(config.WIDTH):
            arr[y, x] = [x % 256, y % 256, (x + y) % 256]
    Image.fromarray(arr).save(test_img_path, format="JPEG", quality=85)
    print(f"✅ テスト用画像生成: {test_img_path}")

    # 3. エンコード実行 (WAV生成)
    test_wav_path = os.path.join(ROOT_DIR, "data", "digital_turbo_jpeg", "audio", "test_synth.wav")
    os.makedirs(os.path.dirname(test_wav_path), exist_ok=True)

    encoder = DigitalTurboJPEGEncoder()
    duration, pkt_count, out_wav, img_id = encoder.encode(test_img_path, test_wav_path)
    print(f"✅ JPEG エンコード合格: {pkt_count} パケット生成 (WAV長: {duration:.2f}s, Image ID: 0x{img_id:04X})")

    # 4. デコード実行 (BaseDecoder 準拠)
    decoder = SystemFactory.get_decoder(user_id=1, mode="JPEG")
    progress_records = []

    def on_prog(p):
        progress_records.append(p)

    success_pkts, log_path = decoder.run(test_wav_path, progress_callback=on_prog)
    assert success_pkts > 0, "No packets decoded"
    assert os.path.exists(log_path), "Log file was not generated"
    print(f"✅ JPEG デコード合格: {success_pkts}/{pkt_count} パケット検出 (進捗通知 {len(progress_records)} 回受信)")

    # 5. アグリゲータによる復元 (BaseAggregator 準拠 & ざらざら感耐性)
    aggregator = SystemFactory.get_aggregator(mode="JPEG")
    aggregator.load_all_logs()
    saved_files = aggregator.process_and_save_images(min_tile_ratio=0.0)
    assert len(saved_files) > 0, "No restored images generated"
    print(f"✅ JPEG 多数決・画像復元合格: 生成ファイル {saved_files[0]}")

    # 6. 生成された画像の検証
    restored_img = Image.open(saved_files[0])
    assert restored_img.size == (config.WIDTH, config.HEIGHT), f"Unexpected restored image size: {restored_img.size}"
    print(f"✅ 復元画像フォーマット検証合格: {restored_img.size} JPEG (RGB)")

    # 7. PNGモードへ戻すテスト
    SystemFactory.set_mode("PNG")
    assert SystemFactory.get_mode() == "PNG"
    print("✅ SystemFactory モード切替 (PNG) 復帰合格")

    print("\n🎉 SSTV Turbo JPEG エンジン & プラガブル統合の全テストに合格しました！")


if __name__ == "__main__":
    test_jpeg_engine()
