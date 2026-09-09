import os
import time
import sys
import numpy as np
from PIL import Image, ImageFile
from flask import Blueprint, request, jsonify, current_app, Response, session
from werkzeug.utils import secure_filename
import io
import uuid

# 破損・途切れJPEG/PNGでも例外を出さずに読み込む
setattr(ImageFile, 'LOAD_TRUNCATED_IMAGES', True)

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.system_factory import SystemFactory
from web_turbo_png.routes.auth_routes import login_required
from web_turbo_png.services.job_manager import create_job, update_job, get_job, cleanup_old_jobs

upload_bp = Blueprint('upload', __name__)

ALLOWED_EXTENSIONS = {'wav', 'm4a', 'mp3', 'aac', 'ogg', 'flac', 'webm', 'caf', '3gp'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def convert_and_normalize_audio(input_path, output_wav_path):
    """
    ffmpeg を使用して任意の音声形式（.m4a, .mp3, .aac, .wav等）を
    44.1kHz モノラル 16bit PCM WAV に変換し、
    帯域フィルタ (400Hz〜9500Hz) と動的ゲイン正規化 (dynaudnorm) を適用する。
    これにより、スマホ録音時の音量不足、振幅の揺らぎ、低周波雑音を自動的に補正する。
    """
    import subprocess
    import shutil
    ffmpeg_bin = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"
    cmd = [
        ffmpeg_bin, "-y", "-i", input_path,
        "-af", "highpass=f=400,lowpass=f=9500,dynaudnorm=f=150:g=15:p=0.9",
        "-ar", "44100",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        output_wav_path
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        if res.returncode != 0:
            print(f"[Upload] ffmpeg failed (code {res.returncode}): {res.stderr.decode('utf-8', errors='ignore')[:300]}")
        return res.returncode == 0
    except Exception as e:
        print(f"[Upload] ffmpeg conversion error: {e}")
        return False


def parse_turbo_log_line(line, config_module=None):
    """バイナリビットストリームログ行をパース（0と1のみの形式）"""
    config = config_module or SystemFactory.get_config()
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
        
    job_data = get_job(job_id, retries=5)
    if job_data:
        return jsonify(job_data)
    else:
        return jsonify({"error": "Job not found"}), 404


@upload_bp.route('/progress-stream')
def progress_stream():
    job_id = request.args.get('job_id')
    def generate():
        import json
        retry_count = 0
        max_initial_retries = 30  # 最大3秒初期化待ち
        while True:
            job = get_job(job_id, retries=2)
            if not job:
                retry_count += 1
                if retry_count < max_initial_retries:
                    time.sleep(0.1)
                    continue
                yield f"data: {json.dumps({'error': 'Job not found'})}\n\n"
                break
                
            payload = json.dumps({"progress": job["progress"], "status": job["status"], "error": job["error"]})
            yield f"data: {payload}\n\n"
            
            if job["progress"] >= 100 or job["error"]:
                break
            time.sleep(0.15)
            
    response = Response(generate(), mimetype='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    return response


def process_upload(filepath, original_filename, job_id, app, user_id):
    """アップロードされた音声ファイルの処理（現在のシステム稼働中モードで厳密に実行）"""
    try:
        mode_name = SystemFactory.get_mode()
        config = SystemFactory.get_config(mode_name)
        update_job(job_id, progress=3, status="音響信号の動的正規化・最適化中...")

        # どんなスマホ録音音声でも 44.1kHz モノラル PCM WAV に自動正規化
        norm_wav_path = os.path.splitext(filepath)[0] + "_norm.wav"
        if convert_and_normalize_audio(filepath, norm_wav_path) and os.path.exists(norm_wav_path):
            decode_target_path = norm_wav_path
        else:
            # ffmpeg が無い場合、WAV以外は即座に親切なエラーを返す
            ext = os.path.splitext(filepath)[1].lower()
            if ext not in ['.wav', '.wave']:
                update_job(
                    job_id, progress=100, status="エラー",
                    error="サーバーに音声変換ツール(ffmpeg)がありません。ページを再読み込み(リロード)して再度アップロードするか、.wav 形式の音声ファイルを選択してください。"
                )
                return
            decode_target_path = filepath

        # ===== Step1: デュアルエンジン自動判定（PNG/JPEG 両対応） =====
        primary_mode = mode_name
        fallback_mode = "JPEG" if primary_mode == "PNG" else "PNG"
        modes_to_try = [primary_mode, fallback_mode]

        detected_mode = None
        decoded_bits_list = []
        decoder = None

        for try_mode in modes_to_try:
            update_job(job_id, progress=6, status=f"音声信号を検出中 ({try_mode} モード)...")
            cur_decoder = SystemFactory.get_decoder(user_id=user_id, mode=try_mode)

            def decode_progress_callback(prog):
                calc_prog = 6 + int(prog * 0.54)
                update_job(job_id, progress=calc_prog, status=f"音声信号の高速デコード中 ({try_mode})... {int(prog)}%")

            success_count, log_path = cur_decoder.run(decode_target_path, progress_callback=decode_progress_callback)

            cur_bits_list = []
            if os.path.exists(log_path):
                with open(log_path, "r", encoding="utf-8") as f:
                    cur_bits_list = [line.strip() for line in f if line.strip()]

            if cur_bits_list:
                detected_mode = try_mode
                decoded_bits_list = cur_bits_list
                decoder = cur_decoder
                mode_name = detected_mode
                config = SystemFactory.get_config(mode_name)
                # システム稼働モードを自動的に合致したモードに同期更新
                SystemFactory.set_mode(mode_name)
                print(f"[Upload] 🎯 デュアルエンジン自動検出成功: {mode_name} モードの信号を検出 ({len(cur_bits_list)} パケット)")
                break

        if not decoded_bits_list:
            update_job(
                job_id, progress=100, status="エラー",
                error="SSTV Turbo 信号が検出できませんでした。音声をスピーカーから再生し、スマホのマイクを近づけて最初（1000Hzのピ音）から最後までしっかり録音したファイルを選択してください。"
            )
            return
            
        update_job(job_id, progress=60, status="データベースへの登録中...")

        # ===== Step2: ファクトリからアグリゲータを取得してDB蓄積 =====
        log_dir = getattr(config, "TEXT_LOG_DIR", f"data/digital_turbo_{mode_name.lower()}/logs")
        if not os.path.isabs(log_dir):
            log_dir_path = os.path.join(ROOT_DIR, log_dir)
        else:
            log_dir_path = log_dir
        
        update_job(job_id, progress=70, status="統合処理中 (多数決アグリゲータ)...")
        
        aggregator = SystemFactory.get_aggregator(log_dir=log_dir_path, mode=mode_name)
        aggregator.load_all_logs()
        # 全員のパケットで多数決画像を生成
        aggregator.process_and_save_images(min_tile_ratio=0.0, user_id=None)
            
        update_job(job_id, progress=85, status="復元画像を生成中...")

        output_dir = os.path.join(ROOT_DIR, "web_turbo_png", "static", "output")
        os.makedirs(output_dir, exist_ok=True)

        image_counts = aggregator.db.get_all_image_ids_with_counts(user_id=None)
        available_image_ids = sorted([f"{img_id:04X}" for img_id in image_counts.keys()])

        # ===== Step3: ユーザーの今回ログをパースして自分の受信画像を生成 =====
        user_packets_by_id = {}

        for bits in decoded_bits_list:
            parsed = parse_turbo_log_line(bits, config_module=config)
            if parsed is None:
                continue
            img_id, tile_x, tile_y, plen, pbits, snr_val = parsed
            img_id_hex = f"{img_id:04X}"
            if img_id_hex not in user_packets_by_id:
                user_packets_by_id[img_id_hex] = []
            user_packets_by_id[img_id_hex].append(parsed)

        tile_count_x = config.TILE_COUNT_X
        tile_count_y = config.TILE_COUNT_Y
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

            user_image_buffer = Image.new("RGB", (config.WIDTH, config.HEIGHT), color="black")
            matched_packets = 0

            db_tiles = aggregator.db.get_packets_for_image(int(img_id_hex, 16))

            for parsed in packets:
                img_id_int, tile_x, tile_y, plen, user_payload, snr_val = parsed

                voted_payload = ""
                if tile_y in db_tiles and tile_x in db_tiles[tile_y]:
                    len_dict = db_tiles[tile_y][tile_x]
                    if len_dict:
                        best_plen = max(len_dict.keys(), key=lambda k: sum((p[1] + 1.0) for p in len_dict[k]))
                        db_packets = len_dict[best_plen]
                        payload_bit_len = best_plen * 8
                        score_0 = np.zeros(payload_bit_len, dtype=float)
                        score_1 = np.zeros(payload_bit_len, dtype=float)
                        for row in db_packets:
                            p_bits_str = row[0]
                            p_weight = row[1] + 1.0
                            if len(p_bits_str) < payload_bit_len:
                                continue
                            for i, bit_char in enumerate(p_bits_str[:payload_bit_len]):
                                if bit_char == '1':
                                     score_1[i] += p_weight
                                elif bit_char == '0':
                                     score_0[i] += p_weight
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
                    p_bytes = bits_to_bytearray(user_payload)[:plen]
                    tile_img = None
                    if hasattr(aggregator, 'decode_tile_bytes_safely'):
                        tile_img = aggregator.decode_tile_bytes_safely(p_bytes, config.TILE_SIZE, config.TILE_SIZE)
                    else:
                        tile_img = Image.open(io.BytesIO(p_bytes)).convert("RGB")
                        tile_img.load()

                    if tile_img is not None:
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

            # モードに応じた拡張子と保存フォーマット
            img_ext = ".jpg" if mode_name == "JPEG" else ".png"
            img_fmt = "JPEG" if mode_name == "JPEG" else "PNG"

            # 1. 今回のセッション固有画像を保存 (キャッシュ回避 & 確実な表示)
            session_img_fname = f"user_session_{job_id}_ID_{img_id_hex}{img_ext}"
            session_img_path = os.path.join(output_dir, session_img_fname)
            user_image_buffer.save(session_img_path, format=img_fmt)

            # 2. ユーザー最新単体画像としても保存 (user_{user_id}_ID_{hex}.{ext})
            if user_id:
                user_img_path = os.path.join(output_dir, f"user_{user_id}_ID_{img_id_hex}{img_ext}")
                user_image_buffer.save(user_img_path, format=img_fmt)

            if img_id_hex == current_image_id:
                user_output_url = f"/static/output/{session_img_fname}"
                main_score = round((matched_packets / total_required_packets) * 100, 1)
                if main_score > 100.0:
                    main_score = 100.0
                main_matched = matched_packets

        # 2. このユーザーが過去に送信したすべての画像IDについて累積画像をDBから完全合成して保存
        img_ext = ".jpg" if mode_name == "JPEG" else ".png"
        img_fmt = "JPEG" if mode_name == "JPEG" else "PNG"

        if user_id:
            user_all_counts = aggregator.db.get_all_image_ids_with_counts(user_id=user_id)
            for u_img_id_int in user_all_counts.keys():
                u_img_id_hex = f"{u_img_id_int:04X}"
                user_db_tiles = aggregator.db.get_packets_for_image(u_img_id_int, user_id=user_id)
                u_canvas = Image.new("RGB", (config.WIDTH, config.HEIGHT), color="black")
                for ty in range(config.TILE_COUNT_Y):
                    for tx in range(config.TILE_COUNT_X):
                        len_dict = user_db_tiles[ty][tx]
                        if not len_dict:
                            continue
                        best_plen = max(len_dict.keys(), key=lambda k: sum((p[1] + 1.0) for p in len_dict[k]))
                        pkts = len_dict[best_plen]
                        best_pkt = max(pkts, key=lambda p: p[1])
                        try:
                            p_bytes = bits_to_bytearray(best_pkt[0])
                            tile_img = None
                            if hasattr(aggregator, 'decode_tile_bytes_safely'):
                                tile_img = aggregator.decode_tile_bytes_safely(p_bytes, config.TILE_SIZE, config.TILE_SIZE)
                            else:
                                tile_img = Image.open(io.BytesIO(p_bytes)).convert("RGB")
                                tile_img.load()

                            if tile_img is not None:
                                tw, th = tile_img.size
                                paste_x = tx * tw
                                paste_y = ty * th
                                if paste_x + tw <= config.WIDTH and paste_y + th <= config.HEIGHT:
                                    u_canvas.paste(tile_img, (paste_x, paste_y))
                                else:
                                    tile_img = tile_img.crop((0, 0, min(tw, config.WIDTH - paste_x), min(th, config.HEIGHT - paste_y)))
                                    u_canvas.paste(tile_img, (paste_x, paste_y))
                        except Exception:
                            pass
                # ユーザー累積画像は user_cumulative_ プレフィックスで保存！
                u_accum_path = os.path.join(output_dir, f"user_cumulative_{user_id}_ID_{u_img_id_hex}{img_ext}")
                u_canvas.save(u_accum_path, format=img_fmt)

        if not current_image_id:
            all_ids = list(user_packets_by_id.keys())
            current_image_id = all_ids[0] if all_ids else "UNKNOWN"

        net_img_filename = f"restored_ID_{current_image_id}{img_ext}"

        result_data = {
            "image_id": current_image_id,
            "current_image_id": current_image_id,
            "user_score": main_score,
            "network_score": 0.0,
            "packets_received": max_packets,
            "total_required": total_required_packets,
            "user_image_url": user_output_url,
            "network_image_url": f"/static/output/{net_img_filename}",
            "available_image_ids": available_image_ids,
            "engine_mode": mode_name
        }

        update_job(job_id, progress=100, status="完了", result_data=result_data)

    except Exception as e:
        import traceback
        traceback.print_exc()
        update_job(job_id, progress=100, status="エラー", error=f"処理中にエラーが発生しました: {str(e)}")


@upload_bp.route('/upload', methods=['POST'])
@login_required
def upload_audio():
    user_id = session.get('user_id')
    
    file = request.files.get('file') or request.files.get('audio')
    if not file:
        return jsonify({"error": "No file part", "success": False}), 400

    raw_filename = file.filename or ""
    if not raw_filename or raw_filename == '':
        return jsonify({"error": "No selected file", "success": False}), 400

    if not allowed_file(raw_filename):
        return jsonify({
            "error": "対応していないファイル形式です。.wav, .m4a, .mp3 などの音声ファイルを指定してください。",
            "success": False
        }), 400

    ext = raw_filename.rsplit('.', 1)[1].lower() if '.' in raw_filename else 'wav'
    base_raw = raw_filename.rsplit('.', 1)[0]
    safe_base = secure_filename(base_raw) or f"upload_{int(time.time())}"
    filename = f"{safe_base}.{ext}"
        
    upload_folder = current_app.config['UPLOAD_FOLDER']
    os.makedirs(upload_folder, exist_ok=True)
    
    unique_filename = f"{uuid.uuid4().hex}_{filename}"
    filepath = os.path.join(upload_folder, unique_filename)
    file.save(filepath)

    job_id = create_job()
    cleanup_old_jobs()

    import threading
    app_obj = getattr(current_app, '_get_current_object', lambda: current_app)()
    thread = threading.Thread(
        target=process_upload,
        args=(filepath, filename, job_id, app_obj, user_id)
    )
    thread.daemon = True
    thread.start()

    return jsonify({
        "status": "processing",
        "success": True,
        "job_id": job_id,
        "message": "アップロードを受け付けました。処理を開始します。"
    })
