import os
import sys

# テスト時はBasic認証をスキップ
os.environ['DISABLE_BASIC_AUTH'] = '1'

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from web_turbo_png.app import app
from web_turbo_png.services.auth_db import get_auth_db
from web_turbo_png.routes.api_routes import get_analyzer
from digital_turbo_png.database_turbo import PacketDatabaseTurboPNG
import digital_turbo_png.config_turbo as config

def test_admin_insights():
    print("=== 管理者ダッシュボード 高度インサイト & 管制機能のテスト開始 ===")
    
    auth_db = get_auth_db()
    admin_email = "koseikazu@icloud.com"
    contributor_1 = "station_alpha@example.com"
    contributor_2 = "station_bravo@example.com"
    
    admin_id = auth_db.create_user(admin_email, "123456789") or auth_db.verify_user(admin_email, "123456789")
    user1_id = auth_db.create_user(contributor_1, "password123") or auth_db.verify_user(contributor_1, "password123")
    user2_id = auth_db.create_user(contributor_2, "password123") or auth_db.verify_user(contributor_2, "password123")
    
    # 1. テストパケットの注入（異なるSNR・ユーザーで作成）
    log_dir = os.path.join(PROJECT_ROOT, config.TEXT_LOG_DIR)
    pkt_db = PacketDatabaseTurboPNG(log_dir)
    
    test_img_id = int("FACE", 16)
    
    # User 1: 高SNRパケットを3つ
    pkt_db.insert_packets_bulk("test_station_alpha.txt", [
        (test_img_id, 0, 0, 10, "1" * 80, 18.5),
        (test_img_id, 1, 0, 10, "0" * 80, 16.0),
        (test_img_id, 2, 0, 10, "1" * 80, 15.2),
    ], user_id=user1_id)
    
    # User 2: 中〜低SNRパケットを2つ
    pkt_db.insert_packets_bulk("test_station_bravo.txt", [
        (test_img_id, 0, 1, 10, "1" * 80, 11.0),
        (test_img_id, 1, 1, 10, "0" * 80, 6.5),
    ], user_id=user2_id)
    
    pkt_db.close()
    
    analyzer = get_analyzer()
    
    # 2. SNR アナリティクス検証
    print("1. SNR (電波品質) 集計検証...")
    snr_data = analyzer.get_snr_analytics()
    print(f"✅ SNR 集計結果: avg={snr_data['avg_snr']}dB, cond={snr_data['condition_label']}")
    assert int(snr_data['total_packets']) >= 5
    assert float(snr_data['avg_snr']) > 0.0
    assert int(snr_data['count_high']) >= 3
    assert int(snr_data['count_mid']) >= 1
    assert int(snr_data['count_low']) >= 1
    
    # 3. ユーザー貢献ランキング検証
    print("2. ユーザー貢献ランキング検証...")
    contributors = analyzer.get_top_contributors(limit=5)
    print(f"✅ トップ貢献者: {[(c['email'], c['packet_count']) for c in contributors]}")
    assert len(contributors) >= 2
    assert contributors[0]['packet_count'] >= contributors[1]['packet_count']
    assert any(c['email'] == contributor_1 for c in contributors)
    
    # 4. パケットトラフィック時系列検証
    print("3. パケットトラフィック時系列データ検証...")
    traffic_today = analyzer.get_hourly_packet_traffic(period='today')
    assert len(traffic_today['labels']) == 24
    assert sum(traffic_today['packets']) >= 5
    print("✅ パケットトラフィック 24時間データ生成合格")
    
    # 5. システム健全度 & ストレージメトリクス検証
    print("4. システムメトリクス検証...")
    sys_metrics = analyzer.get_system_health_metrics()
    assert sys_metrics['db_size_mb'] >= 0.0
    assert sys_metrics['total_packets'] >= 5
    print(f"✅ ストレージメトリクス: {sys_metrics}")
    
    # 6. 管理画面 & 各種APIの検証
    print("5. 管理者ダッシュボード API & DB最適化検証...")
    with app.test_client() as client_admin:
        client_admin.post('/login', data={'email': admin_email, 'password': '123456789'})
        
        # HTML 描画
        res_page = client_admin.get('/admin')
        assert res_page.status_code == 200
        html = res_page.get_data(as_text=True)
        assert 'Active Decoder Engine' in html or 'エンジン切替' in html
        assert '受信画像データ管理' in html or '画像管理' in html
        assert 'すべての画像をクリア' in html or '全クリア' in html
        print("✅ 管理画面 HTML 完全描画合格")
        
        # DB VACUUM 最適化 API
        res_opt = client_admin.post('/admin/api/optimize_db')
        assert res_opt.status_code == 200
        opt_data = res_opt.get_json()
        assert opt_data['status'] == 'success'
        print("✅ DB 最適化 API 合格:", opt_data['message'])
        
        # キャッシュクリア API
        res_cache = client_admin.post('/admin/api/clear_cache')
        assert res_cache.status_code == 200
        cache_data = res_cache.get_json()
        assert cache_data['status'] == 'success'
        print("✅ キャッシュクリア API 合格:", cache_data['message'])

        # トラフィック含む activity_stats API
        res_stats = client_admin.get('/admin/api/activity_stats?period=today')
        assert res_stats.status_code == 200
        stats_json = res_stats.get_json()
        assert 'packet_traffic' in stats_json
        assert len(stats_json['packet_traffic']['packets']) == 24
        print("✅ activity_stats (パケットトラフィック複合) レスポンス合格")
        
        # クリーンアップ（テスト画像削除）
        analyzer.delete_images([f"{test_img_id:04X}"])

    print("\n🎉 管理者ダッシュボード 高度インサイト & 管制機能の全テストに合格しました！")

if __name__ == '__main__':
    test_admin_insights()
