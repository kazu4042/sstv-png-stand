import urllib.request
import urllib.parse
import urllib.error
import http.cookiejar
import json
import base64
import ssl
import sys

BASE_URL = "https://127.0.0.1"
BASIC_USER = "Nagasaki"
BASIC_PASS = "123456789"
AUTH_USER = "koseikazu@icloud.com"
AUTH_PASS = "123456789"

def run_checks():
    print("=== 管理者ダッシュボード改善 & 音声再生モード切替 検証開始 ===")
    
    cj = http.cookiejar.CookieJar()
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    auth_str = f"{BASIC_USER}:{BASIC_PASS}"
    basic_header = f"Basic {base64.b64encode(auth_str.encode('utf-8')).decode('utf-8')}"
    
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj),
        urllib.request.HTTPSHandler(context=ctx)
    )

    def do_req(path, data=None, headers=None, method=None):
        url = f"{BASE_URL}{path}"
        req_headers = {
            "Host": "sstv-aggregator.space",
            "Authorization": basic_header,
            "User-Agent": "Mozilla/5.0"
        }
        if headers:
            req_headers.update(headers)
        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        try:
            with opener.open(req) as resp:
                return resp.getcode(), resp.read(), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    # 1. ログイン
    login_body = urllib.parse.urlencode({"email": AUTH_USER, "password": AUTH_PASS, "next": "/admin"}).encode('utf-8')
    status, _, _ = do_req("/login", data=login_body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert status in (200, 302), f"ログイン失敗: {status}"
    print("1. ログイン認証: 成功")

    # 2. デモ音声再生ページ (/demo) の検証
    # (a) デフォルトアクセス
    status, body_bytes, _ = do_req("/demo")
    assert status == 200, f"/demo 取得失敗: {status}"
    html = body_bytes.decode('utf-8')
    assert "btnModePng" in html, "btnModePng が HTML に存在しません"
    assert "btnModeJpeg" in html, "btnModeJpeg が HTML に存在しません"
    assert "turbo_256_256.wav" in html, "JPEG用音声 turbo_256_256.wav が HTML に含まれていません"
    assert "turbo_png_256_256.wav" in html, "PNG用音声 turbo_png_256_256.wav が HTML に含まれていません"
    print("2. /demo ページ: モード別音声切り替え機能の存在を確認（PNG/JPEG両音声定義済み）")

    # (b) JPEGモードクエリでのアクセス (?mode=JPEG)
    status, body_bytes, _ = do_req("/demo?mode=JPEG")
    assert status == 200
    html_jpeg = body_bytes.decode('utf-8')
    assert "段階的復元用テスト音声" in html_jpeg, "JPEGモードのラベルが表示されていません"
    print("   -> /demo?mode=JPEG: JPEG用テスト音声表示を確認")

    # 3. 管理画面 (/admin) および API (/admin/api/images) の検証
    status, body_bytes, _ = do_req("/admin/api/images?mode=ALL")
    assert status == 200, f"/admin/api/images 取得失敗: {status}"
    api_data = json.loads(body_bytes.decode('utf-8'))
    assert api_data.get('status') == 'success'
    images = api_data.get('images', [])
    print(f"3. 管理画面API (/admin/api/images): {len(images)} 件の画像データを取得")
    for img in images:
        thumb = img.get('thumbnail_url')
        if thumb:
            assert "?v=" in thumb, f"サムネイルURLにキャッシュ回避タイムスタンプが付与されていません: {thumb}"
            print(f"   -> 画像 0x{img.get('image_id_hex')} サムネイルURL（キャッシュバスター付き）: {thumb}")

    print("\n==================================================")
    print("🎉 すべての修正項目の検証に合格しました！")
    print("==================================================")

if __name__ == "__main__":
    run_checks()
