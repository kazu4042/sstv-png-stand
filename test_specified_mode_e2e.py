import os
import sys
import time
import json
import numpy as np
import scipy.io.wavfile as wavfile
from PIL import Image
import io

from web_turbo_png.app import app
from core.system_factory import SystemFactory
from web_turbo_png.services.job_manager import get_job

def test_specified_mode_only():
    print("================================================================")
    print("🎯 指定モード単独スキャン E2E デバッグ検証開始")
    print("================================================================")
    
    client = app.test_client()
    
    # 1. ログイン
    login_res = client.post("/login", data={"email": "Nagasaki", "password": "123456789"}, follow_redirects=True)
    assert login_res.status_code == 200, f"ログイン失敗: {login_res.status_code}"
    print("✅ ログイン成功 (Nagasaki)")
    
    # ===================================================================
    # テスト 1: JPEG モード指定でのスマホ録音シミュレーション音声の復元検証
    # ===================================================================
    print("\n--- [1] サーバーを JPEG モードに指定 ---")
    SystemFactory.set_mode("JPEG")
    assert SystemFactory.get_mode() == "JPEG"
    print(f"✅ サーバー稼働モード: {SystemFactory.get_mode()}")
    
    from digital_turbo_jpeg import config_turbo as jpeg_config
    from digital_turbo_jpeg.encoder_turbo import DigitalTurboJPEGEncoder
    
    # テスト画像を生成
    test_img_path = "test_jpeg_sample.png"
    img = Image.new("RGB", (jpeg_config.WIDTH, jpeg_config.HEIGHT), color=(180, 80, 40))
    img.save(test_img_path)
    
    encoder = DigitalTurboJPEGEncoder()
    encoder.max_packets = 5
    jpeg_wav_path = "test_jpeg_audio_spec.wav"
    encoder.encode(test_img_path, jpeg_wav_path)
    
    # スマホ実音響録音を模倣:
    # 1. 先頭・末尾に 0.6 秒の無音（再生前後のスマホ録音マージン）
    # 2. 音量を 65% に減衰（スマホ録音の控えめな音量）
    # 3. わずかな室内ホワイトノイズ（SNR 35dB）
    sr, data = wavfile.read(jpeg_wav_path)
    silence_prefix = np.zeros(int(sr * 0.6), dtype=data.dtype)
    silence_suffix = np.zeros(int(sr * 0.6), dtype=data.dtype)
    phone_data = (data * 0.65).astype(np.float32)
    noise = np.random.normal(0, 150, phone_data.shape).astype(np.float32)
    phone_data = np.clip(phone_data + noise, -32768, 32767).astype(np.int16)
    phone_wav_data = np.concatenate([silence_prefix, phone_data, silence_suffix])
    
    phone_wav_path = "test_phone_jpeg_spec.wav"
    wavfile.write(phone_wav_path, sr, phone_wav_data)
    print(f"✅ スマホ音響模倣 JPEG 音声生成完了: {phone_wav_path} (先頭無音+音量減衰+室内環境ノイズ)")
    
    # アップロード
    with open(phone_wav_path, "rb") as f:
        file_bytes = io.BytesIO(f.read())
        upload_res = client.post("/api/upload", data={"file": (file_bytes, "smartphone_jpeg_recording.wav")}, content_type='multipart/form-data')
    
    assert upload_res.status_code == 200, f"アップロード受付失敗: {upload_res.get_data(as_text=True)}"
    job_id = upload_res.get_json()["job_id"]
    print(f"✅ アップロード受付完了: job_id = {job_id}")
    
    # 完了待機
    final_job = None
    for _ in range(60):
        time.sleep(0.5)
        job = get_job(job_id)
        if job:
            if job.get("progress") >= 100 or job.get("error"):
                final_job = job
                break
                
    print(f"\n--- 最終ジョブ結果 (JPEGモード) ---")
    print(json.dumps(final_job, indent=2, ensure_ascii=False))
    
    assert not final_job.get("error"), f"エラーが発生しました: {final_job.get('error')}"
    assert final_job.get("progress") == 100, f"進捗が100%に達していません: {final_job.get('progress')}"
    
    res_data = final_job.get("result_data", {})
    assert res_data.get("engine_mode") == "JPEG", f"engine_mode が JPEG ではありません: {res_data.get('engine_mode')}"
    assert res_data.get("packets_received") == 5, f"期待パケット数 5 に対し、受信数は {res_data.get('packets_received')}"
    print(f"🎯 復元画像ID: {res_data.get('image_id')}")
    print(f"🎯 ユーザー復元画像URL: {res_data.get('user_image_url')}")
    print(f"🎯 統合ネットワーク画像URL: {res_data.get('network_image_url')}")
    
    # 生成された画像ファイルが存在するか確認
    if res_data.get("user_image_url"):
        local_rel = res_data.get("user_image_url").lstrip("/")
        local_path = os.path.join(os.path.dirname(__file__), "web_turbo_png", local_rel)
        assert os.path.exists(local_path), f"ユーザー復元画像が見つかりません: {local_path}"
        assert os.path.getsize(local_path) > 0, "画像ファイルサイズが0です"
        print(f"✅ ユーザー復元画像ファイル確認: {local_path} ({os.path.getsize(local_path)} bytes)")
    
    print("✅ [テスト1] JPEGモード単独スキャンでのスマホ録音復元: 100% 成功！\n")
    
    # ===================================================================
    # テスト 2: PNG モード指定でのスマホ録音シミュレーション音声の復元検証
    # ===================================================================
    print("--- [2] サーバーを PNG モードに指定 ---")
    SystemFactory.set_mode("PNG")
    assert SystemFactory.get_mode() == "PNG"
    print(f"✅ サーバー稼働モード: {SystemFactory.get_mode()}")
    
    from digital_turbo_png import config_turbo as png_config
    from digital_turbo_png.encoder_turbo import DigitalTurboPNGEncoder
    
    test_png_img_path = "test_png_sample_spec.png"
    img_png = Image.new("RGB", (png_config.WIDTH, png_config.HEIGHT), color=(40, 140, 200))
    img_png.save(test_png_img_path)
    
    png_encoder = DigitalTurboPNGEncoder()
    png_encoder.max_packets = 5
    png_wav_path = "test_png_audio_spec.wav"
    png_encoder.encode(test_png_img_path, png_wav_path)
    
    # スマホ実音響模倣
    sr_p, data_p = wavfile.read(png_wav_path)
    sil_p1 = np.zeros(int(sr_p * 0.5), dtype=data_p.dtype)
    sil_p2 = np.zeros(int(sr_p * 0.5), dtype=data_p.dtype)
    p_noisy = (data_p * 0.7).astype(np.float32)
    noise_p = np.random.normal(0, 120, p_noisy.shape).astype(np.float32)
    p_noisy = np.clip(p_noisy + noise_p, -32768, 32767).astype(np.int16)
    phone_png_wav = np.concatenate([sil_p1, p_noisy, sil_p2])
    
    phone_png_path = "test_phone_png_spec.wav"
    wavfile.write(phone_png_path, sr_p, phone_png_wav)
    print(f"✅ スマホ音響模倣 PNG 音声生成完了: {phone_png_path}")
    
    with open(phone_png_path, "rb") as f:
        file_bytes = io.BytesIO(f.read())
        upload_res_png = client.post("/api/upload", data={"file": (file_bytes, "smartphone_png_recording.wav")}, content_type='multipart/form-data')
        
    assert upload_res_png.status_code == 200
    job_id_png = upload_res_png.get_json()["job_id"]
    
    final_job_png = None
    for _ in range(60):
        time.sleep(0.5)
        job = get_job(job_id_png)
        if job:
            if job.get("progress") >= 100 or job.get("error"):
                final_job_png = job
                break
                
    assert not final_job_png.get("error"), f"エラー: {final_job_png.get('error')}"
    assert final_job_png.get("progress") == 100
    res_png_data = final_job_png.get("result_data", {})
    assert res_png_data.get("engine_mode") == "PNG", f"engine_mode が PNG ではありません: {res_png_data.get('engine_mode')}"
    assert res_png_data.get("packets_received") == 5
    print(f"🎯 復元画像ID: {res_png_data.get('image_id')}")
    print(f"🎯 ユーザー復元画像URL: {res_png_data.get('user_image_url')}")
    print("✅ [テスト2] PNGモード単独スキャンでのスマホ録音復元: 100% 成功！\n")
    
    # クリーンアップ
    for p in [test_img_path, jpeg_wav_path, phone_wav_path, test_png_img_path, png_wav_path, phone_png_path]:
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

    print("================================================================")
    print("🎉 全テスト合格: 指定されたモードのみで正確にスキャン・復元完了！")
    print("================================================================")

if __name__ == "__main__":
    test_specified_mode_only()
