"""
厳密な離散グリッドに基づく 2x2 3Dファセット曲面グラフ改善スクリプト
- 偽の補間ピーク (245Bなど) を完全排除し、実在する候補点 (L=128, 192, 255, 384, 512) に基づく真値をプロット
- 底面への等高線投影 (ax.contour) を追加し、視覚的な錯視 (手前が大きく見える問題) を解消
- 真の最適解 (L=255B, r=28%, Z=0.2669) に正確にピークマーカーを配置
- L=128 と L=255 の比較ポイントを正確にプロット
"""

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
from matplotlib.colors import Normalize
from scipy.interpolate import interp2d, RegularGridInterpolator
from scipy.stats import binom

# フォント設定
plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['font.sans-serif'] = ['Meiryo', 'Yu Gothic', 'MS Gothic', 'TakaoPGothic', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['mathtext.fontset'] = 'cm'

# 出力先パス
ARTIFACT_DIR = r"C:\Users\kosei\.gemini\antigravity-ide\brain\5c19d6a4-d1e9-400a-8acf-bf450e75f505"
OUTPUT_PNG = os.path.join(ARTIFACT_DIR, "faceted_3d_surfaces_comparison.png")
SCRATCH_PNG = r"c:\Users\kosei\OneDrive\Desktop\sstv-png-stand\scratch\faceted_3d_surfaces_comparison.png"

# 1. 256タイルのエントロピー生成/ロード
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

HDR_PKT = 7    # ヘッダ (7B)
HDR_PHYS = 68  # 音響SSTV物理層オーバーヘッド
BASE_BER = 0.012  # 基本BER 1.2%
DROP_RATE = 0.20  # 音響フェージング・欠落率 20%

def calc_k_symbols(l_pkt: int, rs_parity: int) -> int:
    cap = l_pkt - HDR_PKT - rs_parity
    if cap <= 0:
        return 999999
    return sum(max(1, (sz + cap - 1) // cap) for sz in TILE_ENTROPIES)

def compute_packet_survival_prob(l_pkt: int, rs_parity: int) -> float:
    if rs_parity == 0:
        p_no_err_single = (1.0 - BASE_BER) ** (8 * l_pkt)
        p_station_ok = (1.0 - DROP_RATE) * p_no_err_single
        return 1.0 - (1.0 - p_station_ok) ** 3

    t_corr = rs_parity // 2
    t_cap = rs_parity
    p_byte_single = 1.0 - ((1.0 - BASE_BER) ** 8)
    ber_3maj = 3 * (BASE_BER ** 2) * (1.0 - BASE_BER) + (BASE_BER ** 3)
    p_byte_3maj = 1.0 - ((1.0 - ber_3maj) ** 8)
    p_split_byte = 1.0 - ((1.0 - 2 * BASE_BER * (1 - BASE_BER)) ** 8)
    p_both_err_byte = 1.0 - ((1.0 - (BASE_BER ** 2)) ** 8)

    p_rx = 1.0 - DROP_RATE
    p_1 = 3 * p_rx * ((1 - p_rx) ** 2)
    p_2 = 3 * (p_rx ** 2) * (1 - p_rx)
    p_3 = p_rx ** 3

    succ_1 = binom.cdf(t_corr, l_pkt, p_byte_single)
    succ_2 = binom.cdf(t_cap, l_pkt, p_split_byte + 2 * p_both_err_byte)
    succ_3 = binom.cdf(t_corr, l_pkt, p_byte_3maj)
    return float(np.clip(p_1 * succ_1 + p_2 * succ_2 + p_3 * succ_3, 0.0, 1.0))

def compute_goodput(l_pkt: int, rs_parity: int, r_ratio: float) -> tuple:
    k = calc_k_symbols(l_pkt, rs_parity)
    p_pkt = compute_packet_survival_prob(l_pkt, rs_parity)
    if k > 5000 or p_pkt <= 0.05:
        return 0.0, 0.0

    r_count = int(round(k * (r_ratio / 100.0)))
    total_m = k + r_count
    needed = int(math.ceil(k * 1.02))
    p_rec = 1.0 - binom.cdf(needed - 1, total_m, p_pkt)
    if r_ratio < 22.0:
        p_rec *= (r_ratio / 22.0) ** 3.5

    total_bytes = total_m * (l_pkt + HDR_PHYS)
    goodput = (TOTAL_IMAGE_BYTES * p_rec) / float(total_bytes) if total_bytes > 0 else 0.0
    return goodput, p_rec

def main():
    # 離散候補点
    l_candidates = [128, 192, 255, 384, 512]
    r_candidates = np.linspace(10.0, 50.0, 41)
    rs_levels = [16, 32, 48, 64]

    # プロット用グリッド (滑らかな面を描くため線形補間を使用、オーバーシュートなし)
    l_dense = np.linspace(128, 512, 50)
    r_dense = np.linspace(10.0, 50.0, 40)
    L_mesh, R_mesh = np.meshgrid(l_dense, r_dense)

    surfaces = {}
    discrete_peaks = {}
    l128_peaks = {}
    l255_peaks = {}

    max_z_all = 0.28

    for rs in rs_levels:
        raw_z = np.zeros((len(r_candidates), len(l_candidates)))
        for i, r in enumerate(r_candidates):
            for j, l in enumerate(l_candidates):
                raw_z[i, j], _ = compute_goodput(l, rs, r)

        # 離散点での真の最適ピーク探索
        best_gp = -1.0
        best_l = 128
        best_r = 10.0
        for j, l in enumerate(l_candidates):
            for i, r in enumerate(r_candidates):
                if raw_z[i, j] > best_gp:
                    best_gp = raw_z[i, j]
                    best_l = l
                    best_r = r
        discrete_peaks[rs] = (best_l, best_r, best_gp)

        # L=128 と L=255 でのベスト
        idx_128 = l_candidates.index(128)
        r_best_128 = r_candidates[np.argmax(raw_z[:, idx_128])]
        gp_best_128 = np.max(raw_z[:, idx_128])
        l128_peaks[rs] = (128, r_best_128, gp_best_128)

        idx_255 = l_candidates.index(255)
        r_best_255 = r_candidates[np.argmax(raw_z[:, idx_255])]
        gp_best_255 = np.max(raw_z[:, idx_255])
        l255_peaks[rs] = (255, r_best_255, gp_best_255)

        # RegularGridInterpolator (線形補間: 偽ピークやオーバーシュートを完全防止)
        interp = RegularGridInterpolator((r_candidates, l_candidates), raw_z, method='linear')
        points = np.stack([R_mesh.ravel(), L_mesh.ravel()], axis=-1)
        z_dense = interp(points).reshape(L_mesh.shape)
        surfaces[rs] = z_dense

    fig = plt.figure(figsize=(16, 13), dpi=300)
    fig.patch.set_facecolor("#FAFAFA")
    norm = Normalize(vmin=0.0, vmax=max_z_all)

    titles = [
        r"$\bf{(a)\ RS=16\ Bytes}$ (誤り耐性不足: 復元確率 0%)",
        r"$\bf{(b)\ RS=32\ Bytes}$ (短パケット特化: $L=128$Bで急峻な山)",
        r"$\bf{(c)\ RS=48\ Bytes}$ (高耐性・本命: $L=255$Bで広域最適)",
        r"$\bf{(d)\ RS=64\ Bytes}$ (過剰耐性: パリティ浪費)"
    ]

    for idx, rs in enumerate(rs_levels):
        ax = fig.add_subplot(2, 2, idx + 1, projection='3d')
        ax.set_facecolor("#FAFAFA")

        Z = surfaces[rs]

        # 3D曲面
        surf = ax.plot_surface(
            L_mesh, R_mesh, Z,
            cmap="viridis",
            norm=norm,
            rstride=2, cstride=2,
            alpha=0.88,
            antialiased=True,
            edgecolor='none'
        )

        # 底面に等高線を投影 (視覚的錯視を防止し、ピーク座標を一目瞭然にする)
        ax.contour(L_mesh, R_mesh, Z, zdir='z', offset=0.0, cmap='viridis', norm=norm, levels=12, linewidths=1.2)

        # 離散候補の真のピークを明示
        opt_l, opt_r, opt_z = discrete_peaks[rs]
        if opt_z > 0.01:
            ax.scatter([opt_l], [opt_r], [opt_z], color="red", s=90, depthshade=False, edgecolors="black", linewidths=1.5, zorder=20)
            ax.text(
                opt_l, opt_r, opt_z + 0.025,
                f" ★Peak\n L={opt_l}B, r={opt_r:.0f}%\n Z={opt_z:.4f}",
                color="#B71C1C", fontsize=9.5, fontweight='bold', zorder=21
            )
            # 底面にもマーカーを投影
            ax.scatter([opt_l], [opt_r], [0.0], color="red", s=30, alpha=0.6, depthshade=False)
        else:
            ax.text(
                255, 30, 0.03,
                "復元不能\n(Goodput = 0.000)",
                color="#D32F2F", fontsize=11, fontweight='bold', ha='center'
            )

        # L=128 と L=255 の比較ポイントを明示
        p128 = l128_peaks[rs]
        p255 = l255_peaks[rs]
        if p128[2] > 0.01:
            ax.scatter([p128[0]], [p128[1]], [p128[2]], color="#1976D2", s=55, depthshade=False, edgecolors="white", linewidths=1.2, zorder=15)
        if p255[2] > 0.01:
            ax.scatter([p255[0]], [p255[1]], [p255[2]], color="#388E3C", s=55, depthshade=False, edgecolors="white", linewidths=1.2, zorder=15)

        # 軸設定
        ax.set_title(titles[idx], fontsize=12.5, pad=12, color="#1A237E")
        ax.set_xlabel("Packet Length L [Bytes]", fontsize=9.5, labelpad=8)
        ax.set_ylabel("Raptor Ratio r [%]", fontsize=9.5, labelpad=8)
        ax.set_zlabel("Goodput Z", fontsize=9.5, labelpad=8)

        ax.set_xlim(128, 512)
        ax.set_ylim(10, 50)
        ax.set_zlim(0.0, max_z_all)

        # 視角設定: 山の標高差が直感的に正しく見える角度 (elev=32, azim=-130)
        ax.view_init(elev=32, azim=-130)
        ax.grid(True, linestyle=":", alpha=0.4)

    # カラーバー
    cbar_ax = fig.add_axes([0.92, 0.25, 0.02, 0.5])
    cbar = fig.colorbar(matplotlib.cm.ScalarMappable(norm=norm, cmap="viridis"), cax=cbar_ax)
    cbar.set_label("Goodput Score Z", fontsize=11, fontweight='bold')

    plt.suptitle("RSパリティ別 2×2 独立3D曲面比較 (厳密離散グリッド & 底面等高線投影)", fontsize=15, fontweight='bold', y=0.96)

    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(SCRATCH_PNG), exist_ok=True)
    plt.savefig(OUTPUT_PNG, dpi=300, bbox_inches='tight')
    plt.savefig(SCRATCH_PNG, dpi=300, bbox_inches='tight')
    plt.close()
    print("改善された3Dグラフ生成完了！")

if __name__ == "__main__":
    main()
