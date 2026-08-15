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

ALLOWED_EXTENSIONS = {'wav'}

# Global jobs dictionary for tracking background tasks
# Format: {job_id: {"progress": 0, "status": "", "error": "", "result_data": {}}}
jobs = {}


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


@upload_bp.route('/progress-stream')
def progress_stream():
    job_id = request.args.get('job_id')
    def generate():
        while True:
            import json
            if job_id not in jobs:
                yield f"data: {json.dumps({'error': 'Job not found'})}\n\n"
                break
                
            job = jobs[job_id]
            payload = json.dumps({"progress": job["progress"], "status": job["status"], "error": job["error"]})
            yield f"data: {payload}\n\n"
            
            if job["progress"] >= 100 or job["error"]:
                break
            time.sleep(0.1)
    return Response(generate(), mimetype='text/event-stream')

def run_decode_background(job_id, save_path, app_static_folder, available_image_ids, user_id=None):
    job = jobs[job_id]
    try:
        job["progress"] = 15
        job["status"] = "音声をTurboPNGパケットとしてデコード中..."

        # ===== Step1: デコード =====
        decoder = DigitalTurboPNGDecoder(user_id=user_id)
        decoder.run(save_path)
        job["progress"] = 50

        latest_user_log = decoder.output_raw

        job["status"] = "デコード結果を集計・分析中..."

        # ===== Step2: アグリゲータでDBに蓄積 =====
        log_dir = getattr(config, "TEXT_LOG_DIR", "data/digital_turbo_png/logs")
        if not os.path.isabs(log_dir):
            log_dir_path = os.path.join(ROOT_DIR, log_dir)
        else:
            log_dir_path = log_dir

        aggregator = TurboPNGAggregator(log_dir=log_dir_path)
        aggregator.process_and_save_images(min_tile_ratio=0.0, user_id=user_id)
        job["progress"] = 70

        job["status"] = "復元画像を生成中..."

        # 復元画像を output ディレクトリに保存
        output_dir = os.path.join(app_static_folder, "output")
        os.makedirs(output_dir, exist_ok=True)

        # DB から全画像ID を取得
        image_counts = aggregator.db.get_all_image_ids_with_counts()
        available_image_ids = sorted([f"{img_id:04X}" for img_id in image_counts.keys()])

        # ===== Step3: ユーザーの今回ログをパースして自分の受信画像を生成 =====
        user_packets_by_id = {}  # {img_id_hex: [(img_id, tx, ty, plen, pbits, snr), ...]}

        if latest_user_log and os.path.exists(latest_user_log):
            with open(latest_user_log, "r", encoding="utf-8") as f:
                for line in f:
                    parsed = parse_turbo_log_line(line)
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

        for idx_id, (img_id_hex, packets) in enumerate(user_packets_by_id.items()):
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
                        for p_bits_str, weight, _ in db_packets:
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

                # ユーザー受信タイルを描画（PNG実サイズで座標計算）
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

            user_img_path = os.path.join(output_dir, f"user_ID_{img_id_hex}.png")
            user_image_buffer.save(user_img_path, format="PNG")

            if img_id_hex == current_image_id:
                user_output_url = f"/static/output/user_ID_{img_id_hex}.png"
                main_score = round((matched_packets / total_required_packets) * 100, 1)
                if main_score > 100.0:
                    main_score = 100.0
                main_matched = matched_packets

        if not current_image_id:
            current_image_id = available_image_ids[0] if available_image_ids else "0000"

        aggregator.close()

        from web_turbo_png.routes.api_routes import invalidate_analyzer_cache
        invalidate_analyzer_cache()

        job["result_data"] = {
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
            "main_matched": main_matched,
            "is_perfect": main_matched >= total_required_packets
        }

        job["progress"] = 100
        job["status"] = "完了"

    except Exception as e:
        import traceback
        traceback.print_exc()
        job["error"] = str(e)
        job["progress"] = 100

@upload_bp.route('/upload', methods=['POST'])
@login_required
def upload_file():
    user_id = session.get('user_id')
    job_id = uuid.uuid4().hex
    jobs[job_id] = {"progress": 0, "status": "初期化中...", "error": "", "result_data": {}}

    if 'file' not in request.files:
        jobs[job_id]["error"] = "ファイルが見つかりません"
        return jsonify({'success': False, 'error': jobs[job_id]["error"]}), 400

    file = request.files['file']
    if file.filename == '':
        jobs[job_id]["error"] = "ファイルが選択されていません"
        return jsonify({'success': False, 'error': jobs[job_id]["error"]}), 400

    if file and allowed_file(file.filename):
        try:
            jobs[job_id]["progress"] = 5
            jobs[job_id]["status"] = "音声をアップロード中..."

            os.makedirs(current_app.config['UPLOAD_FOLDER'], exist_ok=True)
            from werkzeug.utils import secure_filename
            save_path = os.path.join(current_app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
            file.save(save_path)

            import threading
            thread = threading.Thread(target=run_decode_background, args=(job_id, save_path, current_app.static_folder, [], user_id))
            thread.start()

            return jsonify({'success': True, 'message': 'Processing started in background', 'job_id': job_id})

        except Exception as e:
            import traceback
            traceback.print_exc()
            upload_error = f"エラーが発生しました: {str(e)}"
            return jsonify({'success': False, 'error': upload_error}), 500

    upload_error = "許可されていないファイル形式です"
    return jsonify({'success': False, 'error': upload_error}), 400
