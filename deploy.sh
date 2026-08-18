#!/bin/bash
set -e

echo '🚀 Pulling latest changes from GitHub...'
cd /var/www/sstv-png-stand
git pull origin main

echo '📦 Updating dependencies if needed...'
source .venv/bin/activate
pip install -r requirements.txt

echo '🔄 Restarting Gunicorn service...'
sudo systemctl restart gunicorn

echo '✅ Deployment completed successfully!'
