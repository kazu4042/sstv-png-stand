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

from core.system_factory import SystemFactory

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
    config = SystemFactory.get_config()
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
    config = SystemFactory.get_config()
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
    """デコード結果画面を表示（現在のシステムモードで厳密に分離）"""
    job_id = request.args.get('job_id')
    req_image_id = request.args.get('image_id')
    user_id = session.get('user_id')
    result_data = {}
    
    if job_id:
        from web_turbo_png.services.job_manager import get_job
        job = get_job(job_id)
        if job and "result_data" in job:
            result_data = job.get("result_data", {})
    
    current_engine_mode = SystemFactory.get_mode()
    from web_turbo_png.routes.api_routes import get_analyzer
    analyzer = get_analyzer(mode=current_engine_mode)
    available_ids = analyzer.get_available_image_ids(user_id=None)

    target_image_id = req_image_id or result_data.get('current_image_id') or result_data.get('image_id')
    if not target_image_id and available_ids:
        target_image_id = available_ids[0]

    result_data = dict(result_data)
    result_data['available_image_ids'] = available_ids
    result_data['current_engine_mode'] = current_engine_mode

    if target_image_id:
        status_info = analyzer.get_image_status(target_image_id, user_id=user_id)
        job_user_img = result_data.get('user_image_url') or result_data.get('user_output_url')
        
        result_data['current_image_id'] = target_image_id
        result_data['main_score'] = result_data.get('main_score') or status_info['user_score']
        result_data['contribution_score'] = result_data.get('contribution_score') or status_info['user_score']
        result_data['network_score'] = status_info['network_score']
        result_data['network_received'] = status_info['network_received']
        
        # ジョブ固有の送信画像があれば最優先で採用（送信直後の確実な表示）
        chosen_user_img = job_user_img or status_info['user_img_url'] or ''
        result_data['user_output_url'] = chosen_user_img
        result_data['user_has_data'] = bool(chosen_user_img or status_info['user_has_data'])
        result_data['network_image_url'] = status_info['restored_img_url'] or result_data.get('network_image_url') or ''

    config = SystemFactory.get_config(current_engine_mode)
    result_data.pop('current_engine_mode', None)
    result_data.pop('available_image_ids', None)
    return render_template(
        'result.html',
        current_engine_mode=current_engine_mode,
        available_image_ids=available_ids,
        show_heatmap=getattr(config, 'ENABLE_HEATMAP', False),
        show_ranking=getattr(config, 'ENABLE_RANKING', False),
        **result_data
    )



@main_bp.route('/calendar')
@login_required
def calendar():
    """カレンダー（履歴）画面"""
    config = SystemFactory.get_config()
    return render_template(
        'calendar.html',
        show_heatmap=getattr(config, 'ENABLE_HEATMAP', False),
        show_ranking=getattr(config, 'ENABLE_RANKING', False)
    )


@main_bp.route('/heatmap')
def heatmap():
    """SNRヒートマップ画面"""
    config = SystemFactory.get_config()
    if not getattr(config, 'ENABLE_HEATMAP', False):
        from flask import redirect, url_for
        return redirect(url_for('main.result'))
    return render_template('heatmap.html')


@main_bp.route('/demo')
@login_required
def demo_player():
    """音声再生ページ（PNG / JPEG モード別音声再生）"""
    current_mode = SystemFactory.get_mode()
    req_mode = request.args.get('mode', current_mode).upper()
    if req_mode not in ('PNG', 'JPEG'):
        req_mode = current_mode

    if req_mode == 'JPEG':
        audio_filename = 'audio/turbo_256_256.wav'
        audio_label = 'JPEG モード (ざらざら感・段階的復元用テスト音声)'
    else:
        audio_filename = 'audio/turbo_png_256_256.wav'
        audio_label = 'PNG モード (0/100 多数決復元用テスト音声)'

    return render_template(
        'demo_player.html',
        current_engine_mode=current_mode,
        selected_mode=req_mode,
        audio_filename=audio_filename,
        audio_label=audio_label
    )
