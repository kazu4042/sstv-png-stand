import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ['DISABLE_BASIC_AUTH'] = '1'

from web_turbo_png.app import app
from web_turbo_png.services.auth_db import get_auth_db
from digital_turbo_png.database_turbo import PacketDatabaseTurboPNG
import digital_turbo_png.config_turbo as config_png
from core.system_factory import SystemFactory


def test_admin_delete_e2e():
    print("=== 管理者（koseikazu@icloud.com）E2E画像削除検証開始 ===")

    # 1. 管理者ユーザー確認
    auth_db = get_auth_db()
    admin_email = "koseikazu@icloud.com"
    admin_pass = "123456789"
    admin_id = auth_db.verify_user(admin_email, admin_pass)
    if not admin_id:
        admin_id = auth_db.create_user(admin_email, admin_pass)
    print(f"✅ 管理者アカウント確認: {admin_email} (ID: {admin_id})")

    # 2. テスト用PNG画像データ作成
    SystemFactory.set_mode("PNG")
    log_dir = os.path.join(PROJECT_ROOT, config_png.TEXT_LOG_DIR)
    os.makedirs(log_dir, exist_ok=True)
    png_db = PacketDatabaseTurboPNG(log_dir)

    test_png_id_hex = "A1B2"
    test_png_id_int = int(test_png_id_hex, 16)
    png_log_file = "turbo_png_bitstream_test_a1b2.txt"
    png_log_path = os.path.join(log_dir, png_log_file)

    # 2進数行 (image_id:16bit=A1B2, tile_x:8bit=0, tile_y:8bit=0, payload_len:16bit=5, payload:40bit, snr:4bit=1111)
    bin_line = f"{test_png_id_int:016b}00000000000000000000000000000101" + ("1" * 40) + "1111\n"
    with open(png_log_path, "w", encoding="utf-8") as f:
        f.write(bin_line)

    png_db.insert_packets_bulk(png_log_file, [
        (test_png_id_int, 0, 0, 5, "1" * 40, 15.0)
    ], user_id=admin_id)
    png_db.close()

    # 静的ダミー画像ファイル作成
    static_out = os.path.join(PROJECT_ROOT, "web_turbo_png", "static", "output")
    os.makedirs(static_out, exist_ok=True)
    dummy_img = os.path.join(static_out, f"restored_ID_{test_png_id_hex}.png")
    with open(dummy_img, "w") as f:
        f.write("dummy_png_content")

    # 3. テストクライアントでログイン & 削除操作
    with app.test_client() as client:
        # ログイン
        res_login = client.post('/login', data={'email': admin_email, 'password': admin_pass})
        assert res_login.status_code in [200, 302], f"Login failed: {res_login.status_code}"
        print("✅ koseikazu@icloud.com でのログイン成功")

        # 管理画面アクセステスト
        res_admin = client.get('/admin')
        assert res_admin.status_code == 200, f"/admin アクセス失敗: {res_admin.status_code}"
        print("✅ 管理画面 (/admin) アクセス成功")

        # 画像一覧API取得
        res_images = client.get('/admin/api/images?mode=PNG')
        assert res_images.status_code == 200
        images_data = res_images.get_json()
        ids = [img['image_id_hex'] for img in images_data['images']]
        assert test_png_id_hex in ids, f"{test_png_id_hex} が一覧に存在しません: {ids}"
        print(f"✅ 画像一覧に {test_png_id_hex} の存在を確認")

        # 個別削除を実行 (POST /admin/delete_images)
        res_del = client.post('/admin/delete_images', json={'image_ids': [test_png_id_hex]})
        assert res_del.status_code == 200, f"削除失敗: {res_del.status_code}"
        del_json = res_del.get_json()
        assert del_json['status'] == 'success'
        print(f"✅ 画像削除API成功: {del_json['message']}")

        # ログファイル・静的ファイルがディスクから消えているか確認
        assert not os.path.exists(png_log_path), f"ログファイル {png_log_path} が消えていません！"
        assert not os.path.exists(dummy_img), f"静的ファイル {dummy_img} が消えていません！"
        print("✅ ディスク上のログファイルおよび復元画像ファイルの削除を確認")

        # 画像一覧APIを再取得（同期が走る）
        res_after = client.get('/admin/api/images?mode=PNG')
        after_ids = [img['image_id_hex'] for img in res_after.get_json()['images']]
        assert test_png_id_hex not in after_ids, f"ERROR: {test_png_id_hex} が復活しています！"
        print(f"✅ 削除後の最新一覧で {test_png_id_hex} が完全に消滅していることを確認（ゾンビ復活なし）")

        # 全画像クリアの検証 (POST /admin/clear_all_images)
        res_clear = client.post('/admin/clear_all_images', json={'mode': ''})
        assert res_clear.status_code == 200
        print("✅ 全画像クリア API 成功")

        res_final = client.get('/admin/api/images?mode=PNG')
        final_ids = [img['image_id_hex'] for img in res_final.get_json()['images']]
        assert len(final_ids) == 0, f"全クリア後にも画像が残っています: {final_ids}"
        print("✅ 全クリア後の0件維持を確認")

    print("\n🎉 全ての管理者画像削除 E2E テストが完全に合格しました！")


if __name__ == '__main__':
    test_admin_delete_e2e()
