import os
import sys
import time

# テスト時はBasic認証をスキップ
os.environ['DISABLE_BASIC_AUTH'] = '1'

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from web_turbo_png.app import app
from web_turbo_png.services.auth_db import get_auth_db

def test_activity_stats_and_graph():
    print("=== アクティブ数・時間帯別アクセス統計機能のテスト開始 ===")
    
    auth_db = get_auth_db()
    admin_email = "koseikazu@icloud.com"
    normal_email = "test_user_activity@example.com"
    
    admin_id = auth_db.create_user(admin_email, "123456789") or auth_db.verify_user(admin_email, "123456789")
    normal_id = auth_db.create_user(normal_email, "password123") or auth_db.verify_user(normal_email, "password123")
    
    # 1. アクセスログの記録テスト
    print("1. ページアクセスによる access_logs 記録テスト...")
    with app.test_client() as client:
        # ログイン
        client.post('/login', data={'email': normal_email, 'password': 'password123'})
        
        # 複数ページへアクセス
        res1 = client.get('/')
        assert res1.status_code == 200
        
        res2 = client.get('/calendar')
        assert res2.status_code == 200
        
        res3 = client.get('/result')
        assert res3.status_code == 200

    # DB内のログ確認
    recent_logs = auth_db.get_recent_access_logs(limit=10)
    assert len(recent_logs) >= 3, f"アクセスログが記録されていません: {len(recent_logs)}"
    endpoints = [l['endpoint'] for l in recent_logs]
    print(f"✅ 直近アクセスログ確認: {endpoints[:3]}")
    
    # 2. 概要統計（Overview Stats）の確認
    overview = auth_db.get_today_overview_stats()
    print(f"✅ 本日概要統計: {overview}")
    assert overview['today_pv'] >= 3
    assert overview['today_uu'] >= 1
    assert overview['active_now'] >= 1
    
    # 3. グラフデータ生成テスト (today, 24h, 7d, 30d)
    print("2. グラフ集計データ生成テスト...")
    graph_today = auth_db.get_activity_graph_data(period='today')
    assert len(graph_today['labels']) == 24, "today の時間軸が24時間分ありません"
    assert len(graph_today['pv']) == 24
    assert len(graph_today['uu']) == 24
    print("✅ 'today' グラフデータ生成合格 (24時間スロット)")
    
    graph_24h = auth_db.get_activity_graph_data(period='24h')
    assert len(graph_24h['labels']) == 24
    print("✅ '24h' グラフデータ生成合格")
    
    graph_7d = auth_db.get_activity_graph_data(period='7d')
    assert len(graph_7d['labels']) == 7
    print("✅ '7d' グラフデータ生成合格")
    
    graph_30d = auth_db.get_activity_graph_data(period='30d')
    assert len(graph_30d['labels']) == 30
    print("✅ '30d' グラフデータ生成合格")
    
    # 4. 管理者ダッシュボード API テスト
    print("3. 管理画面 API エンドポイント検証...")
    # 一般ユーザーでのアクセス拒否 (403)
    with app.test_client() as client_normal:
        client_normal.post('/login', data={'email': normal_email, 'password': 'password123'})
        res_forbidden = client_normal.get('/admin/api/activity_stats')
        assert res_forbidden.status_code == 403
        print("✅ 一般ユーザーからの統計APIアクセス拒否 (403) 合格")
        
    # 管理者でのアクセス成功 (200)
    with app.test_client() as client_admin:
        client_admin.post('/login', data={'email': admin_email, 'password': '123456789'})
        
        # HTMLダッシュボード表示確認
        res_admin_page = client_admin.get('/admin')
        assert res_admin_page.status_code == 200
        html = res_admin_page.get_data(as_text=True)
        assert 'activityChart' in html
        assert '時間帯別アクティブ・閲覧者数推移' in html
        assert 'pagesPieChart' in html
        print("✅ 管理者ダッシュボード (/admin) HTML 描画合格")
        
        # グラフデータAPI
        res_api = client_admin.get('/admin/api/activity_stats?period=today')
        assert res_api.status_code == 200
        api_data = res_api.get_json()
        assert api_data['status'] == 'success'
        assert len(api_data['graph']['labels']) == 24
        print("✅ 管理者 /admin/api/activity_stats レスポンス合格")
        
        # リアルタイムAPI
        res_realtime = client_admin.get('/admin/api/realtime_stats')
        assert res_realtime.status_code == 200
        rt_data = res_realtime.get_json()
        assert rt_data['status'] == 'success'
        assert 'active_now' in rt_data['overview']
        assert 'recent_logs' in rt_data
        print("✅ 管理者 /admin/api/realtime_stats レスポンス合格")

    print("\n🎉 アクティブ数・時間帯別アクセス統計機能の全テストに合格しました！")

if __name__ == '__main__':
    test_activity_stats_and_graph()
