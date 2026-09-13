import os
import sys
import shutil
import json

for stream in (sys.stdout, sys.stderr):
    reconf = getattr(stream, 'reconfigure', None)
    if callable(reconf):
        try:
            reconf(encoding='utf-8')
        except Exception:
            pass

ROOT_DIR = os.path.abspath(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


def _get_config_meta(config_module):
    """現在のconfigからメタ情報を取得"""
    return {
        "ms_symbol": getattr(config_module, "MS_SYMBOL", None),
        "ms_sync": getattr(config_module, "MS_SYNC", None),
        "sample_rate": getattr(config_module, "SAMPLE_RATE", None),
        "tile_size": getattr(config_module, "TILE_SIZE", None),
        "width": getattr(config_module, "WIDTH", None),
        "height": getattr(config_module, "HEIGHT", None),
    }


def _needs_regeneration(wav_path, config_module):
    """音声ファイルの再生成が必要か判定（メタファイルとconfig比較）"""
    if not os.path.exists(wav_path) or os.path.getsize(wav_path) < 1000000:
        return True

    meta_path = wav_path + ".meta"
    if not os.path.exists(meta_path):
        return True

    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            saved_meta = json.load(f)
    except Exception:
        return True

    current_meta = _get_config_meta(config_module)
    for key in current_meta:
        if current_meta[key] != saved_meta.get(key):
            print(f"  ⚡ Config変更検出: {key} = {saved_meta.get(key)} → {current_meta[key]}")
            return True

    return False


def _save_meta(wav_path, config_module):
    """音声ファイルのメタ情報を保存"""
    meta_path = wav_path + ".meta"
    meta = _get_config_meta(config_module)
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
    except Exception as e:
        print(f"  ⚠️ メタファイル保存失敗: {e}")


def setup_demo_audio():
    print("=== SSTV Turbo デモ音声自動セットアップ ===")
    
    static_audio_dir = os.path.join(ROOT_DIR, "web_turbo_png", "static", "audio")
    os.makedirs(static_audio_dir, exist_ok=True)
    
    jpeg_target = os.path.join(static_audio_dir, "turbo_256_256.wav")
    png_target = os.path.join(static_audio_dir, "turbo_png_256_256.wav")
    
    input_img = os.path.join(ROOT_DIR, "data", "input", "test.jpg")
    if not os.path.exists(input_img):
        print(f"❌ 入力画像が見つかりません: {input_img}")
        return False

    # 1. JPEG 音声の確認・セットアップ
    from digital_turbo_jpeg import config_turbo as jpeg_config
    if _needs_regeneration(jpeg_target, jpeg_config):
        print("⚙️ JPEG デモ音声をエンコード生成中 (configに基づく最新版)...")
        from digital_turbo_jpeg.encoder_turbo import DigitalTurboJPEGEncoder
        enc = DigitalTurboJPEGEncoder()
        enc.encode(input_img, jpeg_target)
        _save_meta(jpeg_target, jpeg_config)
        print(f"✅ JPEG デモ音声生成完了: {jpeg_target} ({os.path.getsize(jpeg_target)} bytes)")
    else:
        print(f"✅ JPEG デモ音声は最新です: {jpeg_target} ({os.path.getsize(jpeg_target)} bytes)")

    # 2. PNG 音声の確認・セットアップ
    from digital_turbo_png import config_turbo as png_config
    if _needs_regeneration(png_target, png_config):
        print("⚙️ PNG デモ音声をエンコード生成中 (configに基づく最新版)...")
        from digital_turbo_png.encoder_turbo import DigitalTurboPNGEncoder
        enc_png = DigitalTurboPNGEncoder()
        enc_png.encode(input_img, png_target)
        _save_meta(png_target, png_config)
        print(f"✅ PNG デモ音声生成完了: {png_target} ({os.path.getsize(png_target)} bytes)")
    else:
        print(f"✅ PNG デモ音声は最新です: {png_target} ({os.path.getsize(png_target)} bytes)")

    # data ディレクトリ側にもコピーして同期
    data_jpeg_audio = os.path.join(ROOT_DIR, "data", "digital_turbo_jpeg", "audio", "turbo_256_256.wav")
    os.makedirs(os.path.dirname(data_jpeg_audio), exist_ok=True)
    if not os.path.exists(data_jpeg_audio) or os.path.getsize(data_jpeg_audio) < 1000000:
        shutil.copyfile(jpeg_target, data_jpeg_audio)

    print("\n🎉 デモ音声のセットアップがすべて完了しました！")
    return True

if __name__ == "__main__":
    setup_demo_audio()
