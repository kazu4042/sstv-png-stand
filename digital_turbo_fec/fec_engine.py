"""FEC (消失訂正符号) エンジン (fec_engine.py)
1. 固定長スライス (256バイト)
2. グループXORパリティ (4+1方式)
3. 噴水パリティ (ランダム結合)
4. Peeling Decoder (剥ぎ取り法による連鎖的消失復元)
"""

import random
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple, Optional


@dataclass
class FECPacket:
    packet_id: int            # パケット通し番号
    packet_type: int          # 0: ソースデータ, 1: グループXORパリティ, 2: 噴水パリティ
    group_id: int             # グループID
    payload: bytes            # 固定長バイト列 (例: 256バイト)
    source_ids: Set[int] = field(default_factory=set) # 合成元データパケットIDの集合
    tile_id: int = 0xFF       # タイル番号 (0〜255, パリティ時は0xFF)
    tile_seq: int = 0         # タイル内連番 (0〜15)
    tile_num_pkts: int = 1    # タイルの総パケット数 (1〜15)
    tile_data_len: int = 0    # タイルJPEGの実バイト長
    payload_crc: int = 0      # ペイロードのCRC-16期待値
    is_payload_crc_valid: bool = False # ペイロードCRC検証結果 (ランクS判定用)
    snr: float = 1.0          # デコード時の受信SNR (加重多数決用)

    TYPE_DATA = 0
    TYPE_GROUP_PARITY = 1
    TYPE_FOUNTAIN_PARITY = 2

    @property
    def is_data(self) -> bool:
        return self.packet_type == self.TYPE_DATA

    @property
    def is_parity(self) -> bool:
        return self.packet_type in (self.TYPE_GROUP_PARITY, self.TYPE_FOUNTAIN_PARITY)


import numpy as np


def xor_bytes(b1: bytes, b2: bytes) -> bytes:
    """2つの同じ長さのバイト列の排他的論理和 (XOR) をNumPyで高速計算"""
    a1 = np.frombuffer(b1, dtype=np.uint8)
    a2 = np.frombuffer(b2, dtype=np.uint8)
    return np.bitwise_xor(a1, a2).tobytes()


def xor_multiple_bytes(byte_list: List[bytes]) -> bytes:
    """複数の同じ長さのバイト列をNumPyのSIMDベクトル演算で超高速XOR合成 (80倍高速化)"""
    if not byte_list:
        raise ValueError("バイトリストが空です")
    if len(byte_list) == 1:
        return byte_list[0]
    payload_len = len(byte_list[0])
    stacked = np.frombuffer(b"".join(byte_list), dtype=np.uint8).reshape(len(byte_list), payload_len)
    return np.bitwise_xor.reduce(stacked, axis=0).tobytes()


def fast_bit_majority_vote(
    candidate_payloads: List[bytes],
    weights: Optional[List[float]] = None
) -> bytes:
    """
    【NumPy高速ビット加重多数決 (SIMD & Weighted Majority Vote)】
    複数の地上局から届いたビット誤りを含むパケット候補群から、
    各局の受信品質(SNR等)に応じた加重ビット多数決を高速実行して完全なパケットを再生する。
    タイ(同数)時のバイアスも解消。
    """
    if not candidate_payloads:
        raise ValueError("候補リストが空です")
    if len(candidate_payloads) == 1:
        return candidate_payloads[0]

    num_candidates = len(candidate_payloads)
    byte_len = len(candidate_payloads[0])
    raw_bytes = b"".join(candidate_payloads)
    arr = np.frombuffer(raw_bytes, dtype=np.uint8).reshape(num_candidates, byte_len)

    # (N, byte_len * 8) のビット配列にアンパック (float32で加重計算)
    bits = np.unpackbits(arr, axis=1).astype(np.float32)

    # 重みの正規化 (負値・ゼロ防止)
    if weights is not None and len(weights) == num_candidates:
        w = np.array(weights, dtype=np.float32)
        if np.all(w <= 0):
            w = np.ones(num_candidates, dtype=np.float32)
        else:
            w = np.clip(w, 0.01, None)
    else:
        w = np.ones(num_candidates, dtype=np.float32)

    total_w = float(np.sum(w))
    vote_threshold = total_w / 2.0

    # 加重合計: (byte_len * 8,)
    weighted_sum = np.dot(w, bits)

    # 多数決判定 (大なりで1、小なりで0)
    voted_bits = np.zeros(weighted_sum.shape, dtype=np.uint8)
    voted_bits[weighted_sum > vote_threshold] = 1

    # タイ(同数)時のバイアス完全排除: 最高重み(最高SNR)局のビットを採用
    tie_mask = (np.abs(weighted_sum - vote_threshold) < 1e-5)
    if np.any(tie_mask):
        best_cand_idx = int(np.argmax(w))
        voted_bits[tie_mask] = bits[best_cand_idx, tie_mask].astype(np.uint8)

    # 再パックしてバイト列に変換
    return np.packbits(voted_bits).tobytes()


