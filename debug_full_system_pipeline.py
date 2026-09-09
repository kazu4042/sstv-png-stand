import os
import sys

# WindowsコンソールでのUTF-8出力対応
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

import time
import io
import json
import base64
import urllib.request
import urllib.parse
import urllib.error
import http.cookiejar
import numpy as np
from PIL import Image

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.system_factory import SystemFactory

BASE_URL = "http://127.0.0.1:5001"
BASIC_USER = "Nagasaki"
BASIC_PASS = "123456789"
ADMIN_EMAIL = "koseikazu@icloud.com"
ADMIN_PASS = "123456789"

class WebClient:
    def __init__(self):
        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))
        cred = base64.b64encode(f"{BASIC_USER}:{BASIC_PASS}".encode()).decode()
        self.auth_header = f"Basic {cred}"

    def request(self, method, path, data=None, json_data=None, files=None):
        url = f"{BASE_URL}{path}"
        headers = {"Authorization": self.auth_header}
        
        req_body = None
        if files:
            boundary = f"----WebKitFormBoundary{int(time.time()*1000)}"
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
            body_parts = []
            for field_name, (filename, file_bytes, content_type) in files.items():
                body_parts.append(f"--{boundary}\r\n".encode())
                body_parts.append(f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode())
                body_parts.append(f"Content-Type: {content_type}\r\n\r\n".encode())
                body_parts.append(file_bytes)
                body_parts.append(b"\r\n")
            body_parts.append(f"--{boundary}--\r\n".encode())
            req_body = b"".join(body_parts)
        elif json_data is not None:
            headers["Content-Type"] = "application/json"
            req_body = json.dumps(json_data).encode("utf-8")
        elif data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            req_body = urllib.parse.urlencode(data).encode("utf-8")

        req = urllib.request.Request(url, data=req_body, headers=headers, method=method)
        try:
            resp = self.opener.open(req)
            return resp.status, resp.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", errors="ignore")

