import numpy as np
import scipy.stats as stats
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize

def generate_realistic_fec_data():
    l_vals = np.linspace(128, 255, 128)
    r_vals = np.linspace(10, 50, 81)
    L, R = np.meshgrid(l_vals, r_vals)

    rs_levels = [16, 32, 48, 64]
    data_dict = {}

    s_img = 256 * 113.0  # 28928 Bytes
    hdr_pkt = 7.0
    hdr_phys = 42.0      # 音響プリアンブル・同期オーバーヘッド等価
    ber = 0.0105         # 1.05% BER
    ps = 1.0 - (1.0 - ber)**8 # シンボル誤り率 (~8.1%)
    p_loss = 0.15        # 音響遮蔽消失率 (15%)

    for rs in rs_levels:
        t = rs / 2.0
        cap = np.maximum(L - hdr_pkt - rs, 1.0)
        
        # タイル分割ペナルティ
        frag = 1.0 + 0.35 * (1.0 / (1.0 + np.exp((cap - 113.0) * 0.15)))
        k_pkts = np.ceil(s_img / cap) * frag

        # RS パケット誤り訂正確率 (正規近似による高速連続計算)
        mean_err = L * ps
        std_err = np.maximum(np.sqrt(L * ps * (1.0 - ps)), 0.1)
        # 誤り数が t 以下である確率
        p_rs_succ = stats.norm.cdf(t + 0.5, loc=mean_err, scale=std_err)
        
        # 物理層生存確率
        p_pkt = p_rs_succ * (1.0 - p_loss)

        # 送信パケット数
        n_tx = k_pkts * (1.0 + R / 100.0)

        # ラプター符号の復元成功確率 (正規近似)
        mu_recv = n_tx * p_pkt
        sigma_recv = np.maximum(np.sqrt(n_tx * p_pkt * np.maximum(1.0 - p_pkt, 0.05)), 0.1)
        k_req = k_pkts * 1.02  # 2% ラプターオーバーヘッド
        p_rec = stats.norm.cdf((mu_recv - k_req) / sigma_recv)

        # 総送信コスト (等価バイト数)
        total_tx = n_tx * (L + hdr_phys)

        # Goodput
        z = (s_img * p_rec) / total_tx
        data_dict[rs] = z

    return l_vals, r_vals, rs_levels, data_dict

if __name__ == "__main__":
    l_vals, r_vals, rs_levels, data = generate_realistic_fec_data()
    for rs in rs_levels:
        z = data[rs]
        max_idx = np.unravel_index(np.argmax(z), z.shape)
        print(f"RS={rs}: Max Z={z[max_idx]:.4f} at L={l_vals[max_idx[1]]:.1f}, r={r_vals[max_idx[0]]:.1f}%")
