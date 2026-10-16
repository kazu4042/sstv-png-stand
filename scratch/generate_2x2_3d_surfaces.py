"""
RSパリティ別 2x2 3Dファセット曲面グラフ生成スクリプト
- ユーザー提案: RS固定の3D曲面 (X: パケット長 L, Y: ラプター比率 r, Z: 有効Goodput) をRSを変えて4個 (2x2) 並列描画
- 対象RS: 16B, 32B, 48B, 64B
- 各面における L=128 vs L=255 の比較、および各面のピーク (山頂) を明示
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
from scipy.interpolate import RectBivariateSpline
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

# 通信物理パラメータ
HDR_PKT = 7    # ヘッダ (タイルID + ESI等)
HDR_PHYS = 68  # 音響SSTV物理層オーバーヘッド (プリアンブル + ガードタイム等価長)
BASE_BER = 0.012  # 基本ビット誤り率 1.2%
DROP_RATE = 0.20  # 音響フェージング・欠落率 20%

def calc_k_symbols(l_pkt: int, rs_parity: int) -> int:
    cap = l_pkt - HDR_PKT - rs_parity
    if cap <= 0:
        return 999999
    # 各タイル独立パケット化 (即時描画・跨ぎ混載禁止)
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
    needed = int(math.ceil(k * 1.02))  # ラプター復元に必要なシンボル数
    p_rec = 1.0 - binom.cdf(needed - 1, total_m, p_pkt)
    if r_ratio < 22.0:
        p_rec *= (r_ratio / 22.0) ** 3.5

    total_bytes = total_m * (l_pkt + HDR_PHYS)
    goodput = (TOTAL_IMAGE_BYTES * p_rec) / float(total_bytes) if total_bytes > 0 else 0.0
    return goodput, p_rec

def main():
    # グリッド定義
    l_candidates = np.array([128, 192, 255, 384, 512])
    r_candidates = np.linspace(10.0, 60.0, 26)  # 10% - 60%
    rs_levels = [16, 32, 48, 64]

    # データグリッド作成とスプライン補間
    l_dense = np.linspace(128, 512, 60)
    r_dense = np.linspace(10.0, 60.0, 60)
    L_mesh, R_mesh = np.meshgrid(l_dense, r_dense)

    surfaces = {}
    peak_info = {}
    l128_peak = {}
    l255_peak = {}

    max_z_all = 0.0

    for rs in rs_levels:
        z_grid = np.zeros((len(r_candidates), len(l_candidates)))
        for i, r in enumerate(r_candidates):
            for j, l in enumerate(l_candidates):
                gp, prec = compute_goodput(int(l), rs, r)
                z_grid[i, j] = gp

        # 2次元スプライン補間
        spline = RectBivariateSpline(r_candidates, l_candidates, z_grid, kx=2, ky=2)
        z_dense = spline(r_dense, l_dense)
        z_dense = np.clip(z_dense, 0.0, None)
        surfaces[rs] = z_dense

        # ピーク探索
        p_idx = np.unravel_index(np.argmax(z_dense), z_dense.shape)
        opt_r = r_dense[p_idx[0]]
        opt_l = l_dense[p_idx[1]]
        opt_z = z_dense[p_idx]
        peak_info[rs] = (opt_l, opt_r, opt_z)

        # L=128 と L=255 の局所最大値
        l128_idx = np.argmin(np.abs(l_dense - 128))
        l128_profile = z_dense[:, l128_idx]
        l128_best_r = r_dense[np.argmax(l128_profile)]
        l128_peak[rs] = (128, l128_best_r, np.max(l128_profile))

        l255_idx = np.argmin(np.abs(l_dense - 255))
        l255_profile = z_dense[:, l255_idx]
        l255_best_r = r_dense[np.argmax(l255_profile)]
        l255_peak[rs] = (255, l255_best_r, np.max(l255_profile))

        if opt_z > max_z_all:
            max_z_all = opt_z

    print(f"全体最大 Goodput: {max_z_all:.4f}")
    for rs in rs_levels:
        opt_l, opt_r, opt_z = peak_info[rs]
        print(f"RS={rs}B -> Peak: L={opt_l:.0f}B, r={opt_r:.1f}%, Goodput={opt_z:.4f}")
        print(f"   L=128B: r={l128_peak[rs][1]:.1f}%, Goodput={l128_peak[rs][2]:.4f}")
        print(f"   L=255B: r={l255_peak[rs][1]:.1f}%, Goodput={l255_peak[rs][2]:.4f}")

    # 2x2 の 3D サブプロット描画
    fig = plt.figure(figsize=(16, 13), dpi=300)
    fig.patch.set_facecolor("#FAFAFA")
    norm = Normalize(vmin=0.0, vmax=max_z_all * 1.05)

    titles = [
        r"$\bf{(a)\ RS=16\ Bytes}$ (誤り訂正弱 / オーバーヘッド最小)",
        r"$\bf{(b)\ RS=32\ Bytes}$ (標準訂正 / バランス型)",
        r"$\bf{(c)\ RS=48\ Bytes}$ (高耐性 / 本命候補)",
        r"$\bf{(d)\ RS=64\ Bytes}$ (過剰耐性 / パリティ浪費)"
    ]

    for idx, rs in enumerate(rs_levels):
        ax = fig.add_subplot(2, 2, idx + 1, projection='3d')
        ax.set_facecolor("#FAFAFA")
        
        Z = surfaces[rs]
        # 曲面描画
        surf = ax.plot_surface(
            L_mesh, R_mesh, Z,
            cmap="viridis",
            norm=norm,
            rstride=2, cstride=2,
            alpha=0.88,
            antialiased=True,
            edgecolor='none'
        )
        
        # ワイヤーフレームで等高線を表現
        ax.plot_wireframe(L_mesh, R_mesh, Z, rstride=8, cstride=8, color="#222222", alpha=0.15, linewidth=0.5)

        # 最適ピークのマーカー
        opt_l, opt_r, opt_z = peak_info[rs]
        ax.scatter([opt_l], [opt_r], [opt_z], color="red", s=70, depthshade=False, edgecolors="black", zorder=10)
        ax.text(
            opt_l, opt_r, opt_z + 0.015,
            f" Peak\n L={opt_l:.0f}B, r={opt_r:.0f}%\n Z={opt_z:.3f}",
            color="#B71C1C", fontsize=9, fontweight='bold', zorder=11
        )

        # L=128 と L=255 の比較ポイント
        p128 = l128_peak[rs]
        p255 = l255_peak[rs]
        ax.scatter([p128[0]], [p128[1]], [p128[2]], color="#1976D2", s=45, depthshade=False, edgecolors="white", zorder=9)
        ax.scatter([p255[0]], [p255[1]], [p255[2]], color="#388E3C", s=45, depthshade=False, edgecolors="white", zorder=9)

        # 軸ラベル・タイトル
        ax.set_title(titles[idx], fontsize=13, pad=12, color="#1A237E")
        ax.set_xlabel("Packet Length L [Bytes]", fontsize=10, labelpad=8)
        ax.set_ylabel("Raptor Ratio r [%]", fontsize=10, labelpad=8)
        ax.set_zlabel("Goodput Z", fontsize=10, labelpad=8)

        ax.set_xlim(128, 512)
        ax.set_ylim(10, 60)
        ax.set_zlim(0.0, max_z_all * 1.15)

        # 視点角度 (山頂が見えやすい角度)
        ax.view_init(elev=28, azim=-125)
        ax.grid(True, linestyle=":", alpha=0.5)

    # カラーバー
    cbar_ax = fig.add_axes([0.92, 0.25, 0.02, 0.5])
    cbar = fig.colorbar(matplotlib.cm.ScalarMappable(norm=norm, cmap="viridis"), cax=cbar_ax)
    cbar.set_label("Goodput Score Z", fontsize=11, fontweight='bold')

    plt.suptitle("RSパリティ別 2×2 独立3D曲面比較 (Full Joint Optimization)", fontsize=16, fontweight='bold', y=0.96)
    
    # 保存
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(SCRATCH_PNG), exist_ok=True)
    plt.savefig(OUTPUT_PNG, dpi=300, bbox_inches='tight')
    plt.savefig(SCRATCH_PNG, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"3Dグラフ生成完了: {OUTPUT_PNG}")

if __name__ == "__main__":
    main()
