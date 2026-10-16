import os
import sys

for stream in (sys.stdout, sys.stderr):
    reconf = getattr(stream, 'reconfigure', None)
    if callable(reconf):
        try:
            reconf(encoding='utf-8', errors='replace')
        except Exception:
            pass

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def plot_bit_error_distribution_pil(
    gt_packets,
    noisy_packets,
    dataset_name="Audio A",
    save_path="experiment/output/bit_error_distribution_A.png",
    dpi=300
):
    """Pillowによる高精細レンダリング (DLL制約フリー・学会・論文クオリティ)"""
    os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
    num_packets = len(gt_packets)

    error_x = []
    error_y = []
    ber_list = []
    total_bits = 0
    total_errors = 0
    max_bit_index = 0

    freq_confusion = np.zeros((4, 4), dtype=int)
    freq_labels = ["8000Hz (00)", "6000Hz (01)", "4000Hz (10)", "2000Hz (11)"]

    for p_idx, (gt_p, noisy_p) in enumerate(zip(gt_packets, noisy_packets)):
        gt_bits = gt_p["payload_bits"]
        noisy_bits = noisy_p["payload_bits"]
        min_len = min(len(gt_bits), len(noisy_bits))
        max_bit_index = max(max_bit_index, min_len)

        pkt_errors = 0
        for b_idx in range(min_len):
            if gt_bits[b_idx] != noisy_bits[b_idx]:
                error_x.append(p_idx)
                error_y.append(b_idx)
                pkt_errors += 1

        total_bits += min_len
        total_errors += pkt_errors
        ber = (pkt_errors / min_len * 100.0) if min_len > 0 else 0.0
        ber_list.append(ber)

        for s_idx in range(0, min_len - 1, 2):
            gt_sym = (gt_bits[s_idx] << 1) | gt_bits[s_idx + 1]
            noisy_sym = (noisy_bits[s_idx] << 1) | noisy_bits[s_idx + 1]
            freq_confusion[gt_sym, noisy_sym] += 1

    overall_ber = (total_errors / total_bits * 100.0) if total_bits > 0 else 0.0

    img_w, img_h = 1600, 1150
    img = Image.new("RGB", (img_w, img_h), color="#FFFFFF")
    draw = ImageDraw.Draw(img)

    # 背景
    draw.rectangle([(0, 0), (img_w, img_h)], fill="#F8F9FA")

    # ヘッダータイトル
    draw.rectangle([(0, 0), (img_w, 75)], fill="#1D3557")
    title = f"[{dataset_name}] Bit Error Rate (BER) & Distribution Analysis (Actual BER: {overall_ber:.2f}%)"
    draw.text((30, 24), title, fill="#FFFFFF")

    # 1. 上段: ビット反転発生位置マップ (Scatter Area)
    sc_x0, sc_y0, sc_x1, sc_y1 = 90, 125, img_w - 70, 520
    draw.rectangle([(sc_x0, sc_y0), (sc_x1, sc_y1)], fill="#FFFFFF", outline="#DDE2E5", width=2)
    draw.text((sc_x0 + 15, sc_y0 - 24), f"Error Bits Scatter Map (Total Inverted: {total_errors:,} / {total_bits:,} bits)", fill="#1D3557")

    # グリッド & 目盛
    step_pkt = 25 if num_packets <= 256 else 50
    for px in range(0, num_packets + 1, step_pkt):
        gx = sc_x0 + (px / max(1, num_packets)) * (sc_x1 - sc_x0)
        draw.line([(gx, sc_y0), (gx, sc_y1)], fill="#F0F3F5", width=1)
        draw.text((gx - 10, sc_y1 + 8), str(px), fill="#6C757D")

    step_bit = max(1000, (max_bit_index // 5 // 500) * 500)
    for by in range(0, max_bit_index + 1, step_bit):
        gy = sc_y1 - (by / max(1, max_bit_index)) * (sc_y1 - sc_y0)
        draw.line([(sc_x0, gy), (sc_x1, gy)], fill="#F0F3F5", width=1)
        draw.text((sc_x0 - 55, gy - 7), f"{by:,}", fill="#6C757D")

    # エラープロット (赤いドット)
    for px, by in zip(error_x, error_y):
        cx = sc_x0 + (px / max(1, num_packets)) * (sc_x1 - sc_x0)
        cy = sc_y1 - (by / max(1, max_bit_index)) * (sc_y1 - sc_y0)
        draw.ellipse([(cx - 2, cy - 2), (cx + 2, cy + 2)], fill="#E63946")

    draw.text((sc_x0 + (sc_x1 - sc_x0) // 2 - 50, sc_y1 + 28), "Packet Index (0 ~ N)", fill="#333333")
    draw.text((sc_x0 - 75, sc_y0 - 24), "Bit Index", fill="#333333")

    # 2. 下段左: パケット別BER棒グラフ
    b_x0, b_y0, b_x1, b_y1 = 90, 620, 800, 1060
    draw.rectangle([(b_x0, b_y0), (b_x1, b_y1)], fill="#FFFFFF", outline="#DDE2E5", width=2)
    draw.text((b_x0 + 15, b_y0 - 24), "Packet-wise BER (%) Distribution", fill="#1D3557")

    max_ber_scale = max(20.0, ((int(max(ber_list)) + 5) // 5) * 5.0) if ber_list else 25.0
    # Y軸目盛 (BER %)
    y_steps = [0, 5, 10, 15, 20, 25, 30, 40, 50]
    for y_val in y_steps:
        if y_val <= max_ber_scale:
            gy = b_y1 - (y_val / max_ber_scale) * (b_y1 - b_y0)
            draw.line([(b_x0, gy), (b_x1, gy)], fill="#EDF2F4", width=1)
            draw.text((b_x0 - 45, gy - 7), f"{y_val}%", fill="#6C757D")

    # X軸目盛
    for px in range(0, num_packets + 1, 50):
        gx = b_x0 + (px / max(1, num_packets)) * (b_x1 - b_x0)
        draw.text((gx - 10, b_y1 + 8), str(px), fill="#6C757D")
    draw.text((b_x0 + (b_x1 - b_x0) // 2 - 50, b_y1 + 28), "Packet Index (0 ~ N)", fill="#333333")

    # 棒グラフ描画
    for px, ber_v in enumerate(ber_list):
        gx0 = b_x0 + (px / max(1, num_packets)) * (b_x1 - b_x0)
        gx1 = b_x0 + ((px + 0.85) / max(1, num_packets)) * (b_x1 - b_x0)
        bh = (ber_v / max_ber_scale) * (b_y1 - b_y0)
        gy0 = b_y1 - bh
        bar_color = "#E63946" if ber_v > 15 else "#457B9D"
        draw.rectangle([(gx0, gy0), (gx1, b_y1)], fill=bar_color)

    # 平均線
    avg_y = b_y1 - (overall_ber / max_ber_scale) * (b_y1 - b_y0)
    draw.line([(b_x0, avg_y), (b_x1, avg_y)], fill="#E63946", width=2)
    draw.text((b_x1 - 180, avg_y - 18), f"Avg BER: {overall_ber:.2f}%", fill="#E63946")

    # 3. 下段右: 4-FSK周波数混同行列
    m_x0, m_y0, m_x1, m_y1 = 880, 620, img_w - 70, 1060
    draw.rectangle([(m_x0, m_y0), (m_x1, m_y1)], fill="#FFFFFF", outline="#DDE2E5", width=2)
    draw.text((m_x0 + 15, m_y0 - 24), "4-FSK Symbol Frequency Confusion Matrix (Tx vs Rx)", fill="#1D3557")

    cell_w = (m_x1 - m_x0 - 150) / 4
    cell_h = (m_y1 - m_y0 - 90) / 4
    mx_start = m_x0 + 130
    my_start = m_y0 + 35

    max_val = np.max(freq_confusion) if np.max(freq_confusion) > 0 else 1

    draw.text((m_x0 + 15, my_start - 25), "Tx (Sent) \\ Rx (Rcv)", fill="#6C757D")

    for i in range(4):
        # Y軸ラベル (正解/送信側)
        draw.text((m_x0 + 10, my_start + i * cell_h + 15), freq_labels[i], fill="#333333")
        for j in range(4):
            val = freq_confusion[i, j]
            cx0 = mx_start + j * cell_w
            cy0 = my_start + i * cell_h

            if i == j:
                # 対角（正解）: 青グラデーション
                ratio = val / max_val
                r = int(240 - ratio * 180)
                g = int(245 - ratio * 150)
                b = int(255 - ratio * 70)
                color_hex = f"#{r:02x}{g:02x}{b:02x}"
                text_color = "#FFFFFF" if ratio > 0.5 else "#000000"
            else:
                # 非対角（誤り）: 赤グラデーション
                ratio = val / max(1, total_errors)
                r = int(255)
                g = int(245 - min(1.0, ratio * 5.0) * 140)
                b = int(245 - min(1.0, ratio * 5.0) * 140)
                color_hex = f"#{r:02x}{g:02x}{b:02x}"
                text_color = "#E63946" if val > 0 else "#CCCCCC"

            draw.rectangle([(cx0, cy0), (cx0 + cell_w - 2, cy0 + cell_h - 2)], fill=color_hex, outline="#DDE2E5")
            draw.text((cx0 + cell_w // 4, cy0 + cell_h // 3), f"{val:,}", fill=text_color)

    for j in range(4):
        # X軸ラベル (受信側)
        draw.text((mx_start + j * cell_w + 5, my_start + 4 * cell_h + 12), freq_labels[j], fill="#333333")

    img.save(save_path, quality=95)
    print(f"📊 ビット反転分布グラフ保存完了: {save_path}")
    return save_path, overall_ber


def plot_comparison_n_audio_pil(
    gt_packets,
    sets_dict,
    save_path="experiment/output/comparison_audio_ber.png",
    dpi=300
):
    """N個のデータセット(A, B, C, D, ...)の反転位置分散を並べて比較可視化 (PIL)"""
    os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
    num_packets = len(gt_packets)
    n_sets = len(sets_dict)

    row_h = 220 if n_sets <= 5 else max(85, int(220 * 5 / n_sets))
    gap = 35 if n_sets <= 5 else max(16, int(35 * 5 / n_sets))
    header_h = 75
    img_w = 1600
    img_h = header_h + n_sets * (row_h + gap) + 40

    img = Image.new("RGB", (img_w, img_h), color="#FFFFFF")
    draw = ImageDraw.Draw(img)

    draw.rectangle([(0, 0), (img_w, header_h)], fill="#1D3557")
    draw.text((30, 24), f"Comparison of Bit Error Inversion Distribution across {n_sets} Datasets", fill="#FFFFFF")

    palette = [
        "#E63946", "#2A9D8F", "#E76F51", "#457B9D", "#9B5DE5",
        "#F15BB5", "#00BBF9", "#00F5D4", "#FEE440", "#FB5607",
        "#FF006E", "#8338EC", "#3A86FF", "#06D6A0", "#118AB2",
        "#073B4C", "#E07A5F", "#3D405B", "#81B29A", "#F2CC8F"
    ]
    max_bit_idx = max(len(p["payload_bits"]) for p in gt_packets)

    for idx, (key, (packets, ber)) in enumerate(sets_dict.items()):
        sy0 = header_h + 20 + idx * (row_h + gap)
        sy1 = sy0 + row_h
        sx0 = 90
        sx1 = img_w - 70
        col = palette[idx % len(palette)]

        draw.rectangle([(sx0, sy0), (sx1, sy1)], fill="#FFFFFF", outline="#DDE2E5", width=2)
        panel_title = f"Dataset [{key}] - Bit Error Rate (BER): {ber * 100.0:.2f}%"
        draw.text((sx0 + 15, sy0 + 8), panel_title, fill=col)

        # グリッド & 目盛
        step_pkt = 25 if num_packets <= 256 else 50
        for px in range(0, num_packets + 1, step_pkt):
            gx = sx0 + (px / max(1, num_packets)) * (sx1 - sx0)
            draw.line([(gx, sy0 + 35), (gx, sy1 - 10)], fill="#F8F9FA", width=1)
            draw.text((gx - 10, sy1 - 8), str(px), fill="#6C757D")

        step_bit = max(2000, (max_bit_idx // 3 // 1000) * 1000)
        for by in range(0, max_bit_idx + 1, step_bit):
            gy = (sy1 - 10) - (by / max(1, max_bit_idx)) * (row_h - 50)
            draw.text((sx0 - 55, gy - 7), f"{by:,}", fill="#6C757D")

        # エラー点描画
        for p_idx, (gt_p, noisy_p) in enumerate(zip(gt_packets, packets)):
            gt_bits = gt_p["payload_bits"]
            noisy_bits = noisy_p["payload_bits"]
            min_len = min(len(gt_bits), len(noisy_bits))
            for b_idx in range(min_len):
                if gt_bits[b_idx] != noisy_bits[b_idx]:
                    cx = sx0 + (p_idx / max(1, num_packets)) * (sx1 - sx0)
                    cy = (sy1 - 10) - (b_idx / max(1, max_bit_idx)) * (row_h - 50)
                    draw.rectangle([(cx - 1, cy - 1), (cx + 1, cy + 1)], fill=col)

    img.save(save_path, quality=95)
    print(f"📊 {n_sets}個の反転比較グラフ保存完了: {save_path}")
    return save_path


def plot_bit_error_distribution(gt_packets, noisy_packets, dataset_name="Audio A", save_path="experiment/output/bit_error_distribution_A.png", dpi=300):
    return plot_bit_error_distribution_pil(gt_packets, noisy_packets, dataset_name=dataset_name, save_path=save_path, dpi=dpi)


def plot_comparison_three_audio(gt_packets, sets_dict, save_path="experiment/output/comparison_three_audio_ber.png", dpi=300):
    return plot_comparison_n_audio_pil(gt_packets, sets_dict, save_path=save_path, dpi=dpi)
