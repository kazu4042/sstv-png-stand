from flask import Blueprint, request, jsonify, session, redirect, url_for, render_template
from web_turbo_png.services.auth_db import get_auth_db
from functools import wraps
import os
import sys
import time

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

auth_bp = Blueprint('auth', __name__)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            # 未ログインの場合
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Unauthorized', 'status': 'error'}), 401
            return redirect(url_for('auth.login', next=request.url))

        return f(*args, **kwargs)
    return decorated_function


def is_admin(email_or_user):
    if not email_or_user:
        return False
    user_str = str(email_or_user).strip().lower()
    admin_emails = [e.strip().lower() for e in os.environ.get('ADMIN_EMAILS', 'koseikazu@icloud.com').split(',') if e.strip()]
    basic_user = os.environ.get('BASIC_AUTH_USERNAME', 'Nagasaki').strip().lower()
    return user_str in admin_emails or user_str == basic_user or user_str in ['nagasaki', 'admin', 'koseikazu@icloud.com']


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        
        db = get_auth_db()
        user_id = db.verify_user(email, password)
        
        if user_id:
            user_data = db.get_user_by_id(user_id)
            effective_email = user_data['email'] if user_data else email

            session.permanent = False
            session['user_id'] = user_id
            session['email'] = effective_email
            session['basic_auth_passed'] = True  # Basic認証も通過扱いにする
            
            next_url = request.form.get('next')
            go_to_admin = request.form.get('go_to_admin')
            
            # チェックボックスがオン、または next が /admin の場合は管理者画面へ移動
            if go_to_admin == '1' or (next_url and next_url.startswith('/admin')):
                if is_admin(effective_email) or is_admin(email):
                    return redirect(url_for('auth.admin_dashboard'))
            
            # オープンリダイレクト脆弱性・無限ループ対策
            if next_url and next_url.startswith('/') and not next_url.startswith('//'):
                if not next_url.startswith('/login') and not next_url.startswith('/logout'):
                    return redirect(next_url)
                
            return redirect(url_for('main.index'))
        else:
            return render_template('login.html', error='メールアドレス/ユーザー名 または パスワードが間違っています。')
            
    return render_template('login.html')

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        
        if not email or not password:
            return render_template('register.html', error='メールアドレスとパスワードを入力してください。')
            
        db = get_auth_db()
        user_id = db.create_user(email, password)
        
        if user_id:
            session.permanent = True
            session['user_id'] = user_id
            session['email'] = email
            session['basic_auth_passed'] = True
            return redirect(url_for('main.index'))
        else:
            return render_template('register.html', error='このメールアドレスは既に登録されています。')
            
    return render_template('register.html')


@auth_bp.route('/logout')
def logout():
    basic_passed = session.get('basic_auth_passed', True)
    session.clear()
    session['basic_auth_passed'] = basic_passed
    return redirect(url_for('auth.login'))


@auth_bp.route('/admin')
@login_required
def admin_dashboard():
    # 管理者権限のチェック
    current_email = session.get('email')
    
    if not is_admin(current_email):
        # 管理者でない場合はトップページへリダイレクト
        return redirect(url_for('main.index'))
        
    from web_turbo_png.routes.api_routes import get_analyzer
    from core.system_factory import SystemFactory

    current_engine_mode = SystemFactory.get_mode()

    analyzer_png = get_analyzer(mode='PNG')
    images_png = analyzer_png.get_all_images_summary(mode='PNG')
    for img in images_png:
        img['engine_mode'] = 'PNG'

    analyzer_jpeg = get_analyzer(mode='JPEG')
    images_jpeg = analyzer_jpeg.get_all_images_summary(mode='JPEG')
    for img in images_jpeg:
        img['engine_mode'] = 'JPEG'

    current_images = images_jpeg if current_engine_mode == 'JPEG' else images_png

    from web_turbo_png.services.audio_manager import AudioManager
    audio_tracks, active_tracks = AudioManager.list_tracks()

    return render_template(
        'admin.html',
        images=current_images,
        png_count=len(images_png),
        jpeg_count=len(images_jpeg),
        all_count=len(images_png) + len(images_jpeg),
        current_engine_mode=current_engine_mode,
        audio_tracks=audio_tracks,
        active_tracks=active_tracks
    )


