import os
import sys
import time

ROOT_DIR = os.path.abspath(os.path.dirname(__file__))
WAV_PATH = os.path.join(ROOT_DIR, "data", "digital_turbo_png", "audio", "test_short.wav")

def test_upload_flow():
    print("=== 音声ファイルアップロード & ジョブ進捗 E2E テスト開始 ===")

    from web_turbo_png.app import app
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['basic_auth_passed'] = True
        sess['user_id'] = 1
        sess['email'] = 'koseikazu@icloud.com'

    # 1. アップロード API テスト
    with open(WAV_PATH, 'rb') as f:
        upload_res = client.post('/api/upload', data={
            'audio': (f, 'test_short.wav')
        }, content_type='multipart/form-data')

    assert upload_res.status_code == 200, f"Upload failed: {upload_res.status_code}"
    upload_data = upload_res.get_json()
    print("✅ 1. アップロード成功:", upload_data)
    assert upload_data['success'] is True
    job_id = upload_data['job_id']
    assert job_id, "Job ID is empty!"

    # 2. 進捗確認（Job not found にならず、progress が取得できること）
    max_wait = 30
    start_time = time.time()
    finished = False
    last_prog = -1

    while time.time() - start_time < max_wait:
        prog_res = client.get(f'/api/progress?job_id={job_id}')
        assert prog_res.status_code == 200, f"Progress API failed: {prog_res.status_code}"
        prog_data = prog_res.get_json()
        assert "error" not in prog_data or not prog_data["error"], f"Job error: {prog_data.get('error')}"

        prog = prog_data.get("progress", 0)
        status = prog_data.get("status", "")
        if prog != last_prog:
            print(f"  進捗更新: {prog}% ({status})")
            last_prog = prog

        if prog >= 100 or prog_data.get("finished"):
            finished = True
            break
        time.sleep(0.5)

    assert finished, "デコード処理が制限時間内に完了しませんでした"
    print("✅ 2. ジョブ進行 & 100% 完了確認 合格！ (Job not found エラーなし)")

    # 3. 結果画面へアクセス (/result?job_id=...)
    res_page = client.get(f'/result?job_id={job_id}')
    assert res_page.status_code == 200, f"Result page failed: {res_page.status_code}"
    html = res_page.get_data(as_text=True)
    assert "復元結果" in html or "SSTV Aggregator" in html
    print("✅ 3. 結果画面の正常表示 合格！")

    print("\n🎉 アップロードおよび進捗管理のE2Eテストに完全合格しました！")

if __name__ == "__main__":
    test_upload_flow()
