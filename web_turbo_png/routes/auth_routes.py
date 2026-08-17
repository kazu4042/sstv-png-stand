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

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        db = get_auth_db()
        user_id = db.verify_user(email, password)
        
        if user_id:
            session['user_id'] = user_id
            session['email'] = email
            next_url = request.form.get('next')
            go_to_admin = request.form.get('go_to_admin')
            
            # チェックボックスがオンの場合は管理者画面へ強制移動
            if go_to_admin == '1':
                admin_emails = os.environ.get('ADMIN_EMAILS', '').split(',')
                if email not in admin_emails:
                    session.clear()
                    return render_template('login.html', error='不正なログインです。')
                return redirect(url_for('auth.admin_dashboard'))
            
            # オープンリダイレクト脆弱性対策: next_url が相対パス（'/'で始まり '//'で始まらない）であることを確認
            if next_url and not (next_url.startswith('/') and not next_url.startswith('//')):
                next_url = url_for('main.index')
                
            return redirect(next_url or url_for('main.index'))
        else:
            return render_template('login.html', error='メールアドレスまたはパスワードが間違っています。')
            
    return render_template('login.html')

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
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
    return redirect(url_for('main.index'))

@auth_bp.route('/admin')
@login_required
def admin_dashboard():
    # 管理者権限のチェック
    admin_emails = os.environ.get('ADMIN_EMAILS', '').split(',')
    current_email = session.get('email')
    
    if current_email not in admin_emails:
        # 管理者でない場合はトップページへリダイレクト
        return redirect(url_for('main.index'))
        
    db = get_auth_db()
    users = db.get_all_users()
    
    return render_template('admin.html', users=users)
