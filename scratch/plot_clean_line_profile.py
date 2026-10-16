"""
Academic Paper Plot Generator: Clean 2D Line Profile for Parameter Optimization
学術論文用 視認性最優先 2D プロファイル曲線グラフ
- 横軸: パケット長 L [Bytes]
- 縦軸: 有効Goodput スコア Z
- 曲線: RSパリティ 2t (16, 32, 48, 64 Bytes) ごとの最適包絡線 (各Lにおける最適ラプター比率 r* を適用)
- 大域的最適解 (Global Optimum) を明確に強調
"""

import numpy as np
import scipy.stats as stats
import matplotlib.pyplot as plt

plt.rcParams['font.family'] = 'DejaVu Sans'
plt.rcParams['mathtext.fontset'] = 'cm'
plt.rcParams['font.size'] = 11
plt.rcParams['axes.labelsize'] = 12
plt.rcParams['axes.titlesize'] = 13
plt.rcParams['legend.fontsize'] = 10.5

def calculate_clean_profile():
    l_vals = np.linspace(128, 255, 128)
    r_vals = np.linspace(10, 50, 100)
    rs_levels = [16, 32, 48, 64]

    s_img = 256.0 * 113.0
    hdr_pkt = 7.0
    hdr_phys = 48.0
    ber = 0.0125
    ps = 1.0 - (1.0 - ber)**8
    p_loss = 0.18

    results = {}

    for rs in rs_levels:
        t = rs / 2.0
        best_z_per_l = []
        best_r_per_l = []

        for l in l_vals:
            cap = max(l - hdr_pkt - rs, 1.0)
            frag = 1.0 + 0.45 * (1.0 / (1.0 + np.exp((cap - 113.0) * 0.12)))
            k_pkts = np.ceil(s_img / cap) * frag

            mean_err = l * ps
            std_err = max(np.sqrt(l * ps * (1.0 - ps)), 0.1)
            p_rs_succ = stats.norm.cdf(t + 0.5, loc=mean_err, scale=std_err)
            p_pkt = p_rs_succ * (1.0 - p_loss)

            # 各rにおけるGoodputを走査
            n_tx = k_pkts * (1.0 + r_vals / 100.0)
            mu_recv = n_tx * p_pkt
            sigma_recv = np.maximum(np.sqrt(n_tx * p_pkt * np.maximum(1.0 - p_pkt, 0.05)), 0.1)
            k_req = k_pkts * 1.03
            p_rec = stats.norm.cdf((mu_recv - k_req) / sigma_recv)
            total_tx = n_tx * (l + hdr_phys)
            z = (s_img * p_rec) / total_tx * 0.85

            best_idx = np.argmax(z)
            best_z_per_l.append(z[best_idx])
            best_r_per_l.append(r_vals[best_idx])

        results[rs] = {
            'z': np.array(best_z_per_l),
            'r': np.array(best_r_per_l)
        }

    return l_vals, rs_levels, results

def plot_profile_comparison(l_vals, rs_levels, results, output_path="paper_clean_line_profile.png"):
    fig, ax = plt.subplots(figsize=(9.2, 6.0), dpi=300)
    fig.patch.set_facecolor('white')

    colors = {16: '#94a3b8', 32: '#0284c7', 48: '#ef4444', 64: '#10b981'}
    styles = {16: ':', 32: '--', 48: '-', 64: '-.'}
    widths = {16: 1.8, 32: 2.2, 48: 3.2, 64: 2.2}

    global_max_z = -1.0
    global_opt = None

    for rs in rs_levels:
        z = results[rs]['z']
        r = results[rs]['r']
        lbl = f"RS Parity $2t = {rs}$ Bytes ($t={rs//2}$B)"
        ax.plot(l_vals, z, label=lbl, color=colors[rs], linestyle=styles[rs], linewidth=widths[rs])

        max_idx = np.argmax(z)
        if z[max_idx] > global_max_z:
            global_max_z = z[max_idx]
            global_opt = (rs, l_vals[max_idx], r[max_idx], z[max_idx])

    # 大域最適点の強調プロット
    opt_rs, opt_l, opt_r, opt_z = global_opt
    ax.scatter([opt_l], [opt_z], color='#ef4444', s=200, marker='*', edgecolors='black', linewidths=1.2, zorder=10)

    # アノテーション
    ann_text = (f"★ Global Optimum\n"
                f"$2t^* = {opt_rs}$ Bytes\n"
                f"$L^* = {opt_l:.0f}$ Bytes\n"
                f"$r^* = {opt_r:.1f}\\%$\n"
                f"$Z^* = {opt_z:.3f}$")
    ax.annotate(ann_text,
                xy=(opt_l, opt_z),
                xytext=(opt_l - 45, opt_z - 0.12),
                fontsize=10.5,
                fontweight='bold',
                color='#b91c1c',
                bbox=dict(boxstyle="round,pad=0.5", facecolor='white', edgecolor='#ef4444', lw=1.8, alpha=0.95),
                arrowprops=dict(arrowstyle="->", color='#ef4444', lw=2.0, connectionstyle="arc3,rad=-0.2"),
                zorder=12)

    ax.set_xlabel("Packet Length $L$ [Bytes]", fontweight='bold')
    ax.set_ylabel("Maximum Goodput Score $Z^*$ [Ratio]", fontweight='bold')
    ax.set_title("Optimal Goodput Profiles Across Packet Lengths for Each RS Parity Level\n"
                 "(Evaluated at Optimal Raptor Redundancy $r^*(L)$)", fontweight='bold', pad=12)
    ax.grid(True, linestyle='--', alpha=0.5)
    ax.set_ylim(-0.02, 0.42)
    ax.set_xlim(125, 258)
    ax.legend(frameon=True, facecolor='white', edgecolor='#cbd5e1', loc='upper left')

    plt.tight_layout()
    plt.savefig(output_path, dpi=300)
    plt.close()
    print(f"[SUCCESS] Saved line profile to {output_path}")

if __name__ == "__main__":
    l_vals, rs_levels, results = calculate_clean_profile()
    plot_profile_comparison(l_vals, rs_levels, results, "paper_clean_line_profile.png")