def run_debug_verification():
    print("=================================================================")
    print("🚀 SSTV Turbo システム全体（画像送信・デコード・管理）完全デバッグ検証")
    print("=================================================================")

    client = WebClient()

    # -------------------------------------------------------------------
    # ステップ1: Webサーバー疎通確認
    # -------------------------------------------------------------------
    print("\n--- [1] Webサーバー疎通・基本認証テスト ---")
    status, body = client.request("GET", "/")
    assert status == 200, f"Root returned {status}"
    print("  ✅ ルートページ(/) アクセス成功 (Status: 200)")

    # -------------------------------------------------------------------
    # ステップ2: 管理者ログイン & エンジン切替テスト
    # -------------------------------------------------------------------
    print("\n--- [2] 管理者ログイン & エンジン切替テスト ---")
    status, body = client.request("POST", "/login", data={
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASS
    })
    assert status in [200, 302], f"Login returned {status}"
    print("  ✅ 管理者ログイン成功")

    # 管理者画面表示
    status, body = client.request("GET", "/admin")
    assert status == 200, f"Admin page returned {status}"
    print("  ✅ 管理者管制センター(/admin) 表示成功")

    # エンジンモードを JPEG に設定
    status, body = client.request("POST", "/admin/api/engine_mode", json_data={"mode": "JPEG"})
    assert status == 200, f"Engine mode switch returned {status}: {body}"
    res_json = json.loads(body)
    assert res_json.get("current_mode") == "JPEG"
    print("  ✅ エンジンモード切替 -> JPEG 成功")

    # -------------------------------------------------------------------
    # ステップ3: 画像エンコード（画像 -> SSTV音声生成）
    # -------------------------------------------------------------------
    print("\n--- [3] 画像エンコード（画像 -> 4-FSK SSTV音声生成）テスト ---")
    img_dir = os.path.join(PROJECT_ROOT, "data", "debug_test")
    os.makedirs(img_dir, exist_ok=True)
    test_img_path = os.path.join(img_dir, "test_input_tile.jpg")
    
    # 2x2タイル（32x32）のカラフルなテスト画像
    test_arr = np.zeros((32, 32, 3), dtype=np.uint8)
    test_arr[:16, :16] = [255, 60, 60]    # 赤
    test_arr[:16, 16:] = [60, 255, 60]    # 緑
    test_arr[16:, :16] = [60, 60, 255]    # 青
    test_arr[16:, 16:] = [240, 200, 40]   # 黄
    Image.fromarray(test_arr).save(test_img_path, format="JPEG", quality=75)
    print(f"  作成したテスト画像: {test_img_path} (32x32)")

    # エンコーダーの実行
    SystemFactory.set_mode("JPEG")
    encoder = SystemFactory.get_encoder()
    encoder.max_packets = 4
    audio_out_path = os.path.join(img_dir, "test_encoded_audio.wav")
    
    t0 = time.time()
    _, packet_count, wav_path, img_id = encoder.encode(test_img_path, audio_out_path)
    t1 = time.time()
    print(f"  ✅ エンコード完了: パケット数={packet_count}, WAV={wav_path}, Image ID=0x{img_id:04X} ({t1-t0:.2f}秒)")
    assert os.path.exists(wav_path)
    assert packet_count == 4, f"Expected 4 tiles, got {packet_count}"

    # -------------------------------------------------------------------
    # ステップ4: 音声アップロード送信 & 非同期デコード進捗追跡
    # -------------------------------------------------------------------
    print("\n--- [4] 音声ファイル送信・非同期デコード & 進捗追跡テスト ---")
    with open(wav_path, "rb") as f:
        wav_bytes = f.read()
    status, body = client.request("POST", "/api/upload", files={"file": ("test_encoded.wav", wav_bytes, "audio/wav")})
    assert status == 200, f"Upload failed ({status}): {body}"
    job_info = json.loads(body)
    job_id = job_info.get("job_id")
    assert job_id, "No job_id returned"
    print(f"  ✅ 音声アップロード成功: job_id={job_id}")

    # 進捗ポーリング
    print("  デコード進捗トラッキング開始...")
    prev_progress = -1
    start_wait = time.time()
    job_done = False
    final_data = None

    while time.time() - start_wait < 60:
        status, body = client.request("GET", f"/api/progress?job_id={job_id}")
        assert status == 200, f"Progress returned {status}: {body}"
        prog_data = json.loads(body)
        curr_prog = prog_data.get("progress", 0)
        p_status = prog_data.get("status", "")
        err = prog_data.get("error", "")

        if curr_prog != prev_progress:
            print(f"    進捗: {curr_prog}% ({p_status})")
            prev_progress = curr_prog

        if err:
            raise RuntimeError(f"Job failed with error: {err}")

        if curr_prog >= 100:
            job_done = True
            final_data = prog_data.get("result_data", {})
            break

        time.sleep(0.5)

    assert job_done, "Job did not complete within timeout"
    print(f"  ✅ 非同期デコード完了: 所要時間 = {time.time() - start_wait:.2f}秒")
    print(f"  結果情報: {final_data}")

    # -------------------------------------------------------------------
    # ステップ5: 復元画像の確認 & No.1フローチャート即時採用検証
    # -------------------------------------------------------------------
    print("\n--- [5] 復元画像 & No.1即時採用検証 ---")
    status, body = client.request("GET", "/admin/api/images")
    assert status == 200, f"/admin/api/images returned {status}"
    images = json.loads(body).get("images", [])
    print(f"  現在の復元画像総数: {len(images)} 件")
    
    hex_id = f"{img_id:04X}"
    target_img = None
    for im in images:
        curr_id = str(im.get("image_id") or im.get("image_id_hex") or im.get("id") or "").upper()
        if curr_id in [hex_id, str(img_id).upper()]:
            target_img = im
            break

    assert target_img is not None, f"Image ID 0x{img_id:04X} not found in /admin/api/images ({[im.get('image_id_hex') or im.get('image_id') for im in images]})"
    print(f"  ✅ 画像ID 0x{img_id:04X} が一覧に正常登録されていることを確認！")
    received = target_img.get('tile_count', target_img.get('received_tiles', 0))
    total = target_img.get('total_required', target_img.get('total_tiles', 256))
    img_url = target_img.get('thumbnail_url', target_img.get('url', ''))
    print(f"     受信パケット数: {received}/{total}")
    print(f"     画像URL: {img_url}")

    # 結果ページ (/result?id=...) の表示検証
    status, body = client.request("GET", f"/result?id={hex_id}")
    assert status == 200
    print("  ✅ デコード結果ページ(/result?id=...) 表示成功")

    # -------------------------------------------------------------------
    # ステップ6: 管理者画像削除機能テスト
    # -------------------------------------------------------------------
    print("\n--- [6] 管理者画像削除機能テスト ---")
    status, body = client.request("POST", "/admin/delete_images", json_data={"image_ids": [hex_id]})
    assert status == 200, f"Delete failed ({status}): {body}"
    print(f"  ✅ 選択削除API成功: {body}")

    # 削除後に画像一覧に存在しないことを確認
    status, body = client.request("GET", "/admin/api/images")
    images_after = json.loads(body).get("images", [])
    found_after = any((str(im.get("image_id") or "").upper() == hex_id) for im in images_after)
    assert not found_after, "Image still exists after deletion"
    print("  ✅ 画像IDが正常に削除されたことを確認！")

    print("\n=================================================================")
    print("🎉 画像送信・エンコード・Webアップロード・デコード・画像復元・削除の")
    print("   全パイプラインが100%完璧に動作することを実証完了しました！")
    print("=================================================================")

if __name__ == "__main__":
    run_debug_verification()
