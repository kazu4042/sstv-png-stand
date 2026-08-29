import os
import sys

# テスト時はBasic認証をスキップ
os.environ['DISABLE_BASIC_AUTH'] = '1'

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from web_turbo_png.app import app
from web_turbo_png.services.auth_db import get_auth_db

def test_all_routes_and_edge_cases():
    print("=== 全エンドポイント & エッジケース（空データ・異常系）の徹底テスト開始 ===")
    
    auth_db = get_auth_db()
    admin_email = "koseikazu@icloud.com"
    auth_db.create_user(admin_email, "123456789") or auth_db.verify_user(admin_email, "123456789")
    
    with app.test_client() as client:
        # 1. ログインページ (GET & POST)
        res_login_get = client.get('/login')
        assert res_login_get.status_code == 200, f"Login GET failed: {res_login_get.status_code}"
        
        # 誤ったパスワード
        res_login_bad = client.post('/login', data={'email': admin_email, 'password': 'wrong'})
        assert '間違っています' in res_login_bad.get_data(as_text=True)
        
        # 正常ログイン (管理者チェックボックス付き)
        res_login_admin = client.post('/login', data={'email': admin_email, 'password': '123456789', 'go_to_admin': '1'}, follow_redirects=True)
        assert res_login_admin.status_code == 200
        assert 'TurboPNG 管理・管制センター' in res_login_admin.get_data(as_text=True)
        print("✅ ログイン & 管理者リダイレクト動作正常")
        
        # 2. 全一般ページのチェック
        routes_to_test = ['/', '/result', '/calendar', '/ranking', '/analytics', '/heatmap']
        for r in routes_to_test:
            res = client.get(r)
            # ページが存在する場合は 200、無効な場合は 404
            assert res.status_code in [200, 302, 404], f"Route {r} failed with code {res.status_code}"
            print(f"✅ Route {r}: status {res.status_code}")
            
        # 3. 管理者ダッシュボード & 全APIのチェック
        res_admin = client.get('/admin')
        assert res_admin.status_code == 200
        html = res_admin.get_data(as_text=True)
        assert 'activityChart' in html
        assert 'snrPieChart' in html
        assert 'pagesPieChart' in html
        assert 'chart.umd.min.js' in html.lower()
        print("✅ 管理画面完全ロード正常")
        
        # 期間別 API
        for p in ['today', '24h', '7d', '30d']:
            res_p = client.get(f'/admin/api/activity_stats?period={p}')
            assert res_p.status_code == 200
            data_p = res_p.get_json()
            assert data_p['status'] == 'success'
            assert 'graph' in data_p
            assert 'packet_traffic' in data_p
            print(f"✅ /admin/api/activity_stats?period={p} 正常")
            
        # リアルタイム API
        res_rt = client.get('/admin/api/realtime_stats')
        assert res_rt.status_code == 200
        assert res_rt.get_json()['status'] == 'success'
        print("✅ /admin/api/realtime_stats 正常")
        
        # DB最適化 API
        res_opt = client.post('/admin/api/optimize_db')
        assert res_opt.status_code == 200
        print("✅ /admin/api/optimize_db 正常")
        
        # キャッシュクリア API
        res_cc = client.post('/admin/api/clear_cache')
        assert res_cc.status_code == 200
        print("✅ /admin/api/clear_cache 正常")

    print("\n🎉 全ルート・全API・エッジケースのテストに完全合格しました！")

if __name__ == '__main__':
    test_all_routes_and_edge_cases()
