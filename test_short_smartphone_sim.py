import os
import sys
import numpy as np
from scipy.io import wavfile

ROOT_DIR = os.path.abspath(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from digital_turbo_jpeg import config_turbo as config
from digital_turbo_jpeg.decoder_turbo import DigitalTurboJPEGDecoder, apply_bandpass_filter_np
from digital_turbo_jpeg.encoder_turbo import DigitalTurboJPEGEncoder
from PIL import Image

def test_short_smartphone_sim():
    # 6パケットの短いWAVを生成
    img = Image.new("RGB", (256, 256), color=(100, 150, 200))
    test_img_path = os.path.join(ROOT_DIR, "test_short_acoustic_orig.png")
    img.save(test_img_path)
    encoder = DigitalTurboJPEGEncoder()
    encoder.max_packets = 6
    res = encoder.encode(test_img_path)
    clean_wav_path = res[2] if isinstance(res, tuple) else res
    rate, clean_data = wavfile.read(clean_wav_path)
    if len(clean_data.shape) > 1:
        clean_data = np.mean(clean_data, axis=1)
    clean_data = clean_data[:rate * 35]
    clean_data = clean_data.astype(np.float32)
    clean_data = clean_data / (np.max(np.abs(clean_data)) + 1e-10)

    # スマホ録音シミュレーション: 低周波エアコン音 + リバーブ + クロックドリフト
    t = np.arange(len(clean_data)) / rate
    hum = (0.40 * np.sin(2 * np.pi * 60 * t) +
           0.30 * np.sin(2 * np.pi * 120 * t) +
           0.20 * np.sin(2 * np.pi * 240 * t) +
           0.20 * np.random.randn(len(clean_data)))

    reverb_30ms = int(rate * 0.03)
    reverbed = clean_data.copy()
    if len(clean_data) > reverb_30ms:
        reverbed[reverb_30ms:] += 0.25 * clean_data[:-reverb_30ms]

    # クロックドリフト (+0.05%)
    drift_factor = 1.0005
    n_drift = int(len(reverbed) * drift_factor)
    drifted = np.interp(np.linspace(0, len(reverbed), n_drift, endpoint=False), np.arange(len(reverbed)), reverbed)

    t_drift = np.arange(len(drifted)) / rate
    hum_drift = (0.40 * np.sin(2 * np.pi * 60 * t_drift) +
                 0.30 * np.sin(2 * np.pi * 120 * t_drift) +
                 0.20 * np.random.randn(len(drifted)))
    sim_recorded = drifted + hum_drift
    sim_recorded = sim_recorded / np.max(np.abs(sim_recorded))

    short_sim_wav = os.path.join(ROOT_DIR, "test_short_sim_smartphone.wav")
    wavfile.write(short_sim_wav, rate, (sim_recorded * 32767).astype(np.int16))
    print(f"短尺スマホ録音シミュレーション音声を生成: {short_sim_wav} ({len(sim_recorded)/rate:.1f}秒)")

    decoder = DigitalTurboJPEGDecoder()
    import time
    t0 = time.time()
    count, log_file = decoder.run(short_sim_wav, progress_callback=lambda p: print(f"  [Prog] {p}%"))
    el = time.time() - t0
    print(f"結果: {count} パケット検出, 所要時間: {el:.2f}秒")

if __name__ == "__main__":
    test_short_smartphone_sim()
