# 🚀 SSTV TurboPNG VPS デプロイ手順書 (Ubuntu / Debian 向け)

VPSサーバー（ConoHa, さくらのVPS, AWS EC2, Linode など）で本アプリケーションを常時稼働させるための完全手順です。

---

## 1. サーバーの初期準備 & パッケージインストール
VPSにSSHログイン後、必要なパッケージをインストールします。

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv nginx git
```

---

## 2. プロジェクトの配置 & 権限設定
プロジェクトを `/var/www/sstv-png-stand` に配置します。

```bash
# ディレクトリ作成
sudo mkdir -p /var/www/sstv-png-stand
sudo chown -R $USER:$USER /var/www/sstv-png-stand

# Gitからクローン（またはファイルを転送）
git clone <あなたのリポジトリURL> /var/www/sstv-png-stand
cd /var/www/sstv-png-stand
```

---

## 3. Python 仮想環境の構築 & 依存パッケージのインストール
仮想環境を作成し、必要なライブラリを一括インストールします。

```bash
# 仮想環境の作成
python3 -m venv .venv

# 仮想環境のアクティベート
source .venv/bin/activate

# パッケージのインストール
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 4. 環境変数 (.env) の設定
`.env.example` をコピーして本番用 `.env` を作成します。

```bash
cp .env.example .env
nano .env
```
> **設定項目:**
> - `BASIC_AUTH_USERNAME`: サイト閲覧用のBasic認証ユーザー名
> - `BASIC_AUTH_PASSWORD`: サイト閲覧用のBasic認証パスワード
> - `FLASK_SECRET_KEY`: ランダムな長い秘密鍵（例: `openssl rand -hex 24` で生成）
> - `ADMIN_EMAILS`: 管理者権限を付与するメールアドレス

---

## 5. 所有権・パーミッションの設定
Webサーバー (Nginx / Gunicorn: `www-data`) が画像やDBファイルに書き込めるように権限を設定します。

```bash
sudo chown -R www-data:www-data /var/www/sstv-png-stand
sudo chmod -R 775 /var/www/sstv-png-stand/data
sudo chmod -R 775 /var/www/sstv-png-stand/web_turbo_png/static
```

---

## 6. Gunicorn (systemd サービス) の登録 & 起動
バックグラウンドでPythonアプリを常時起動し、障害時に自動再起動するように設定します。

```bash
# サービスファイルのコピー
sudo cp deployment/gunicorn.service /etc/systemd/system/

# systemdの再読み込みと起動・自動起動の有効化
sudo systemctl daemon-reload
sudo systemctl start gunicorn
sudo systemctl enable gunicorn

# 動作確認（active (running) になっていればOK）
sudo systemctl status gunicorn
```

---

## 7. Nginx の設定 & 起動
リバースプロキシとして Nginx を設定します。

```bash
# Nginx 設定ファイルのコピー
sudo cp deployment/sstv-nginx.conf /etc/nginx/sites-available/sstv

# IPアドレスまたはドメインに合わせて編集
sudo nano /etc/nginx/sites-available/sstv
# (server_name example.com 123.456.78.90; の部分をVPSのIPやドメインに変更)

# 設定の有効化 & デフォルトページの無効化
sudo ln -s /etc/nginx/sites-available/sstv /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default

# 構文チェック & Nginxの再起動
sudo nginx -t
sudo systemctl restart nginx
```

---

## 8. ファイアウォール設定 (UFW)
HTTP (80番ポート) および SSH (22番ポート) の通信を許可します。

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

---

## 🔍 トラブルシューティング / ログ確認コマンド

- **Gunicorn のリアルタイムログ:**
  ```bash
  sudo journalctl -u gunicorn -f
  ```
- **Nginx のアクセスログ / エラーログ:**
  ```bash
  sudo tail -f /var/log/nginx/error.log
  ```
- **アプリの再起動:**
  ```bash
  sudo systemctl restart gunicorn
  ```
