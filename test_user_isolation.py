import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from web_turbo_png.app import app
from web_turbo_png.services.auth_db import get_auth_db
from web_turbo_png.routes.api_routes import get_analyzer, invalidate_analyzer_cache
from digital_turbo_png.database_turbo import PacketDatabaseTurboPNG
import digital_turbo_png.config_turbo as config

def run_tests():
    print("=== テスト開始 ===")
    
    # 1. ユーザー作成 & DB準備
    auth_db = get_auth_db()
    u1_email = "test_user_uploader@example.com"
    u2_email = "test_user_newbie@example.com"
    
    u1_id = auth_db.create_user(u1_email, "password123") or auth_db.verify_user(u1_email, "password123")
    u2_id = auth_db.create_user(u2_email, "password123") or auth_db.verify_user(u2_email, "password123")
    
    print(f"ユーザー1 (投稿あり想定): ID={u1_id}, Email={u1_email}")
    print(f"ユーザー2 (新規・投稿なし想定): ID={u2_id}, Email={u2_email}")
    
    # 2. パケットDBにダミーパケットをユーザー1名義で挿入
    log_dir = os.path.join(PROJECT_ROOT, config.TEXT_LOG_DIR)
    pkt_db = PacketDatabaseTurboPNG(log_dir)
    
    test_img_id_hex = "1234"
    test_img_int = int(test_img_id_hex, 16)
    
    dummy_packets = [
        (test_img_int, 0, 0, 10, "1" * 80, 10.0),
        (test_img_int, 1, 0, 10, "0" * 80, 12.0),
    ]
    pkt_db.insert_packets_bulk(f"test_log_user_{u1_id}.txt", dummy_packets, user_id=u1_id)
    pkt_db.close()
    
    # キャッシュをクリアして再ロード
    invalidate_analyzer_cache()
    
    # 3. アナライザーの検証
    analyzer = get_analyzer()
    analyzer.aggregator.load_all_logs()
    
    all_available = analyzer.get_available_image_ids(user_id=None)
    u1_available = analyzer.get_available_image_ids(user_id=u1_id)
    u2_available = analyzer.get_available_image_ids(user_id=u2_id)
    
    print(f"全体画像ID一覧: {all_available}")
    print(f"ユーザー1の投稿画像ID一覧: {u1_available}")
    print(f"ユーザー2の投稿画像ID一覧: {u2_available}")
    
    assert test_img_id_hex in all_available, f"エラー: {test_img_id_hex} が全体一覧にありません"
    assert test_img_id_hex in u1_available, "エラー: ユーザー1の一覧に含まれていません"
    assert test_img_id_hex not in u2_available, "エラー: ユーザー2の一覧に誤って含まれています"
    
    # 4. get_image_status の検証
    status_u1 = analyzer.get_image_status(test_img_id_hex, user_id=u1_id)
    status_u2 = analyzer.get_image_status(test_img_id_hex, user_id=u2_id)
    
    print(f"ユーザー1のステータス: user_has_data={status_u1['user_has_data']}, user_score={status_u1['user_score']}, network_score={status_u1['network_score']}")
    print(f"ユーザー2のステータス: user_has_data={status_u2['user_has_data']}, user_score={status_u2['user_score']}, network_score={status_u2['network_score']}")
    
    assert status_u1['user_has_data'] == True, "ユーザー1は user_has_data=True である必要があります"
    assert status_u2['user_has_data'] == False, "ユーザー2は user_has_data=False である必要があります"
    assert status_u2['user_score'] == 0.0, "ユーザー2の貢献スコアは 0.0 である必要があります"
    assert status_u2['network_score'] > 0.0 or status_u2['network_received'] >= 0, "ネットワークスコアが計算されている必要があります"
    
    # 5. Flask クライアントでの検証
    with app.test_client() as client:
        # --- ユーザー2 (新規・投稿なし) の検証 ---
        with client.session_transaction() as sess:
            sess['user_id'] = u2_id
            sess['email'] = u2_email
            sess['basic_auth_passed'] = True
        
        # /api/image_status テスト
        res = client.get(f"/api/image_status?image_id={test_img_id_hex}")
        assert res.status_code == 200, f"/api/image_status failed: {res.status_code}"
        data = res.get_json()
        assert data['status'] == 'success'
        assert data['user_has_data'] == False
        print("✅ /api/image_status (ユーザー2) テスト合格")
        
        # /result 画面の取得テスト
        res_page = client.get(f"/result?image_id={test_img_id_hex}")
        assert res_page.status_code == 200, f"/result failed: {res_page.status_code}"
        html_text = res_page.get_data(as_text=True)
        # サイドバーに画像IDが表示されているか
        assert f"0x{test_img_id_hex}" in html_text, "サイドバーに画像IDが表示されていません"
        # ALLバッジが表示されているか（YOUバッジではなく）
        assert "ALL" in html_text, "ユーザー2にALLバッジが表示されていません"
        print("✅ /result 画面 (ユーザー2) テスト合格")
        
        # --- ユーザー1 (投稿あり) の検証 ---
        with client.session_transaction() as sess:
            sess['user_id'] = u1_id
            sess['email'] = u1_email
            sess['basic_auth_passed'] = True
            
        res_u1 = client.get(f"/api/image_status?image_id={test_img_id_hex}")
        assert res_u1.status_code == 200
        data_u1 = res_u1.get_json()
        assert data_u1['user_has_data'] == True
        print("✅ /api/image_status (ユーザー1) テスト合格")
        
        res_page_u1 = client.get(f"/result?image_id={test_img_id_hex}")
        assert res_page_u1.status_code == 200
        html_text_u1 = res_page_u1.get_data(as_text=True)
        assert "YOU" in html_text_u1, "ユーザー1にYOUバッジが表示されていません"
        print("✅ /result 画面 (ユーザー1) テスト合格")
        
    print("\n🎉 すべてのテストに合格しました！")

if __name__ == '__main__':
    run_tests()
