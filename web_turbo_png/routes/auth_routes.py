from flask import Blueprint, request, jsonify, session, redirect, url_for, render_template
from web_turbo_png.services.auth_db import get_auth_db
from functools import wraps
import os

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
            session.permanent = False
            session['user_id'] = user_id
            session['email'] = email
            session['basic_auth_passed'] = True  # Basic認証も通過扱いにする
            
            next_url = request.form.get('next')
            go_to_admin = request.form.get('go_to_admin')
            
            # チェックボックスがオンの場合は管理者画面へ移動
            if go_to_admin == '1':
                if is_admin(email):
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
        
    db = get_auth_db()
    users = db.get_all_users()
    
    from web_turbo_png.routes.api_routes import get_analyzer
    analyzer = get_analyzer()
    images = analyzer.get_all_images_summary()

    # アクセス統計データ
    overview_stats = db.get_today_overview_stats()
    initial_graph = db.get_activity_graph_data(period='today')
    top_pages = db.get_top_pages_stats(days=7, limit=5)
    recent_logs = db.get_recent_access_logs(limit=15)

    # 電波品質・SNR統計
    snr_stats = analyzer.get_snr_analytics()

    # トップ貢献者ランキング
    top_contributors = analyzer.get_top_contributors(limit=8)

    # パケットトラフィック推移 (今日)
    packet_traffic = analyzer.get_hourly_packet_traffic(period='today')

    # システム健全度・ストレージメトリクス
    system_health = analyzer.get_system_health_metrics()

    # 復元達成サマリー
    restoration_overview = analyzer.get_restoration_overview()
    
    return render_template(
        'admin.html',
        users=users,
        images=images,
        overview_stats=overview_stats,
        initial_graph=initial_graph,
        top_pages=top_pages,
        recent_logs=recent_logs,
        snr_stats=snr_stats,
        top_contributors=top_contributors,
        packet_traffic=packet_traffic,
        system_health=system_health,
        restoration_overview=restoration_overview
    )


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



@auth_bp.route('/admin/delete_images', methods=['POST'])
@login_required
def admin_delete_images():
    current_email = session.get('email')
    if not is_admin(current_email):
        return jsonify({'status': 'error', 'message': '権限がありません'}), 403

    # JSONまたはフォームから画像IDリストを取得
    data = request.get_json(silent=True) or {}
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

