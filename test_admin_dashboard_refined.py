import os
import sys

ROOT_DIR = os.path.abspath(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from web_turbo_png.app import app
from core.system_factory import SystemFactory


def test_refined_admin():
    print("=== 洗練された管理者ダッシュボード & API の総合テスト開始 ===")
    app.config['TESTING'] = True
    client = app.test_client()

    admin_email = "koseikazu@icloud.com"

    with client.session_transaction() as sess:
        sess['basic_auth_passed'] = True
        sess['user_id'] = 1
        sess['email'] = admin_email
        sess['display_name'] = "Admin"

    # 1. 管理者ダッシュボードのHTMLレンダリングテスト
    res = client.get('/admin')
    assert res.status_code == 200, f"Dashboard GET failed: {res.status_code}"
    html = res.get_data(as_text=True)
    assert "Active Decoder Engine 切替" in html, "エンジン切替セクションが見つかりません"
    assert "受信画像データ管理・クリア" in html, "画像管理クリアセクションが見つかりません"
    assert "すべての画像をクリア" in html, "全クリアボタンが見つかりません"
    # 不要な要素が削除されているかの検証
    assert "累計復元画像数" not in html, "削除されたはずの旧統計カードが残っています"
    assert "リアルタイムアクセス動向" not in html, "削除されたはずの旧アクセス解析が残っています"
    assert "電波受信品質 (SNR) 解析" not in html, "削除されたはずの旧SNRタブが残っています"
    print("✅ 1. 管理者ダッシュボード HTML レンダリング & 不要機能排除確認 合格")

    # 2. 画像一覧取得 API テスト
    res_images = client.get('/admin/api/images')
    assert res_images.status_code == 200
    images_data = res_images.get_json()
    assert images_data['status'] == 'success'
    assert 'images' in images_data
    assert 'current_engine_mode' in images_data
    print(f"✅ 2. 画像一覧取得 API (/admin/api/images) 合格 (取得件数: {images_data['count']})")

    # 3. エンジン切替 API テスト (PNG -> JPEG -> PNG)
    # JPEGへ切り替え
    res_jpeg = client.post('/admin/api/engine_mode', json={'mode': 'JPEG'})
    assert res_jpeg.status_code == 200
    assert res_jpeg.get_json()['mode'] == 'JPEG'
    assert SystemFactory.get_mode() == 'JPEG'

    # 画像一覧APIを再確認（JPEGモードが反映されているか）
    res_images_jpeg = client.get('/admin/api/images')
    assert res_images_jpeg.get_json()['current_engine_mode'] == 'JPEG'
    print("✅ 3-1. JPEGモードへの切替 & API同期 合格")

    # PNGへ切り替え
    res_png = client.post('/admin/api/engine_mode', json={'mode': 'PNG'})
    assert res_png.status_code == 200
    assert res_png.get_json()['mode'] == 'PNG'
    assert SystemFactory.get_mode() == 'PNG'
    print("✅ 3-2. PNGモードへの切替復帰 合格")

    # 4. 単一/選択画像削除 API テスト (ダミー画像IDでテスト)
    res_del = client.post('/admin/delete_images', json={'image_ids': ['FFFF']})
    assert res_del.status_code == 200
    del_data = res_del.get_json()
    assert del_data['status'] == 'success'
    print("✅ 4. 画像削除 API (/admin/delete_images) 合格")

    # 5. 全画像クリア API テスト
    res_clear_all = client.post('/admin/clear_all_images')
    assert res_clear_all.status_code == 200
    clear_data = res_clear_all.get_json()
    assert clear_data['status'] == 'success'
    assert '全画像データを一括クリア' in clear_data['message']
    print(f"✅ 5. 全画像クリア API (/admin/clear_all_images) 合格: {clear_data['message']}")

    # 6. 一般ユーザー（非管理者）アクセスの権限テスト
    with client.session_transaction() as sess:
        sess['email'] = 'normal_user@example.com'

    res_unauth = client.get('/admin')
    assert res_unauth.status_code in [302, 403], f"一般ユーザーが管理画面に入れています: {res_unauth.status_code}"

    res_unauth_api = client.post('/admin/api/engine_mode', json={'mode': 'JPEG'})
    assert res_unauth_api.status_code == 403, "一般ユーザーがエンジン切替できてしまいます"

    res_unauth_clear = client.post('/admin/clear_all_images')
    assert res_unauth_clear.status_code == 403, "一般ユーザーが画像クリアできてしまいます"
    print("✅ 6. 非管理者権限ガード (セキュリティ保護) 合格")

    print("\n🎉 全てのテストケースが完全にパスしました！")


if __name__ == "__main__":
    test_refined_admin()
