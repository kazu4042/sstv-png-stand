import sys
import os

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

# Windowsでの絵文字printエラーやログ出力エラーを回避
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

from flask import Flask
from web_turbo_png.routes.upload_routes import upload_bp
from web_turbo_png.routes.api_routes import api_bp
from web_turbo_png.routes.main_routes import main_bp

import tempfile

app = Flask(__name__,
            static_folder=os.path.join(CURRENT_DIR, 'static'),
            template_folder=os.path.join(CURRENT_DIR, 'templates'))

# アップロードファイルの保存先設定
if os.environ.get('VERCEL') == '1':
    app.config['UPLOAD_FOLDER'] = os.path.join(tempfile.gettempdir(), 'uploads')
else:
    app.config['UPLOAD_FOLDER'] = os.path.join(app.static_folder, 'uploads')

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key-turbo-png')
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# ====================================================================
# Blueprint 登録
# ====================================================================
app.register_blueprint(upload_bp, url_prefix='/api')
app.register_blueprint(api_bp)
app.register_blueprint(main_bp)


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
