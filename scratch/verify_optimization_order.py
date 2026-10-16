"""
全探索検証スクリプト (Full 3D Grid vs Sequential Optimization Verification)
L (5通り) x 2t (8通り) x r (11通り) = 計440通りの全組み合わせを総当たり評価し、
「逐次最適化 (RS固定 -> L,r探索 -> RS探索)」が局所解に陥るリスクと、
真の全体最適解 (Global Optimum) を数学的・実証的に検証する。
"""

import io
import math
import os
import sys
import numpy as np
from PIL import Image
from scipy.stats import binom

# 1. 画像エントロピー
def load_test_image_entropies():
    img_path = r"C:\Users\kosei\OneDrive\Desktop\sstv-jpeg-fec\data\input\test.jpg"
    if os.path.exists(img_path):
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
    else:
        np.random.seed(42)
        entropies = [int(np.random.normal(113, 18)) for _ in range(256)]
        return entropies, sum(entropies)

TILE_ENTROPIES, TOTAL_IMAGE_BYTES = load_test_image_entropies()

HDR_PKT = 7
HDR_PHYS = 40

def calc_k_symbols(l_pkt: int, rs_parity: int) -> int:
    cap = l_pkt - HDR_PKT - rs_parity
    if cap <= 0:
        return 999999
    return sum(max(1, (sz + cap - 1) // cap) for sz in TILE_ENTROPIES)

def compute_packet_survival_prob(l_pkt: int, rs_parity: int, base_ber=0.012, drop_rate=0.20) -> float:
    if rs_parity == 0:
        p_no_err_single = (1.0 - base_ber) ** (8 * l_pkt)
        p_station_ok = (1.0 - drop_rate) * p_no_err_single
        return 1.0 - (1.0 - p_station_ok) ** 3

    t_corr = rs_parity // 2
    t_cap = rs_parity
    p_byte_single = 1.0 - ((1.0 - base_ber) ** 8)
    ber_3maj = 3 * (base_ber ** 2) * (1.0 - base_ber) + (base_ber ** 3)
    p_byte_3maj = 1.0 - ((1.0 - ber_3maj) ** 8)
    p_split_byte = 1.0 - ((1.0 - 2 * base_ber * (1 - base_ber)) ** 8)
    p_both_err_byte = 1.0 - ((1.0 - (base_ber ** 2)) ** 8)

    p_rx = 1.0 - drop_rate
    p_1 = 3 * p_rx * ((1 - p_rx) ** 2)
    p_2 = 3 * (p_rx ** 2) * (1 - p_rx)
    p_3 = p_rx ** 3

    succ_1 = binom.cdf(t_corr, l_pkt, p_byte_single)
    succ_2 = binom.cdf(t_cap, l_pkt, p_split_byte + 2 * p_both_err_byte)
    succ_3 = binom.cdf(t_corr, l_pkt, p_byte_3maj)
    return float(np.clip(p_1 * succ_1 + p_2 * succ_2 + p_3 * succ_3, 0.0, 1.0))

def compute_goodput(l_pkt: int, rs_parity: int, r_ratio: float) -> float:
    k = calc_k_symbols(l_pkt, rs_parity)
    p_pkt = compute_packet_survival_prob(l_pkt, rs_parity)
    if k > 5000 or p_pkt <= 0.05:
        return 0.0

    r_count = int(round(k * (r_ratio / 100.0)))
    total_m = k + r_count
    needed = int(math.ceil(k * 1.02))
    p_rec = 1.0 - binom.cdf(needed - 1, total_m, p_pkt)
    if r_ratio < 24.0:
        p_rec *= (r_ratio / 24.0) ** 3.5

    total_bytes = total_m * (l_pkt + HDR_PHYS)
    return (TOTAL_IMAGE_BYTES * p_rec) / float(total_bytes) if total_bytes > 0 else 0.0

# ----------------------------------------------------------------------
# 全探索 (Full Grid Search)
# ----------------------------------------------------------------------
L_CANDIDATES = [128, 192, 255, 384, 512]
RS_CANDIDATES = [0, 16, 24, 32, 40, 48, 56, 64]
R_CANDIDATES = [10.0, 15.0, 20.0, 24.0, 28.0, 30.0, 32.0, 35.0, 40.0, 50.0, 60.0]

print("=== 1. 全探索 (Full 3D Grid: 5 x 8 x 11 = 440 通り) 実行中 ===")
all_results = []
for l in L_CANDIDATES:
    for rs in RS_CANDIDATES:
        for r in R_CANDIDATES:
            g = compute_goodput(l, rs, r)
            all_results.append((g, l, rs, r))

all_results.sort(key=lambda x: x[0], reverse=True)
global_best = all_results[0]
print(f"★ 大域的最適解 (Global Optimum):")
print(f"   Goodput = {global_best[0]:.6f}")
print(f"   パケット長 L = {global_best[1]} Bytes")
print(f"   RSパリティ 2t = {global_best[2]} Bytes")
print(f"   ラプター比率 r = {global_best[3]:.1f} %\n")

print("上位5つの組み合わせ:")
for rank, (g, l, rs, r) in enumerate(all_results[:5], 1):
    print(f"   {rank}位: L={l:3d}B, RS={rs:2d}B, r={r:4.1f}% -> Goodput={g:.6f} (スコア {(g/global_best[0])*100:.1f}点)")

# ----------------------------------------------------------------------
# 逐次最適化 (Sequential Coordinate Descent) の検証
# ----------------------------------------------------------------------
print("\n=== 2. 逐次探索の検証 (RS固定 -> L,r最適化 -> RS最適化) ===")
# もし初期RSを 16B に固定した場合
fixed_rs = 16
best_step1_rs16 = max([(compute_goodput(l, fixed_rs, r), l, fixed_rs, r) for l in L_CANDIDATES for r in R_CANDIDATES], key=lambda x: x[0])
print(f"[パターンA: 初期RS=16Bに固定]")
print(f"   ステップ1での見かけの最適: L={best_step1_rs16[1]}B, r={best_step1_rs16[3]:.1f}% (Goodput={best_step1_rs16[0]:.6f})")
# その L をもとに RS を探す
best_step2_rs16 = max([(compute_goodput(best_step1_rs16[1], rs, best_step1_rs16[3]), best_step1_rs16[1], rs, best_step1_rs16[3]) for rs in RS_CANDIDATES], key=lambda x: x[0])
print(f"   ステップ2でRSを最適化後: L={best_step2_rs16[1]}B, RS={best_step2_rs16[2]}B, r={best_step2_rs16[3]:.1f}% -> Goodput={best_step2_rs16[0]:.6f}")
print(f"   判定: 大域最適に到達できたか？ -> {'完全成功' if best_step2_rs16[1]==global_best[1] and best_step2_rs16[2]==global_best[2] else '局所解トラップで失敗！'}\n")

# もし初期RSを 32B に固定した場合
fixed_rs = 32
best_step1_rs32 = max([(compute_goodput(l, fixed_rs, r), l, fixed_rs, r) for l in L_CANDIDATES for r in R_CANDIDATES], key=lambda x: x[0])
print(f"[パターンB: 初期RS=32Bに固定]")
print(f"   ステップ1での見かけの最適: L={best_step1_rs32[1]}B, r={best_step1_rs32[3]:.1f}% (Goodput={best_step1_rs32[0]:.6f})")
best_step2_rs32 = max([(compute_goodput(best_step1_rs32[1], rs, best_step1_rs32[3]), best_step1_rs32[1], rs, best_step1_rs32[3]) for rs in RS_CANDIDATES], key=lambda x: x[0])
print(f"   ステップ2でRSを最適化後: L={best_step2_rs32[1]}B, RS={best_step2_rs32[2]}B, r={best_step2_rs32[3]:.1f}% -> Goodput={best_step2_rs32[0]:.6f}")
print(f"   判定: 大域最適に到達できたか？ -> {'完全成功' if best_step2_rs32[1]==global_best[1] and best_step2_rs32[2]==global_best[2] else '局所解トラップで失敗！'}\n")

# もし初期RSを 48B に固定した場合
fixed_rs = 48
best_step1_rs48 = max([(compute_goodput(l, fixed_rs, r), l, fixed_rs, r) for l in L_CANDIDATES for r in R_CANDIDATES], key=lambda x: x[0])
print(f"[パターンC: 初期RS=48Bに固定]")
print(f"   ステップ1での見かけの最適: L={best_step1_rs48[1]}B, r={best_step1_rs48[3]:.1f}% (Goodput={best_step1_rs48[0]:.6f})")
best_step2_rs48 = max([(compute_goodput(best_step1_rs48[1], rs, best_step1_rs48[3]), best_step1_rs48[1], rs, best_step1_rs48[3]) for rs in RS_CANDIDATES], key=lambda x: x[0])
print(f"   ステップ2でRSを最適化後: L={best_step2_rs48[1]}B, RS={best_step2_rs48[2]}B, r={best_step2_rs48[3]:.1f}% -> Goodput={best_step2_rs48[0]:.6f}")
print(f"   判定: 大域最適に到達できたか？ -> {'完全成功' if best_step2_rs48[1]==global_best[1] and best_step2_rs48[2]==global_best[2] else '局所解トラップで失敗！'}\n")
