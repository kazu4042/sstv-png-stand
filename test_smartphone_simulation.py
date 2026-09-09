import os
import sys
import numpy as np
from scipy.io import wavfile

ROOT_DIR = os.path.abspath(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from digital_turbo_jpeg import config_turbo as config
from digital_turbo_jpeg.decoder_turbo import DigitalTurboJPEGDecoder, apply_bandpass_filter_np

def test_smartphone_acoustic_simulation():
    # 既存のテスト音声（compare_jpeg.wav または short_jpeg.wav）を探す
    candidate_paths = [
        os.path.join(ROOT_DIR, "data", "digital_turbo_jpeg", "audio", "short_jpeg.wav"),
        os.path.join(ROOT_DIR, "data", "digital_turbo_jpeg", "audio", "compare_jpeg.wav"),
    ]
    wav_path = None
    for p in candidate_paths:
        if os.path.exists(p):
            wav_path = p
            break
            
    if not wav_path:
        print("テスト用WAVが見つかりません。新規作成します。")
        from digital_turbo_jpeg.encoder_turbo import TurboJpegEncoder
        from PIL import Image
        img = Image.new("RGB", (256, 256), color=(100, 150, 200))
        test_img_path = os.path.join(ROOT_DIR, "test_acoustic_orig.png")
        img.save(test_img_path)
        encoder = TurboJpegEncoder()
        encoder.max_packets = 6
        wav_path = encoder.encode(test_img_path)

    rate, clean_data = wavfile.read(wav_path)
    if len(clean_data.shape) > 1:
        clean_data = np.mean(clean_data, axis=1)
    clean_data = clean_data.astype(np.float32)
    clean_data = clean_data / (np.max(np.abs(clean_data)) + 1e-10)

    # スマホ録音特有の音響劣化シミュレーション
    # 1. 低周波空調・ハム音ノイズ (60Hz, 120Hz, 240Hz, 350Hz)
    t = np.arange(len(clean_data)) / rate
    hum = (0.35 * np.sin(2 * np.pi * 60 * t) +
           0.25 * np.sin(2 * np.pi * 120 * t) +
           0.20 * np.sin(2 * np.pi * 240 * t) +
           0.15 * np.random.randn(len(clean_data)))

    # 2. 部屋の反響（リバーブ：直接音 + 30ms遅延音 + 70ms遅延音）
    reverb_30ms = int(rate * 0.03)
    reverb_70ms = int(rate * 0.07)
    reverbed = clean_data.copy()
    if len(clean_data) > reverb_30ms:
        reverbed[reverb_30ms:] += 0.25 * clean_data[:-reverb_30ms]
    if len(clean_data) > reverb_70ms:
        reverbed[reverb_70ms:] += 0.15 * clean_data[:-reverb_70ms]

    # 3. 録音側の微小サンプリングレートドリフト (+0.05% = 44122 Hz 相当)
    # 実空間ではPCスピーカー再生クロックとスマホ録音クロックが完全一致しない
    drift_factor = 1.0005
    n_drift = int(len(reverbed) * drift_factor)
    drifted = np.interp(np.linspace(0, len(reverbed), n_drift, endpoint=False), np.arange(len(reverbed)), reverbed)

    # 4. 合成（反響＋ドリフト音 ＋ 大振幅低周波ノイズ）
    t_drift = np.arange(len(drifted)) / rate
    hum_drift = (0.40 * np.sin(2 * np.pi * 60 * t_drift) +
                 0.25 * np.sin(2 * np.pi * 120 * t_drift) +
                 0.15 * np.random.randn(len(drifted)))
    sim_recorded = drifted + hum_drift
    sim_recorded = sim_recorded / np.max(np.abs(sim_recorded))

    sim_wav_path = os.path.join(ROOT_DIR, "test_sim_smartphone_recorded.wav")
    wavfile.write(sim_wav_path, rate, (sim_recorded * 32767).astype(np.int16))
    print(f"スマホ録音シミュレーション音声を生成しました: {sim_wav_path} (時間: {len(sim_recorded)/rate:.1f}秒)")

    # デコード実行
    decoder = DigitalTurboJPEGDecoder()
    
    progress_history = []
    def on_prog(p):
        progress_history.append(p)
        print(f"  [Progress] {p}%")

    import time
    t0 = time.time()
    try:
        count, log_file = decoder.run(sim_wav_path, progress_callback=on_prog)
        el = time.time() - t0
        print(f"デコード結果: {count} パケット検出, 所要時間: {el:.2f}秒, ログ: {log_file}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"デコード中に例外発生: {e}")

if __name__ == "__main__":
    test_smartphone_acoustic_simulation()
