import os
import sys

ROOT_DIR = os.path.abspath(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from web_turbo_png.app import app
from core.system_factory import SystemFactory


def test_engine_switch_api():
    print("=== 管理者エンジン切替 API (PNG ↔ JPEG) のテスト開始 ===")
    app.config['TESTING'] = True
    client = app.test_client()

    admin_email = "koseikazu@icloud.com"

    with client.session_transaction() as sess:
        sess['basic_auth_passed'] = True
        sess['user_id'] = 1
        sess['email'] = admin_email
        sess['display_name'] = "Admin"

    # 1. 初期モード確認 (GET)
    res = client.get('/admin/api/engine_mode')
    assert res.status_code == 200, f"Failed GET engine_mode: {res.status_code}"
    data = res.get_json()
    print(f"✅ 初期モード取得: {data['mode']}")

    # 2. JPEGモードへ切り替え (POST)
    res_post = client.post('/admin/api/engine_mode', json={'mode': 'JPEG'})
    assert res_post.status_code == 200
    assert res_post.get_json()['mode'] == 'JPEG'
    assert SystemFactory.get_mode() == 'JPEG'
    print("✅ API経由での JPEG モード切替合格")

    # 3. PNGモードへ切り替え (POST)
    res_post2 = client.post('/admin/api/engine_mode', json={'mode': 'PNG'})
    assert res_post2.status_code == 200
    assert res_post2.get_json()['mode'] == 'PNG'
    assert SystemFactory.get_mode() == 'PNG'
    print("✅ API経由での PNG モード切替復帰合格")

    print("\n🎉 管理者エンジン切替 API の全テストに合格しました！")


if __name__ == "__main__":
    test_engine_switch_api()
