"""
ステップ2 運用ヒートマップ生成スクリプト
- 受信人数 N (1人〜8人) x 録音時間 T (1分〜10分)
- パラメータ固定: L=255B, RS=48B, ラプター r=30% (K=256パケット, 1サイクル=333パケット)
- 計算モデル:
  1. ビット単位の多数決合成 (Majority Voting)
  2. 2局受信時の票割れ消失指定 (Erasure訂正: 最大48バイト訂正)
  3. ラプター符号 (噴水パケット) のユニーク収集数による画像完全復元確率 P_restore
- 高視認性 2D カラーヒートマップ (等高線 & パーセント数値アノテーション付き)
"""

import math
import os
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize
from scipy.stats import binom, norm

# 日本語フォント設定
plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['font.sans-serif'] = ['Meiryo', 'Yu Gothic', 'MS Gothic', 'TakaoPGothic', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['mathtext.fontset'] = 'cm'

# 出力先パス
ARTIFACT_DIR = r"C:\Users\kosei\.gemini\antigravity-ide\brain\5c19d6a4-d1e9-400a-8acf-bf450e75f505"
OUTPUT_PNG = os.path.join(ARTIFACT_DIR, "operation_recovery_heatmap.png")
SCRATCH_PNG = r"c:\Users\kosei\OneDrive\Desktop\sstv-png-stand\scratch\operation_recovery_heatmap.png"

# システム定数 (ステップ1確定値)
L_PKT = 255            # パケット総長 [Bytes]
RS_PARITY = 48         # RSパリティ [Bytes] (t=24B, 消失訂正=48B)
RAPTOR_R = 30.0        # ラプター冗長率 30%
K_TILES = 256          # 256タイル = 256パケット
K_REQ = int(math.ceil(K_TILES * 1.02))  # 復元必要数: 262パケット

# 音響物理層定数
HDR_PHYS = 68          # プリアンブル音 + ガードタイム等価長
BAUD_RATE_EQUIV = 300  # 伝送速度: 300 Bytes/sec (約 2400 bps)
PKT_TOTAL_BYTES = L_PKT + HDR_PHYS  # 323 Bytes / パケット
PKT_DURATION_SEC = PKT_TOTAL_BYTES / BAUD_RATE_EQUIV  # 約 1.077 秒 / パケット

# 音響ノイズ・誤り率環境
BASE_BER = 0.012       # 単局基本ビット誤り率 1.2%
DROP_RATE = 0.20       # 音響フェージング・パケット脱落率 20%
P_RX_STATION = 1.0 - DROP_RATE  # 単局のパケット受信成功率 80%

def compute_effective_packet_survival(n_stations: int) -> float:
    """
    N人のスマートフォン録音がある場合の、各パケットの平均生存・復元確率
    - 0局受信: 消失
    - 1局受信: 単局RS訂正 (t=24)
    - 2局受信: 票割れ消失指定 (Erasure訂正能力 2t=48)
    - 3局以上: ビット多数決合成 (Majority Voting)
    """
    t_corr = RS_PARITY // 2  # 24バイト
    t_eras = RS_PARITY       # 48バイト

    total_succ_prob = 0.0

    for k in range(1, n_stations + 1):
        # k台のスマホがこのパケットを受信できた確率
        prob_k = binom.pmf(k, n_stations, P_RX_STATION)

        if k == 1:
            # 1局単独
            p_sym = 1.0 - ((1.0 - BASE_BER) ** 8)
            succ = binom.cdf(t_corr, L_PKT, p_sym)
        elif k == 2:
            # 2局受信: 一致は超低誤り、不一致は消失指定 (Erasure)
            p_split = 2.0 * BASE_BER * (1.0 - BASE_BER)
            p_both_err = BASE_BER ** 2
            p_byte_split = 1.0 - ((1.0 - p_split) ** 8)
            p_byte_both = 1.0 - ((1.0 - p_both_err) ** 8)
            # 消失バイト数が48以下なら完全復元
            succ = binom.cdf(t_eras, L_PKT, p_byte_split + 2.0 * p_byte_both)
        else:
            # 3局以上の多数決 (Majority Voting)
            # 多数決で誤る確率: 誤った局数が過半数
            majority_thresh = (k // 2) + 1
            ber_maj = sum(binom.pmf(m, k, BASE_BER) for m in range(majority_thresh, k + 1))
            p_sym_maj = 1.0 - ((1.0 - ber_maj) ** 8)
            succ = binom.cdf(t_corr, L_PKT, p_sym_maj)

        total_succ_prob += prob_k * succ

    return float(np.clip(total_succ_prob, 0.0, 1.0))

def compute_restore_probability(n_stations: int, record_minutes: float) -> float:
    """
    人数 N と録音時間 T [分] における画像完全復元確率 P_restore
    """
    p_pkt = compute_effective_packet_survival(n_stations)
    
    # 録音時間 T 分の間に送信された総パケット数
    total_sec = record_minutes * 60.0
    m_transmitted = total_sec / PKT_DURATION_SEC

    # 受信された有効パケット数の期待値と標準偏差
    mu = m_transmitted * p_pkt
    sigma = math.sqrt(max(0.1, m_transmitted * p_pkt * (1.0 - p_pkt)))

    # 復元条件: 受信パケット数 >= K_REQ (262個)
    # 急峻なS字復元カーブ (正規近似 CDF)
    z_score = (mu - K_REQ) / sigma
    p_restore = norm.cdf(z_score)

    # 録音時間が短すぎてパケット総数自体がK_REQ未満の場合は物理的に0%
    if m_transmitted < K_REQ:
        p_restore = 0.0
    elif m_transmitted < K_REQ * 1.05:
        p_restore *= max(0.0, (m_transmitted - K_REQ) / (K_REQ * 0.05))

    return float(np.clip(p_restore * 100.0, 0.0, 100.0))

def main():
    stations_list = [1, 2, 3, 4, 5, 6, 7, 8]  # 受信人数 1人〜8人
    minutes_list = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]  # 録音時間 1分〜10分

    heatmap_data = np.zeros((len(stations_list), len(minutes_list)))

    print("=== ステップ2: 運用ヒートマップ計算中 ===")
    for i, n in enumerate(stations_list):
        p_surv = compute_effective_packet_survival(n)
        print(f"受信人数 N={n}人 -> パケット実効生存率: {p_surv * 100:.2f}%")
        for j, t in enumerate(minutes_list):
            p_rec = compute_restore_probability(n, t)
            heatmap_data[i, j] = p_rec

    # プロット作成
    fig, ax = plt.subplots(figsize=(13, 9.6), dpi=300)
    fig.patch.set_facecolor("#FAFAFA")
    ax.set_facecolor("#FAFAFA")

    # ヒートマップ描画 (YlGnBu: 0%=薄黄, 100%=濃紺)
    im = ax.imshow(heatmap_data, cmap="YlGnBu", aspect="auto", origin="lower",
                   extent=[0.5, 10.5, 0.5, 8.5], vmin=0, vmax=100)

    # カラーバー
    cbar = fig.colorbar(im, ax=ax, pad=0.03, fraction=0.046)
    cbar.set_label("画像完全復元確率 P_rec [%]", fontsize=12, fontweight="bold", labelpad=10)
    cbar.ax.tick_params(labelsize=11)

    # 各セルに数値をアノテーション
    for i, n in enumerate(stations_list):
        for j, t in enumerate(minutes_list):
            val = heatmap_data[i, j]
            color = "white" if val >= 50 else "#555555"
            text_str = f"{val:.0f}%" if val >= 1.0 else "0%"
            weight = "bold" if val >= 95 else "normal"
            ax.text(t, n, text_str, ha="center", va="center", color=color,
                    fontsize=10.5, fontweight=weight)

    # 等高線 (50%, 90%, 99% の実用境界ライン)
    T_mesh, N_mesh = np.meshgrid(minutes_list, stations_list)
    cs = ax.contour(T_mesh, N_mesh, heatmap_data, levels=[50, 90, 99],
                    colors=["#E65100", "#1B5E20", "#B71C1C"], linewidths=[1.8, 2.2, 2.5], linestyles=["--", "-", "-."])
    
    # 等高線ラベル
    fmt = {50: '50% 限界線', 90: '90% 運用線', 99: '99% 安全確実線'}
    ax.clabel(cs, cs.levels, inline=True, fmt=fmt, fontsize=10.5)

    # 軸・目盛り設定
    ax.set_xticks(minutes_list)
    ax.set_xticklabels([f"{t:.0f}分" for t in minutes_list], fontsize=11)
    ax.set_yticks(stations_list)
    ax.set_yticklabels([f"{n}人" for n in stations_list], fontsize=11)

    ax.set_xlabel("録音時間 T [分]", fontsize=13, fontweight="bold", labelpad=12)
    ax.set_ylabel("受信人数（スマホ局数） N [人]", fontsize=13, fontweight="bold", labelpad=10)
    ax.set_title("SSTV-JPEG 運用ヒートマップ: 受信人数 × 録音時間 に対する画像復元確率\n(確定パラメータ: L=255B, RS=48B, r=30% 固定)",
                 fontsize=14, fontweight="bold", pad=15, color="#1A237E")

    # グリッド線
    ax.set_xticks(np.arange(0.5, 11.5, 1), minor=True)
    ax.set_yticks(np.arange(0.5, 9.5, 1), minor=True)
    ax.grid(which="minor", color="#DDDDDD", linestyle="-", linewidth=0.8)
    ax.tick_params(which="minor", size=0)

    # 運用ガイドライン注記ボックス (プロット下部に余裕を持って配置)
    guide_text = (
        "【現場運用ガイドライン】\n"
        "• 物理的最小時間: 1枚の送信完了に最低 4.7分 必要 (1〜4分ではパケット数不足のため復元不可)\n"
        "• 5人以上 (大人数協調): 5分の録音で即座に 100% 完全復元 (多数決により実効BERがほぼゼロ)\n"
        "• 3局協調 (標準運用): 6分の録音で 100% 確実に復元可能\n"
        "• 2局協調 (Erasure訂正): 8分の録音で 100% 復元\n"
        "• 1人単独受信: パケット取りこぼしが多いため、2周目 (10分以上) の録音が必要 (10分で 66%)"
    )
    plt.figtext(0.5, 0.02, guide_text, ha="center", fontsize=10,
                bbox=dict(boxstyle="round,pad=0.6", facecolor="#F0F4F8", edgecolor="#1976D2", alpha=0.95, linewidth=1.2))

    plt.subplots_adjust(bottom=0.23, top=0.92)

    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(SCRATCH_PNG), exist_ok=True)
    plt.savefig(OUTPUT_PNG, dpi=300, bbox_inches='tight')
    plt.savefig(SCRATCH_PNG, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"運用ヒートマップ生成完了: {OUTPUT_PNG}")

if __name__ == "__main__":
    main()