@auth_bp.route('/admin/api/images', methods=['GET'])
@login_required
def admin_get_images():
    current_email = session.get('email')
    if not is_admin(current_email):
        return jsonify({'status': 'error', 'message': '権限がありません'}), 403

    from web_turbo_png.routes.api_routes import get_analyzer
    from core.system_factory import SystemFactory

    current_mode = SystemFactory.get_mode()
    req_mode = request.args.get('mode', current_mode).upper()

    analyzer_png = get_analyzer(mode='PNG')
    images_png = analyzer_png.get_all_images_summary(mode='PNG')
    for img in images_png:
        img['engine_mode'] = 'PNG'

    analyzer_jpeg = get_analyzer(mode='JPEG')
    images_jpeg = analyzer_jpeg.get_all_images_summary(mode='JPEG')
    for img in images_jpeg:
        img['engine_mode'] = 'JPEG'

    if req_mode == 'PNG':
        display_images = images_png
    elif req_mode == 'JPEG':
        display_images = images_jpeg
    elif req_mode == 'ALL':
        display_images = images_png + images_jpeg
    else:
        display_images = images_jpeg if current_mode == 'JPEG' else images_png

    return jsonify({
        'status': 'success',
        'current_engine_mode': current_mode,
        'requested_mode': req_mode,
        'images': display_images,
        'count': len(display_images),
        'png_count': len(images_png),
        'jpeg_count': len(images_jpeg),
        'all_count': len(images_png) + len(images_jpeg)
    })


@auth_bp.route('/admin/api/engine_mode', methods=['GET', 'POST'])
@login_required
def admin_engine_mode():
    current_email = session.get('email')
    if not is_admin(current_email):
        return jsonify({'status': 'error', 'message': '権限がありません'}), 403

    from core.system_factory import SystemFactory
    from web_turbo_png.routes.api_routes import invalidate_analyzer_cache

    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        mode = data.get('mode') or request.form.get('mode')
        if not mode or mode.upper() not in ('PNG', 'JPEG'):
            return jsonify({'status': 'error', 'message': 'モードは PNG または JPEG を指定してください'}), 400

        SystemFactory.set_mode(mode.upper())
        invalidate_analyzer_cache()
        return jsonify({
            'status': 'success',
            'mode': SystemFactory.get_mode(),
            'current_mode': SystemFactory.get_mode(),
            'message': f'デコードエンジンを {SystemFactory.get_mode()} モードに変更しました'
        })

    return jsonify({
        'status': 'success',
        'mode': SystemFactory.get_mode(),
        'current_mode': SystemFactory.get_mode()
    })


@auth_bp.route('/admin/api/activity_stats')
@login_required
def admin_activity_stats():
    current_email = session.get('email')
    if not is_admin(current_email):
        return jsonify({'status': 'error', 'message': '権限がありません'}), 403

    period = request.args.get('period', 'today')
    if period not in ['today', '24h', '7d', '30d']:
        period = 'today'

    db = get_auth_db()
    graph_data = db.get_activity_graph_data(period=period)
    overview_stats = db.get_today_overview_stats()

    from web_turbo_png.routes.api_routes import get_analyzer
    analyzer = get_analyzer()
    packet_traffic = analyzer.get_hourly_packet_traffic(period=period)

    return jsonify({
        'status': 'success',
        'graph': graph_data,
        'packet_traffic': packet_traffic,
        'overview': overview_stats
    })


@auth_bp.route('/admin/api/realtime_stats')
@login_required
def admin_realtime_stats():
    current_email = session.get('email')
    if not is_admin(current_email):
        return jsonify({'status': 'error', 'message': '権限がありません'}), 403

    db = get_auth_db()
    overview_stats = db.get_today_overview_stats()
    recent_logs = db.get_recent_access_logs(limit=15)

    from web_turbo_png.routes.api_routes import get_analyzer
    analyzer = get_analyzer()
    snr_stats = analyzer.get_snr_analytics()
    system_health = analyzer.get_system_health_metrics()

    return jsonify({
        'status': 'success',
        'overview': overview_stats,
        'recent_logs': recent_logs,
        'snr_stats': snr_stats,
        'system_health': system_health
    })


