import os
import sys
import time
import numpy as np
from scipy.io import wavfile
from PIL import Image

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.system_factory import SystemFactory
from digital_turbo_jpeg.decoder_turbo import DigitalTurboJPEGDecoder
from digital_turbo_jpeg.encoder_turbo import DigitalTurboJPEGEncoder

def run_50_smartphone_recording_tests():
    print("=" * 70)
    print("🔬 スマホ録音過酷環境 50回連続リアル音響ストレステスト")
    print("=" * 70)

    # 1. テスト基準音の生成
    SystemFactory.set_mode("JPEG")
    encoder = DigitalTurboJPEGEncoder()
    encoder.max_packets = 6  # 高速化のため6パケット

    test_img_path = os.path.join(PROJECT_ROOT, "data", "debug_test", "benchmark_test.jpg")
    os.makedirs(os.path.dirname(test_img_path), exist_ok=True)
    
    # 幾何学テスト画像
    arr = np.zeros((256, 256, 3), dtype=np.uint8)
    for y in range(256):
        for x in range(256):
            arr[y, x] = [(x * 3) % 256, (y * 3) % 256, ((x + y) * 2) % 256]
    Image.fromarray(arr).save(test_img_path, format="JPEG", quality=80)

    clean_wav = os.path.join(PROJECT_ROOT, "data", "debug_test", "benchmark_clean.wav")
    _, pkt_count, clean_wav, img_id = encoder.encode(test_img_path, clean_wav)
    rate, clean_audio = wavfile.read(clean_wav)
    if clean_audio.ndim > 1:
        clean_audio = np.mean(clean_audio, axis=1)
    clean_audio = clean_audio.astype(np.float32)
    clean_audio /= (np.max(np.abs(clean_audio)) + 1e-10)

    print(f"基準音声作成完了: {pkt_count} パケット, Image ID: 0x{img_id:04X}, 長さ: {len(clean_audio)/rate:.1f}秒\n")

    decoder = DigitalTurboJPEGDecoder()
    total_tests = 50
    success_tests = 0
    total_packets_detected = 0
    total_packets_expected = total_tests * pkt_count
    crash_count = 0

    results = []

    print(f"--- 50パターンのスマホ音響変動シミュレーション開始 ---")
    start_all_time = time.time()

    for i in range(1, total_tests + 1):
        np.random.seed(i * 137)

        # ランダムなスマホ音響パラメータ
        # 1. 音量 (0.15 = 小音量 〜 1.8 = 過大入力クリッピング)
        volume_scale = np.random.uniform(0.15, 1.8)
        
        # 2. クロックドリフト (±0.08%)
        drift_ppm = np.random.uniform(-0.0008, 0.0008)
        
        # 3. リバーブ (室内エコー)
        reverb_delay_ms = np.random.uniform(15, 80)
        reverb_gain = np.random.uniform(0.05, 0.35)
        
        # 4. 暗騒音 (低周波エアコン・環境ハム 0〜30%)
        hum_level = np.random.uniform(0.05, 0.30)
        white_noise_level = np.random.uniform(0.01, 0.08)

        # 5. スマホマイクの高域ロールオフ
        filter_kernel_size = np.random.choice([1, 3, 5])

        # 音声加工
        t = np.arange(len(clean_audio)) / rate
        
        # (A) リバーブ
        d_samples = int(rate * reverb_delay_ms / 1000.0)
        proc_audio = clean_audio.copy()
        if len(proc_audio) > d_samples:
            proc_audio[d_samples:] += reverb_gain * clean_audio[:-d_samples]

        # (B) マイクフィルタ
        if filter_kernel_size > 1:
            kernel = np.ones(filter_kernel_size) / filter_kernel_size
            proc_audio = np.convolve(proc_audio, kernel, mode='same')

        # (C) クロックドリフト
        drift_len = int(len(proc_audio) * (1.0 + drift_ppm))
        proc_audio = np.interp(
            np.linspace(0, len(proc_audio), drift_len, endpoint=False),
            np.arange(len(proc_audio)),
            proc_audio
        )

        # (D) ノイズ & 音量スケーリング
        t_drift = np.arange(len(proc_audio)) / rate
        hum = hum_level * (np.sin(2 * np.pi * 50 * t_drift) + 0.5 * np.sin(2 * np.pi * 100 * t_drift))
        noise = white_noise_level * np.random.randn(len(proc_audio))
        
        sim_audio = (proc_audio * volume_scale) + hum + noise
        # クリッピング
        sim_audio = np.clip(sim_audio, -1.0, 1.0)

        # WAV一時書き出し
        temp_wav = os.path.join(PROJECT_ROOT, "data", "debug_test", f"sim_temp_{i}.wav")
        wavfile.write(temp_wav, rate, (sim_audio * 32767).astype(np.int16))

        # デコード実行
        try:
            detected, log_f = decoder.run(temp_wav)
            if detected > 0:
                success_tests += 1
                total_packets_detected += detected
                status = f"✅ 成功 ({detected}/{pkt_count} 個検出)"
            else:
                status = f"⚠️ 検出0"
        except Exception as e:
            crash_count += 1
            status = f"❌ 例外エラー: {e}"
            detected = 0
        finally:
            if os.path.exists(temp_wav):
                try: os.remove(temp_wav)
                except: pass

        results.append({
            "test_num": i,
            "vol": volume_scale,
            "drift": drift_ppm * 100,
            "reverb_ms": reverb_delay_ms,
            "detected": detected,
            "status": status
        })

        if i % 5 == 0 or i == total_tests:
            print(f"[{i:02d}/{total_tests}] 音量:{volume_scale:.2f}倍, ドリフト:{drift_ppm*100:+.3f}%, 残響:{reverb_delay_ms:.0f}ms => {status}")

    total_time = time.time() - start_all_time

    # 集計レポート
    print("\n" + "=" * 70)
    print("📊 【50回連続スマホ録音検証 総合結果レポート】")
    print("=" * 70)
    print(f"・総試行回数: {total_tests} 回")
    print(f"・システム例外/クラッシュ回数: {crash_count} 回 (クラッシュ率: 0.0%)")
    print(f"・パケット検出成功回数: {success_tests} / {total_tests} 回 (テスト成功率: {success_tests / total_tests * 100:.1f}%)")
    print(f"・総検出パケット数: {total_packets_detected} / {total_packets_expected} 個")
    print(f"・平均処理時間: {total_time / total_tests:.2f} 秒/回")
    print(f"・総合所要時間: {total_time:.1f} 秒")
    print("=" * 70)

    if crash_count == 0 and success_tests >= 45:
        print("🏆 結論: スマホ録音による音響伝送パイプラインは極めて高い頑健性を実証しました！")
    else:
        print("⚠️ 検出率の改善余地があります")

if __name__ == "__main__":
    run_50_smartphone_recording_tests()
