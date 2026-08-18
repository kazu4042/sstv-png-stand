from flask import Blueprint, request, jsonify, session, redirect, url_for, render_template
from web_turbo_png.services.auth_db import get_auth_db
from functools import wraps
import os

auth_bp = Blueprint('auth', __name__)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            # For API routes
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Unauthorized'}), 401
            # For template routes
            return redirect(url_for('auth.login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def is_admin(email_or_user):
    if not email_or_user:
        return False
    user_str = str(email_or_user).strip().lower()
    admin_emails = [e.strip().lower() for e in os.environ.get('ADMIN_EMAILS', '').split(',') if e.strip()]
    basic_user = os.environ.get('BASIC_AUTH_USERNAME', 'admin').strip().lower()
    return user_str in admin_emails or user_str == basic_user

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        
        db = get_auth_db()
        user_id = db.verify_user(email, password)
        
        if user_id:
            session['user_id'] = user_id
            session['email'] = email
            next_url = request.form.get('next')
            go_to_admin = request.form.get('go_to_admin')
            
            # チェックボックスがオンの場合は管理者画面へ移動
            if go_to_admin == '1':
                if not is_admin(email):
                    # 管理者でない場合はメッセージを表示して通常画面へ
                    return redirect(url_for('main.index'))
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
            session['user_id'] = user_id
            session['email'] = email
            return redirect(url_for('main.index'))
        else:
            return render_template('register.html', error='このメールアドレスは既に登録されています。')
            
    return render_template('register.html')

@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('main.landing'))

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
    
    return render_template('admin.html', users=users)

