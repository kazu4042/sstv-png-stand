import os
import sys
import shutil

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

def setup_demo_audio(force=False):
    print("=== SSTV Turbo デモ音声自動セットアップ ===")
    
    static_audio_dir = os.path.join(ROOT_DIR, "web_turbo_png", "static", "audio")
    os.makedirs(static_audio_dir, exist_ok=True)
    
    jpeg_target = os.path.join(static_audio_dir, "turbo_256_256.wav")
    png_target = os.path.join(static_audio_dir, "turbo_png_256_256.wav")
    
    input_img = os.path.join(ROOT_DIR, "data", "input", "test.jpg")
    if not os.path.exists(input_img):
        print(f"❌ 入力画像が見つかりません: {input_img}")
        return False

    # 1. JPEG 音声の確認・セットアップ (4ms対応: 約260MB以上)
    # 200MB未満のファイルは旧設定(2ms)とみなして再生成
    needs_jpeg_update = force or not os.path.exists(jpeg_target) or os.path.getsize(jpeg_target) < 200000000
    if needs_jpeg_update:
        source_jpeg = os.path.join(ROOT_DIR, "data", "digital_turbo_jpeg", "audio", "turbo_256_256.wav")
        compare_jpeg = os.path.join(ROOT_DIR, "data", "digital_turbo_jpeg", "audio", "compare_jpeg.wav")
        if os.path.exists(source_jpeg) and os.path.getsize(source_jpeg) >= 200000000:
            print(f"📦 既存の4ms JPEG音声をコピー中: {source_jpeg} -> {jpeg_target}")
            shutil.copyfile(source_jpeg, jpeg_target)
        elif os.path.exists(compare_jpeg) and os.path.getsize(compare_jpeg) >= 200000000:
            print(f"📦 既存の4ms JPEG音声をコピー中: {compare_jpeg} -> {jpeg_target}")
            shutil.copyfile(compare_jpeg, jpeg_target)
        else:
            print("⚙️ 長崎人工衛星画像 (test.jpg) から 4ms JPEG 音声をエンコード生成中...")
            from digital_turbo_jpeg.encoder_turbo import DigitalTurboJPEGEncoder
            enc = DigitalTurboJPEGEncoder()
            enc.encode(input_img, jpeg_target)
            
        print(f"✅ JPEG デモ音声配置完了: {jpeg_target} ({os.path.getsize(jpeg_target)} bytes)")
    else:
        print(f"✅ JPEG デモ音声 (4ms) は既に配置済みです: {jpeg_target} ({os.path.getsize(jpeg_target)} bytes)")

    # 2. PNG 音声の確認・セットアップ
    if force or not os.path.exists(png_target) or os.path.getsize(png_target) < 1000000:
        source_png = os.path.join(ROOT_DIR, "data", "digital_turbo_png", "audio", "compare_png.wav")
        if os.path.exists(source_png) and os.path.getsize(source_png) > 10000000:
            print(f"📦 既存の高品質 PNG 音声をコピー中: {source_png} -> {png_target}")
            shutil.copyfile(source_png, png_target)
        else:
            print("⚙️ 長崎人工衛星画像 (test.jpg) から PNG 音声をエンコード生成中...")
            from digital_turbo_png.encoder_turbo import DigitalTurboPNGEncoder
            enc_png = DigitalTurboPNGEncoder()
            enc_png.encode(input_img, png_target)
        print(f"✅ PNG デモ音声配置完了: {png_target} ({os.path.getsize(png_target)} bytes)")
    else:
        print(f"✅ PNG デモ音声は既に配置済みです: {png_target} ({os.path.getsize(png_target)} bytes)")

    # data ディレクトリ側にもコピーして同期
    data_jpeg_audio = os.path.join(ROOT_DIR, "data", "digital_turbo_jpeg", "audio", "turbo_256_256.wav")
    os.makedirs(os.path.dirname(data_jpeg_audio), exist_ok=True)
    if force or not os.path.exists(data_jpeg_audio) or os.path.getsize(data_jpeg_audio) < 200000000:
        shutil.copyfile(jpeg_target, data_jpeg_audio)

    print("\n🎉 デモ音声のセットアップがすべて完了しました！")
    return True

if __name__ == "__main__":
    force_opt = "--force" in sys.argv or "-f" in sys.argv
    setup_demo_audio(force=force_opt)
