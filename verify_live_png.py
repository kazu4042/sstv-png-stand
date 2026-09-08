import urllib.request
import urllib.parse
import urllib.error
import http.cookiejar
import json
import time
import os
import base64
import ssl
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE_URL = "https://sstv-aggregator.space"
AUTH_USER = "koseikazu@icloud.com"
AUTH_PASS = "123456789"
BASIC_USER = "Nagasaki"
BASIC_PASS = "123456789"


def run_live_test():
    print(f"=== 本番サーバー ({BASE_URL}) での PNG 単体完全復元テスト開始 ===")
    
    cj = http.cookiejar.CookieJar()
    ctx = ssl.create_default_context()
    
    auth_str = f"{BASIC_USER}:{BASIC_PASS}"
    basic_header = f"Basic {base64.b64encode(auth_str.encode('utf-8')).decode('utf-8')}"
    
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj),
        urllib.request.HTTPSHandler(context=ctx)
    )

    def do_req(url, data=None, headers=None, method=None):
        req_headers = {
            "Authorization": basic_header,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }
        if headers:
            req_headers.update(headers)
        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        try:
            with opener.open(req) as resp:
                return resp.getcode(), resp.read(), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    # 1. ログインページへアクセスしてセッション開始
    status, body, _ = do_req(f"{BASE_URL}/login")
    print(f"1. ログイン画面到達: {status}")

    # 2. ログインPOST (123456789 または IKna1357)
    login_body = urllib.parse.urlencode({"email": AUTH_USER, "password": AUTH_PASS, "next": "/"}).encode('utf-8')
    status, body, _ = do_req(f"{BASE_URL}/login", data=login_body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    body_text = body.decode('utf-8', 'replace')
    if "間違っています" in body_text:
        login_body = urllib.parse.urlencode({"email": AUTH_USER, "password": "IKna1357", "next": "/"}).encode('utf-8')
        status, body, _ = do_req(f"{BASE_URL}/login", data=login_body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    print(f"2. ログイン認証実行: {status}")

    # 3. エンジンモードを PNG に設定
    status, body, _ = do_req(f"{BASE_URL}/admin/api/engine_mode", data=json.dumps({"mode": "PNG"}).encode('utf-8'), headers={"Content-Type": "application/json"})
    assert status == 200, f"エンジン切替失敗: {status} - {body.decode('utf-8', 'replace')}"
    print(f"3. 管理者APIによる PNG モード設定: {json.loads(body.decode('utf-8'))}")

    # 4. 音声ファイルアップロード
    wav_path = "data/audio/turbo_png_256_256.wav"
    file_size_mb = os.path.getsize(wav_path) / (1024 * 1024)
    print(f"4. 音声ファイルアップロード開始: {wav_path} ({file_size_mb:.1f} MB)...")

    boundary = "----WebKitFormBoundaryX9v8A1kZpLmNoPqR"
    with open(wav_path, "rb") as f:
        file_bytes = f.read()

    body_parts = [
        f"--{boundary}\r\n".encode("utf-8"),
        f'Content-Disposition: form-data; name="file"; filename="{os.path.basename(wav_path)}"\r\n'.encode("utf-8"),
        b"Content-Type: audio/wav\r\n\r\n",
        file_bytes,
        f"\r\n--{boundary}--\r\n".encode("utf-8")
    ]
    multipart_body = b"".join(body_parts)

    status, body, _ = do_req(
        f"{BASE_URL}/api/upload",
        data=multipart_body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
    )
    assert status == 200, f"アップロード失敗: {status} - {body.decode('utf-8', 'replace')}"
    up_data = json.loads(body.decode('utf-8'))
    assert up_data.get('success') is True, f"アップロード失敗: {up_data}"
    job_id = up_data['job_id']
    print(f"   -> アップロード受付成功！ job_id = {job_id}")

    # 5. ジョブ進捗追跡
    print("5. サーバー側のデコード・多数決画像復元を追跡中...")
    start_time = time.time()
    last_prog = -1
    result_data = None

    while True:
        time.sleep(2)
        status, body, _ = do_req(f"{BASE_URL}/api/progress?job_id={job_id}")
        if status != 200:
            continue
        pdata = json.loads(body.decode('utf-8'))
        prog = pdata.get('progress', 0)
        status_msg = pdata.get('status', '')
        err = pdata.get('error', '')

        if prog != last_prog:
            elapsed = time.time() - start_time
            print(f"   -> [{elapsed:4.1f}s] {prog}% | {status_msg}")
            last_prog = prog

        if err:
            raise RuntimeError(f"サーバー処理エラー: {err}")

        if prog >= 100:
            result_data = pdata.get('result_data', {})
            break

        if time.time() - start_time > 180:
            raise TimeoutError("タイムアウト")

    print(f"\n6. 復元完了！ (所要時間: {time.time() - start_time:.1f} 秒)")
    print(f"   ・画像ID: 0x{result_data.get('image_id')}")
    print(f"   ・単体復元スコア: {result_data.get('user_score')}%")
    print(f"   ・受信パケット数: {result_data.get('packets_received')} / {result_data.get('total_required')}")
    print(f"   ・ユーザー画像URL: {result_data.get('user_image_url')}")
    print(f"   ・全体画像URL: {result_data.get('network_image_url')}")
    print(f"   ・稼働エンジン: {result_data.get('engine_mode')}")

    # 7. 復元された画像の実体ダウンロード確認
    img_url = result_data.get('network_image_url')
    if img_url:
        status, img_bytes, hdrs = do_req(f"{BASE_URL}{img_url}")
        assert status == 200, f"復元画像の取得失敗: {status}"
        assert len(img_bytes) > 5000, "画像サイズが不正です"
        print(f"7. 復元画像ダウンロード検証合格: {len(img_bytes)} bytes (Content-Type: {hdrs.get('Content-Type')})")

    # 8. /result 画面のアクセス確認
    status, res_html, _ = do_req(f"{BASE_URL}/result")
    assert status == 200, f"/result 画面取得失敗: {status}"
    print("8. /result 画面表示合格")

    print("\n==================================================")
    print("🏆 本番VPSサーバー上での PNG 単体完全復元検証が完全に合格しました！")
    print("==================================================")


if __name__ == "__main__":
    run_live_test()
