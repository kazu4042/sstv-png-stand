import os
import sys
import io

ROOT_DIR = os.path.abspath(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from web_turbo_png.app import app
from core.system_factory import SystemFactory
from digital_turbo_jpeg.encoder_turbo import DigitalTurboJPEGEncoder
from PIL import Image
import numpy as np
import time


def test_upload_flow():
    print("=== アップロードフロー完全結合テスト開始 ===")
    app.config['TESTING'] = True
    client = app.test_client()

    # 1. テスト用音声ファイル生成 (JPEG) - テスト用には高速処理のため小さめ画像
    input_dir = os.path.join(ROOT_DIR, "data", "input")
    os.makedirs(input_dir, exist_ok=True)
    img_path = os.path.join(input_dir, "test_upload_sample.jpg")
    arr = np.zeros((64, 64, 3), dtype=np.uint8)
    for y in range(64):
        for x in range(64):
            arr[y, x] = [x % 256, y % 256, (x + y) % 256]
    Image.fromarray(arr).save(img_path, format="JPEG", quality=80)

    # 2. JPEG モードに設定してエンコード
    SystemFactory.set_mode("JPEG")
    jpeg_wav = os.path.join(ROOT_DIR, "data", "digital_turbo_jpeg", "audio", "test_upload_jpeg.wav")
    os.makedirs(os.path.dirname(jpeg_wav), exist_ok=True)
    enc = DigitalTurboJPEGEncoder()
    enc.encode(img_path, jpeg_wav)

    # 3. ログインセッション準備
    with client.session_transaction() as sess:
        sess['basic_auth_passed'] = True
        sess['user_id'] = 1
        sess['email'] = "koseikazu@icloud.com"
        sess['display_name'] = "Admin"

    # 4. アップロードリクエスト送信 (name="file")
    with open(jpeg_wav, 'rb') as f:
        wav_bytes = f.read()

    res = client.post('/api/upload', data={
        'file': (io.BytesIO(wav_bytes), 'test_upload_jpeg.wav')
    }, content_type='multipart/form-data')

    assert res.status_code == 200, f"Upload failed: {res.status_code} - {res.get_data(as_text=True)}"
    data = res.get_json()
    assert data.get('success') is True, f"Success flag missing: {data}"
    job_id = data['job_id']
    print(f"✅ /api/upload 受付成功: job_id={job_id}")

    # 5. ジョブの完了待ち (最大90秒)
    for _ in range(180):
        time.sleep(0.5)
        prog_res = client.get(f'/api/progress?job_id={job_id}')
        if prog_res.status_code == 200:
            pdata = prog_res.get_json()
            if pdata.get('progress') == 100:
                if pdata.get('error'):
                    raise RuntimeError(f"Job failed with error: {pdata['error']}")
                print(f"✅ ジョブ完了確認: status={pdata['status']}, result_data={pdata.get('result_data')}")
                break
    else:
        raise TimeoutError("Job timed out")

    print("\n🎉 音声アップロードフローの全テストに完全合格しました！")


if __name__ == "__main__":
    test_upload_flow()
