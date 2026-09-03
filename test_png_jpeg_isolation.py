import os
import sys

ROOT_DIR = os.path.abspath(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from web_turbo_png.app import app
from core.system_factory import SystemFactory
from web_turbo_png.routes.api_routes import get_analyzer, invalidate_analyzer_cache


def test_png_jpeg_isolation():
    print("=== PNG / JPEG 完全分離・混在防止テスト開始 ===")
    app.config['TESTING'] = True
    client = app.test_client()

    admin_email = "koseikazu@icloud.com"

    with client.session_transaction() as sess:
        sess['basic_auth_passed'] = True
        sess['user_id'] = 1
        sess['email'] = admin_email
        sess['display_name'] = "Admin"

    # 1. 念のためクリア
    invalidate_analyzer_cache()
    client.post('/admin/clear_all_images')

    # 2. PNGのDBにダミーパケットを挿入 (画像ID: 0x1111)
    analyzer_png = get_analyzer(mode='PNG')
    dummy_png_packets = [
        (0x1111, 0, 0, 10, "1" * 80, 15.0),
        (0x1111, 1, 0, 10, "0" * 80, 14.0)
    ]
    analyzer_png.aggregator.db.insert_packets_bulk("test_isolation_png.txt", dummy_png_packets, user_id=1)

    # 3. JPEGのDBにダミーパケットを挿入 (画像ID: 0x2222)
    analyzer_jpeg = get_analyzer(mode='JPEG')
    dummy_jpeg_packets = [
        (0x2222, 0, 0, 10, "1" * 80, 15.0),
        (0x2222, 1, 0, 10, "0" * 80, 14.0)
    ]
    analyzer_jpeg.aggregator.db.insert_packets_bulk("test_isolation_jpeg.txt", dummy_jpeg_packets, user_id=1)

    # 4. API経由で PNGモードの画像一覧を取得
    res_png = client.get('/admin/api/images?mode=PNG')
    assert res_png.status_code == 200
    data_png = res_png.get_json()
    png_ids = [img['image_id_hex'] for img in data_png['images']]
    print(f"PNG取得結果: {png_ids} (件数: {data_png['png_count']})")
    assert "1111" in png_ids, "PNGの画像 0x1111 が一覧に含まれていません"
    assert "2222" not in png_ids, "❌ 致命的バグ: PNG一覧にJPEGの画像 0x2222 が混ざっています！"
    for img in data_png['images']:
        assert img['engine_mode'] == 'PNG'
    print("✅ 1. PNG画像一覧の完全分離確認 合格（JPEGの混入ゼロ）")

    # 5. API経由で JPEGモードの画像一覧を取得
    res_jpeg = client.get('/admin/api/images?mode=JPEG')
    assert res_jpeg.status_code == 200
    data_jpeg = res_jpeg.get_json()
    jpeg_ids = [img['image_id_hex'] for img in data_jpeg['images']]
    print(f"JPEG取得結果: {jpeg_ids} (件数: {data_jpeg['jpeg_count']})")
    assert "2222" in jpeg_ids, "JPEGの画像 0x2222 が一覧に含まれていません"
    assert "1111" not in jpeg_ids, "❌ 致命的バグ: JPEG一覧にPNGの画像 0x1111 が混ざっています！"
    for img in data_jpeg['images']:
        assert img['engine_mode'] == 'JPEG'
    print("✅ 2. JPEG画像一覧の完全分離確認 合格（PNGの混入ゼロ）")

    # 6. ALL指定で両方が正しく区分されて取得できるか確認
    res_all = client.get('/admin/api/images?mode=ALL')
    assert res_all.status_code == 200
    data_all = res_all.get_json()
    all_ids = [img['image_id_hex'] for img in data_all['images']]
    assert "1111" in all_ids and "2222" in all_ids
    assert data_all['png_count'] >= 1
    assert data_all['jpeg_count'] >= 1
    print(f"✅ 3. 全画像一覧取得 合格 (PNG: {data_all['png_count']}, JPEG: {data_all['jpeg_count']})")

    # 7. クリーンアップ
    client.post('/admin/clear_all_images')
    print("✅ 4. テストデータ全クリア 合格")

    print("\n🎉 PNG/JPEG の完全分離・混在防止テストに完全合格しました！")


if __name__ == "__main__":
    test_png_jpeg_isolation()
