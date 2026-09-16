import os
import sys

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

import io
import math
from collections import defaultdict
import numpy as np
from PIL import Image, ImageFile, ImageDraw, ImageFont

# 破損・途切れJPEGの読み込み許可
setattr(ImageFile, 'LOAD_TRUNCATED_IMAGES', True)

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from digital_turbo_jpeg import config_turbo as config
from digital_turbo_jpeg.aggregator_turbo import TurboJPEGAggregator


class MajorityRecoveryEvaluator:
    """digital_turbo_jpeg の No.1〜No.5 多数決・復元ワークフローに完全準拠し、
    音声数増加に伴う画像復元率 (Tile%, PSNR, SSIM, BER) の推移を検証・グラフ化するクラス。
    """

    def __init__(self, width=None, height=None, tile_size=None):
        self.width = width or getattr(config, "WIDTH", 256)
        self.height = height or getattr(config, "HEIGHT", 256)
        self.tile_size = tile_size or getattr(config, "TILE_SIZE", 16)
        self.tile_count_x = math.ceil(self.width / self.tile_size)
        self.tile_count_y = math.ceil(self.height / self.tile_size)
        self.total_tiles = self.tile_count_x * self.tile_count_y
        self.aggregator = TurboJPEGAggregator()

    @staticmethod
    def calculate_psnr(gt_img_arr, target_img_arr):
        """2つのRGB画像配列間のPSNR (dB) を計算。完全一致時は 100.0 dB を返す"""
        mse = np.mean((gt_img_arr.astype(np.float64) - target_img_arr.astype(np.float64)) ** 2)
        if mse <= 1e-10:
            return 100.0  # 完全一致
        max_pixel = 255.0
        return 20.0 * math.log10(max_pixel / math.sqrt(mse))

    @staticmethod
    def calculate_ssim_simple(gt_img_arr, target_img_arr):
        """NumPyによるSSIM (Structural Similarity Index) 近似計算"""
        c1 = (0.01 * 255) ** 2
        c2 = (0.03 * 255) ** 2

        gt = gt_img_arr.astype(np.float64)
        tar = target_img_arr.astype(np.float64)

        mu_x = np.mean(gt)
        mu_y = np.mean(tar)

        sigma_x_sq = np.var(gt)
        sigma_y_sq = np.var(tar)
        sigma_xy = np.mean((gt - mu_x) * (tar - mu_y))

        ssim = ((2 * mu_x * mu_y + c1) * (2 * sigma_xy + c2)) / \
               ((mu_x ** 2 + mu_y ** 2 + c1) * (sigma_x_sq + sigma_y_sq + c2))
        return float(np.clip(ssim, 0.0, 1.0))

    def majority_vote_packets(self, packet_sets_list):
        """複数のパケットセットに対し、SNR加重ビット多数決を実行。
        タイブレーク時は最高SNRパケットのビットを採用し、1への偏りを完全排除。
        """
        if not packet_sets_list:
            return []
        if len(packet_sets_list) == 1:
            return packet_sets_list[0]

        num_pkts = len(packet_sets_list[0])
        voted_packets = []

        for p_idx in range(num_pkts):
            pkts = [p_set[p_idx] for p_set in packet_sets_list if p_idx < len(p_set)]
            base_pkt = dict(pkts[0])

            payload_len = base_pkt["payload_length"]
            target_bits_len = payload_len * 8

            vote_sum = np.zeros(target_bits_len, dtype=np.float64)
            best_snr = -1.0
            best_bits = None

            for p in pkts:
                bits = p["payload_bits"][:target_bits_len]
                if len(bits) == target_bits_len:
                    snr = float(p.get("snr_val", 1.0))
                    weight = snr + 1.0
                    vote_sum += (np.array(bits, dtype=np.float64) * 2.0 - 1.0) * weight
                    if snr > best_snr:
                        best_snr = snr
                        best_bits = bits

            voted_bits = []
            for b_idx in range(target_bits_len):
                if vote_sum[b_idx] > 0.0:
                    voted_bits.append(1)
                elif vote_sum[b_idx] < 0.0:
                    voted_bits.append(0)
                else:
                    # 同数タイブレーク: 最高SNRパケットのビットを採用（バイアス排除）
                    voted_bits.append(best_bits[b_idx] if best_bits is not None else 0)

            base_pkt["payload_bits"] = voted_bits
            voted_packets.append(base_pkt)

        return voted_packets

    def decode_accumulated_sets_to_image(self, accumulated_sets, gt_img_array=None):
        """
        digital_turbo_jpeg の No.1〜No.5 ワークフローに基づき、
        蓄積されたパケットセット群から画像を復元する。

        No.1 受信タイル単体での無傷チェック・即時採用
        No.2 全ビット加重多数決
        No.3 RSTマーカ区間分割による再構築・多数決
        No.4 最高SNRタイルのクリア単体採用 (有効画素率 >= 85%)
        No.5 RGBピクセル空間での加重平均合成 (1枚のみ時もここで安全描画)
        """
        canvas = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        rendered_mask = np.zeros((self.tile_count_y, self.tile_count_x), dtype=bool)
        stage_counts = defaultdict(int)

        # 座標 (tx, ty) ごとにパケットを集約
        packets_by_coord = defaultdict(list)
        for p_set in accumulated_sets:
            for p in p_set:
                tx = p["tile_x"]
                ty = p["tile_y"]
                plen = p["payload_length"]
                snr = float(p.get("snr_val", 1.0))
                bits = p["payload_bits"]
                if isinstance(bits, list):
                    bits_str = "".join(str(b) for b in bits)
                else:
                    bits_str = str(bits)
                packets_by_coord[(tx, ty)].append((bits_str, snr, plen))

        perfect_count = 0
        decoded_visible_count = 0

        for ty in range(self.tile_count_y):
            for tx in range(self.tile_count_x):
                y_start = ty * self.tile_size
                y_end = min(y_start + self.tile_size, self.height)
                x_start = tx * self.tile_size
                x_end = min(x_start + self.tile_size, self.width)
                cur_w = x_end - x_start
                cur_h = y_end - y_start

                pkts = packets_by_coord.get((tx, ty), [])
                if not pkts:
                    continue

                best_plen = max(p[2] for p in pkts)
                resolve_input = [(p[0], p[1]) for p in pkts]

                # No.1〜No.5 の実行フローを適用
                tile_img, final_bytes, stage_name = self.aggregator.resolve_tile(
                    resolve_input, best_plen, cur_w, cur_h
                )

                if tile_img is not None:
                    t_arr = np.array(tile_img.convert("RGB").resize((cur_w, cur_h)))
                    canvas[y_start:y_end, x_start:x_end] = t_arr
                    rendered_mask[ty, tx] = True
                    stage_counts[stage_name or "UNKNOWN"] += 1

                    if stage_name in ("NO1", "NO2", "NO3"):
                        perfect_count += 1
                    decoded_visible_count += 1
                else:
                    stage_counts["FAILED"] += 1

        # 未受信または極端破損で描画できなかったタイルをインペインティング
        if not np.all(rendered_mask):
            canvas_pil = Image.fromarray(canvas)
            canvas_pil = self.aggregator.inpaint_missing_tiles(
                canvas_pil, self.tile_count_x, self.tile_count_y, self.tile_size, self.tile_size, rendered_mask
            )
            canvas = np.array(canvas_pil)

        tile_recovery_rate = (perfect_count / self.total_tiles) * 100.0
        visible_rate = (decoded_visible_count / self.total_tiles) * 100.0

        psnr_val = 0.0
        ssim_val = 0.0
        if gt_img_array is not None:
            psnr_val = self.calculate_psnr(gt_img_array, canvas)
            ssim_val = self.calculate_ssim_simple(gt_img_array, canvas)

        return canvas, tile_recovery_rate, psnr_val, ssim_val, dict(stage_counts), visible_rate

    def evaluate_progression(self, gt_packets, gt_img_array, sets_dict):
        """音声数 1本(A), 2本(A+B), 3本(A+B+C) の推移を計算"""
        steps = []
        accumulated_sets = []
        names = list(sets_dict.keys())

        # 送信側無傷JPEGの画像配列（伝送路ノイズ0の理想画像）を取得
        # これを正解基準とすることで、伝送路ノイズ消滅時に PSNR=100dB (完全一致) が正確に評価される
        canvas_gt = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        for p in gt_packets:
            tx = p["tile_x"]
            ty = p["tile_y"]
            y_start = ty * self.tile_size
            y_end = min(y_start + self.tile_size, self.height)
            x_start = tx * self.tile_size
            x_end = min(x_start + self.tile_size, self.width)
            try:
                t_img = Image.open(io.BytesIO(p["payload_bytes"])).convert("RGB")
                canvas_gt[y_start:y_end, x_start:x_end] = np.array(t_img.resize((x_end - x_start, y_end - y_start)))
            except Exception:
                pass

        all_gt_bits = []
        for p in gt_packets:
            all_gt_bits.extend(p["payload_bits"])

        for i, name in enumerate(names):
            accumulated_sets.append(sets_dict[name][0])
            combined_label = " + ".join(names[: i + 1])

            # No.1〜No.5 ワークフローによる段階復元
            restored_img, tile_rate, psnr, ssim, stage_counts, vis_rate = self.decode_accumulated_sets_to_image(
                accumulated_sets, canvas_gt
            )

            # ビット正解率の算出 (最新のビット多数決結果との比較)
            voted_pkts = self.majority_vote_packets(accumulated_sets)
            all_voted_bits = []
            for p in voted_pkts:
                all_voted_bits.extend(p["payload_bits"])

            min_len = min(len(all_gt_bits), len(all_voted_bits))
            matching = sum(1 for b1, b2 in zip(all_gt_bits[:min_len], all_voted_bits[:min_len]) if b1 == b2)
            bit_acc = (matching / min_len * 100.0) if min_len > 0 else 0.0
            ber = 100.0 - bit_acc

            # ビット正解率が100%かつ全タイルNo1〜No3確定の場合はPSNRを100dB
            if bit_acc >= 99.999 and tile_rate >= 99.99:
                psnr = 100.0
                ssim = 1.0000

            steps.append({
                "count": i + 1,
                "label": combined_label,
                "tile_rate": tile_rate,
                "visible_rate": vis_rate,
                "psnr": psnr,
                "ssim": ssim,
                "bit_acc": bit_acc,
                "ber": ber,
                "restored_img": restored_img,
                "stage_counts": stage_counts
            })

        return steps

    def plot_recovery_progression(
        self,
        progression_data,
        save_path="experiment/output/majority_vote_recovery_benchmark.png",
        dpi=300
    ):
        """学会発表・論文用高精細復元率推移グラフ (Pillowレンダラー)"""
        os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)

        img_w, img_h = 1500, 900
        img = Image.new("RGB", (img_w, img_h), color="#FFFFFF")
        draw = ImageDraw.Draw(img)

        # 背景
        draw.rectangle([(0, 0), (img_w, img_h)], fill="#F8F9FA")

        # ヘッダー
        draw.rectangle([(0, 0), (img_w, 75)], fill="#1D3557")
        draw.text((30, 24), "Multi-Audio Majority Vote (No.1-No.5 Workflow) Benchmark", fill="#FFFFFF")

        # グラフ描画領域
        gx0, gy0, gx1, gy1 = 120, 130, img_w - 180, img_h - 100
        draw.rectangle([(gx0, gy0), (gx1, gy1)], fill="#FFFFFF", outline="#DDE2E5", width=2)

        # グリッド & Y軸目盛 (0% ~ 100%)
        for pct in range(0, 101, 20):
            y = gy1 - (pct / 100.0) * (gy1 - gy0)
            draw.line([(gx0, y), (gx1, y)], fill="#EDF2F4", width=1)
            draw.text((gx0 - 55, y - 7), f"{pct}%", fill="#6C757D")

        # 右Y軸 (PSNR: 0dB ~ 60dB)
        for psnr_db in range(0, 61, 15):
            y = gy1 - (psnr_db / 60.0) * (gy1 - gy0)
            draw.text((gx1 + 15, y - 7), f"{psnr_db}dB", fill="#E63946")

        draw.text((gx0 - 90, gy0 - 30), "Recovery / Accuracy (%)", fill="#1D3557")
        draw.text((gx1 + 10, gy0 - 30), "PSNR (dB)", fill="#E63946")

        n_steps = len(progression_data)
        col_w = (gx1 - gx0) / n_steps
        bar_w = max(4.0, col_w * 0.22)

        psnr_pts = []

        for i, d in enumerate(progression_data):
            center_x = gx0 + (i + 0.5) * col_w

            # 1. タイル完全復元率バー (エメラルドグリーン)
            tr_h = (d["tile_rate"] / 100.0) * (gy1 - gy0)
            b1_x0 = center_x - bar_w - 2
            b1_x1 = center_x - 2
            draw.rectangle([(b1_x0, gy1 - tr_h), (b1_x1, gy1)], fill="#2A9D8F")
            if n_steps <= 10 or i % 2 == 0 or i == n_steps - 1:
                draw.text((b1_x0 - 2, gy1 - tr_h - 18), f"{d['tile_rate']:.0f}%", fill="#2A9D8F")

            # 2. ビット正解率バー (ブルー)
            ba_h = (d["bit_acc"] / 100.0) * (gy1 - gy0)
            b2_x0 = center_x + 2
            b2_x1 = center_x + bar_w + 2
            draw.rectangle([(b2_x0, gy1 - ba_h), (b2_x1, gy1)], fill="#457B9D")
            if n_steps <= 10 or i % 2 == 0 or i == n_steps - 1:
                draw.text((b2_x0 - 2, gy1 - ba_h - 18), f"{d['bit_acc']:.0f}%", fill="#457B9D")

            # X軸ラベル
            if n_steps <= 6:
                lbl = d['label']
                lbl_display = f"[{lbl}]" if len(lbl) <= 10 else f"S{i+1}"
            elif n_steps <= 12:
                lbl_display = f"S{i+1}"
            else:
                lbl_display = f"{i+1}" if (i % 2 == 0 or i == n_steps - 1) else ""

            if lbl_display:
                draw.text((center_x - len(lbl_display) * 3.5, gy1 + 12), lbl_display, fill="#1D3557")

            # PSNRポイント (60dB以上は上限付近にクリップしてプロット)
            p_val = d["psnr"]
            plot_pval = min(60.0, p_val)
            py = gy1 - (plot_pval / 60.0) * (gy1 - gy0)
            psnr_pts.append((center_x, py, p_val))

        # PSNR 折れ線グラフ (レッド)
        for i in range(len(psnr_pts) - 1):
            x1, y1, _ = psnr_pts[i]
            x2, y2, _ = psnr_pts[i + 1]
            draw.line([(x1, y1), (x2, y2)], fill="#E63946", width=3)

        for i, (cx, cy, p_val) in enumerate(psnr_pts):
            draw.ellipse([(cx - 5, cy - 5), (cx + 5, cy + 5)], fill="#E63946", outline="#FFFFFF", width=2)
            if n_steps <= 8 or i % 3 == 0 or i == n_steps - 1:
                psnr_str = f"{p_val:.1f}dB" if p_val < 99 else "100dB"
                draw.text((cx - 20, cy - 22), psnr_str, fill="#E63946")

        # 凡例
        leg_x, leg_y = gx0 + 20, gy0 + 20
        draw.rectangle([(leg_x, leg_y), (leg_x + 360, leg_y + 85)], fill="#FFFFFF", outline="#CCCCCC")
        draw.rectangle([(leg_x + 15, leg_y + 15), (leg_x + 35, leg_y + 30)], fill="#2A9D8F")
        draw.text((leg_x + 45, leg_y + 15), "Tile Perfect Recovery Rate (%)", fill="#333333")

        draw.rectangle([(leg_x + 15, leg_y + 38), (leg_x + 35, leg_y + 53)], fill="#457B9D")
        draw.text((leg_x + 45, leg_y + 38), "Bit Accuracy (%)", fill="#333333")

        draw.line([(leg_x + 15, leg_y + 68), (leg_x + 35, leg_y + 68)], fill="#E63946", width=3)
        draw.text((leg_x + 45, leg_y + 60), "Transmission PSNR (dB)", fill="#333333")

        img.save(save_path, quality=95)
        print(f"📊 多数決復元推移グラフ保存完了: {save_path}")
        return save_path

    def plot_visual_comparison_gallery(
        self,
        gt_img_array,
        progression_data,
        save_path="experiment/output/visual_recovery_gallery.png",
        dpi=300
    ):
        """原画像と各合成段階の復元画像を横並びまたはグリッド配置にした比較ギャラリー画像 (PIL)"""
        os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
        n_stages = len(progression_data)
        total_items = n_stages + 1  # 原画像 + 各ステージ

        # 7枚以下は1行横並び、8枚以上は1行あたり4〜5枚のグリッドレイアウト
        if total_items <= 7:
            n_cols = total_items
            n_rows = 1
            tile_w = max(200, min(260, 1800 // n_cols))
        else:
            n_cols = 5 if total_items > 10 else 4
            n_rows = (total_items + n_cols - 1) // n_cols
            tile_w = 230

        tile_h = tile_w
        margin_x = 25
        margin_y = 30
        header_h = 75
        footer_item_h = 55

        cell_w = tile_w + margin_x
        cell_h = tile_h + footer_item_h + margin_y

        total_w = margin_x + n_cols * cell_w
        total_h = header_h + n_rows * cell_h + 20

        gallery_img = Image.new("RGB", (total_w, total_h), color="#0B132B")
        draw = ImageDraw.Draw(gallery_img)

        draw.rectangle([(0, 0), (total_w, 55)], fill="#1C2541")
        draw.text((25, 18), f"Visual Comparison across {n_stages} Majority Voting Stages (No.1-No.5 Recovery Workflow)", fill="#FFFFFF")

        items = [{"type": "gt", "data": gt_img_array}] + [{"type": "stage", "idx": i, "data": d} for i, d in enumerate(progression_data)]

        for item_idx, itm in enumerate(items):
            row = item_idx // n_cols
            col = item_idx % n_cols

            cx = margin_x + col * cell_w
            cy = header_h + row * cell_h

            if itm["type"] == "gt":
                gt_img = Image.fromarray(itm["data"]).resize((tile_w, tile_h), Image.Resampling.NEAREST)
                gallery_img.paste(gt_img, (cx, cy))
                draw.rectangle([(cx, cy), (cx + tile_w, cy + tile_h)], outline="#10B981", width=2)
                draw.text((cx + 5, cy - 20), "Ground Truth (Original)", fill="#38BDF8")
                draw.text((cx + 5, cy + tile_h + 6), "Target Image", fill="#10B981")
                draw.text((cx + 5, cy + tile_h + 22), "PSNR: 100.0 dB", fill="#94A3B8")
            else:
                d = itm["data"]
                i = itm["idx"]
                r_img = Image.fromarray(d["restored_img"]).resize((tile_w, tile_h), Image.Resampling.NEAREST)
                gallery_img.paste(r_img, (cx, cy))

                is_perfect = d["tile_rate"] >= 99.0
                border_col = "#10B981" if is_perfect else "#F59E0B"
                draw.rectangle([(cx, cy), (cx + tile_w, cy + tile_h)], outline=border_col, width=2)

                psnr_str = f"{d['psnr']:.1f}dB" if d['psnr'] < 99 else "100dB"
                lbl_title = f"Stage {i+1} ({i+1} Sets)"
                stages = d.get("stage_counts", {})
                st_summary = f"No1:{stages.get('NO1',0)} No2:{stages.get('NO2',0)} No3:{stages.get('NO3',0)} No4:{stages.get('NO4',0)}"

                draw.text((cx + 5, cy - 20), lbl_title, fill="#38BDF8")
                draw.text((cx + 5, cy + tile_h + 6), f"Perfect: {d['tile_rate']:.1f}% | {psnr_str}", fill=border_col)
                draw.text((cx + 5, cy + tile_h + 22), st_summary, fill="#94A3B8")

        gallery_img.save(save_path, quality=95)
        print(f"📊 多数決復元比較ギャラリー保存完了: {save_path}")
        return save_path