@auth_bp.route('/admin/api/optimize_db', methods=['POST'])
@login_required
def admin_optimize_db():
    current_email = session.get('email')
    if not is_admin(current_email):
        return jsonify({'status': 'error', 'message': '権限がありません'}), 403

    from web_turbo_png.routes.api_routes import get_analyzer
    analyzer = get_analyzer()
    success = analyzer.optimize_db()

    if success:
        return jsonify({'status': 'success', 'message': 'データベースの VACUUM 最適化が完了しました'})
    else:
        return jsonify({'status': 'error', 'message': '最適化処理中にエラーが発生しました'}), 500


@auth_bp.route('/admin/api/clear_cache', methods=['POST'])
@login_required
def admin_clear_cache():
    current_email = session.get('email')
    if not is_admin(current_email):
        return jsonify({'status': 'error', 'message': '権限がありません'}), 403

    from web_turbo_png.routes.api_routes import invalidate_analyzer_cache
    invalidate_analyzer_cache()
    return jsonify({'status': 'success', 'message': 'アナライザーキャッシュをクリアしました'})



@auth_bp.route('/admin/clear_all_images', methods=['POST'])
@auth_bp.route('/admin/api/clear_all_images', methods=['POST'])
@login_required
def admin_clear_all_images():
    current_email = session.get('email')
    if not is_admin(current_email):
        return jsonify({'status': 'error', 'message': '権限がありません'}), 403

    data = request.get_json(silent=True) or {}
    mode_target = data.get('mode') # 'PNG', 'JPEG', or None/'' (全クリア)

    from web_turbo_png.routes.api_routes import get_analyzer
    analyzer = get_analyzer()

    if mode_target and mode_target.upper() in ('PNG', 'JPEG'):
        target_m = mode_target.upper()
        result = analyzer.clear_all_images(mode_only=True, target_mode=target_m)
        msg = f"{target_m} エンジンの画像データをクリアしました（パケット数: {result['deleted_packets']}）"
    else:
        result = analyzer.clear_all_images(mode_only=False)
        msg = f"全画像データを一括クリアしました（パケット数: {result['deleted_packets']}）"

    return jsonify({
        'status': 'success',
        'message': msg,
        'result': result
    })


@auth_bp.route('/admin/delete_images', methods=['POST'])
@login_required
def admin_delete_images():
    current_email = session.get('email')
    if not is_admin(current_email):
        return jsonify({'status': 'error', 'message': '権限がありません'}), 403

    # JSONまたはフォームから画像IDリストを取得
    data = request.get_json(silent=True) or {}
    
    # 全削除フラグがある場合
    if data.get('clear_all') is True:
        from web_turbo_png.routes.api_routes import get_analyzer
        analyzer = get_analyzer()
        result = analyzer.clear_all_images()
        return jsonify({
            'status': 'success',
            'message': f"全画像データを一括クリアしました（クリア画像: {result['deleted_images']}件, パケット数: {result['deleted_packets']}）",
            'result': result
        })

    image_ids = data.get('image_ids')
    
    if not image_ids and 'image_ids' in request.form:
        image_ids = request.form.getlist('image_ids')
        if not image_ids and request.form.get('image_ids'):
            image_ids = [request.form.get('image_ids')]

    if not image_ids:
        return jsonify({'status': 'error', 'message': '削除対象の画像IDが指定されていません'}), 400

    from web_turbo_png.routes.api_routes import get_analyzer
    analyzer = get_analyzer()
    result = analyzer.delete_images(image_ids)

    return jsonify({
        'status': 'success',
        'message': f"{result['deleted_images']}件の画像をクリーンしました（削除パケット数: {result['deleted_packets']}）",
        'result': result
    })


@auth_bp.route('/admin/delete_user', methods=['POST'])
@login_required
def admin_delete_user():
    current_email = session.get('email')
    if not is_admin(current_email):
        return jsonify({'status': 'error', 'message': '権限がありません'}), 403

    data = request.get_json(silent=True) or {}
    user_id = data.get('user_id') or request.form.get('user_id')

    if not user_id:
        return jsonify({'status': 'error', 'message': 'ユーザーIDが指定されていません'}), 400

    db = get_auth_db()
    db.delete_user(int(user_id))

    return jsonify({'status': 'success', 'message': f'ユーザー #{user_id} を削除しました'})


# ==========================================
# 音声トラック管理 API (Audio Tracks Manager)
# ==========================================

