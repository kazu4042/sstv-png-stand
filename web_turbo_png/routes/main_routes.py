import os
import sys
import time
import json
from flask import Blueprint, render_template, session, request, redirect, url_for
from pathlib import Path
from web_turbo_png.routes.auth_routes import login_required

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import digital_turbo_png.config_turbo as config

main_bp = Blueprint('main', __name__)


@main_bp.route('/')
def landing():
    """ホーム画面"""
    return render_template('landing.html', engine_type='TurboPNG')

@main_bp.route('/app')
@login_required
def index():
    """ファイルアップロード画面（メインアプリ）"""
    return render_template('index.html')


@main_bp.route('/analytics')
def analytics():
    """アナリティクス画面を表示"""
    return render_template(
        'analytics.html',
        timestamp=int(time.time()),
        width=config.WIDTH,
        height=config.HEIGHT,
        image_width=config.WIDTH,
        image_height=config.HEIGHT,
        tile_count_x=config.TILE_COUNT_X,
        tile_count_y=config.TILE_COUNT_Y,
        tile_size=config.TILE_SIZE,
        snr_max_thresh=getattr(config, 'SNR_MAX_THRESH', 14),
        snr_min_thresh=getattr(config, 'SNR_MIN_THRESH', 0)
    )


@main_bp.route('/ranking')
def ranking():
    """受信者ランキング画面"""
    if not getattr(config, 'ENABLE_RANKING', False):
        from flask import redirect, url_for
        return redirect(url_for('main.result'))

    ranking_json_path = Path(__file__).parent.parent / "static" / "data" / "rankings.json"
    rankings_data = []

    if ranking_json_path.is_file():
        try:
            with open(ranking_json_path, 'r', encoding='utf-8') as f:
                rankings_dict = json.load(f)
            rankings_data = [
                {"callsign": callsign, **data}
                for callsign, data in rankings_dict.items()
            ]
        except Exception as e:
            print(f"⚠️ ランキングデータ読み込みエラー: {e}")

    result_data = session.get('result_data', {})
    my_callsign = result_data.get('callsign', '')

    return render_template(
        'ranking.html',
        rankings=rankings_data,
        timestamp=int(time.time()),
        my_callsign=my_callsign
    )


@main_bp.route('/result')
@login_required
def result():
    """デコード結果画面を表示"""
    job_id = request.args.get('job_id')
    if job_id:
        from web_turbo_png.routes.upload_routes import jobs
        if job_id in jobs:
            session['result_data'] = jobs[job_id].get("result_data", {})
    
    result_data = session.get('result_data', {})
    return render_template(
        'result.html',
        show_heatmap=getattr(config, 'ENABLE_HEATMAP', False),
        show_ranking=getattr(config, 'ENABLE_RANKING', False),
        **result_data
    )


@main_bp.route('/calendar')
@login_required
def calendar():
    """カレンダー（履歴）画面"""
    return render_template(
        'calendar.html',
        show_heatmap=getattr(config, 'ENABLE_HEATMAP', False),
        show_ranking=getattr(config, 'ENABLE_RANKING', False)
    )


@main_bp.route('/heatmap')
def heatmap():
    """SNRヒートマップ画面"""
    if not getattr(config, 'ENABLE_HEATMAP', False):
        from flask import redirect, url_for
        return redirect(url_for('main.result'))
    return render_template('heatmap.html')
