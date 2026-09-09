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

def test_dual_engine_detection():
    print("=== デュアルエンジン自動検出 E2E デバッグテスト開始 ===")
    
    client = app.test_client()
    
    # 1. ログイン
    login_res = client.post("/login", data={"email": "Nagasaki", "password": "123456789"}, follow_redirects=True)
    assert login_res.status_code == 200, f"ログイン失敗: {login_res.status_code}"
    print("✅ ログイン成功 (Nagasaki)")
    
    # 2. サーバーを明示的に PNG モードにする
    SystemFactory.set_mode("PNG")
    print(f"✅ サーバーモードを PNG に設定: {SystemFactory.get_mode()}")
    assert SystemFactory.get_mode() == "PNG"
    
    # 3. JPEG の音声ファイルを準備
    from digital_turbo_jpeg import config_turbo as jpeg_config
    from digital_turbo_jpeg.encoder_turbo import DigitalTurboJPEGEncoder
    
    test_img_path = "test_jpeg_sample.png"
    img = Image.new("RGB", (jpeg_config.WIDTH, jpeg_config.HEIGHT), color=(200, 50, 50))
    img.save(test_img_path)
    
    encoder = DigitalTurboJPEGEncoder()
    encoder.max_packets = 5  # テスト用に5パケット（約20秒）に制限
    jpeg_wav_path = "test_jpeg_audio_e2e.wav"
    encoder.encode(test_img_path, jpeg_wav_path)
    print(f"✅ テスト用 JPEG 音声生成完了: {jpeg_wav_path}")
    
    # スマホ録音を模倣して少し低音量 & 先頭に無音を追加
    sr, data = wavfile.read(jpeg_wav_path)
    silence_prefix = np.zeros(int(sr * 0.5), dtype=data.dtype) # 0.5秒の先頭無音
    silence_suffix = np.zeros(int(sr * 0.5), dtype=data.dtype) # 0.5秒の末尾無音
    phone_sim_data = np.concatenate([silence_prefix, (data * 0.7).astype(data.dtype), silence_suffix])
    phone_wav_path = "test_phone_sim_e2e.wav"
    wavfile.write(phone_wav_path, sr, phone_sim_data)
    print(f"✅ スマホ録音模倣音声生成 (先頭無音+音量減衰): {phone_wav_path}")
    
    # 4. サーバー（PNGモード中）にこの JPEG 音声をアップロード！
    print("\n--- PNGモードのサーバーに JPEG 音声をアップロード中 ---")
    with open(phone_wav_path, "rb") as f:
        file_bytes = io.BytesIO(f.read())
        upload_res = client.post("/api/upload", data={"file": (file_bytes, "phone_recording.wav")}, content_type='multipart/form-data')
    
    assert upload_res.status_code == 200, f"アップロード失敗: {upload_res.get_data(as_text=True)}"
    job_data = upload_res.get_json()
    job_id = job_data["job_id"]
    print(f"✅ アップロード受付完了: job_id = {job_id}")
    
    # 5. バックグラウンドジョブの完了を待機
    from web_turbo_png.services.job_manager import get_job
    final_job = None
    for _ in range(60):
        time.sleep(0.5)
        job = get_job(job_id)
        if job:
            print(f"  [Progress] {job.get('progress')}% - {job.get('status')}")
            if job.get("progress") >= 100 or job.get("error"):
                final_job = job
                break
            
    print(f"\n--- 最終ジョブ結果 ---")
    print(json.dumps(final_job, indent=2, ensure_ascii=False))
    
    assert not final_job.get("error"), f"エラーが発生しました: {final_job.get('error')}"
    assert final_job.get("progress") == 100, f"進捗が100%に達していません: {final_job.get('progress')}"
    
    print("\n🎉 [テスト1/2完了] PNGサーバー -> JPEG音声の自動判定合格！\n")

    # -------------------------------------------------------------------
    # テスト 2: サーバーが JPEG モードのときに PNG 音声をアップロード
    # -------------------------------------------------------------------
    print("--- テスト 2: サーバーを JPEG に設定し、PNG 音声をアップロード ---")
    SystemFactory.set_mode("JPEG")
    assert SystemFactory.get_mode() == "JPEG"
    
    from digital_turbo_png import config_turbo as png_config
    from digital_turbo_png.encoder_turbo import DigitalTurboPNGEncoder
    
    png_test_img_path = "test_png_sample.png"
    img_png = Image.new("RGB", (png_config.WIDTH, png_config.HEIGHT), color=(50, 150, 200))
    img_png.save(png_test_img_path)
    
    png_encoder = DigitalTurboPNGEncoder()
    png_encoder.max_packets = 5
    png_wav_path = "test_png_audio_e2e.wav"
    png_encoder.encode(png_test_img_path, png_wav_path)
    
    # スマホ模倣 (無音付加 + 音量減衰)
    sr_png, data_png = wavfile.read(png_wav_path)
    sil_pre = np.zeros(int(sr_png * 0.4), dtype=data_png.dtype)
    sil_suf = np.zeros(int(sr_png * 0.4), dtype=data_png.dtype)
    phone_png_data = np.concatenate([sil_pre, (data_png * 0.75).astype(data_png.dtype), sil_suf])
    phone_png_wav = "test_phone_sim_png_e2e.wav"
    wavfile.write(phone_png_wav, sr_png, phone_png_data)
    
    with open(phone_png_wav, "rb") as f:
        file_bytes_png = io.BytesIO(f.read())
        upload_res_png = client.post("/api/upload", data={"file": (file_bytes_png, "phone_png_recording.wav")}, content_type='multipart/form-data')
        
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
    res_data_png = final_job_png.get("result_data", {})
    assert res_data_png.get("engine_mode") == "PNG", f"モードがPNGになっていません: {res_data_png.get('engine_mode')}"
    assert SystemFactory.get_mode() == "PNG"
    print(f"🎯 PNG画像ID: {res_data_png.get('image_id')}, URL: {res_data_png.get('user_image_url')}")
    print("✅ [テスト2/2完了] JPEGサーバー -> PNG音声の自動判定合格！")

if __name__ == "__main__":
    test_dual_engine_detection()

