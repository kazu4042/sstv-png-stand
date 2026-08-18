from flask import Blueprint, jsonify, request, session
import os
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import digital_turbo_png.config_turbo as config
from web_turbo_png.services.analyzer_service import TurboPNGAnalyzerService
from web_turbo_png.routes.auth_routes import login_required
from digital_turbo_png.database_turbo import PacketDatabaseTurboPNG

api_bp = Blueprint('api', __name__, url_prefix='/api')

_analyzer_cache = None


def get_analyzer():
    """アナライザーをキャッシュで管理"""
    global _analyzer_cache
    if _analyzer_cache is None:
        _analyzer_cache = TurboPNGAnalyzerService()
    return _analyzer_cache


def invalidate_analyzer_cache():
    """新しいファイルがアップロードされた際にキャッシュを破棄する"""
    global _analyzer_cache
    _analyzer_cache = None


@api_bp.route('/images', methods=['GET'])
def get_available_images():
    """利用可能なすべての画像IDをリストで返す"""
    try:
        analyzer = get_analyzer()
        image_ids = analyzer.get_available_image_ids()

        return jsonify({
            "status": "success",
            "total_images": len(image_ids),
            "image_ids": image_ids,
            "config": {
                "width": config.WIDTH,
                "height": config.HEIGHT,
                "snr_max": getattr(config, 'SNR_MAX_THRESH', 14),
                "snr_min": getattr(config, 'SNR_MIN_THRESH', 0)
            }
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"画像リスト取得エラー: {str(e)}"
        }), 500


@api_bp.route('/missing', methods=['GET'])
def get_missing_packets():
    """不足しているパケットの一覧をJSONデータとしてブラウザに返す"""
    try:
        image_id = request.args.get('image_id')
        if not image_id:
            return jsonify({
                "status": "error",
                "message": "リクエストに image_id が含まれていません。"
            }), 400

        analyzer = get_analyzer()
        missing_packets = analyzer.find_missing_packets(target_image_id_hex=image_id, max_limit=999999)

        total_blocks = config.TILE_COUNT_X * config.TILE_COUNT_Y
        true_missing_count = sum(1 for p in missing_packets if p.get('status', 'MISSING') == 'MISSING')
        overall_score = max(0.0, ((total_blocks - true_missing_count) / total_blocks) * 100.0)

        return jsonify({
            "status": "success",
            "image_id": image_id,
            "total_missing_found": true_missing_count,
            "total_blocks": total_blocks,
            "overall_score": overall_score,
            "missing_packets": missing_packets[:2048]
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"不足パケット検出エラー: {str(e)}"
        }), 500


@api_bp.route('/reliability', methods=['GET'])
def get_reliability():
    """各パケットのSNR重み付き信頼度と詳細データをJSONとして返す"""
    try:
        image_id = request.args.get('image_id')
        if not image_id:
            return jsonify({
                "status": "error",
                "message": "リクエストに image_id が含まれていません。"
            }), 400

        analyzer = get_analyzer()
        current_user_id = session.get('user_id')
        scores = analyzer.calculate_reliability_scores(target_image_id_hex=image_id, current_user_id=current_user_id)

        return jsonify({
            "status": "success",
            "image_id": image_id,
            "total_packets": len(scores),
            "snr_settings": {
                "max_threshold": getattr(config, 'SNR_MAX_THRESH', 14),
                "min_threshold": getattr(config, 'SNR_MIN_THRESH', 0)
            },
            "reliability_scores": scores
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"信頼度スコア取得エラー: {str(e)}"
        }), 500


@api_bp.route('/merge_stats', methods=['GET'])
def get_merge_stats():
    """マージ統計情報を返す（TurboPNG ではクラスタリングなし）"""
    try:
        image_id = request.args.get('image_id')
        if not image_id:
            return jsonify({
                "status": "error",
                "message": "リクエストに image_id が含まれていません。"
            }), 400

        analyzer = get_analyzer()
        stats = analyzer.get_merge_stats(target_image_id_hex=image_id)

        return jsonify({
            "status": "success",
            "image_id": image_id,
            "total_merged": stats["total_merged"],
            "details": stats["details"]
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"マージ情報取得エラー: {str(e)}"
        }), 500


@api_bp.route('/stats', methods=['GET'])
def get_statistics():
    """全体統計情報を返す"""
    try:
        analyzer = get_analyzer()
        image_ids = analyzer.get_available_image_ids()

        stats_by_image = {}
        total_packets = 0

        for img_id in image_ids:
            scores = analyzer.calculate_reliability_scores(img_id)
            packet_count = len(scores)
            stats_by_image[img_id] = {
                "packet_count": packet_count,
                "avg_snr": round(sum(s.get('avg_snr', 0) for s in scores) / len(scores), 1) if scores else 0
            }
            total_packets += packet_count

        return jsonify({
            "status": "success",
            "total_images": len(image_ids),
            "total_packets": total_packets,
            "statistics": stats_by_image
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"統計取得エラー: {str(e)}"
        }), 500


@api_bp.route('/rankings', methods=['GET'])
def get_rankings():
    """ランキング情報をJSON形式で返す"""
    try:
        from pathlib import Path
        import json
        ranking_json_path = Path(__file__).parent.parent / "static" / "data" / "rankings.json"
        if ranking_json_path.is_file():
            with open(ranking_json_path, 'r', encoding='utf-8') as f:
                rankings = json.load(f)
            if isinstance(rankings, dict):
                rankings_list = []
                for cs, data in rankings.items():
                    if isinstance(data, dict):
                        d = data.copy()
                        d['callsign'] = d.get('callsign') or cs
                        rankings_list.append(d)
                return jsonify(rankings_list)
            return jsonify(rankings)
        return jsonify([])
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"ランキング取得エラー: {str(e)}"
        }), 500



@api_bp.route('/heatmap-data', methods=['GET'])
def get_heatmap_data():
    """ヒートマップ用データを返す"""
    try:
        from pathlib import Path
        import json
        ranking_json_path = Path(__file__).parent.parent / "static" / "data" / "rankings.json"
        stations = []
        if ranking_json_path.is_file():
            with open(ranking_json_path, 'r', encoding='utf-8') as f:
                rankings = json.load(f)
            items = list(rankings.values()) if isinstance(rankings, dict) else rankings
            for item in items:
                if 'latitude' in item and 'longitude' in item:
                    stations.append({
                        'callsign': item.get('callsign', '(匿名)'),
                        'lat': item['latitude'],
                        'lon': item['longitude'],
                        'snr': item.get('avg_snr', 0),
                        'country': item.get('country', ''),
                        'grid': item.get('grid', ''),
                        'packets': item.get('total', 0),
                        'contribution': item.get('contribution', 0),
                        'is_egg': item.get('is_egg', False)
                    })
        stations.sort(key=lambda x: x['snr'], reverse=True)

        return jsonify({
            "status": "success",
            "stations": stations,
            "total_stations": len(stations),
            "timestamp": int(__import__('time').time())
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"ヒートマップデータ取得エラー: {str(e)}"
        }), 500

@api_bp.route('/history', methods=['GET'])
@login_required
def get_user_history_api():
    """ログインユーザーのアップロード履歴を返す"""
    try:
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({"status": "error", "message": "Unauthorized"}), 401
            
        log_dir = os.path.join(ROOT_DIR, config.TEXT_LOG_DIR)
        db = PacketDatabaseTurboPNG(log_dir)
        history = db.get_user_history(user_id)
        db.close()
        
        return jsonify({
            "status": "success",
            "history": history
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"履歴取得エラー: {str(e)}"
        }), 500
