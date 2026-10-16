import os
import sys
import json
import base64
import io
import time
import webbrowser
from flask import Flask, render_template, request, jsonify, send_from_directory, send_file
from PIL import Image
import numpy as np

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from experiment.bit_flipper import JPEGTextBitFlipper
from experiment.plot_bit_errors import plot_comparison_n_audio_pil
from experiment.plot_majority_recovery import MajorityRecoveryEvaluator

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
app = Flask(__name__, template_folder=TEMPLATE_DIR)

OUT_DIR = os.path.join(ROOT_DIR, "experiment", "output")
UPLOAD_DIR = os.path.join(ROOT_DIR, "data", "input")
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/output/<path:filename>')
def serve_output(filename):
    return send_from_directory(OUT_DIR, filename)


@app.route('/api/current_image')
def current_image():
    path = os.path.join(ROOT_DIR, "data", "input", "test.jpg")
    if os.path.exists(path):
        return send_file(path)
    return "Not found", 404


@app.route('/api/glossary')
def get_glossary():
    path = os.path.join(ROOT_DIR, "experiment", "GLOSSARY.md")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return jsonify({'status': 'ok', 'content': f.read()})
    return jsonify({'status': 'error', 'message': 'GLOSSARY.md not found'}), 404


@app.route('/api/upload_image', methods=['POST'])
def upload_image():
    if 'image' not in request.files:
        return jsonify({'status': 'error', 'message': 'No file uploaded'}), 400
    file = request.files['image']
    if file.filename == '':
        return jsonify({'status': 'error', 'message': 'Empty filename'}), 400

    save_path = os.path.join(UPLOAD_DIR, "uploaded_test.jpg")
    img = Image.open(file.stream).convert('RGB')
    img.save(save_path, format="JPEG", quality=90)
    return jsonify({
        'status': 'ok',
        'path': "data/input/uploaded_test.jpg",
        'filename': file.filename
    })


@app.route('/api/run', methods=['POST'])
def api_run():
    try:
        data = request.json or {}
        image_path = data.get('image_path', 'data/input/test.jpg')
        if not os.path.isabs(image_path):
            image_path = os.path.join(ROOT_DIR, image_path)

        if not os.path.exists(image_path):
            return jsonify({'status': 'error', 'message': f'画像が見つかりません: {image_path}'}), 400

        num_sources = int(data.get('num_sources', 3))
        error_rates = data.get('error_rates', [25.0, 15.0, 10.0])
        mode = data.get('mode', '8000_to_4000')
        generate_wav = bool(data.get('generate_wav', False))

        flipper = JPEGTextBitFlipper(tile_size=16, quality=75)
        img = Image.open(image_path).convert('RGB').resize((256, 256))
        gt_img_arr = np.array(img)
        gt_packets = flipper.image_to_ground_truth_packets(gt_img_arr)

        gt_text_path = os.path.join(OUT_DIR, "ground_truth_bitstream.txt")
        flipper.save_packets_to_text(gt_packets, gt_text_path)

        sets = flipper.generate_n_divergent_sets(
            gt_packets,
            num_sets=num_sources,
            error_rates=error_rates[:num_sources],
            mode=mode,
            base_seed=42
        )

        # テキスト & WAV 保存
        for name, (pkts, ber) in sets.items():
            txt_path = os.path.join(OUT_DIR, f"audio_{name}_noisy_bitstream.txt")
            flipper.save_packets_to_text(pkts, txt_path)
            if generate_wav:
                wav_path = os.path.join(OUT_DIR, f"audio_{name}_noisy.wav")
                flipper.packets_to_wav(pkts, wav_path, seed=42)

        # 1. 音声ごとのBER比較プロット
        comp_plot_path = os.path.join(OUT_DIR, "comparison_audio_ber.png")
        plot_comparison_n_audio_pil(gt_packets, sets, save_path=comp_plot_path)

        # 2. No.1〜No.5 ワークフローによる段階的画像復元 & 多数決評価
        evaluator = MajorityRecoveryEvaluator(tile_size=16)
        progression = evaluator.evaluate_progression(gt_packets, gt_img_arr, sets)

        # 3. 推移グラフの描画・保存 (300DPI)
        benchmark_graph_path = os.path.join(OUT_DIR, "graph2_majority_recovery_benchmark_300dpi.png")
        evaluator.plot_recovery_progression(progression, save_path=benchmark_graph_path, dpi=300)

        # 4. 復元比較ギャラリーの描画・保存
        gallery_path = os.path.join(OUT_DIR, "graph2_visual_recovery_gallery.png")
        evaluator.plot_visual_comparison_gallery(gt_img_arr, progression, save_path=gallery_path, dpi=300)

        prog_json = []
        for d in progression:
            prog_json.append({
                "count": d["count"],
                "label": d["label"],
                "tile_rate": float(d["tile_rate"]),
                "visible_rate": float(d.get("visible_rate", d["tile_rate"])),
                "bit_acc": float(d["bit_acc"]),
                "bit_errors": int(d.get("bit_errors", 0)),
                "psnr": float(d["psnr"]),
                "ssim": float(d["ssim"]),
                "stage_breakdown": d.get("stage_counts", {})
            })

        return jsonify({
            'status': 'ok',
            'progression': prog_json
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500


def start_server(port=5005, open_browser=True):
    print("=" * 70)
    print(f"🚀 SSTV Turbo JPEG 実験スタジオ Pro 起動中: http://127.0.0.1:{port}")
    print("=" * 70)
    if open_browser:
        try:
            webbrowser.open(f"http://127.0.0.1:{port}")
        except Exception:
            pass
    app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    start_server()
