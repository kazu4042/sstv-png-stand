import os
import sys
import time
import json
import io

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ['DISABLE_BASIC_AUTH'] = '1'

from web_turbo_png.app import app
from web_turbo_png.services.auth_db import get_auth_db
from core.system_factory import SystemFactory

def test_smartphone_upload_e2e():
    print("=== スマホ録音音声 E2E アップロード＆デコード解析検証開始 ===")

    # 1. ユーザーアカウント確認
    auth_db = get_auth_db()
    admin_email = "koseikazu@icloud.com"
    admin_pass = "123456789"
    admin_id = auth_db.verify_user(admin_email, admin_pass)
    if not admin_id:
        admin_id = auth_db.create_user(admin_email, admin_pass)
    print(f"  [OK] アカウント確認: {admin_email} (ID: {admin_id})")

    # 2. JPEG モードに設定
    SystemFactory.set_mode("JPEG")
    print(f"  [OK] 現在のモード: {SystemFactory.get_mode()}")

    # 3. テスト用スマホ録音シミュレーション音声ファイルを確認
    sim_wav = os.path.join(PROJECT_ROOT, "test_short_sim_smartphone.wav")
    assert os.path.exists(sim_wav), f"テスト音声が見つかりません: {sim_wav}"
    file_size = os.path.getsize(sim_wav)
    print(f"  [OK] テスト音声確認: {sim_wav} (サイズ: {file_size} bytes)")

    with app.test_client() as client:
        # 4. ログイン
        res_login = client.post('/login', data={'email': admin_email, 'password': admin_pass})
        assert res_login.status_code in [200, 302], f"ログイン失敗: {res_login.status_code}"
        print("  [OK] ログインセッション確立")

        # 5. 音声アップロード実行
        with open(sim_wav, "rb") as f:
            audio_io = io.BytesIO(f.read())

        res_upload = client.post('/api/upload', data={
            'file': (audio_io, "smartphone_recording.wav", "audio/wav")
        }, content_type='multipart/form-data')

        assert res_upload.status_code == 200, f"アップロード失敗: {res_upload.status_code}, {res_upload.data}"
        upload_data = json.loads(res_upload.data.decode('utf-8'))
        assert upload_data.get("success") is True, f"アップロード不正: {upload_data}"
        job_id = upload_data["job_id"]
        print(f"  [OK] アップロード受付成功 (job_id: {job_id})")

        # 6. バックグラウンド処理の進捗モニタリング
        print("  -> バックグラウンド進捗を追跡中...")
        start_t = time.time()
        prog_history = []
        last_prog = -1

        while True:
            res_prog = client.get(f'/api/progress?job_id={job_id}')
            assert res_prog.status_code == 200
            job = json.loads(res_prog.data.decode('utf-8'))

            cur_prog = job.get("progress", 0)
            status_msg = job.get("status", "")
            err = job.get("error")

            if cur_prog != last_prog:
                print(f"    [Prog] {cur_prog}% - {status_msg}")
                prog_history.append(cur_prog)
                last_prog = cur_prog

            if err:
                print(f"  [ERROR] ジョブエラー発生: {err}")
                sys.exit(1)

            if cur_prog >= 100:
                print(f"  [OK] 解析完了！ (所要時間: {time.time() - start_t:.2f}秒)")
                result_data = job.get("result_data", {})
                print(f"    復元画像ID: {result_data.get('image_id')}")
                print(f"    受信パケット数: {result_data.get('packets_received')} / {result_data.get('total_required')}")
                print(f"    ユーザー画像URL: {result_data.get('user_image_url')}")
                print(f"    多数決復元画像URL: {result_data.get('network_image_url')}")
                break

            time.sleep(0.3)
            if time.time() - start_t > 60:
                print("  [TIMEOUT] 60秒以内に完了しませんでした")
                sys.exit(1)

        # 7. 進捗通知の細かさを検証（途中で固まらず複数回の進捗が通知されたか）
        print(f"  [OK] 進捗履歴: {prog_history} (合計 {len(prog_history)} 回の進捗更新を確認)")
        assert len(prog_history) >= 4, f"進捗通知が少なすぎます: {prog_history}"

        # 8. 結果画面へのアクセス確認
        res_result = client.get(f'/result?job_id={job_id}')
        assert res_result.status_code == 200, f"結果ページアクセス失敗: {res_result.status_code}"
        print("  [OK] 結果ページアクセス確認完了 (Status: 200)")

    print("\n🎉 スマホ録音音声のE2Eアップロード＆デコード解析検証に100%合格しました！")

if __name__ == "__main__":
    test_smartphone_upload_e2e()
