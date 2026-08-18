import os
import time
import sys
import numpy as np
from PIL import Image
from flask import Blueprint, request, jsonify, current_app, Response, session
from werkzeug.utils import secure_filename
import io

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import digital_turbo_png.config_turbo as config
from digital_turbo_png.decoder_turbo import DigitalTurboPNGDecoder
from digital_turbo_png.aggregator_turbo import TurboPNGAggregator

upload_bp = Blueprint('upload', __name__)

import uuid
from web_turbo_png.routes.auth_routes import login_required
from web_turbo_png.services.job_manager import create_job, update_job, get_job, cleanup_old_jobs

ALLOWED_EXTENSIONS = {'wav'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def parse_turbo_log_line(line):
    """バイナリビットストリームログ行をパース（0と1のみの形式）"""
    line = line.strip()
    if not line or not all(c in '01' for c in line):
        return None
    try:
        hdr = (config.BIT_IMAGE_CRC + config.BIT_TILE_X
               + config.BIT_TILE_Y + config.BIT_PAYLOAD_LENGTH)
        if len(line) < hdr + 4:
            return None
        idx = 0
        img_id         = int(line[idx:idx + config.BIT_IMAGE_CRC], 2);       idx += config.BIT_IMAGE_CRC
        tile_x         = int(line[idx:idx + config.BIT_TILE_X], 2);          idx += config.BIT_TILE_X
        tile_y         = int(line[idx:idx + config.BIT_TILE_Y], 2);          idx += config.BIT_TILE_Y
        payload_length = int(line[idx:idx + config.BIT_PAYLOAD_LENGTH], 2);  idx += config.BIT_PAYLOAD_LENGTH
        payload_bits   = line[idx:idx + payload_length * 8]
        snr_str        = line[idx + payload_length * 8:]
        snr_val = float(int(snr_str, 2)) if len(snr_str) == 4 else 1.0
        return (img_id, tile_x, tile_y, payload_length, payload_bits, snr_val)
    except (ValueError, IndexError):
        return None


def bits_to_bytearray(bits_str):
    """ビット列をバイト配列に変換"""
    byte_list = []
    for i in range(0, len(bits_str), 8):
        chunk = bits_str[i:i + 8]
        if len(chunk) == 8:
            val = 0
            for b in chunk:
                val = (val << 1) | (1 if b == '1' else 0)
            byte_list.append(val)
    return bytearray(byte_list)


@upload_bp.route('/progress')
def upload_progress():
    job_id = request.args.get('job_id')
    if not job_id:
        return jsonify({"error": "Missing job_id"}), 400
        
    job_data = get_job(job_id)
    if job_data:
        return jsonify(job_data)
    else:
        return jsonify({"error": "Job not found"}), 404

@upload_bp.route('/progress-stream')
def progress_stream():
    job_id = request.args.get('job_id')
    def generate():
        while True:
            import json
            job = get_job(job_id)
            if not job:
                yield f"data: {json.dumps({'error': 'Job not found'})}\n\n"
                break
                
            payload = json.dumps({"progress": job["progress"], "status": job["status"], "error": job["error"]})
            yield f"data: {payload}\n\n"
            
            if job["progress"] >= 100 or job["error"]:
                break
            time.sleep(0.1)
    return Response(generate(), mimetype='text/event-stream')

def process_upload(filepath, original_filename, job_id, app, user_id):
    """アップロードされたファイルの処理（バックグラウンド）"""
    try:
        update_job(job_id, progress=5, status="音声のデコード中...")

        # ===== Step1: デコード =====
        decoder = DigitalTurboPNGDecoder(user_id=user_id)
        
        def decode_progress_callback(prog):
            # progは0.0〜100.0のパーセンテージ
            calc_prog = 5 + int(prog * 0.55)
            update_job(job_id, progress=calc_prog, status=f"音声信号の高速デコード中... {int(prog)}%")
                
        success_count, log_path = decoder.run(filepath, progress_callback=decode_progress_callback)
        
        decoded_bits_list = []
        if os.path.exists(log_path):
            with open(log_path, "r", encoding="utf-8") as f:
                decoded_bits_list = [line.strip() for line in f if line.strip()]
        
        if not decoded_bits_list:
            update_job(job_id, progress=100, status="エラー", error="SSTV TurboPNGの信号が検出できませんでした。")
            return
            
        update_job(job_id, progress=50, status="データベースへの登録中...")

        # ===== Step2: アグリゲータでDBに蓄積 =====
        log_dir = getattr(config, "TEXT_LOG_DIR", "data/digital_turbo_png/logs")
        if not os.path.isabs(log_dir):
            log_dir_path = os.path.join(ROOT_DIR, log_dir)
        else:
            log_dir_path = log_dir
        
        update_job(job_id, progress=60, status="統合処理中 (Aggregator)...")
        
        aggregator = TurboPNGAggregator(log_dir=log_dir_path)
        # 全員のパケットで多数決画像を生成（user_idフィルタなし）
        aggregator.process_and_save_images(min_tile_ratio=0.0, user_id=None)
        if user_id:
            # そのユーザー単体の累積画像も生成
            aggregator.process_and_save_images(min_tile_ratio=0.0, user_id=user_id)
            
        update_job(job_id, progress=70, status="復元画像を生成中...")

        # 復元画像を output ディレクトリに保存
        output_dir = os.path.join(app.static_folder, "output")
        os.makedirs(output_dir, exist_ok=True)

        # DB から全画像ID を取得（全ユーザー横断）
        image_counts = aggregator.db.get_all_image_ids_with_counts(user_id=None)
        available_image_ids = sorted([f"{img_id:04X}" for img_id in image_counts.keys()])

        # ===== Step3: ユーザーの今回ログをパースして自分の受信画像を生成 =====
        user_packets_by_id = {}  # {img_id_hex: [(img_id, tx, ty, plen, pbits, snr), ...]}

        # デコード結果からログデータを取得
        for bits in decoded_bits_list:
            parsed = parse_turbo_log_line(bits)
            if parsed is None:
                continue
            img_id, tile_x, tile_y, plen, pbits, snr_val = parsed
            img_id_hex = f"{img_id:04X}"
            if img_id_hex not in user_packets_by_id:
                user_packets_by_id[img_id_hex] = []
            user_packets_by_id[img_id_hex].append(parsed)

        tile_count_x = config.TILE_COUNT_X
        tile_count_y = config.TILE_COUNT_Y
        tile_size    = config.TILE_SIZE
        total_required_packets = tile_count_x * tile_count_y

        current_image_id = None
        max_packets = 0
        main_score = 0.0
        main_matched = 0
        user_output_url = ""

        for img_id_hex, packets in user_packets_by_id.items():
            if len(packets) > max_packets:
                max_packets = len(packets)
                current_image_id = img_id_hex

            # ユーザー受信分の画像を描画
            user_image_buffer = Image.new("RGB", (config.WIDTH, config.HEIGHT), color="black")
            matched_packets = 0

            # DB から投票済みペイロードを取得してユーザー一致率を計算
            db_tiles = aggregator.db.get_packets_for_image(int(img_id_hex, 16))

            for parsed in packets:
                img_id_int, tile_x, tile_y, plen, user_payload, snr_val = parsed

                # 投票済みペイロードと比較
                voted_payload = ""
                if tile_y in db_tiles and tile_x in db_tiles[tile_y]:
                    len_dict = db_tiles[tile_y][tile_x]
                    if len_dict:
                        best_plen = max(len_dict.keys(), key=lambda k: sum(p[1] for p in len_dict[k]))
                        db_packets = len_dict[best_plen]
                        payload_bit_len = best_plen * 8
                        score_0 = np.zeros(payload_bit_len, dtype=float)
                        score_1 = np.zeros(payload_bit_len, dtype=float)
                        for p_bits_str, weight, _, _, _ in db_packets:
                            if len(p_bits_str) < payload_bit_len:
                                continue
                            for i, bit_char in enumerate(p_bits_str[:payload_bit_len]):
                                if bit_char == '1':
                                    score_1[i] += weight
                                elif bit_char == '0':
                                    score_0[i] += weight
                        voted_payload = "".join(
                            '1' if score_1[i] >= score_0[i] else '0'
                            for i in range(payload_bit_len)
                        )

                if voted_payload:
                    bit_matches = sum(1 for u_b, v_b in zip(user_payload, voted_payload) if u_b == v_b)
                    if len(voted_payload) > 0 and (bit_matches / len(voted_payload)) >= 0.9:
                        matched_packets += 1

                # ユーザー受信タイルを描画
                try:
                    p_bytes = bits_to_bytearray(user_payload)
                    tile_img = Image.open(io.BytesIO(p_bytes)).convert("RGB")
                    tw, th = tile_img.size
                    paste_x = tile_x * tw
                    paste_y = tile_y * th
                    if paste_x + tw <= config.WIDTH and paste_y + th <= config.HEIGHT:
                        user_image_buffer.paste(tile_img, (paste_x, paste_y))
                    else:
                        tile_img = tile_img.crop((0, 0,
                            min(tw, config.WIDTH - paste_x),
                            min(th, config.HEIGHT - paste_y)))
                        user_image_buffer.paste(tile_img, (paste_x, paste_y))
                except Exception:
                    pass

            user_img_path = os.path.join(output_dir, f"user_{user_id}_ID_{img_id_hex}.png")
            user_image_buffer.save(user_img_path, format="PNG")

            if img_id_hex == current_image_id:
                user_output_url = f"/static/output/user_{user_id}_ID_{img_id_hex}.png"
                main_score = round((matched_packets / total_required_packets) * 100, 1)
                if main_score > 100.0:
                    main_score = 100.0
                main_matched = matched_packets

        if not current_image_id:
            current_image_id = available_image_ids[0] if available_image_ids else "0000"

        # ネットワーク全体の復元度を計算（全ユーザー横断）
        network_received = 0
        if current_image_id:
            network_tiles = aggregator.db.get_packets_for_image(int(current_image_id, 16), user_id=None)
            for ty in range(tile_count_y):
                for tx in range(tile_count_x):
                    if ty in network_tiles and tx in network_tiles[ty] and network_tiles[ty][tx]:
                        network_received += 1
        network_score = round((network_received / total_required_packets) * 100, 1) if total_required_packets > 0 else 0.0

        aggregator.close()

        result_data = {
            "available_image_ids": available_image_ids,
            "current_image_id": current_image_id,
            "main_image_url": user_output_url,
            "tile_count_x": tile_count_x,
            "tile_count_y": tile_count_y,
            "tile_size": tile_size,
            "total_packets": total_required_packets,
            "total_required": total_required_packets,
            "received_packets": max_packets,
            "main_score": main_score,
            "contribution_score": main_score,
            "main_matched": main_matched,
            "is_perfect": main_matched >= total_required_packets,
            "network_score": network_score,
            "network_received": network_received,
            "timestamp": int(time.time())
        }

        update_job(job_id, progress=100, status="完了", result_data=result_data)

    except Exception as e:
        import traceback
        traceback.print_exc()
        update_job(job_id, progress=100, status="エラー", error=str(e))
    finally:
        # 処理終了後、必ず音声ファイルを削除する（ディスク容量対策）
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
                print(f"[Cleanup] Deleted audio file: {filepath}")
            except Exception as del_err:
                print(f"[Cleanup Error] Failed to delete {filepath}: {del_err}")

@upload_bp.route('/upload', methods=['POST'])
@login_required
def upload_file():
    user_id = session.get('user_id')
    job_id = uuid.uuid4().hex
    create_job(job_id)

    if 'file' not in request.files:
        update_job(job_id, progress=100, status="エラー", error="ファイルが見つかりません")
        return jsonify({'success': False, 'error': "ファイルが見つかりません"}), 400

    file = request.files['file']
    if file.filename == '' or file.filename is None:
        update_job(job_id, progress=100, status="エラー", error="ファイルが選択されていません")
        return jsonify({'success': False, 'error': "ファイルが選択されていません"}), 400

    filename = file.filename

    if file and allowed_file(file.filename):
        try:
            update_job(job_id, progress=5, status="音声をアップロード中...")

            os.makedirs(current_app.config['UPLOAD_FOLDER'], exist_ok=True)
            from werkzeug.utils import secure_filename
            save_path = os.path.join(current_app.config['UPLOAD_FOLDER'], secure_filename(filename))
            file.save(save_path)

            import threading
            app = current_app._get_current_object()  # pyrefly: ignore
            
            # 古いジョブやファイルのクリーンアップ処理（バックグラウンドではなくここで軽く実行）
            cleanup_old_jobs(max_age_seconds=86400)
            
            thread = threading.Thread(target=process_upload, args=(save_path, secure_filename(filename), job_id, app, user_id))
            thread.start()

            return jsonify({'success': True, 'message': 'Processing started in background', 'job_id': job_id})

        except Exception as e:
            import traceback
            traceback.print_exc()
            upload_error = f"エラーが発生しました: {str(e)}"
            return jsonify({'success': False, 'error': upload_error}), 500

    upload_error = "許可されていないファイル形式です"
    return jsonify({'success': False, 'error': upload_error}), 400
