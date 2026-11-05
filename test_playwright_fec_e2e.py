"""SSTV Turbo FEC モード Playwright 有頭(Headed) ブラウザ E2E テスト
(test_playwright_fec_e2e.py)
ユーザー操作シミュレーション:
1. ブラウザ視覚起動 (--headed, headless=False)
2. ログイン操作 (/login -> ユーザー名・パスワード入力 -> ログインクリック)
3. メイン画面 (/app) 遷移 & モード確認 (FEC)
4. スマホ室内録音音声をファイル選択 -> 送信クリック
5. プログレスバー & 進捗メッセージ (SSE) の追跡
6. 結果画面 (/result) 自動遷移 & 復元画像 (user-img, target-img) 表示確認
7. 複数のスマホ録音環境 (通常ノイズ・反響 / エアコン+小音量+ドリフト / 48kHz録音) で反復テスト & 検証
"""

import os
import sys
import time
import threading
import struct
import numpy as np
import scipy.io.wavfile as wavfile
from PIL import Image

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.system_factory import SystemFactory
from web_turbo_png.app import app


SERVER_PORT = 5005
SERVER_URL = f"http://127.0.0.1:{SERVER_PORT}"


def run_test_server():
    """テスト専用ポートで Flask アプリを起動"""
    app.run(host="127.0.0.1", port=SERVER_PORT, debug=False, use_reloader=False)


def generate_scenario_audio(scenario_name: str, drift_ppm: float = 0.0, target_sr: int = 44100) -> str:
    """指定シナリオに基づきスマホ録音を再現した WAV 音声を生成"""
    fec_config = SystemFactory.get_config("FEC")
    encoder = SystemFactory.get_encoder("FEC")
    encoder.max_packets = 6  # E2Eテスト用に6パケット

    # テスト画像作成
    test_img_path = os.path.join(PROJECT_ROOT, f"temp_{scenario_name}_input.jpg")
    img = Image.new("RGB", (fec_config.WIDTH, fec_config.HEIGHT), color=(50, 120, 190))
    img.save(test_img_path, format="JPEG", quality=80)

    clean_wav_path = os.path.join(PROJECT_ROOT, f"temp_{scenario_name}_clean.wav")
    total_time, pkt_count, out_wav, img_id = encoder.encode(test_img_path, clean_wav_path)

    sr, clean_audio = wavfile.read(clean_wav_path)
    if clean_audio.ndim > 1:
        clean_audio = np.mean(clean_audio, axis=1)
    clean_audio = clean_audio.astype(np.float32) / 32767.0

    # 1. 室内反響 (30ms リバーブ)
    reverb_len = int(sr * 0.03)
    reverb_audio = clean_audio.copy()
    if len(clean_audio) > reverb_len:
        reverb_audio[reverb_len:] += 0.22 * clean_audio[:-reverb_len]

    t = np.arange(len(clean_audio)) / float(sr)

    # 2. 低周波エアコン・換気扇音 (60Hz, 120Hz)
    ac_noise = 0.12 * np.sin(2 * np.pi * 60 * t) + 0.08 * np.sin(2 * np.pi * 120 * t)

    # 3. スマホ小音量・環境雑音合成
    phone_recorded = (reverb_audio * 0.70) + ac_noise + 0.04 * np.random.randn(len(clean_audio))

    # 4. クロックドリフト (スマホ水晶発振子誤差) シミュレーション
    if abs(drift_ppm) > 1e-4:
        drift_ratio = drift_ppm / 1e6
        indices = np.arange(len(phone_recorded)) * (1.0 + drift_ratio)
        valid_idx = indices < len(phone_recorded)
        phone_recorded = np.interp(indices[valid_idx], np.arange(len(phone_recorded)), phone_recorded)

    # 5. 録音開始ラグ (無音・環境音)
    lead_silence = 0.02 * np.random.randn(int(sr * 0.6))
    phone_final = np.concatenate([lead_silence, phone_recorded])
    phone_final = phone_final / np.max(np.abs(phone_final)) * 0.75

    # 6. スマホ録音形式 (48kHz サンプリング等)
    out_phone_wav = os.path.join(PROJECT_ROOT, f"temp_{scenario_name}_phone.wav")
    if target_sr != sr:
        num_target = int(round(len(phone_final) * (target_sr / sr)))
        phone_resampled = np.interp(
            np.linspace(0, len(phone_final), num_target, endpoint=False),
            np.arange(len(phone_final)),
            phone_final
        )
        wavfile.write(out_phone_wav, target_sr, (phone_resampled * 32767).astype(np.int16))
    else:
        wavfile.write(out_phone_wav, sr, (phone_final * 32767).astype(np.int16))

    # クリーン一時ファイルの掃除
    for p in [test_img_path, clean_wav_path]:
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

    return out_phone_wav


