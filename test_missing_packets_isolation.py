import os
import sys
import unittest

ROOT_DIR = os.path.abspath(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from web_turbo_png.app import app
from core.system_factory import SystemFactory
from web_turbo_png.routes.api_routes import get_analyzer, invalidate_analyzer_cache


class TestMissingPacketsIsolation(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        self.client = app.test_client()
        with self.client.session_transaction() as sess:
            sess['basic_auth_passed'] = True
            sess['user_id'] = 1
            sess['email'] = "admin@test.com"
            sess['display_name'] = "Admin"

        invalidate_analyzer_cache()

    def test_missing_packets_png_and_jpeg_isolation(self):
        """PNGとJPEGの不足パケットが互いのDBに惑わされず完全に区別されて取得されるか検証"""
        analyzer_png = get_analyzer(mode='PNG')
        # 0x3333 を PNG DB に挿入: (0,0) 高品質, (1,0) 低品質(SNR 2.0)
        dummy_png_packets = [
            (0x3333, 0, 0, 10, "1" * 80, 15.0),
            (0x3333, 1, 0, 10, "0" * 80, 2.0)
        ]
        analyzer_png.aggregator.db.insert_packets_bulk("test_missing_png.txt", dummy_png_packets, user_id=1)

        analyzer_jpeg = get_analyzer(mode='JPEG')
        # 0x4444 を JPEG DB に挿入: (0,0) 高品質, (1,0) 高品質
        dummy_jpeg_packets = [
            (0x4444, 0, 0, 10, "1" * 80, 14.0),
            (0x4444, 1, 0, 10, "0" * 80, 13.0)
        ]
        analyzer_jpeg.aggregator.db.insert_packets_bulk("test_missing_jpeg.txt", dummy_jpeg_packets, user_id=1)

        # 1. システムが JPEG モードの時でも PNG (0x3333) の不足状況を正確に判定できるか
        SystemFactory.set_mode('JPEG')
        res_png = self.client.get('/api/missing?image_id=3333')
        self.assertEqual(res_png.status_code, 200)
        data_png = res_png.get_json()
        
        self.assertEqual(data_png['status'], 'success')
        self.assertEqual(data_png['mode'], 'PNG')
        self.assertEqual(data_png['total_blocks'], 256)
        # 256ブロック中、(0,0)と(1,0)は届いているので欠損は 254個
        self.assertEqual(data_png['total_missing_found'], 254, "PNGの欠損数が不正です（JPEG DBを参照している可能性）")
        # (1,0) は SNR 2.0 なので低品質パケットとして1個検出されるべき
        self.assertEqual(data_png['total_poor_found'], 1, "低品質パケットが検出されていません")

        # パケットデータのキーと内容を検証
        missing_pkts = data_png['missing_packets']
        self.assertTrue(len(missing_pkts) > 0)
        first_pkt = missing_pkts[0]
        for key in ('tile_x', 'tile_y', 'tx', 'ty', 'status', 'mode'):
            self.assertIn(key, first_pkt, f"パケット情報に必須キー '{key}' が不足しています")

        # 2. システムが PNG モードの時でも JPEG (0x4444) の不足状況を正確に判定できるか
        SystemFactory.set_mode('PNG')
        res_jpeg = self.client.get('/api/missing?image_id=4444')
        self.assertEqual(res_jpeg.status_code, 200)
        data_jpeg = res_jpeg.get_json()

        self.assertEqual(data_jpeg['status'], 'success')
        self.assertEqual(data_jpeg['mode'], 'JPEG')
        self.assertEqual(data_jpeg['total_blocks'], 256)
        self.assertEqual(data_jpeg['total_missing_found'], 254, "JPEGの欠損数が不正です（PNG DBを参照している可能性）")
        self.assertEqual(data_jpeg['total_poor_found'], 0)

        # 3. 明示的に mode パラメータを指定した場合の動作検証
        res_explicit = self.client.get('/api/missing?image_id=3333&mode=PNG')
        self.assertEqual(res_explicit.status_code, 200)
        self.assertEqual(res_explicit.get_json()['mode'], 'PNG')

        # 4. result.html のレンダリング検証（テーブル要素・バッジが存在するか）
        res_html = self.client.get('/result?image_id=3333')
        self.assertEqual(res_html.status_code, 200)
        html_text = res_html.get_data(as_text=True)
        self.assertIn('missing-summary-tbody', html_text, "行サマリー表のtbodyが存在しません")
        self.assertIn('missing-detail-tbody', html_text, "詳細パケット表のtbodyが存在しません")
        self.assertIn('poor-summary-tbody', html_text, "低品質パケット表のtbodyが存在しません")
        self.assertIn('missing-mode-badge', html_text, "モード識別バッジが存在しません")
        self.assertIn('view-tab-summary', html_text, "行サマリータブ切り替えボタンが存在しません")
        self.assertIn('view-tab-detail', html_text, "詳細パケットタブ切り替えボタンが存在しません")


if __name__ == '__main__':
    unittest.main()