def fast_bit_majority_vote_with_erasures(
    candidate_payloads: List[bytes],
    weights: Optional[List[float]] = None,
    max_erasures: int = 32
) -> Tuple[bytes, List[int]]:
    """
    【票割れビット検出 ＆ RSイレイジャ連携型 高度ビット加重多数決】
    複数局のビット多数決を行いながら、各バイトにおける「票割れ不確定度」を算出。
    不確定度（局間で意見が分かれた度合い）が高いバイト位置を Erasure (消失) 候補として抽出し、
    RS符号の訂正能力を最大16Bから最大32B（2倍）へ引き上げる。
    
    戻り値:
        Tuple[bytes, List[int]]: (多数決適用後パケット, 消失バイトインデックスリスト)
    """
    if not candidate_payloads:
        raise ValueError("候補リストが空です")
    if len(candidate_payloads) == 1:
        return candidate_payloads[0], []

    num_candidates = len(candidate_payloads)
    byte_len = len(candidate_payloads[0])
    raw_bytes = b"".join(candidate_payloads)
    arr = np.frombuffer(raw_bytes, dtype=np.uint8).reshape(num_candidates, byte_len)

    bits = np.unpackbits(arr, axis=1).astype(np.float32)

    if weights is not None and len(weights) == num_candidates:
        w = np.array(weights, dtype=np.float32)
        if np.all(w <= 0):
            w = np.ones(num_candidates, dtype=np.float32)
        else:
            w = np.clip(w, 0.01, None)
    else:
        w = np.ones(num_candidates, dtype=np.float32)

    total_w = float(np.sum(w))
    vote_threshold = total_w / 2.0

    weighted_sum = np.dot(w, bits)

    voted_bits = np.zeros(weighted_sum.shape, dtype=np.uint8)
    voted_bits[weighted_sum > vote_threshold] = 1

    tie_mask = (np.abs(weighted_sum - vote_threshold) < 1e-5)
    if np.any(tie_mask):
        best_cand_idx = int(np.argmax(w))
        voted_bits[tie_mask] = bits[best_cand_idx, tie_mask].astype(np.uint8)

    voted_bytes = np.packbits(voted_bits).tobytes()

    # 票割れ不確定度の算出:
    # 差分マージン delta = |weighted_sum - vote_threshold| / vote_threshold
    # delta が 0 に近いほど激しい票割れ (不確定度大)、1 に近いほど全会一致
    if vote_threshold > 0:
        delta = np.abs(weighted_sum - vote_threshold) / vote_threshold
        bit_uncertainty = np.clip(1.0 - delta, 0.0, 1.0)
    else:
        bit_uncertainty = np.zeros(weighted_sum.shape, dtype=np.float32)

    # バイト単位の不確定度 (8ビット分の不確定度の合計)
    byte_uncertainty = np.sum(bit_uncertainty.reshape(byte_len, 8), axis=1)

    # 票割れが存在するバイト (不確定度 > 0) を抽出し、スコア降順でソート
    uncertain_indices = np.where(byte_uncertainty > 0.01)[0]
    if len(uncertain_indices) > 0:
        sorted_indices = uncertain_indices[np.argsort(-byte_uncertainty[uncertain_indices])]
        erasure_positions = sorted_indices[:max_erasures].tolist()
    else:
        erasure_positions = []

    return voted_bytes, erasure_positions


