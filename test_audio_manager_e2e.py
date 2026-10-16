import unittest
import os
import sys
import json
import io
import shutil
from PIL import Image

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from web_turbo_png.app import app
from web_turbo_png.services.audio_manager import AudioManager


class TestAudioManagerE2E(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app.config['TESTING'] = True
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.client = self.app.test_client()

        # 管理者ログインセッション
        with self.client.session_transaction() as sess:
            sess['email'] = 'koseikazu@icloud.com'
            sess['user_id'] = 1

    def test_01_list_tracks(self):
        """トラック一覧取得 API テスト"""
        res = self.client.get('/admin/api/audio/list')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data['status'], 'success')
        self.assertIn('tracks', data)
        self.assertIn('active', data)
        print(f"\n[Test 1] Audio Tracks: {len(data['tracks'])} items. Active: {data['active']}")

    def test_02_set_active_track(self):
        """アクティブトラック設定 API テスト"""
        # JPEG のアクティブトラックを変更
        res = self.client.post('/admin/api/audio/set_active', json={
            'mode': 'JPEG',
            'filename': 'turbo_256_256.wav'
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['active']['JPEG'], 'turbo_256_256.wav')
        print(f"[Test 2] Set active track success: {data['message']}")

    def test_03_upload_image_and_auto_encode(self):
        """テスト用の小さな画像を作成してアップロード＆自動エンコードの検証"""
        # 32x32 のダミー画像作成
        img = Image.new('RGB', (32, 32), color=(255, 120, 60))
        img_bytes = io.BytesIO()
        img.save(img_bytes, format='JPEG')
        img_bytes.seek(0)

        data = {
            'file': (img_bytes, 'test_tiny_flower.jpg'),
            'label': 'テスト用小型画像',
            'mode': 'JPEG',
            'type': 'image',
            'description': '自動テスト用'
        }

        res = self.client.post('/admin/api/audio/upload', data=data, content_type='multipart/form-data')
        self.assertEqual(res.status_code, 200)
        resp_data = res.get_json()
        self.assertEqual(resp_data['status'], 'success')
        track = resp_data['track']
        print(f"[Test 3] Encoded track: {track['filename']}, Image ID: {track['image_id']}, Size: {track['size_mb']} MB")

        # 削除テスト
        del_res = self.client.post('/admin/api/audio/delete', json={'filename': track['filename']})
        self.assertEqual(del_res.status_code, 200)
        del_data = del_res.get_json()
        self.assertEqual(del_data['status'], 'success')
        print(f"[Test 3] Deleted created track: {del_data['message']}")

    def test_04_demo_page_rendering(self):
        """demo ページの管理者指定アクティブ音声の固定配信レンダリングテスト"""
        res = self.client.get('/demo')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn('modeTracks', html)
        self.assertIn('currentModeBadge', html)
        print("[Test 4] Demo page rendered with admin-configured active audio successfully.")

    def test_05_upload_direct_wav_audio(self):
        """音声ファイル (.wav) の直接アップロード＆登録＆削除の検証"""
        import numpy as np
        import scipy.io.wavfile as wavfile
        
        # 1秒のテスト用WAV作成
        sr = 44100
        samples = (np.sin(2 * np.pi * 1000 * np.arange(sr) / sr) * 10000).astype(np.int16)
        wav_bytes = io.BytesIO()
        wavfile.write(wav_bytes, sr, samples)
        wav_bytes.seek(0)

        data = {
            'file': (wav_bytes, 'custom_experiment_test.wav'),
            'label': '実験用直接UP音声',
            'mode': 'FEC',
            'type': 'audio',
            'description': '直接アップロードテスト'
        }

        res = self.client.post('/admin/api/audio/upload', data=data, content_type='multipart/form-data')
        self.assertEqual(res.status_code, 200)
        resp_data = res.get_json()
        self.assertEqual(resp_data['status'], 'success')
        track = resp_data['track']
        self.assertEqual(track['mode'], 'FEC')
        print(f"[Test 5] Direct WAV uploaded: {track['filename']}, Duration: {track.get('duration_sec')}s, Size: {track['size_mb']} MB")

        # 削除
        del_res = self.client.post('/admin/api/audio/delete', json={'filename': track['filename']})
        self.assertEqual(del_res.status_code, 200)
        print(f"[Test 5] Direct WAV deleted successfully")


if __name__ == '__main__':
    unittest.main()

