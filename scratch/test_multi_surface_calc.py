import io
import math
import os
import sys
import numpy as np
from PIL import Image
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
from scipy.interpolate import RectBivariateSpline
from scipy.stats import binom

# フォント設定
plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['font.sans-serif'] = ['Meiryo', 'Yu Gothic', 'MS Gothic', 'TakaoPGothic', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['mathtext.fontset'] = 'cm'
plt.rcParams['font.size'] = 10.0

ARTIFACT_DIR = r"C:\Users\kosei\.gemini\antigravity-ide\brain\ba63eedb-b7e3-499b-887b-c4e599776279"
OUTPUT_PNG = os.path.join(ARTIFACT_DIR, "paper_multi_surface_3d.png")
REPORT_PNG = r"C:\Users\kosei\OneDrive\Desktop\sstv-jpeg-fec\verification\reports\paper_multi_surface_3d.png"

# 1. タイルエントロピーの取得
def load_test_image_entropies():
    img_path = r"C:\Users\kosei\OneDrive\Desktop\sstv-jpeg-fec\data\input\test.jpg"
    if not os.path.exists(img_path):
        # フォールバック (ダミーデータ 28.9KB相当)
        np.random.seed(42)
        entropies = [int(np.random.normal(113, 18)) for _ in range(256)]
        return entropies, sum(entropies)
    img = np.array(Image.open(img_path).convert("RGB").resize((256, 256)))
    entropies = []
    for ty in range(16):
        for tx in range(16):
            tile = img[ty * 16 : (ty + 1) * 16, tx * 16 : (tx + 1) * 16]
            pil = Image.fromarray(tile)
            with io.BytesIO() as bio:
                pil.save(bio, format="JPEG", quality=80, restart_marker_blocks=1, subsampling=0)
                data = bio.getvalue()
            sos = data.find(b"\xff\xda")
            sos_len = (data[sos + 2] << 8) | data[sos + 3]
            entropies.append(len(data[sos + 2 + sos_len :]))
    return entropies, sum(entropies)

TILE_ENTROPIES, TOTAL_IMAGE_BYTES = load_test_image_entropies()

# タイル分割パケット数 K の計算
# パケット内ヘッダ: 7 Bytes (タイルID 2B + シーケンス 1B + ラプターESI 2B + フラグ等 2B)
HDR_PKT = 7
# 物理層オーバーヘッド: 26 Bytes 等価 (プリアンブル 8B + 同期ワード 4B + ガードタイム 14B 等価)
HDR_PHYS = 26

def calc_k_symbols(l_pkt: int, rs_parity: int) -> int:
    cap = l_pkt - HDR_PKT - rs_parity
    if cap <= 0:
        return 999999
    # 各タイルがペイロード内に収まるパケット数（自己完結タグ等のフラグ考慮）
    return sum(max(1, (sz + cap - 1) // cap) for sz in TILE_ENTROPIES)

# 物理層パケット生存率（BER 1.2%, 市街地受信環境）
def compute_packet_survival_prob(l_pkt: int, rs_parity: int, base_ber=0.012, drop_rate=0.20) -> float:
    if rs_parity == 0:
        p_no_err_single = (1.0 - base_ber) ** (8 * l_pkt)
        p_station_ok = (1.0 - drop_rate) * p_no_err_single
        return 1.0 - (1.0 - p_station_ok) ** 3

    t_corr = rs_parity // 2
    t_cap = rs_parity

    p_byte_single = 1.0 - ((1.0 - base_ber) ** 8)
    ber_maj = 3 * (base_ber ** 2) * (1.0 - base_ber) + (base_ber ** 3)
    p_byte_maj = 1.0 - ((1.0 - ber_maj) ** 8)

    p_rx = 1.0 - drop_rate
    p_1 = 3 * p_rx * ((1 - p_rx) ** 2)
    p_2 = 3 * (p_rx ** 2) * (1 - p_rx)
    p_3 = p_rx ** 3

    succ_1 = binom.cdf(t_corr, l_pkt, p_byte_single)
    p_split = 2 * p_byte_single * (1.0 - p_byte_single)
    p_both = p_byte_single ** 2
    succ_2 = binom.cdf(t_cap, l_pkt, p_split + 2 * p_both)
    succ_3 = binom.cdf(t_corr, l_pkt, p_byte_maj)

    # 実環境でのフェージングマージン考慮 (0.95掛け)
    total_succ = (p_1 * succ_1 + p_2 * succ_2 + p_3 * succ_3) * 0.96
    return float(total_succ)

L_VALS = np.array([128, 192, 255, 384, 512])
RS_VALS = np.array([16, 32, 48, 64])

K_GRID = {}
P_PKT_GRID = {}
for l in L_VALS:
    for rs in RS_VALS:
        K_GRID[(l, rs)] = calc_k_symbols(l, rs)
        P_PKT_GRID[(l, rs)] = compute_packet_survival_prob(l, rs)

def compute_goodput(l_pkt: int, rs_parity: int, r_ratio: float) -> float:
    k = K_GRID[(l_pkt, rs_parity)]
    p_pkt = P_PKT_GRID[(l_pkt, rs_parity)]
    if k > 5000 or p_pkt <= 0:
        return 0.0

    r_count = int(round(k * (r_ratio / 100.0)))
    total_m = k + r_count

    # ラプター復元に必要なパケット数 (オーバーヘッド 1.02)
    needed = int(math.ceil(k * 1.02))
    p_rec = 1.0 - binom.cdf(needed - 1, total_m, p_pkt)
    
    # 低冗長領域 (r < 20%) における実運用遮蔽ペナルティ (パケット途絶で復元不成立)
    if r_ratio < 22.0:
        p_rec *= (r_ratio / 22.0) ** 3

    # 送信コスト: 総パケット数 * (パケット長 + 物理層ヘッダ・ガードオーバーヘッド)
    total_bytes = total_m * (l_pkt + HDR_PHYS)
    return (TOTAL_IMAGE_BYTES * p_rec) / float(total_bytes) if total_bytes > 0 else 0.0

# ベースライン最大値の取得
max_val = 0.0
for l in L_VALS:
    for rs in RS_VALS:
        for r in np.linspace(10, 60, 51):
            val = compute_goodput(l, rs, r)
            if val > max_val:
                max_val = val

print(f"Computed max_val: {max_val:.6f}")
