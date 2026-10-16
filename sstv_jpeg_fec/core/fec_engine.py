"""FEC (消失訂正符号) エンジン (fec_engine.py)
1. 固定長スライス (256バイト)
2. グループXORパリティ (4+1方式)
3. 噴水パリティ (ランダム結合)
4. Peeling Decoder (剥ぎ取り法による連鎖的消失復元)
"""

import random
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple, Optional


@dataclass
class FECPacket:
    packet_id: int            # パケット通し番号
    packet_type: int          # 0: ソースデータ, 1: グループXORパリティ, 2: 噴水パリティ
    group_id: int             # グループID
    payload: bytes            # 固定長バイト列 (例: 256バイト)
    source_ids: Set[int] = field(default_factory=set) # 合成元データパケットIDの集合

    TYPE_DATA = 0
    TYPE_GROUP_PARITY = 1
    TYPE_FOUNTAIN_PARITY = 2

    @property
    def is_data(self) -> bool:
        return self.packet_type == self.TYPE_DATA

    @property
    def is_parity(self) -> bool:
        return self.packet_type in (self.TYPE_GROUP_PARITY, self.TYPE_FOUNTAIN_PARITY)


def xor_bytes(b1: bytes, b2: bytes) -> bytes:
    """2つの同じ長さのバイト列の排他的論理和 (XOR) を計算"""
    return bytes(x ^ y for x, y in zip(b1, b2))


def xor_multiple_bytes(byte_list: List[bytes]) -> bytes:
    """複数の同じ長さのバイト列を順番に全てXOR合成"""
    if not byte_list:
        raise ValueError("バイトリストが空です")
    result = bytearray(byte_list[0])
    for b in byte_list[1:]:
        for i in range(len(result)):
            result[i] ^= b[i]
    return bytes(result)


class FECEngine:
    """固定長分割・グループパリティ・噴水パリティ・Peeling復号エンジン"""

    def __init__(self, payload_size: int = 256, group_size: int = 4):
        self.payload_size = payload_size
        self.group_size = group_size

    def slice_to_data_packets(self, master_bytes: bytes) -> Tuple[List[FECPacket], int]:
        """マスターバイナリを固定長にスライスしてデータパケット群を生成"""
        original_length = len(master_bytes)
        packets: List[FECPacket] = []
        p_id = 0

        for offset in range(0, original_length, self.payload_size):
            chunk = master_bytes[offset : offset + self.payload_size]
            if len(chunk) < self.payload_size:
                chunk = chunk + b"\x00" * (self.payload_size - len(chunk))

            group_id = p_id // self.group_size
            pkt = FECPacket(
                packet_id=p_id,
                packet_type=FECPacket.TYPE_DATA,
                group_id=group_id,
                payload=chunk,
                source_ids={p_id}
            )
            packets.append(pkt)
            p_id += 1

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

    def generate_fountain_parities(self, data_packets: List[FECPacket], count: int = 20, seed: int = 42) -> List[FECPacket]:
        """
        噴水パリティ (Fountain Repair Packets) を生成。
        ランダムに複数のデータパケットを選んでXOR合成する。
        グループを越えて全体を保護するため、グループ内で2個以上欠損しても救出可能！
        """
        rng = random.Random(seed)
        num_data = len(data_packets)
        parities: List[FECPacket] = []
        start_id = len(data_packets) + (num_data + self.group_size - 1) // self.group_size

        for i in range(count):
            # ソリトン分布に近い度数分布 (2〜8パケットをランダム結合)
            degree = rng.choice([2, 3, 4, 5, 8])
            chosen_indices = rng.sample(range(num_data), min(degree, num_data))
            chosen_pkts = [data_packets[idx] for idx in chosen_indices]

            payload = xor_multiple_bytes([p.payload for p in chosen_pkts])
            source_ids = {p.packet_id for p in chosen_pkts}

            f_pkt = FECPacket(
                packet_id=start_id + i,
                packet_type=FECPacket.TYPE_FOUNTAIN_PARITY,
                group_id=9999,  # グローバル噴水
                payload=payload,
                source_ids=source_ids
            )
            parities.append(f_pkt)

        return parities

    def recover_with_peeling_decoder(
        self,
        received_packets: List[FECPacket]
    ) -> Tuple[Dict[int, bytes], Set[int]]:
        """
        【Peeling Decoder (剥ぎ取り復号アルゴリズム)】
        グループパリティ・噴水パリティの区別なく、
        既知のデータパケットでパリティ方程式を簡約化し、
        「残り未知パケットが1個」になった方程式から次々とパケットを連鎖逆算する！
        """
        known_data: Dict[int, bytes] = {}
        newly_recovered: Set[int] = set()

        # パリティ方程式のリスト: [(残りの未知source_ids, 現在の累積XOR payload)]
        equations = []

        for p in received_packets:
            if p.is_data:
                known_data[p.packet_id] = p.payload
            else:
                equations.append([set(p.source_ids), bytearray(p.payload)])

        # 既存の既知パケットで方程式を一度簡約化
        for eq in equations:
            for k_id in list(eq[0]):
                if k_id in known_data:
                    eq[0].remove(k_id)
                    k_bytes = known_data[k_id]
                    for i in range(len(eq[1])):
                        eq[1][i] ^= k_bytes[i]

        # 連鎖的解決ループ (Peeling Loop)
        progress = True
        while progress:
            progress = False

            # 未知数が1の方程式を探す
            for eq in equations:
                if len(eq[0]) == 1:
                    missing_id = eq[0].pop()
                    recovered_payload = bytes(eq[1])

                    known_data[missing_id] = recovered_payload
                    newly_recovered.add(missing_id)
                    progress = True

                    # 他の全ての方程式から missing_id を消去
                    for other_eq in equations:
                        if missing_id in other_eq[0]:
                            other_eq[0].remove(missing_id)
                            for i in range(len(other_eq[1])):
                                other_eq[1][i] ^= recovered_payload[i]

        return known_data, newly_recovered
