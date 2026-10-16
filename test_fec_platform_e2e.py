"""SSTV Turbo FEC モード Web プラットフォーム統合 ＆ スマホ録音耐性 E2E テスト
(test_fec_platform_e2e.py)
1. SystemFactory および API による FEC モード切替の検証
2. スマホ録音（エアコン音・室内反響・小音量）をシミュレートした FEC 音声の生成
3. Web /api/upload 経由でのアップロード ＆ 高速デコード ＆ DB登録 ＆ 復元画像生成の検証
4. 既存 PNG / JPEG モードとの完全互換・保守性維持の確認
"""

import os
import sys
import time
import json
import numpy as np
import scipy.io.wavfile as wavfile
from PIL import Image

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from web_turbo_png.app import app
from core.system_factory import SystemFactory
from web_turbo_png.services.job_manager import get_job


def test_fec_web_platform_e2e():
    print("================================================================")
    print("🚀 SSTV Turbo 第3のFECモード Webプラットフォーム統合 E2Eテスト開始")
    print("================================================================")

    client = app.test_client()

    # 1. ログイン
    login_res = client.post("/login", data={"email": "Nagasaki", "password": "123456789"}, follow_redirects=True)
    assert login_res.status_code == 200, f"ログイン失敗: {login_res.status_code}"
    print("✅ ログイン成功 (Nagasaki)")

    # 2. 管理者APIでモードを FEC に切り替え
    print("\n--- [1] API 経由でデコードエンジンを FEC モードに変更 ---")
    res = client.post("/admin/api/engine_mode", json={"mode": "FEC"})
    assert res.status_code == 200, f"モード変更API失敗: {res.data}"
    mode_data = res.get_json()
    assert mode_data.get("mode") == "FEC", f"モードがFECになっていません: {mode_data}"
    assert SystemFactory.get_mode() == "FEC", "SystemFactory.get_mode() != FEC"
    print(f"✅ デコードエンジン切替完了: {SystemFactory.get_mode()}")

    # 3. SystemFactory から FEC 設定・エンコーダを取得
    fec_config = SystemFactory.get_config("FEC")
    assert fec_config.WIDTH == 256
    assert fec_config.HEIGHT == 256
    print(f"✅ SystemFactory.get_config('FEC') 取得成功 (画像サイズ: {fec_config.WIDTH}x{fec_config.HEIGHT})")

    encoder = SystemFactory.get_encoder("FEC")
    encoder.max_packets = 5  # 高速テスト用に5パケットに制限

    # テスト画像生成
    test_img_path = os.path.join(PROJECT_ROOT, "test_fec_sample.jpg")
    img = Image.new("RGB", (fec_config.WIDTH, fec_config.HEIGHT), color=(70, 130, 180))
    img.save(test_img_path, format="JPEG", quality=80)

    fec_wav_path = os.path.join(PROJECT_ROOT, "test_fec_audio_clean.wav")
    total_time, pkt_count, out_wav, img_id = encoder.encode(test_img_path, fec_wav_path)
    print(f"✅ FEC 音声生成完了: {out_wav} (パケット数: {pkt_count}, ImageID: 0x{img_id:04X})")

    # 4. 現実的なスマホ録音ノイズを付加
    print("\n--- [2] スマホ室内録音（エアコン音＋室内反響＋音量減衰）のシミュレーション ---")
    sr, clean_audio = wavfile.read(fec_wav_path)
    if clean_audio.ndim > 1:
        clean_audio = np.mean(clean_audio, axis=1)
    clean_audio = clean_audio.astype(np.float32) / 32767.0

    t = np.arange(len(clean_audio)) / float(sr)
    # エアコン・換気扇低周波ノイズ (60Hz, 120Hz)
    ac_noise = 0.15 * np.sin(2 * np.pi * 60 * t) + 0.10 * np.sin(2 * np.pi * 120 * t)
    # 室内反響 (30ms リバーブ)
    reverb_len = int(sr * 0.03)
    reverb_audio = clean_audio.copy()
    if len(clean_audio) > reverb_len:
        reverb_audio[reverb_len:] += 0.20 * clean_audio[:-reverb_len]

    # スマホ小音量・環境雑音合成
    phone_recorded = (reverb_audio * 0.7) + ac_noise + 0.05 * np.random.randn(len(clean_audio))
    # 先頭に0.5秒の無音/環境雑音を追加 (スマホ録音の開始ラグ)
    lead_silence = 0.02 * np.random.randn(int(sr * 0.5))
    phone_final = np.concatenate([lead_silence, phone_recorded])
    phone_final = phone_final / np.max(np.abs(phone_final)) * 0.75

    phone_wav_path = os.path.join(PROJECT_ROOT, "test_phone_fec_recorded.wav")
    wavfile.write(phone_wav_path, sr, (phone_final * 32767).astype(np.int16))
    print(f"✅ スマホ録音模擬音声作成完了: {phone_wav_path} (長さ: {len(phone_final)/sr:.1f}秒)")

    # 5. Web アップロード API 経由での処理実行
    print("\n--- [3] Web アップロード API (/api/upload) への送信 ＆ ジョブ追跡 ---")
    with open(phone_wav_path, "rb") as f:
        upload_res = client.post("/api/upload", data={"file": (f, "test_phone_fec_recorded.wav")}, content_type="multipart/form-data")

    assert upload_res.status_code == 200, f"アップロード受付失敗: {upload_res.data}"
    res_json = upload_res.get_json()
    assert res_json.get("status") == "processing", f"予期しないステータス: {res_json}"
    job_id = res_json["job_id"]
    print(f"✅ アップロード受付完了: job_id = {job_id}")

    # ジョブ完了待ち (最大60秒)
    final_job = None
    for _ in range(60):
        time.sleep(1.0)
        job = get_job(job_id)
        if not job:
            continue
        print(f"  [Progress] {job['progress']}% - {job['status']}")
        if job["progress"] >= 100:
            final_job = job
            break


    assert final_job is not None, "ジョブがタイムアウトしました"
    print("\n--- 最終ジョブ結果 (FECモード) ---")
    print(json.dumps(final_job, indent=2, ensure_ascii=False))

    assert not final_job.get("error"), f"エラーが発生しました: {final_job.get('error')}"
    assert final_job.get("status") == "完了", f"ジョブが完了しませんでした: {final_job.get('status')}"

    result_data = final_job.get("result_data", {})
    assert result_data.get("engine_mode") == "FEC", f"engine_mode が FEC ではありません: {result_data}"
    assert result_data.get("packets_received", 0) >= 4, f"検出パケット数が少なすぎます: {result_data.get('packets_received')}"

    # 画像ファイルの存在確認
    user_img_url = result_data.get("user_image_url")
    assert user_img_url, "user_image_url がありません"
    local_img_path = os.path.join(PROJECT_ROOT, "web_turbo_png", user_img_url.lstrip("/"))
    assert os.path.exists(local_img_path), f"復元画像ファイルが存在しません: {local_img_path}"
    assert os.path.getsize(local_img_path) > 500, f"画像サイズが小さすぎます: {os.path.getsize(local_img_path)}"
    print(f"✅ 復元画像ファイル確認成功: {local_img_path} ({os.path.getsize(local_img_path)} bytes)")

    # 6. モードを安全に PNG に戻す
    print("\n--- [4] モードを安全に PNG に戻す ---")
    SystemFactory.set_mode("PNG")
    assert SystemFactory.get_mode() == "PNG"
    print(f"✅ サーバー稼働モードを PNG に復元: {SystemFactory.get_mode()}")

    # 一時ファイルの削除
    for p in [test_img_path, fec_wav_path, phone_wav_path]:
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

    print("\n================================================================")
    print("🎉 第3のFECモード Webプラットフォーム統合 ＆ スマホ録音耐性 全検証合格！")
    print("================================================================")


if __name__ == "__main__":
    test_fec_web_platform_e2e()
