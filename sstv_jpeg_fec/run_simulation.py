"""SSTV JPEG FEC 動作検証シミュレータ (run_simulation.py)
固定長化・ランダム送信・パケット消失・FECパリティ逆算・タイル即時描画を一発で検証する。
"""

import math
import os
import random
import sys
import numpy as np
from PIL import Image

for stream in (sys.stdout, sys.stderr):
    reconf = getattr(stream, 'reconfigure', None)
    if callable(reconf):
        try:
            reconf(encoding='utf-8', errors='replace')
        except Exception:
            pass

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from sstv_jpeg_fec.config import CFG, ensure_directories
from sstv_jpeg_fec.core.encoder import SSTVJPEGEncoder
from sstv_jpeg_fec.core.aggregator import SSTVJPEGFAggregator


def calculate_psnr(img1: np.ndarray, img2: np.ndarray) -> float:
    mse = np.mean((img1.astype(np.float64) - img2.astype(np.float64)) ** 2)
    if mse <= 1e-10:
        return 100.0
    return 20.0 * math.log10(255.0 / math.sqrt(mse))


def run_experiment(
    image_path: str = None,
    drop_rate: float = 0.15,  # 15% のパケットをランダムに消失させる
    shuffle: bool = True
):
    print("=" * 70)
    print("🚀 SSTV JPEG FEC (固定長パケット・消失訂正) シミュレーション開始")
    print("=" * 70)

    ensure_directories()
    if image_path is None:
        image_path = os.path.join(ROOT_DIR, "data", "input", "test.jpg")

    if not os.path.exists(image_path):
        print(f"⚠️ 指定画像が見つからないためテスト画像を作成します: {image_path}")
        os.makedirs(os.path.dirname(image_path), exist_ok=True)
        sample = Image.new("RGB", (256, 256), color="#1E3A8A")
        sample.save(image_path, format="JPEG", quality=90)

    # 1. エンコード
    print("\n[Step 1] 画像のJPEGタイル化 & マスターバイナリ構築 & 4+1 FECパケット生成...")
    encoder = SSTVJPEGEncoder(CFG)
    enc_result = encoder.encode_to_wav(image_path, shuffle=shuffle)

    orig_len = enc_result["master_length"]
    data_pkts_cnt = enc_result["data_packets"]
    parity_pkts_cnt = enc_result["parity_packets"]
    total_pkts = enc_result["total_packets"]
    fec_packets = enc_result["shuffled_fec_packets"]

    print(f"  -> マスターバイナリ長: {orig_len:,} Bytes")
    print(f"  -> データパケット: {data_pkts_cnt} 個 (各 {CFG.payload_size} Bytes)")
    print(f"  -> 冗長パケット (4+1 XOR): {parity_pkts_cnt} 個")
    print(f"  -> 総送信パケット数: {total_pkts} 個 (冗長率: {parity_pkts_cnt/total_pkts*100:.1f}%)")

    # 2. 伝送路シミュレーション (ランダム消失)
    print(f"\n[Step 2] 伝送路ノイズ・パケット消失シミュレーション (消失率: {drop_rate*100:.1f}%)...")
    random.seed(123)
    survived_packets = []
    dropped_count = 0

    for pkt in fec_packets:
        if random.random() < drop_rate:
            dropped_count += 1
            # ドロップ！
        else:
            survived_packets.append(pkt)

    print(f"  -> 送信 {total_pkts} パケット中、{dropped_count} パケットが電波ノイズで完全消失！")
    print(f"  -> 受信できたパケット: {len(survived_packets)} パケット")

    # 3. アグリゲータによる逐次受信 & 即時タイル描画 & FEC逆算
    print(f"\n[Step 3] 受信局での逐次処理 (即時タイルデコード ＆ 消失逆算)...")
    aggregator = SSTVJPEGFAggregator(CFG, expected_master_length=orig_len)

    step_interval = max(1, len(survived_packets) // 8)
    for idx, pkt in enumerate(survived_packets):
        newly_rendered = aggregator.feed_packet(pkt)

        if (idx + 1) % step_interval == 0 or idx == len(survived_packets) - 1:
            tile_rate = aggregator.tile_recovery_rate
            rec_fec_cnt = len(aggregator.recovered_fec_ids)
            print(f"  [受信 {idx+1:3d}/{len(survived_packets)}] タイル描画進捗: {len(aggregator.rendered_tiles):3d}/{CFG.total_tiles} ({tile_rate:5.1f}%) | パリティ逆算救出: {rec_fec_cnt} 個")

    # 4. 評価と保存
    print("\n" + "=" * 70)
    print("📊 最終検証結果サマリー")
    print("=" * 70)

    gt_img = Image.open(image_path).convert("RGB").resize((CFG.width, CFG.height))
    gt_arr = np.array(gt_img)

    psnr = calculate_psnr(gt_arr, aggregator.canvas)
    print(f"  ■ タイル完全復元率: {aggregator.tile_recovery_rate:.2f}% ({len(aggregator.rendered_tiles)} / {CFG.total_tiles})")
    print(f"  ■ FECパリティによる逆算救出数: {len(aggregator.recovered_fec_ids)} パケット")
    print(f"  ■ 最終画質 PSNR: {psnr:.2f} dB")

    out_png = os.path.join(CFG.output_dir, "simulation_recovered.png")
    Image.fromarray(aggregator.canvas).save(out_png)
    print(f"  ■ 復元画像保存完了: {out_png}")

    if aggregator.is_fully_recovered:
        print("  🎉【大成功】パケットが虫食い状態で消失したにもかかわらず、100%完全復元を達成しました！")
    else:
        print("  💡 パリティによる救出が一部行われ、復元可能なタイルが即時描画されました。")

    print("=" * 70)
    return aggregator


def run_multi_station_experiment(
    image_path: str = None,
    num_stations: int = 2,
    station_drop_rate: float = 0.20,  # 各局とも20%のパケットを消失
    shuffle: bool = True
):
    print("=" * 70)
    print(f"🌍 SSTV JPEG FEC 分散受信 (地上局 {num_stations} 局協調) 実証実験")
    print(f"   各地上局のパケット消失率: {station_drop_rate*100:.1f}%")
    print("=" * 70)

    ensure_directories()
    if image_path is None:
        image_path = os.path.join(ROOT_DIR, "data", "input", "test.jpg")

    # 1. エンコード
    encoder = SSTVJPEGEncoder(CFG)
    enc_result = encoder.encode_to_wav(image_path, shuffle=shuffle)
    fec_packets = enc_result["shuffled_fec_packets"]
    orig_len = enc_result["master_length"]
    total_pkts = len(fec_packets)

    print(f"  -> 送信総パケット数: {total_pkts} パケット (データ: {enc_result['data_packets']}, パリティ: {enc_result['parity_packets']})")

    # 2. 各地上局の受信シミュレーション (独立した電波環境)
    stations_data = []
    for s_id in range(num_stations):
        rng = random.Random(42 + s_id * 100)
        received = [p for p in fec_packets if rng.random() >= station_drop_rate]
        stations_data.append(received)
        print(f"  -> 地上局 {chr(65+s_id)}: {len(received)} / {total_pkts} パケット受信 ({len(received)/total_pkts*100:.1f}%)")

    # 3. クラウド集約アグリゲータによる統合 & FEC逆算
    print("\n[Step 3] クラウド集約サーバーでの協調復元処理...")
    aggregator = SSTVJPEGFAggregator(CFG, expected_master_length=orig_len)

    # 各局のパケットを順次投入
    total_fed = 0
    for s_id, pkts in enumerate(stations_data):
        st_name = chr(65 + s_id)
        for p in pkts:
            aggregator.feed_packet(p)
            total_fed += 1
        print(f"  [地上局 {st_name} データ集約完了] タイル復元率: {aggregator.tile_recovery_rate:5.1f}% | パリティ救出: {len(aggregator.recovered_fec_ids)} パケット")

    # 4. 結果判定
    gt_img = Image.open(image_path).convert("RGB").resize((CFG.width, CFG.height))
    gt_arr = np.array(gt_img)
    psnr = calculate_psnr(gt_arr, aggregator.canvas)

    print("\n" + "=" * 70)
    print("📊 分散協調受信の最終検証結果")
    print("=" * 70)
    print(f"  ■ タイル完全復元率: {aggregator.tile_recovery_rate:.2f}% ({len(aggregator.rendered_tiles)} / {CFG.total_tiles})")
    print(f"  ■ FECパリティによる逆算救出数: {len(aggregator.recovered_fec_ids)} パケット")
    print(f"  ■ 最終画質 PSNR: {psnr:.2f} dB")

    out_png = os.path.join(CFG.output_dir, "multi_station_recovered.png")
    Image.fromarray(aggregator.canvas).save(out_png)
    print(f"  ■ 復元画像保存完了: {out_png}")

    if aggregator.is_fully_recovered:
        print("  🎉🎉🎉【完全復元達成】世界中の地上局が分散受信した断片から、1ビットの狂いもなく100%完全復元を達成しました！")
    print("=" * 70)
    return aggregator


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="SSTV JPEG FEC Simulation")
    parser.add_argument("--mode", type=str, default="multi", choices=["single", "multi"], help="single or multi station")
    parser.add_argument("--stations", type=int, default=2, help="Number of ground stations")
    parser.add_argument("--drop_rate", type=float, default=0.20, help="Packet drop rate (0.0 - 1.0)")
    parser.add_argument("--image", type=str, default=None, help="Input image path")
    args = parser.parse_args()

    if args.mode == "multi":
        run_multi_station_experiment(image_path=args.image, num_stations=args.stations, station_drop_rate=args.drop_rate)
    else:
        run_experiment(image_path=args.image, drop_rate=args.drop_rate)
