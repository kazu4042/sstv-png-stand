import os
import sys

os.environ['DISABLE_BASIC_AUTH'] = '1'

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from web_turbo_png.app import app
from web_turbo_png.services.auth_db import get_auth_db
from web_turbo_png.routes.api_routes import get_analyzer, invalidate_analyzer_cache
from digital_turbo_png.database_turbo import PacketDatabaseTurboPNG
import digital_turbo_png.config_turbo as config

def run_comprehensive_tests():
    print("=== 全シナリオ・ストレステスト開始 ===")
    
    auth_db = get_auth_db()
    admin_email = "koseikazu@icloud.com"
    user_a_email = "user_alpha_test@example.com"
    user_b_email = "user_beta_test@example.com"
    
    admin_id = auth_db.create_user(admin_email, "123456789") or auth_db.verify_user(admin_email, "123456789")
    user_a_id = auth_db.create_user(user_a_email, "pass1234") or auth_db.verify_user(user_a_email, "pass1234")
    user_b_id = auth_db.create_user(user_b_email, "pass1234") or auth_db.verify_user(user_b_email, "pass1234")
    
    # -------------------------------------------------------------
    # シナリオ 1: 未ログイン時のリダイレクト検証
    # -------------------------------------------------------------
    with app.test_client() as anon_client:
        for path in ['/', '/result', '/calendar', '/admin']:
            res = anon_client.get(path)
            assert res.status_code == 302, f"未ログインで {path} にアクセスできてしまいました (status: {res.status_code})"
            assert '/login' in res.headers['Location'], f"{path} が /login にリダイレクトされません"
        print("✅ シナリオ 1 合格: 未ログイン時は必ず /login へ強制誘導")

    # -------------------------------------------------------------
    # シナリオ 2: 新規ユーザー（未投稿）での画面状態
    # -------------------------------------------------------------
    # テスト用パケットを準備 (画像ID: 0x9999)
    log_dir = os.path.join(PROJECT_ROOT, config.TEXT_LOG_DIR)
    pkt_db = PacketDatabaseTurboPNG(log_dir)
    img_9999_int = int("9999", 16)
    pkt_db.insert_packets_bulk("test_other_user_log.txt", [
        (img_9999_int, 0, 0, 10, "1" * 80, 10.0),
    ], user_id=admin_id)
    pkt_db.close()
    invalidate_analyzer_cache()

    with app.test_client() as client_a:
        client_a.post('/login', data={'email': user_a_email, 'password': 'pass1234'})
        
        # /api/image_status でユーザーAのステータス取得
        res_stat = client_a.get('/api/image_status?image_id=9999')
        assert res_stat.status_code == 200
        stat_data = res_stat.get_json()
        assert stat_data['user_has_data'] == False, "未投稿なのに user_has_data が True になっています"
        assert stat_data['user_score'] == 0.0, "未投稿なのに user_score が 0 でありません"
        assert stat_data['network_received'] >= 1, "ネットワーク全体のパケットが取得できていません"
        assert stat_data['user_img_url'] is None, "未投稿なのに user_img_url が存在します"
        print("✅ シナリオ 2 合格: 未投稿アカウントの分離・プレースホルダー判定正常")

    # -------------------------------------------------------------
    # シナリオ 3: ユーザーAが投稿した後のセッション・累積画像状態
    # -------------------------------------------------------------
    pkt_db = PacketDatabaseTurboPNG(log_dir)
    img_8888_int = int("8888", 16)
    pkt_db.insert_packets_bulk("test_user_a_log.txt", [
        (img_8888_int, 0, 0, 10, "1" * 80, 12.0),
        (img_8888_int, 1, 0, 10, "0" * 80, 14.0),
    ], user_id=user_a_id)
    pkt_db.close()
    invalidate_analyzer_cache()

    with app.test_client() as client_a:
        client_a.post('/login', data={'email': user_a_email, 'password': 'pass1234'})
        
        res_stat_a = client_a.get('/api/image_status?image_id=8888')
        stat_data_a = res_stat_a.get_json()
        assert stat_data_a['user_has_data'] == True, "投稿したのに user_has_data が False です"
        assert stat_data_a['user_packet_count'] == 2, f"パケット数が一致しません: {stat_data_a['user_packet_count']}"
        print("✅ シナリオ 3 合格: 投稿ユーザーのパケット・スコア計算正常")

    # -------------------------------------------------------------
    # シナリオ 4: ユーザーB（別アカウント）から見た画像8888
    # -------------------------------------------------------------
    with app.test_client() as client_b:
        client_b.post('/login', data={'email': user_b_email, 'password': 'pass1234'})
        
        res_stat_b = client_b.get('/api/image_status?image_id=8888')
        stat_data_b = res_stat_b.get_json()
        assert stat_data_b['user_has_data'] == False, "ユーザーBからユーザーAのデータが見えてしまっています"
        assert stat_data_b['user_score'] == 0.0
        assert stat_data_b['network_received'] >= 2, "ユーザーBからネットワーク全体パケットが見えていません"
        print("✅ シナリオ 4 合格: 複数アカウント間の完全分離テスト合格")

    # -------------------------------------------------------------
    # シナリオ 5: 管理者クリーンアップ
    # -------------------------------------------------------------
    with app.test_client() as client_admin:
        client_admin.post('/login', data={'email': admin_email, 'password': '123456789'})
        
        res_clean = client_admin.post('/admin/delete_images', json={'image_ids': ['8888', '9999']})
        assert res_clean.status_code == 200
        clean_json = res_clean.get_json()
        assert clean_json['status'] == 'success'
        print("✅ シナリオ 5 合格: 管理者による選択クリーン完了:", clean_json['message'])

    print("\n🎉 すべてのストレステスト・エッジケーステストに完全合格しました！")

if __name__ == '__main__':
    run_comprehensive_tests()
