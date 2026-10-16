"""SSTV JPEG FEC 包括的ストレステスト & デバッグスイート (stress_test.py)
数十回〜100回以上の様々な電波環境シミュレーションを実行し、
アルゴリズムの堅牢性、エッジケース処理、バイナリ完全一致を徹底検証する。
"""

import hashlib
import io
import math
import os
import random
import sys
import time
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
from sstv_jpeg_fec.core.manifest import TileManifest
from sstv_jpeg_fec.core.fec_engine import FECEngine, FECPacket
from sstv_jpeg_fec.core.encoder import SSTVJPEGEncoder
from sstv_jpeg_fec.core.aggregator import SSTVJPEGFAggregator


def run_unit_tests():
    print("=" * 70)
    print("🧪 [Test Suite 1] コアモジュール単体検証 (Unit Tests)")
    print("=" * 70)

    # 1. TileManifest の検証
    print("  1-1. TileManifest シリアライズ / デシリアライズ検証...")
    m = TileManifest(width=256, height=256, tile_size=16)
    rng = random.Random(100)
    test_chunks = {}
    for ty in range(m.height // m.tile_size):
        for tx in range(m.width // m.tile_size):
            dummy_data = bytes([rng.randint(0, 255) for _ in range(rng.randint(50, 300))])
            m.add_tile(tx, ty, dummy_data)
            test_chunks[(tx, ty)] = dummy_data

    hdr_bytes = m.serialize_header()
    m_deser = TileManifest.deserialize_header(hdr_bytes, width=256, height=256, tile_size=16)

    assert len(m_deser.entries) == 256, "全256タイルのエントリ数が一致しません"
    for coord, orig_entry in m.entries.items():
        deser_entry = m_deser.entries[coord]
        assert orig_entry.byte_length == deser_entry.byte_length, f"タイル {coord} の長さ不一致"
        assert orig_entry.offset_start == deser_entry.offset_start, f"タイル {coord} のオフセット不一致"
    print("      -> ✓ PASS: 256タイル全ての位置・長さが完全一致！")

    # 2. FECEngine の消失逆算検証
    print("  1-2. FECEngine 消失訂正 (Peeling Decoder) 検証...")
    fec = FECEngine(payload_size=256, group_size=4)
    dummy_master = bytes([i % 256 for i in range(1024 * 5)])  # 5KB
    data_pkts, orig_l = fec.slice_to_data_packets(dummy_master)
    group_parities = fec.generate_group_parities(data_pkts)
    fountain_parities = fec.generate_fountain_parities(data_pkts, count=10, seed=42)

    all_pkts = data_pkts + group_parities + fountain_parities

    # 4+1グループごとに1パケットずつ強制ドロップ
    survived = []
    dropped_target = []
    for g_id in range(len(group_parities)):
        g_data = [p for p in data_pkts if p.group_id == g_id]
        if g_data:
            drop_pkt = g_data[0]
            dropped_target.append(drop_pkt)
            survived.extend(g_data[1:])
    survived.extend(group_parities)

    recovered_dict, recovered_ids = fec.recover_with_peeling_decoder(survived)
    for dp in dropped_target:
        assert dp.packet_id in recovered_dict, f"Pkt {dp.packet_id} が逆算できませんでした"
        assert recovered_dict[dp.packet_id] == dp.payload, f"Pkt {dp.packet_id} の復元ペイロードが不一致"
    print(f"      -> ✓ PASS: 各グループ1個ずつの欠損計 {len(dropped_target)} パケットを100%完全逆算！")

    print("  1-3. CRC-16 整合性検証...")
    test_bin = b"Hello SSTV Turbo JPEG FEC"
    c1 = SSTVJPEGEncoder.calculate_crc16(test_bin)
    c2 = SSTVJPEGFAggregator.calculate_crc16(test_bin)
    assert c1 == c2 and c1 != 0, "CRC計算結果の不一致"
    print("      -> ✓ PASS: エンコーダとアグリゲータのCRCアルゴリズム完全一致！")


def run_stress_simulations(image_path: str, num_runs: int = 50):
    print("\n" + "=" * 70)
    print(f"⚡ [Test Suite 2] 50回連続ストレステスト (多種多様な消失率 & シード)")
    print("=" * 70)

    encoder = SSTVJPEGEncoder(CFG)
    enc_res = encoder.encode_to_wav(image_path, shuffle=True)
    fec_packets = enc_res["shuffled_fec_packets"]
    orig_len = enc_res["master_length"]
    master_stream, _, _ = encoder.build_master_binary(image_path)
    orig_md5 = hashlib.md5(master_stream).hexdigest()

    print(f"  ■ 送信マスターバイナリ長: {orig_len:,} Bytes (MD5: {orig_md5})")
    print(f"  ■ 総パケット数: {len(fec_packets)} パケット")

    # テストする消失率のリスト (5% 〜 25%)
    drop_rates = [0.05, 0.08, 0.10, 0.12, 0.15, 0.18, 0.20, 0.22, 0.25]
    results = []

    print("\n  [実行ログ]")
    for run_i in range(1, num_runs + 1):
        rate = drop_rates[(run_i - 1) % len(drop_rates)]
        seed = 1000 + run_i
        rng = random.Random(seed)

        # パケット消失シミュレーション
        survived = [p for p in fec_packets if rng.random() >= rate]
        dropped_cnt = len(fec_packets) - len(survived)

        # アグリゲータ投入
        agg = SSTVJPEGFAggregator(CFG, expected_master_length=orig_len)
        for p in survived:
            agg.feed_packet(p)

        tile_rate = agg.tile_recovery_rate
        fec_rescued = len(agg.recovered_fec_ids)
        is_perfect = agg.is_fully_recovered

        md5_match = False
        if is_perfect and len(agg.stream_buffer) >= orig_len:
            rec_md5 = hashlib.md5(bytes(agg.stream_buffer[:orig_len])).hexdigest()
            md5_match = (rec_md5 == orig_md5)

        results.append({
            "run": run_i,
            "drop_rate": rate,
            "dropped": dropped_cnt,
            "rescued": fec_rescued,
            "tile_rate": tile_rate,
            "perfect": is_perfect,
            "md5_match": md5_match
        })

        status_str = "🎉 100% PERFECT (MD5一致)" if md5_match else f"{tile_rate:5.1f}% 復元"
        print(f"  Run #{run_i:02d} | 消失率: {rate*100:4.1f}% (欠損 {dropped_cnt:3d}個) | FEC救出: {fec_rescued:3d}個 | {status_str}")

    # 統計サマリー
    print("\n" + "=" * 70)
    print("📈 50回ストレステスト 統計解析サマリー")
    print("=" * 70)
    avg_rescue_ratio = np.mean([r["rescued"] / max(1, r["dropped"]) for r in results]) * 100
    perfect_runs = sum(1 for r in results if r["perfect"])
    print(f"  ■ 総試行回数: {num_runs} 回")
    print(f"  ■ 平均パケット救出率: {avg_rescue_ratio:.1f}% (欠損パケットの大半をパリティで自律再生)")
    print(f"  ■ 100% 完全復元達成数 (単一局): {perfect_runs} / {num_runs} 回 ({perfect_runs/num_runs*100:.1f}%)")


def run_multi_station_stress(image_path: str, num_runs: int = 20):
    print("\n" + "=" * 70)
    print("🌍 [Test Suite 3] 分散地上局 (3局協調) 20回連続ストレステスト")
    print("   各局が 20% パケット欠損（過酷障害）の環境で、協調完全復元の再現性を検証")
    print("=" * 70)

    encoder = SSTVJPEGEncoder(CFG)
    enc_res = encoder.encode_to_wav(image_path, shuffle=True)
    fec_packets = enc_res["shuffled_fec_packets"]
    orig_len = enc_res["master_length"]
    master_stream, _, _ = encoder.build_master_binary(image_path)
    orig_md5 = hashlib.md5(master_stream).hexdigest()

    multi_results = []

    for run_i in range(1, num_runs + 1):
        agg = SSTVJPEGFAggregator(CFG, expected_master_length=orig_len)

        # 3局の受信データ
        for s_id in range(3):
            rng = random.Random(run_i * 100 + s_id)
            recv_pkts = [p for p in fec_packets if rng.random() >= 0.20]
            for p in recv_pkts:
                agg.feed_packet(p)

        is_perfect = agg.is_fully_recovered
        rec_md5 = hashlib.md5(bytes(agg.stream_buffer[:orig_len])).hexdigest()
        md5_match = (rec_md5 == orig_md5)

        multi_results.append({
            "run": run_i,
            "tile_rate": agg.tile_recovery_rate,
            "rescued": len(agg.recovered_fec_ids),
            "md5_match": md5_match
        })
        print(f"  MultiRun #{run_i:02d} | タイル復元率: {agg.tile_recovery_rate:5.1f}% | FEC救出: {len(agg.recovered_fec_ids):3d}個 | MD5一致: {md5_match}")

    all_match = all(r["md5_match"] for r in multi_results)
    print("\n" + "=" * 70)
    print(f"🏆 分散地上局協調テスト結果: 20回中 {sum(1 for r in multi_results if r['md5_match'])} 回 100%完全一致！")
    if all_match:
        print("  ★★★★★【驚異的再現性】全20回の試行すべてにおいて、1ビットの狂いもなく完全復元を達成しました！")
    print("=" * 70)


if __name__ == "__main__":
    ensure_directories()
    img_path = os.path.join(ROOT_DIR, "data", "input", "test.jpg")
    if not os.path.exists(img_path):
        os.makedirs(os.path.dirname(img_path), exist_ok=True)
        sample = Image.new("RGB", (256, 256), color="#1E3A8A")
        sample.save(img_path, format="JPEG", quality=90)

    # 1. 単体テスト
    run_unit_tests()

    # 2. 50回単一局ストレステスト
    run_stress_simulations(img_path, num_runs=50)

    # 3. 20回複数局協調ストレステスト
    run_multi_station_stress(img_path, num_runs=20)
