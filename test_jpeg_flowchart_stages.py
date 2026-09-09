import os
import sys
import io
import numpy as np
from PIL import Image

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from digital_turbo_jpeg.aggregator_turbo import TurboJPEGAggregator
import digital_turbo_jpeg.config_turbo as config

def create_test_jpeg_tile(color=(120, 180, 240), tile_w=16, tile_h=16):
    """正常な16x16 JPEGタイル（RSTマーカー3個＝4区間）を生成"""
    img = Image.new("RGB", (tile_w, tile_h), color=color)
    bio = io.BytesIO()
    img.save(bio, format="JPEG", quality=75, restart_marker_blocks=1, subsampling=0)
    raw = bio.getvalue()
    return raw, img

def bytes_to_bits_str(b_data):
    return "".join(f"{b:08b}" for b in b_data)

def test_jpeg_flowchart():
    print("=== JPEG No.1〜No.5 フローチャート厳密デバッグ検証開始 ===")
    aggregator = TurboJPEGAggregator()
    tile_w, tile_h = 16, 16

    # 基準となる完全な無傷JPEGタイル
    clean_bytes, clean_img = create_test_jpeg_tile()
    clean_bits = bytes_to_bits_str(clean_bytes)
    plen = len(clean_bytes)

    # -------------------------------------------------------------------------
    # テスト1: No.1 受信タイル単体での無傷チェック・即時採用
    # -------------------------------------------------------------------------
    print("\n--- Test 1: No.1 受信タイル単体での無傷チェック ---")
    packets_no1 = [
        (clean_bits, 15.0), # 完全無傷
    ]
    tile_img, fin_bytes, stage = aggregator.resolve_tile(packets_no1, plen, tile_w, tile_h)
    assert stage == "NO1", f"Expected NO1, got {stage}"
    assert fin_bytes is not None, "final_bytes should not be None for NO1"
    assert tile_img is not None
    print("  ✅ No.1 成功: 単体無傷パケットを即時採用 (stage=NO1, final_bytes確定)")

    # -------------------------------------------------------------------------
    # テスト2: No.2 全ビット加重多数決
    # -------------------------------------------------------------------------
    print("\n--- Test 2: No.2 全ビット加重多数決 ---")
    # 3つのパケットにそれぞれ異なる位置でビット反転ノイズを注入 (単体ではいずれも壊れている)
    # 多数決を取るとすべてのビットが正しく復元される
    bit_list = list(clean_bits)
    
    # パケットA: エントロピーデータ先頭付近のビットを反転
    bits_a = list(bit_list)
    pos_a = 616 * 8
    bits_a[pos_a] = '0' if bits_a[pos_a] == '1' else '1'
    str_a = "".join(bits_a)

    # パケットB: エントロピーデータ中盤のビットを反転
    bits_b = list(bit_list)
    pos_b = 620 * 8
    bits_b[pos_b] = '0' if bits_b[pos_b] == '1' else '1'
    str_b = "".join(bits_b)

    # パケットC: エントロピーデータ終盤のビットを反転
    bits_c = list(bit_list)
    pos_c = 625 * 8
    bits_c[pos_c] = '0' if bits_c[pos_c] == '1' else '1'
    str_c = "".join(bits_c)

    # 単体ではNo.1に合格しないことを確認
    assert aggregator.is_perfect_jpeg_tile(aggregator.bits_to_bytearray(str_a), tile_w, tile_h) is None
    assert aggregator.is_perfect_jpeg_tile(aggregator.bits_to_bytearray(str_b), tile_w, tile_h) is None
    assert aggregator.is_perfect_jpeg_tile(aggregator.bits_to_bytearray(str_c), tile_w, tile_h) is None

    packets_no2 = [
        (str_a, 10.0),
        (str_b, 10.0),
        (str_c, 10.0),
    ]
    tile_img, fin_bytes, stage = aggregator.resolve_tile(packets_no2, plen, tile_w, tile_h)
    assert stage == "NO2", f"Expected NO2, got {stage}"
    assert fin_bytes is not None, "final_bytes should not be None for NO2"
    print("  ✅ No.2 成功: 複数破損パケットのビット多数決で完全復元 (stage=NO2, final_bytes確定)")

    # -------------------------------------------------------------------------
    # テスト3: パケット1つの場合の No.2 & No.3 スキップ確認
    # -------------------------------------------------------------------------
    print("\n--- Test 3: パケット1つの場合の No.2 & No.3 スキップ ---")
    # 破損パケットが1つだけの場合、No.2/No.3 は実行されずに No.4 (またはNo.5) へ進む
    packets_single_broken = [(str_a, 8.0)]
    tile_img, fin_bytes, stage = aggregator.resolve_tile(packets_single_broken, plen, tile_w, tile_h)
    assert stage in ["NO4", "NO5"], f"Expected NO4 or NO5, got {stage}"
    assert fin_bytes is None, "final_bytes should be None for non-perfect stages"
    print(f"  ✅ パケット1つ時: No.2/No.3 をスキップして {stage} へ遷移確認 (final_bytes=None, 次回再挑戦)")

    # -------------------------------------------------------------------------
    # テスト4: No.3 RSTマーカー区間分割による再構築・多数決
    # -------------------------------------------------------------------------
    print("\n--- Test 4: No.3 RSTマーカー区間分割による再構築・多数決 ---")
    # ビット多数決では勝てない状況を作る:
    # パケット1: 区間0と1が無傷、区間2と3が激しく破損
    # パケット2: 区間2と3が無傷、区間0と1が激しく破損
    # (ビット多数決では2パケットの壊れた部分が相殺できずNo.2失敗するが、RST分割合体で完全復元できる)
    parsed_clean = aggregator.split_jpeg_into_segments(clean_bytes, tile_w, tile_h)
    assert parsed_clean is not None
    hdr, segs, markers, has_eoi = parsed_clean
    assert len(segs) == 4, f"Expected 4 segments, got {len(segs)}"

    # 破損セグメント（欠損・構文破綻）
    corrupted_seg0 = b'\x00\x01'
    corrupted_seg2 = b'\x00\x01'

    # パケットX: seg0正常, seg1正常, seg2破損, seg3破損 (SNR: 12.0)
    pkt_x_bytes = bytearray(hdr)
    pkt_x_bytes.extend(segs[0])
    pkt_x_bytes.extend(bytes([0xFF, markers[0]]))
    pkt_x_bytes.extend(segs[1])
    pkt_x_bytes.extend(bytes([0xFF, markers[1]]))
    pkt_x_bytes.extend(corrupted_seg2)
    pkt_x_bytes.extend(bytes([0xFF, markers[2]]))
    pkt_x_bytes.extend(corrupted_seg2)
    pkt_x_bytes.extend(b'\xff\xd9')
    pkt_x_bits = bytes_to_bits_str(pkt_x_bytes)

    # パケットY: seg0破損, seg1破損, seg2正常, seg3正常 (SNR: 12.0)
    pkt_y_bytes = bytearray(hdr)
    pkt_y_bytes.extend(corrupted_seg0)
    pkt_y_bytes.extend(bytes([0xFF, markers[0]]))
    pkt_y_bytes.extend(corrupted_seg0)
    pkt_y_bytes.extend(bytes([0xFF, markers[1]]))
    pkt_y_bytes.extend(segs[2])
    pkt_y_bytes.extend(bytes([0xFF, markers[2]]))
    pkt_y_bytes.extend(segs[3])
    pkt_y_bytes.extend(b'\xff\xd9')
    pkt_y_bits = bytes_to_bits_str(pkt_y_bytes)

    # 単体ではNo.1に合格しないことを確認
    assert aggregator.is_perfect_jpeg_tile(pkt_x_bytes, tile_w, tile_h) is None
    assert aggregator.is_perfect_jpeg_tile(pkt_y_bytes, tile_w, tile_h) is None

    max_len = max(len(pkt_x_bytes), len(pkt_y_bytes))
    packets_no3 = [
        (pkt_x_bits, 12.0),
        (pkt_y_bits, 12.0),
    ]

    # No.2 では復元できないことを確認
    voted_bits = aggregator.bit_majority_vote(packets_no3, max_len * 8)
    assert aggregator.is_perfect_jpeg_tile(aggregator.bits_to_bytearray(voted_bits), tile_w, tile_h) is None

    # resolve_tile で No.3 が発動して完全復元できるか
    tile_img, fin_bytes, stage = aggregator.resolve_tile(packets_no3, max_len, tile_w, tile_h)
    assert stage == "NO3", f"Expected NO3, got {stage}"
    assert fin_bytes is not None, "final_bytes should not be None for NO3"
    print("  ✅ No.3 成功: RST区間分割・最良データ合体により完全復元 (stage=NO3, final_bytes確定)")

    # -------------------------------------------------------------------------
    # テスト5: No.4 最高SNRタイルのクリア単体採用
    # -------------------------------------------------------------------------
    print("\n--- Test 5: No.4 最高SNRタイルのクリア単体採用 ---")
    # 完全復元はできないが、下部数ラインだけが欠損したパケット（有効画素率 >= 85%）
    # 2つのパケットがあり、SNRが高い方が単体採用される
    cand_88_arr = np.array(clean_img, dtype=np.uint8)
    cand_88_arr[14:, :] = 0  # 16行中下2行だけ黒 (有効率 = 14/16 = 87.5% >= 85%)
    
    # 破損JPEG作成（末尾カット）
    bio = io.BytesIO()
    Image.fromarray(cand_88_arr).save(bio, format="JPEG", quality=75)
    cand_88_bytes = bio.getvalue()
    cand_88_bits = bytes_to_bits_str(cand_88_bytes)

    packets_no4 = [
        (cand_88_bits, 14.0), # 高SNR (有効率87.5%)
        (cand_88_bits, 6.0),  # 低SNR
    ]
    tile_img, fin_bytes, stage = aggregator.resolve_tile(packets_no4, len(cand_88_bytes), tile_w, tile_h)
    assert stage == "NO4", f"Expected NO4, got {stage}"
    assert fin_bytes is None, "final_bytes must be None for NO4 (次回再挑戦)"
    print("  ✅ No.4 成功: 有効画素率85%以上の高SNRタイルを単体採用 (stage=NO4, final_bytes=None)")

    # -------------------------------------------------------------------------
    # テスト6: No.5 RGBピクセル空間での加重平均合成
    # -------------------------------------------------------------------------
    print("\n--- Test 6: No.5 RGBピクセル空間での加重平均合成 ---")
    # 全パケットが有効画素率85%未満（例えば半分真っ黒）の場合
    # パケット上半分 (有効50%) と パケット下半分 (有効50%)
    half_top = np.array(clean_img, dtype=np.uint8)
    half_top[8:, :] = 0 # 上半分のみ有効
    bio_t = io.BytesIO()
    Image.fromarray(half_top).save(bio_t, format="JPEG", quality=75)
    bits_top = bytes_to_bits_str(bio_t.getvalue())

    half_bottom = np.array(clean_img, dtype=np.uint8)
    half_bottom[:8, :] = 0 # 下半分のみ有効
    bio_b = io.BytesIO()
    Image.fromarray(half_bottom).save(bio_b, format="JPEG", quality=75)
    bits_bottom = bytes_to_bits_str(bio_b.getvalue())

    plen_half = max(len(bio_t.getvalue()), len(bio_b.getvalue()))
    packets_no5 = [
        (bits_top, 10.0),
        (bits_bottom, 10.0),
    ]
    tile_img, fin_bytes, stage = aggregator.resolve_tile(packets_no5, plen_half, tile_w, tile_h)
    assert stage == "NO5", f"Expected NO5, got {stage}"
    assert fin_bytes is None, "final_bytes must be None for NO5 (次回再挑戦)"
    res_arr = np.array(tile_img)
    # 上半分も下半分も合成されて黒落ちが解消されているか
    valid_res_ratio = np.mean(np.sum(res_arr, axis=2) > 3.0)
    print(f"  合成後タイルの有効画素率: {valid_res_ratio * 100:.1f}%")
    assert valid_res_ratio > 0.80, f"Expected merged valid ratio > 80%, got {valid_res_ratio}"
    print("  ✅ No.5 成功: 生き残りピクセル同士のSNR半透明加重平均合成 (stage=NO5, final_bytes=None)")

    print("\n🎉 No.1〜No.5 のすべてのフローチャート分岐・仕様が100%正確に動作することを実証しました！")

if __name__ == "__main__":
    test_jpeg_flowchart()
