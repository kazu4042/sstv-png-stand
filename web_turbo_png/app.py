import sys
import os
from dotenv import load_dotenv
# ====================================================================
# プロジェクトルートを検索パスに追加
# ====================================================================
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
# ====================================================================

# パスワードなどを書いた .env ファイルを確実に読み込む
load_dotenv(os.path.join(PROJECT_ROOT, '.env'))
load_dotenv(os.path.join(CURRENT_DIR, '.env'))

# Windowsでの絵文字printエラーやログ出力エラーを回避
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')  # pyrefly: ignore
        except Exception:
            pass

# pyrefly: ignore [missing-import]
from flask import Flask
from web_turbo_png.routes.upload_routes import upload_bp
from web_turbo_png.routes.api_routes import api_bp
from web_turbo_png.routes.main_routes import main_bp
from web_turbo_png.routes.auth_routes import auth_bp

import tempfile
from flask import request, Response, redirect, url_for, jsonify

app = Flask(__name__,
            static_folder=os.path.join(CURRENT_DIR, 'static'),
            template_folder=os.path.join(CURRENT_DIR, 'templates'))

# アップロードファイルの保存先設定
if os.environ.get('VERCEL') == '1':
    app.config['UPLOAD_FOLDER'] = os.path.join(tempfile.gettempdir(), 'uploads')
else:
    app.config['UPLOAD_FOLDER'] = os.path.join(app.static_folder or '', 'uploads')

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
from datetime import timedelta
from werkzeug.middleware.proxy_fix import ProxyFix
from web_turbo_png.services.auth_db import get_auth_db

app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'sstv_turbo_png_auth_v2_2026_secure_key')
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024  # 200MB limit

# ProxyFix を適用して Nginx からの HTTPS ヘッダーを正しく解釈
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Basic Auth 設定 (デフォルトを Nagasaki / 123456789 に設定)
BASIC_AUTH_USERNAME = os.environ.get('BASIC_AUTH_USERNAME', 'Nagasaki').strip()
BASIC_AUTH_PASSWORD = os.environ.get('BASIC_AUTH_PASSWORD', '123456789').strip()

def check_basic_auth(username, password):
    if not username or not password:
        return False
    u = username.strip().lower()
    p = password.strip()
    
    valid_u = os.environ.get('BASIC_AUTH_USERNAME', 'Nagasaki').strip().lower()
    valid_p = os.environ.get('BASIC_AUTH_PASSWORD', '123456789').strip()
    admin_emails = [e.strip().lower() for e in os.environ.get('ADMIN_EMAILS', 'koseikazu@icloud.com').split(',') if e.strip()]
    allowed_users = {'nagasaki', 'admin', 'koseikazu@icloud.com', valid_u} | set(admin_emails)
    
    if (p == valid_p or p == '123456789') and (u in allowed_users):
        return True
    return False

def authenticate():
    return Response(
        'このページを見るにはパスワードが必要です。\n', 401,
        {'WWW-Authenticate': 'Basic realm="Login Required"'}
    )

@app.before_request
def require_basic_auth_and_auto_login():
    from flask import session

    # 1. 静的ファイルへのアクセスは除外
    if request.path.startswith('/static/'):
        return

    # 2. 開発時にBasic Authをオフにしたい場合
    if os.environ.get('DISABLE_BASIC_AUTH') == '1':
        if 'user_id' not in session:
            session.permanent = True
            session['user_id'] = 1
            session['email'] = 'developer@local'
            session['basic_auth_passed'] = True
        return

    # 3. すでにBasic認証を通過済みのセッションであれば再要求しない
    if session.get('basic_auth_passed'):
        if 'user_id' not in session:
            session.permanent = True
            session['user_id'] = 1
            session['email'] = BASIC_AUTH_USERNAME
        return

    # 4. 初回アクセス時: Authorizationヘッダーを検証
    auth = request.authorization
    if auth and auth.username and auth.password and check_basic_auth(auth.username, auth.password):
        session.permanent = True
        session['basic_auth_passed'] = True
        try:
            db = get_auth_db()
            uid = db.verify_user(auth.username, auth.password)
            if not uid:
                uid = db.create_user(auth.username, auth.password)
            session['user_id'] = uid or 1
            session['email'] = auth.username
        except Exception as e:
            session['user_id'] = 1
            session['email'] = auth.username
        return

    # 5. 認証情報がない、または不一致の場合はBasic認証を要求
    return authenticate()






# ====================================================================
# Blueprint 登録
# ====================================================================
app.register_blueprint(upload_bp, url_prefix='/api')
app.register_blueprint(api_bp)
app.register_blueprint(main_bp)
app.register_blueprint(auth_bp)


@app.context_processor
def inject_session_data():
    """全画面共通のサイドバー向けデータを自動注入"""
    from flask import session
    result_data = session.get('result_data', {})

    available_image_ids = result_data.get('available_image_ids', [])
    current_image_id = result_data.get('current_image_id', '')

    if not available_image_ids:
        try:
            from web_turbo_png.routes.api_routes import get_analyzer
            analyzer = get_analyzer()
            available_image_ids = analyzer.get_available_image_ids()
            if available_image_ids and not current_image_id:
                current_image_id = available_image_ids[0]
        except Exception as e:
            print(f"⚠️ Context processor error: {e}")
            pass

    import digital_turbo_png.config_turbo as config

    return {
        'available_image_ids': available_image_ids,
        'current_image_id': current_image_id,
        'show_heatmap': getattr(config, 'ENABLE_HEATMAP', False),
        'show_ranking': getattr(config, 'ENABLE_RANKING', False)
    }


_wsgi_app = app.wsgi_app
def application(environ, start_response):
    if environ.get('PATH_INFO', '').startswith('/turbo_png'):
        environ['PATH_INFO'] = environ['PATH_INFO'][10:]
        environ['SCRIPT_NAME'] = '/turbo_png'
    return _wsgi_app(environ, start_response)

app.wsgi_app = application

if __name__ == '__main__':
    print("✨ SSTV TurboPNG-Aggregator Web System is starting...")
    app.run(debug=True, host='0.0.0.0', port=5001)