def run_playwright_e2e():
    """Playwright を使用した有頭ブラウザE2Eテストを実行"""
    from playwright.sync_api import sync_playwright

    print("================================================================")
    print("🚀 Playwright 有頭(Headed)ブラウザ E2E テスト開始 (FECモード)")
    print("================================================================")

    # サーバーを別スレッドで起動
    server_thread = threading.Thread(target=run_test_server, daemon=True)
    server_thread.start()
    time.sleep(1.5)  # サーバー起動待ち

    # モードを FEC に設定
    SystemFactory.set_mode("FEC")
    print(f"✅ システム稼働モードを FEC に設定: {SystemFactory.get_mode()}")

    # テスト対象シナリオ
    scenarios = [
        {"name": "scenario1_standard_phone", "drift": 0.0, "sr": 44100, "label": "通常スマホ室内録音 (反響+環境音)"},
        {"name": "scenario2_ac_drift_phone", "drift": 300.0, "sr": 44100, "label": "エアコン強ノイズ+300ppmドリフト録音"},
        {"name": "scenario3_48k_smart_phone", "drift": -200.0, "sr": 48000, "label": "48kHzスマホ高サンプリング録音"}
    ]

    artifacts_dir = os.path.join(PROJECT_ROOT, "scratch", "playwright_screenshots")
    os.makedirs(artifacts_dir, exist_ok=True)

    with sync_playwright() as p:
        # 有頭ブラウザを起動 (Chromium / Edge)
        print("🌐 有頭ブラウザ (Headed Browser) を起動中...")
        browser = p.chromium.launch(channel="msedge", headless=False, slow_mo=300)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        for idx, sc in enumerate(scenarios, start=1):
            print(f"\n------------------------------------------------------------")
            print(f"▶ [テスト {idx}/{len(scenarios)}] {sc['label']} のE2E検証")
            print(f"------------------------------------------------------------")

            # 音声ファイルを生成
            wav_path = generate_scenario_audio(sc["name"], drift_ppm=sc["drift"], target_sr=sc["sr"])
            print(f"  ・模擬スマホ録音音声生成完了: {os.path.basename(wav_path)}")

            # 1. ログイン画面へアクセス
            page.goto(f"{SERVER_URL}/login")
            page.wait_for_selector("input#email")

            # ログイン情報の入力と送信
            page.fill("input#email", "Nagasaki")
            page.fill("input#password", "123456789")
            screenshot_path = os.path.join(artifacts_dir, f"{sc['name']}_1_login.png")
            page.screenshot(path=screenshot_path)

            page.click("button.btn-submit")

            # 2. メイン画面 (/app) に遷移したことを確認
            page.wait_for_url(f"{SERVER_URL}/app", timeout=10000)
            print("  ・メイン画面 (/app) へのログイン遷移成功")

            # ヘッダーブランド名が 'SSTV' であることを確認
            header_brand = page.locator("header.top-nav span").first.inner_text().strip()
            assert header_brand == "SSTV", f"ヘッダー名がSSTVではありません: '{header_brand}'"
            print(f"  ・Webサイト名検証成功: '{header_brand}'")

            # ファイル入力要素の待機
            file_input = page.wait_for_selector("input#audio-file")

            # 3. 音声ファイルを選択
            file_input.set_input_files(wav_path)
            time.sleep(0.5)

            screenshot_path = os.path.join(artifacts_dir, f"{sc['name']}_2_file_selected.png")
            page.screenshot(path=screenshot_path)

            # 4. アップロードボタンの活性化確認 & クリック
            submit_btn = page.locator("button#btn-submit")
            assert submit_btn.is_enabled(), "送信ボタンが活性化されていません"
            print("  ・ファイル選択完了 -> 送信ボタンをクリック")
            submit_btn.click()

            # 5. プログレスバーの出現と進捗の確認
            progress_wrapper = page.locator("#progress-wrapper")
            progress_wrapper.wait_for(state="visible", timeout=10000)
            print("  ・解析プログレスバー表示確認 (SSE受信中)")

            # 進捗テキストの確認
            progress_text = page.locator("#progress-text")
            time.sleep(0.5)
            print(f"  ・進捗メッセージ: '{progress_text.inner_text().strip()}'")

            # 6. 結果画面 (/result) への遷移完了を待機 (最大60秒)
            print("  ・デコード処理と結果画面 (/result) への遷移を待機中...")
            page.wait_for_url(lambda url: "/result" in url, timeout=60000)
            print(f"  ・結果画面へ遷移成功: {page.url}")

            # 7. 結果画面の主要UI要素の検証
            page.wait_for_selector("#user-img", timeout=15000)
            page.wait_for_selector("#target-img", timeout=15000)

            user_img = page.locator("#user-img")
            target_img = page.locator("#target-img")
            assert user_img.is_visible(), "user-img が表示されていません"
            assert target_img.is_visible(), "target-img が表示されていません"

            user_src = user_img.get_attribute("src")
            target_src = target_img.get_attribute("src")
            print(f"  ・復元画像URL確認 (ユーザー貢献): {user_src}")
            print(f"  ・復元画像URL確認 (ネットワーク統合): {target_src}")

            assert "user_session_" in user_src or "/static/output/" in user_src, f"不正な画像URL: {user_src}"

            screenshot_path = os.path.join(artifacts_dir, f"{sc['name']}_3_result.png")
            page.screenshot(path=screenshot_path)
            print(f"  ・結果画面スクリーンショット保存: {os.path.basename(screenshot_path)}")

            # 一時音声の削除
            if os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except Exception:
                    pass

            print(f"  ✅ [テスト {idx}] 合格: {sc['label']}")

        browser.close()

    print("\n================================================================")
    print("🎉 Playwright 有頭ブラウザ E2E 全テストシナリオ完全合格！")
    print(f"📸 スクリーンショット保存先: {artifacts_dir}")
    print("================================================================")


if __name__ == "__main__":
    run_playwright_e2e()