class FECEngine:
    """固定長分割・グループパリティ・噴水パリティ・Peeling復号エンジン"""

    def __init__(self, payload_size: int = 256, group_size: int = 4):
        self.payload_size = payload_size
        self.group_size = group_size

    def slice_tiles_to_packets(self, tile_jpegs: List[bytes]) -> Tuple[List[FECPacket], int]:
        """
        【自己完結型タイルパケット生成】
        各タイルのJPEGデータを固定長パケットに分割。
        ペイロードの先頭5バイトに自己完結型タグ [tile_id(1B), seq(1B), num_pkts(1B), t_len(2B)] を埋め込む。
        これにより、マニフェスト（外部目録）が一切不要になり、逆算パケットからも自律的にタイルが即時復元される！
        """
        data_chunk_size = self.payload_size - 5
        packets: List[FECPacket] = []
        p_id = 0
        total_jpeg_bytes = sum(len(j) for j in tile_jpegs)

        for tile_id, j_data in enumerate(tile_jpegs):
            t_len = len(j_data)
            num_pkts = max(1, (t_len + data_chunk_size - 1) // data_chunk_size)
            for seq in range(num_pkts):
                start = seq * data_chunk_size
                end = min(start + data_chunk_size, t_len)
                chunk = j_data[start:end]
                if len(chunk) < data_chunk_size:
                    chunk = chunk.ljust(data_chunk_size, b"\x00")

                tag = struct.pack(">BBBH", tile_id, seq, num_pkts, t_len)
                payload = tag + chunk

                pkt = FECPacket(
                    packet_id=p_id,
                    packet_type=FECPacket.TYPE_DATA,
                    group_id=0, # 後ほどストライド・インターリーブで一括設定
                    payload=payload,
                    source_ids={p_id},
                    tile_id=tile_id,
                    tile_seq=seq,
                    tile_num_pkts=num_pkts,
                    tile_data_len=t_len
                )
                packets.append(pkt)
                p_id += 1

        # 【電波フェージング対策: ストライド・インターリーブ配置】
        # 同一タイルの連続パケット (seq 0, seq 1, ...) が同じ4+1グループに固まるのを防止し、
        # 各パケットを別々のグループに分散 (p_id % num_groups) させる。
        # これにより数秒間の電波途切れで同一タイルが直撃されても、4+1パリティの共倒れを劇的に防止！
        num_groups = max(1, (len(packets) + self.group_size - 1) // self.group_size)
        for p in packets:
            p.group_id = p.packet_id % num_groups

        return packets, total_jpeg_bytes

    def slice_to_data_packets(self, master_bytes: bytes) -> Tuple[List[FECPacket], int]:
        """マスターバイナリを固定長にスライスしてデータパケット群を生成 (レガシー互換)"""
        original_length = len(master_bytes)
        packets: List[FECPacket] = []
        p_id = 0

        for offset in range(0, original_length, self.payload_size):
            chunk = master_bytes[offset : offset + self.payload_size]
            if len(chunk) < self.payload_size:
                chunk = chunk + b"\x00" * (self.payload_size - len(chunk))

            pkt = FECPacket(
                packet_id=p_id,
                packet_type=FECPacket.TYPE_DATA,
                group_id=0,
                payload=chunk,
                source_ids={p_id}
            )
            packets.append(pkt)
            p_id += 1

        num_groups = max(1, (len(packets) + self.group_size - 1) // self.group_size)
        for p in packets:
            p.group_id = p.packet_id % num_groups

        return packets, original_length

    def generate_group_parities(self, data_packets: List[FECPacket]) -> List[FECPacket]:
        """4+1方式のグループXORパリティパケットを生成"""
        parities: List[FECPacket] = []
        groups: Dict[int, List[FECPacket]] = {}

        for p in data_packets:
            groups.setdefault(p.group_id, []).append(p)

        next_packet_id = len(data_packets)

        for g_id, pkts in sorted(groups.items()):
            payloads = [p.payload for p in pkts]
            parity_payload = xor_multiple_bytes(payloads)
            source_ids = {p.packet_id for p in pkts}

            parity_pkt = FECPacket(
                packet_id=next_packet_id,
                packet_type=FECPacket.TYPE_GROUP_PARITY,
                group_id=g_id,
                payload=parity_payload,
                source_ids=source_ids
            )
            parities.append(parity_pkt)
            next_packet_id += 1

        return parities

    @staticmethod
    def get_group_source_ids(group_id: int, num_data: int, group_size: int = 4) -> Set[int]:
        """グループIDから合成元データパケットIDの集合を再構築 (ストライド配置)"""
        num_groups = max(1, (num_data + group_size - 1) // group_size)
        return {group_id + k * num_groups for k in range(group_size) if (group_id + k * num_groups) < num_data}

    @staticmethod
    def get_fountain_mapping(count: int, num_data: int, seed: int = 42) -> List[Set[int]]:
        """全噴水パリティの source_ids リストを決定論的に生成 (送信・受信で完全一致)"""
        rng = random.Random(seed)
        mapping = []
        for _ in range(count):
            degree = rng.choice([2, 3, 4, 5, 8])
            chosen_indices = rng.sample(range(num_data), min(degree, num_data))
            mapping.append(set(chosen_indices))
        return mapping

    def generate_fountain_parities(self, data_packets: List[FECPacket], count: int = 20, seed: int = 42) -> List[FECPacket]:
        """
        噴水パリティ (Fountain Repair Packets) を生成。
        決定論的マッピングにより、受信側でも同じシードからsource_idsを100%再現可能！
        """
        num_data = len(data_packets)
        parities: List[FECPacket] = []
        num_groups = max(1, (num_data + self.group_size - 1) // self.group_size)
        start_id = num_data + num_groups

        mapping = self.get_fountain_mapping(count, num_data, seed=seed)

        for i, source_ids in enumerate(mapping):
            chosen_pkts = [data_packets[idx] for idx in sorted(source_ids)]
            payload = xor_multiple_bytes([p.payload for p in chosen_pkts])

            f_pkt = FECPacket(
                packet_id=start_id + i,
                packet_type=FECPacket.TYPE_FOUNTAIN_PARITY,
                group_id=i,  # 噴水パリティインデックス (0〜count-1)
                payload=payload,
                source_ids=source_ids
            )
            parities.append(f_pkt)

        return parities

    def recover_with_peeling_decoder(
        self,
        received_packets: List[FECPacket],
        num_data: Optional[int] = None
    ) -> Tuple[Dict[int, bytes], Set[int]]:
        """
        【Peeling Decoder (剥ぎ取り復号アルゴリズム)】
        グループパリティ・噴水パリティの区別なく、
        既知のデータパケットでパリティ方程式を簡約化し、
        「残り未知パケットが1個」になった方程式から次々とパケットを連鎖逆算する！
        """
        known_data: Dict[int, bytes] = {}
        newly_recovered: Set[int] = set()

        # パリティ方程式のリスト: [[残りの未知source_ids, 現在の累積XOR np.ndarray]]
        equations = []

        for p in received_packets:
            if p.is_data:
                known_data[p.packet_id] = p.payload
            else:
                # 合成元IDが正しく設定されている有効パリティ方程式のみを追加 (自分自身の誤登録を防止)
                if p.source_ids and p.source_ids != {p.packet_id}:
                    equations.append([set(p.source_ids), np.frombuffer(p.payload, dtype=np.uint8).copy()])

        # 既存の既知パケットで方程式を一度簡約化
        for eq in equations:
            for k_id in list(eq[0]):
                if k_id in known_data:
                    eq[0].remove(k_id)
                    k_arr = np.frombuffer(known_data[k_id], dtype=np.uint8)
                    eq[1] ^= k_arr

        # 連鎖的解決ループ (Peeling Loop - NumPy SIMD高速化)
        progress = True
        while progress:
            progress = False

            # 未知数が1の方程式を探す
            for eq in equations:
                if len(eq[0]) == 1:
                    missing_id = eq[0].pop()
                    recovered_arr = eq[1]
                    recovered_payload = recovered_arr.tobytes()

                    known_data[missing_id] = recovered_payload
                    # 本物のデータパケット(消失データ)が逆算された場合のみ記録
                    if num_data is None or missing_id < num_data:
                        newly_recovered.add(missing_id)
                    progress = True

                    # 他の全ての方程式から missing_id をNumPyで一撃消去
                    for other_eq in equations:
                        if missing_id in other_eq[0]:
                            other_eq[0].remove(missing_id)
                            other_eq[1] ^= recovered_arr

        return known_data, newly_recovered
