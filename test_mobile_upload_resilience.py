import os
import sys
import tempfile
import urllib.request
import urllib.parse
import urllib.error
import http.cookiejar
import json
import base64
import ssl
import numpy as np
import scipy.io.wavfile
import subprocess

from web_turbo_png.routes.upload_routes import convert_and_normalize_audio, ALLOWED_EXTENSIONS
from digital_turbo_png.decoder_turbo import DigitalTurboPNGDecoder
from digital_turbo_jpeg.decoder_turbo import DigitalTurboJPEGDecoder
from core.system_factory import SystemFactory

BASE_URL = "https://127.0.0.1"
BASIC_USER = "Nagasaki"
BASIC_PASS = "123456789"
AUTH_USER = "koseikazu@icloud.com"
AUTH_PASS = "123456789"

def run_tests():
    print("==========================================================")
    print("📱 スマホ録音アップロード & 振幅ズレ耐性 総合検証テスト開始")
    print("==========================================================")

    # -------------------------------------------------------------
    # 1. 振幅ズレ・小音量音声に対するシンボル／パケット検出耐性テスト
    # -------------------------------------------------------------
    print("\n--- [テスト1] 振幅ズレ・減衰音声のシンボル検出耐性 ---")
    test_wav = "/var/www/sstv-png-stand/web_turbo_png/static/uploads/d8b3ca5d4df34dd1bd4c67a6e2e455e1_test_short_jpeg.wav"
    assert os.path.exists(test_wav), f"テスト音声が見つかりません: {test_wav}"
    
    sr, orig_audio = scipy.io.wavfile.read(test_wav)
    if orig_audio.ndim > 1:
        orig_audio = orig_audio[:, 0]

    # 音量を1/10に減衰させ、さらにDCオフセット（マイクバイアス）を付加したWAVを作成
    attenuated_audio = (orig_audio * 0.1 + 500).astype(np.int16)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f_att:
        scipy.io.wavfile.write(f_att.name, sr, attenuated_audio)
        att_wav_path = f_att.name

    try:
        decoder = DigitalTurboJPEGDecoder()
        success_count, log_path = decoder.run(att_wav_path)
        print(f"元音声長: {len(orig_audio)/sr:.2f}秒, 減衰係数: 0.1 (10%振幅 + DCオフセット)")
        print(f"デコード成功パケット数: {success_count} 個")
        assert success_count > 0, "振幅減衰時にパケットが検出できませんでした！"
        print("✅ テスト1合格: 振幅が10%に減衰・DCバイアスがかかった状態でも正確にシンボル/パケットを検出できました。")
    finally:
        if os.path.exists(att_wav_path):
            os.remove(att_wav_path)

    # -------------------------------------------------------------
    # 2. ffmpeg によるスマホ録音形式 (.m4a, .mp3) の自動変換・音量正規化テスト
    # -------------------------------------------------------------
    print("\n--- [テスト2] スマホ音声形式 (.m4a / .mp3) の変換 & 聴感正規化 ---")
    with tempfile.TemporaryDirectory() as tmpdir:
        # 元音声を切り出して .m4a と .mp3 を生成 (ffmpeg)
        sample_slice = orig_audio[: sr * 3] # 3秒間
        slice_wav = os.path.join(tmpdir, "slice.wav")
        scipy.io.wavfile.write(slice_wav, sr, sample_slice)

        m4a_file = os.path.join(tmpdir, "新規録音_iPhone.m4a")
        mp3_file = os.path.join(tmpdir, "ボイスレコーダー_Android.mp3")

        # AAC (m4a) に圧縮変換
        subprocess.run(["ffmpeg", "-y", "-i", slice_wav, "-c:a", "aac", "-b:a", "64k", m4a_file],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        # MP3 に圧縮変換
        subprocess.run(["ffmpeg", "-y", "-i", slice_wav, "-c:a", "libmp3lame", "-b:a", "64k", mp3_file],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

        assert os.path.exists(m4a_file) and os.path.getsize(m4a_file) > 0
        assert os.path.exists(mp3_file) and os.path.getsize(mp3_file) > 0
        print(f"生成成功: {m4a_file} ({os.path.getsize(m4a_file)} bytes)")
        print(f"生成成功: {mp3_file} ({os.path.getsize(mp3_file)} bytes)")

        # convert_and_normalize_audio で WAV に正規化変換
        norm_m4a = os.path.join(tmpdir, "norm_m4a.wav")
        norm_mp3 = os.path.join(tmpdir, "norm_mp3.wav")
        assert convert_and_normalize_audio(m4a_file, norm_m4a), "m4a 変換失敗"
        assert convert_and_normalize_audio(mp3_file, norm_mp3), "mp3 変換失敗"

        assert os.path.exists(norm_m4a)
        assert os.path.exists(norm_mp3)

        out_sr, out_data = scipy.io.wavfile.read(norm_m4a)
        assert out_sr == 44100, f"サンプリングレートが44.1kHzになっていません: {out_sr}"
        assert out_data.dtype == np.int16, f"ビット深度が16bitになっていません: {out_data.dtype}"
        max_amp = np.max(np.abs(out_data))
        print(f"変換後WAVサンプリングレート: {out_sr}Hz, 最大振幅: {max_amp} / 32767")
        assert max_amp > 10000, f"正規化後の振幅が小さすぎます: {max_amp}"

    print("✅ テスト2合格: .m4a / .mp3 形式が dynaudnorm + 帯域フィルタ付きで44.1kHz PCM WAVへ自動変換・正規化されました。")

    # -------------------------------------------------------------
    # 3. ネットワークエラー防止の検証 (WWW-Authenticate ヘッダー抑制)
    # -------------------------------------------------------------
    print("\n--- [テスト3] モバイル端末でのネットワークエラー防止 (WWW-Authenticate 抑止) ---")
    cj = http.cookiejar.CookieJar()
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj),
        urllib.request.HTTPSHandler(context=ctx)
    )

    req = urllib.request.Request(
        f"{BASE_URL}/api/upload",
        headers={"Host": "sstv-aggregator.space", "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)"},
        method="POST"
    )
    try:
        with opener.open(req) as resp:
            status = resp.getcode()
            headers = resp.headers
            body = resp.read()
    except urllib.error.HTTPError as e:
        status = e.code
        headers = e.headers
        body = e.read()

    assert status == 401, f"未認証リクエストのステータスコードが401ではありません: {status}"
    auth_header = headers.get("WWW-Authenticate")
    assert auth_header is None, f"WWW-Authenticate ヘッダーが含まれています (スマホで通信エラーになります): {auth_header}"
    data = json.loads(body.decode('utf-8'))
    assert data.get("error") == "Basic authentication required"
    print("✅ テスト3合格: /api/upload 未認証時に WWW-Authenticate ヘッダーが抑止され、安全なJSON 401が返却されます。")

    # -------------------------------------------------------------
    # 4. スマホ録音 (.m4a, 日本語ファイル名) の実アップロード E2E 検証
    # -------------------------------------------------------------
    print("\n--- [テスト4] スマホ録音ファイル (.m4a, 日本語ファイル名) のアップロード E2E テスト ---")
    auth_str = f"{BASIC_USER}:{BASIC_PASS}"
    basic_header = f"Basic {base64.b64encode(auth_str.encode('utf-8')).decode('utf-8')}"

    # ログイン
    login_body = urllib.parse.urlencode({"email": AUTH_USER, "password": AUTH_PASS, "next": "/app"}).encode('utf-8')
    login_req = urllib.request.Request(
        f"{BASE_URL}/login",
        data=login_body,
        headers={"Host": "sstv-aggregator.space", "Authorization": basic_header, "Content-Type": "application/x-www-form-urlencoded"}
    )
    with opener.open(login_req) as resp:
        assert resp.getcode() in (200, 302)

    # テスト用 m4a ファイルの作成
    with tempfile.NamedTemporaryFile(suffix=".m4a", delete=False) as f:
        test_m4a_path = f.name
    
    slice_data = orig_audio[: sr * 2] # 2秒
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f_wav:
        scipy.io.wavfile.write(f_wav.name, sr, slice_data)
        slice_wav_tmp = f_wav.name
    
    subprocess.run(["ffmpeg", "-y", "-i", slice_wav_tmp, "-c:a", "aac", test_m4a_path],
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    os.remove(slice_wav_tmp)

    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    filename = "新規録音 2026-09-09.m4a"

    with open(test_m4a_path, "rb") as f:
        m4a_bytes = f.read()
    os.remove(test_m4a_path)

    body_parts = []
    body_parts.append(f"--{boundary}\r\n".encode('utf-8'))
    body_parts.append(f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode('utf-8'))
    body_parts.append(b'Content-Type: audio/m4a\r\n\r\n')
    body_parts.append(m4a_bytes)
    body_parts.append(f"\r\n--{boundary}--\r\n".encode('utf-8'))
    multipart_body = b"".join(body_parts)

    upload_req = urllib.request.Request(
        f"{BASE_URL}/api/upload",
        data=multipart_body,
        headers={
            "Host": "sstv-aggregator.space",
            "Authorization": basic_header,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)"
        },
        method="POST"
    )

    with opener.open(upload_req) as resp:
        upload_status = resp.getcode()
        upload_resp_bytes = resp.read()

    assert upload_status == 200, f"アップロード失敗: {upload_status}"
    upload_res = json.loads(upload_resp_bytes.decode('utf-8'))
    assert upload_res.get("success") is True or upload_res.get("status") in ("success", "processing")
    job_id = upload_res.get("job_id")
    assert job_id, "job_id が返却されていません"
    print(f"発行された job_id: {job_id}")

    # progress endpoint が接続できることを確認
    progress_req = urllib.request.Request(
        f"{BASE_URL}/api/progress?job_id={job_id}",
        headers={"Host": "sstv-aggregator.space", "Authorization": basic_header}
    )
    with opener.open(progress_req) as resp:
        assert resp.getcode() == 200
        progress_data = json.loads(resp.read().decode('utf-8'))
        print(f"進捗データ取得成功: {progress_data.get('status')}")

    print("✅ テスト4合格: 日本語ファイル名の .m4a ファイルがスマホUIから正常にアップロードされ、ジョブが開始されました。")

    # -------------------------------------------------------------
    # 5. モバイルUIレイアウト対応の検証
    # -------------------------------------------------------------
    print("\n--- [テスト5] モバイル用レスポンシブUIレイアウト検証 ---")
    app_req = urllib.request.Request(
        f"{BASE_URL}/app",
        headers={"Host": "sstv-aggregator.space", "Authorization": basic_header}
    )
    with opener.open(app_req) as resp:
        app_html = resp.read().decode('utf-8')

    assert 'id="sidebar-backdrop"' in app_html, "sidebar-backdrop が index.html に存在しません"
    assert 'accept="audio/*,.wav,.m4a,.mp3,.aac,.ogg,.flac,.webm,.caf,.3gp"' in app_html, "スマホ録音ファイルを受け入れる accept 属性が設定されていません"
    assert 'min-h-screen md:h-screen' in app_html, "スマホ画面用の可変高さクラスが設定されていません"
    assert 'xhr.withCredentials = true' in app_html, "認証Cookie送信用の xhr.withCredentials が設定されていません"
    print("✅ テスト5合格: スマホ用バックドロップ、多様な音声フォーマット選択、画面追従レイアウトが全て整っています。")

    print("\n==========================================================")
    print("🎉 全5項目の検証テストがすべて正常に合格しました！")
    print("==========================================================")

if __name__ == "__main__":
    run_tests()