@auth_bp.route('/admin/api/audio/list', methods=['GET'])
@login_required
def admin_audio_list():
    current_email = session.get('email')
    if not is_admin(current_email):
        return jsonify({'status': 'error', 'message': '権限がありません'}), 403

    from web_turbo_png.services.audio_manager import AudioManager
    mode = request.args.get('mode')
    tracks, active_map = AudioManager.list_tracks(mode=mode)
    return jsonify({
        'status': 'success',
        'tracks': tracks,
        'active': active_map
    })


@auth_bp.route('/admin/api/audio/upload', methods=['POST'])
@login_required
def admin_audio_upload():
    """音声ファイル（.wav 等）の直接アップロード、または画像からの SSTV 自動エンコード"""
    current_email = session.get('email')
    if not is_admin(current_email):
        return jsonify({'status': 'error', 'message': '権限がありません'}), 403

    if 'file' not in request.files:
        return jsonify({'status': 'error', 'message': 'ファイルが選択されていません'}), 400

    file = request.files['file']
    if not file or not file.filename:
        return jsonify({'status': 'error', 'message': 'ファイル名が無効です'}), 400

    label = request.form.get('label', '').strip() or file.filename
    mode = request.form.get('mode', 'JPEG').upper()
    upload_type = request.form.get('type', 'auto')  # 'audio', 'image', or 'auto'
    description = request.form.get('description', '').strip()

    if mode not in ('PNG', 'JPEG'):
        mode = 'JPEG'

    filename = file.filename
    ext = os.path.splitext(filename)[1].lower()

    # 一時保存
    temp_dir = os.path.join(ROOT_DIR, "data", "temp_uploads")
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, f"upload_{int(time.time())}_{filename}")
    file.save(temp_path)

    try:
        from web_turbo_png.services.audio_manager import AudioManager

        # 画像ファイルの場合 (または type='image') -> 自動エンコード
        if upload_type == 'image' or ext in ('.jpg', '.jpeg', '.png', '.bmp', '.webp'):
            track_info = AudioManager.encode_image_to_audio(
                image_path=temp_path,
                label=label,
                mode=mode,
                description=description
            )
            msg = f"画像から {mode} 音声の自動エンコードが完了しました (全{track_info.get('packets')}パケット)"
        else:
            # 音声ファイルの場合 -> 44.1kHz WAV 正規化保存
            track_info = AudioManager.add_audio_file(
                src_path=temp_path,
                filename=filename,
                label=label,
                mode=mode,
                description=description
            )
            msg = f"音声トラック「{track_info.get('label')}」を登録しました"

        return jsonify({
            'status': 'success',
            'message': msg,
            'track': track_info
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f"アップロード・変換エラー: {str(e)}"
        }), 500
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


@auth_bp.route('/admin/api/audio/set_active', methods=['POST'])
@login_required
def admin_audio_set_active():
    """モード別のアクティブ再生トラックを設定"""
    current_email = session.get('email')
    if not is_admin(current_email):
        return jsonify({'status': 'error', 'message': '権限がありません'}), 403

    data = request.get_json(silent=True) or {}
    filename = data.get('filename') or request.form.get('filename')
    mode = (data.get('mode') or request.form.get('mode', 'JPEG')).upper()

    if not filename:
        return jsonify({'status': 'error', 'message': 'トラックが指定されていません'}), 400

    try:
        from web_turbo_png.services.audio_manager import AudioManager
        active_map = AudioManager.set_active_track(mode=mode, filename=filename)
        return jsonify({
            'status': 'success',
            'message': f'{mode} モードの再生音声を「{filename}」に設定しました',
            'active': active_map
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400


@auth_bp.route('/admin/api/audio/delete', methods=['POST'])
@login_required
def admin_audio_delete():
    """実験用トラックを削除"""
    current_email = session.get('email')
    if not is_admin(current_email):
        return jsonify({'status': 'error', 'message': '権限がありません'}), 403

    data = request.get_json(silent=True) or {}
    filename = data.get('filename') or request.form.get('filename')

    if not filename:
        return jsonify({'status': 'error', 'message': '削除対象のトラックが指定されていません'}), 400

    try:
        from web_turbo_png.services.audio_manager import AudioManager
        AudioManager.delete_track(filename)
        return jsonify({
            'status': 'success',
            'message': f'音声トラック「{filename}」を削除しました'
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400


