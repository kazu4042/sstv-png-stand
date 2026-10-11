import os
import sys

os.environ['DISABLE_BASIC_AUTH'] = '1'
ROOT = os.path.abspath('.')
sys.path.insert(0, ROOT)

from web_turbo_png.app import app
from web_turbo_png.services.auth_db import get_auth_db

def test_admin_submissions_e2e():
    print("=== 管理者ダッシュボード 音声投稿＆画像復元監視機能 E2Eテスト開始 ===")
    
    auth_db = get_auth_db()
    admin_email = "koseikazu@icloud.com"
    auth_db.create_user(admin_email, "123456789")

    with app.test_client() as client:
        # 1. 未ログインでのAPIアクセス拒否
        res_unauth = client.get('/admin/api/submissions')
        assert res_unauth.status_code in (302, 401, 403), f"Expected auth block, got {res_unauth.status_code}"
        print("✅ 1. 未ログインでの不正アクセス遮断確認")

        # 2. 一般ユーザー（非管理者）でのAPIアクセス拒否
        general_email = "test_general_user@example.com"
        auth_db.create_user(general_email, "password123")
        client.post('/login', data={'email': general_email, 'password': 'password123'})
        res_forbidden = client.get('/admin/api/submissions')
        assert res_forbidden.status_code == 403, f"Expected 403 Forbidden, got {res_forbidden.status_code}"
        client.get('/logout')
        print("✅ 2. 一般ユーザーのアクセス拒絶 (403 Forbidden) 確認")

        # 3. 管理者ログイン
        login_res = client.post('/login', data={'email': admin_email, 'password': '123456789'})
        assert login_res.status_code == 302
        print("✅ 3. 管理者ログイン成功")

        # 4. /admin 画面のHTML描画
        admin_res = client.get('/admin')
        assert admin_res.status_code == 200
        html = admin_res.get_data(as_text=True)
        assert 'ユーザー音声投稿 & 画像復元ログ' in html
        assert 'section-submissions' in html
        assert 'filter-submission-user' in html
        assert 'stat-total-submissions' in html
        assert 'stat-unique-users' in html
        assert 'stat-total-packets' in html
        assert 'stat-avg-score' in html
        assert 'reloadSubmissions' in html
        print("✅ 4. 管理者画面 HTML への新セクション完全描画確認")

        # 5. /admin/api/submissions 全件取得
        api_res = client.get('/admin/api/submissions')
        assert api_res.status_code == 200
        data = api_res.get_json()
        assert data.get('status') == 'success'
        assert data.get('count', 0) > 0
        submissions = data.get('submissions', [])
        stats = data.get('stats', {})
        print(f"✅ 5. API取得成功: 投稿件数={data['count']}, 参加アカウント数={stats.get('unique_users')}, 総パケット={stats.get('total_packets')}")

        # 6. アカウント別フィルター (user_id=1, guest)
        res_u1 = client.get('/admin/api/submissions?user_id=1')
        assert res_u1.status_code == 200
        u1_data = res_u1.get_json()
        assert all(s.get('user_id') == 1 for s in u1_data.get('submissions', []))
        print(f"✅ 6. アカウント別フィルター (user_id=1) 動作確認: {u1_data['count']}件")

        res_guest = client.get('/admin/api/submissions?user_id=guest')
        assert res_guest.status_code == 200
        guest_data = res_guest.get_json()
        assert all(s.get('user_id') is None for s in guest_data.get('submissions', []))
        print(f"✅ 7. ゲスト局フィルター (user_id=guest) 動作確認: {guest_data['count']}件")

        # 7. モード別フィルター (JPEG, PNG)
        res_jpeg = client.get('/admin/api/submissions?mode=JPEG')
        assert res_jpeg.status_code == 200
        jpeg_data = res_jpeg.get_json()
        assert all(s.get('mode') == 'JPEG' for s in jpeg_data.get('submissions', []))
        print(f"✅ 8. エンジンモードフィルター (JPEG) 動作確認: {jpeg_data['count']}件")

        # 8. キーワード検索
        if submissions:
            target_sub = submissions[0]
            kw = target_sub['image_id_hex']
            res_search = client.get(f'/admin/api/submissions?q={kw}')
            assert res_search.status_code == 200
            search_data = res_search.get_json()
            assert search_data['count'] > 0
            assert any(s['image_id_hex'] == kw for s in search_data['submissions'])
            print(f"✅ 9. キーワード検索 (q={kw}) 動作確認: {search_data['count']}件ヒット")

    print("\n🎉 音声投稿＆画像復元監視機能の全E2E検証に完全合格しました！")

if __name__ == '__main__':
    test_admin_submissions_e2e()
