import os
import sys
import tempfile
import sqlite3

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from web_turbo_png.app import app
from web_turbo_png.services.auth_db import AuthDB, get_auth_db
from web_turbo_png.routes.auth_routes import is_admin

def test_login_resilience():
    print("=================================================================")
    print("🔐 ログイン機能 全パターン徹底デバッグ検証開始")
    print("=================================================================")

    # -------------------------------------------------------------
    # 1. AuthDB 単体レベルでの検証
    # -------------------------------------------------------------
    print("\n--- [1] AuthDB レベルでのエイリアス・自動救済・大文字小文字検証 ---")
    
    # テスト用DBを作成（別ユーザーだけが存在する状態をシミュレート）
    test_db_path = os.path.join(PROJECT_ROOT, "data", "test_auth_debug.db")
    if os.path.exists(test_db_path):
        os.remove(test_db_path)
    
    db = AuthDB(test_db_path)
    # 敢えて管理者以外を先に登録して、_seed_default_admin が走らない状態を偽装
    db.conn.execute("DELETE FROM users")
    db.create_user("dummy@example.com", "password")

    # (A) koseikazu@icloud.com での検証（未登録状態からの自動生成ログイン）
    uid_email = db.verify_user("koseikazu@icloud.com", "123456789")
    assert uid_email is not None, "koseikazu@icloud.com の自動生成ログインに失敗しました"
    print(f"  ✅ [Pass] koseikazu@icloud.com 未登録時からの自動生成ログイン: ID={uid_email}")

    # (B) Nagasaki ユーザー名での検証
    uid_nagasaki = db.verify_user("Nagasaki", "123456789")
    assert uid_nagasaki is not None, "ユーザー名 'Nagasaki' でのログインに失敗しました"
    assert uid_nagasaki == uid_email, f"Nagasaki と email の user_id が一致しません: {uid_nagasaki} != {uid_email}"
    print(f"  ✅ [Pass] ユーザー名 'Nagasaki' でのログイン成功: ID={uid_nagasaki}")

    # (C) admin ユーザー名での検証
    uid_admin = db.verify_user("admin", "123456789")
    assert uid_admin is not None, "ユーザー名 'admin' でのログインに失敗しました"
    print(f"  ✅ [Pass] ユーザー名 'admin' でのログイン成功: ID={uid_admin}")

    # (D) 大文字小文字混在（スマホ自動キャピタライズ）の検証
    uid_caps = db.verify_user("  KoseiKazu@iCloud.COM  ", "123456789")
    assert uid_caps is not None, "大文字混在メールでのログインに失敗しました"
    print(f"  ✅ [Pass] 大文字・前後スペース混在でのログイン成功: ID={uid_caps}")

    uid_nagasaki_caps = db.verify_user("NAGASAKI", "123456789")
    assert uid_nagasaki_caps is not None, "'NAGASAKI' 全大文字でのログインに失敗しました"
    print(f"  ✅ [Pass] 'NAGASAKI' 全大文字でのログイン成功: ID={uid_nagasaki_caps}")

    # (E) 誤ったパスワードの拒絶検証
    uid_wrong = db.verify_user("koseikazu@icloud.com", "wrong_password")
    assert uid_wrong is None, "誤ったパスワードでログインが成功してしまいました"
    print("  ✅ [Pass] 誤ったパスワードの拒絶確認成功")

    # -------------------------------------------------------------
    # 2. Flask Web クライアントによる E2E ログイン検証
    # -------------------------------------------------------------
    print("\n--- [2] Web UI / HTTP レベルでのログイン＆リダイレクト検証 ---")
    app.config['TESTING'] = True
    client = app.test_client()

    # (A) 未ログイン状態でトップページアクセス -> /login にリダイレクトされること
    res_root = client.get('/', follow_redirects=False)
    assert res_root.status_code == 302
    assert '/login' in res_root.headers.get('Location', '')
    print("  ✅ [Pass] 未ログイン時: / -> /login への確実な自動誘導確認")

    # (B) /login 画面が 401 や Basic認証モーダルなしでステータス 200 で開くこと
    res_login_page = client.get('/login')
    assert res_login_page.status_code == 200
    assert "おかえりなさい" in res_login_page.get_data(as_text=True)
    print("  ✅ [Pass] /login 画面表示成功 (HTTP 200, Basic認証ブロックなし)")

    # (C) ユーザー名 'Nagasaki' での Web ログイン
    res_post_nagasaki = client.post('/login', data={
        'email': 'Nagasaki',
        'password': '123456789'
    }, follow_redirects=False)
    assert res_post_nagasaki.status_code == 302, f"Expected 302, got {res_post_nagasaki.status_code}"
    print(f"  ✅ [Pass] 'Nagasaki' でのPOSTログイン成功 -> {res_post_nagasaki.headers.get('Location')}")

    # (D) 'koseikazu@icloud.com' で管理者画面チェック付きでのログイン
    res_post_admin = client.post('/login', data={
        'email': 'koseikazu@icloud.com',
        'password': '123456789',
        'go_to_admin': '1'
    }, follow_redirects=False)
    assert res_post_admin.status_code == 302
    assert '/admin' in res_post_admin.headers.get('Location', '')
    print("  ✅ [Pass] 'koseikazu@icloud.com' + 管理者フラグ -> /admin 遷移成功")

    # (E) ログイン済み状態で /admin にアクセスできること
    res_admin = client.get('/admin')
    assert res_admin.status_code == 200
    assert "SSTV" in res_admin.get_data(as_text=True) or "管理者" in res_admin.get_data(as_text=True)
    print("  ✅ [Pass] ログイン後セッションでの /admin 閲覧確認 (Status: 200)")

    # (F) ログイン済み状態で /app にアクセスできること
    res_app = client.get('/app')
    assert res_app.status_code == 200
    assert "SSTV" in res_app.get_data(as_text=True)
    print("  ✅ [Pass] ログイン後セッションでの /app 閲覧確認 (Status: 200)")

    # クリーンアップ
    if os.path.exists(test_db_path):
        try:
            os.remove(test_db_path)
        except Exception:
            pass

    print("\n=================================================================")
    print("🎉 ログイン機能の全パターン（メール/ユーザー名/大文字/リダイレクト）が")
    print("   100% 確実に動作することを完全実証しました！")
    print("=================================================================")

if __name__ == "__main__":
    test_login_resilience()
