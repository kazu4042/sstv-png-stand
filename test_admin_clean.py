import os
import sys

# テスト時はBasic認証をスキップ
os.environ['DISABLE_BASIC_AUTH'] = '1'

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from web_turbo_png.app import app
from web_turbo_png.services.auth_db import get_auth_db
from web_turbo_png.routes.api_routes import get_analyzer, invalidate_analyzer_cache
from digital_turbo_png.database_turbo import PacketDatabaseTurboPNG
import digital_turbo_png.config_turbo as config

def test_admin_clean():
    print("=== 管理者画像選択クリーン機能のテスト開始 ===")
    
    # 1. ユーザー作成 (管理者 & 一般ユーザー)
    auth_db = get_auth_db()
    admin_email = "koseikazu@icloud.com"  # is_admin で許可されたメールアドレス
    normal_email = "normal_user_test@example.com"
    
    admin_id = auth_db.create_user(admin_email, "password123") or auth_db.verify_user(admin_email, "password123")
    normal_id = auth_db.create_user(normal_email, "password123") or auth_db.verify_user(normal_email, "password123")
    
    # 2. テスト用パケットとファイルを準備
    log_dir = os.path.join(PROJECT_ROOT, config.TEXT_LOG_DIR)
    pkt_db = PacketDatabaseTurboPNG(log_dir)
    
    id_1_hex = "ABCD"
    id_2_hex = "EF01"
    id_1_int = int(id_1_hex, 16)
    id_2_int = int(id_2_hex, 16)
    
    pkt_db.insert_packets_bulk("test_admin_log_1.txt", [
        (id_1_int, 0, 0, 10, "1" * 80, 10.0),
        (id_1_int, 1, 0, 10, "0" * 80, 12.0),
    ], user_id=admin_id)
    
    pkt_db.insert_packets_bulk("test_admin_log_2.txt", [
        (id_2_int, 0, 0, 10, "1" * 80, 15.0),
    ], user_id=normal_id)
    
    pkt_db.close()
    
    # ダミー画像ファイルを static/output に作成
    static_out = os.path.join(PROJECT_ROOT, "web_turbo_png", "static", "output")
    os.makedirs(static_out, exist_ok=True)
    dummy_img_1 = os.path.join(static_out, f"restored_ID_{id_1_hex}.png")
    dummy_img_2 = os.path.join(static_out, f"restored_ID_{id_2_hex}.png")
    with open(dummy_img_1, "w") as f:
        f.write("dummy")
    with open(dummy_img_2, "w") as f:
        f.write("dummy")
        
    invalidate_analyzer_cache()
    
    # 3. テストクライアントで検証
    # --- 非管理者で削除を試みる (403拒否の検証) ---
    with app.test_client() as client_normal:
        login_res = client_normal.post('/login', data={'email': normal_email, 'password': 'password123'})
        assert login_res.status_code in [200, 302]
        
        res_forbidden = client_normal.post("/admin/delete_images", json={"image_ids": [id_1_hex]})
        assert res_forbidden.status_code == 403, f"非管理者が削除できてしまいました: {res_forbidden.status_code}"
        print("✅ 非管理者のアクセス拒否 (403) テスト合格")
        
    # --- 管理者でログインして管理画面 (/admin) を開く ---
    with app.test_client() as client_admin:
        login_res = client_admin.post('/login', data={'email': admin_email, 'password': '123456789'})
        assert login_res.status_code in [200, 302]
        
        res_admin = client_admin.get("/admin")
        assert res_admin.status_code == 200, f"/admin アクセス失敗: {res_admin.status_code}"
        html = res_admin.get_data(as_text=True)
        assert f"0x{id_1_hex}" in html, f"画像ID 0x{id_1_hex} が一覧に表示されていません"
        assert f"0x{id_2_hex}" in html, f"画像ID 0x{id_2_hex} が一覧に表示されていません"
        print("✅ 管理者ダッシュボード画像一覧表示テスト合格")
        
        # --- 管理者で画像1 (0xABCD) を選択クリーン ---
        res_del = client_admin.post("/admin/delete_images", json={"image_ids": [id_1_hex]})
        assert res_del.status_code == 200, f"削除失敗: {res_del.status_code}"
        del_data = res_del.get_json()
        assert del_data['status'] == 'success'
        print("✅ 画像削除API レスポンス合格:", del_data['message'])
        
        # --- DBおよびファイルから削除されたか検証 ---
        analyzer = get_analyzer()
        remaining_ids = analyzer.get_available_image_ids(user_id=None)
        assert id_1_hex not in remaining_ids, f"{id_1_hex} がDBから削除されていません"
        assert id_2_hex in remaining_ids, f"{id_2_hex} が誤って削除されています"
        assert not os.path.exists(dummy_img_1), f"ファイル {dummy_img_1} が削除されていません"
        assert os.path.exists(dummy_img_2), f"ファイル {dummy_img_2} が誤って削除されています"
        print("✅ DBレコードおよび静的ファイル削除確認テスト合格")
        
        # クリーンアップ（テスト用画像2も削除）
        client_admin.post("/admin/delete_images", json={"image_ids": [id_2_hex]})
        if os.path.exists(dummy_img_2):
            os.remove(dummy_img_2)

    print("\n🎉 管理者画像選択クリーン機能の全テストに合格しました！")

if __name__ == '__main__':
    test_admin_clean()
