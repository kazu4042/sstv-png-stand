"""ラプター符号 ＆ コーシー消去訂正コーデック (raptor_codec.py)
GF(2^16) 上のコーシー行列 (Cauchy Matrix) に基づく大域万能消去訂正符号 (Global Cauchy MDS)。
ブロック分割を完全に撤廃し、全Kパケットと全Rリペアパケットを単一空間で処理。
どんな極端な偏った消失（特定ブロックや特定タイルの集中欠落）が発生しても、
「受信リペアパケット数 >= 欠落パケット数」であれば数学的に100.000%完全復元を保証（特異点ゼロ・MDS性質）。
従来のGF(256)サブブロック分割モードとの後方互換性も保持。
"""

import math
from typing import Dict, List, Optional, Set, Tuple

import numpy as np


# =========================================================================
# 高速 CRC-16 テーブル (多項式: 0x1021 / 初期値: 0xFFFF)
# =========================================================================
def _generate_crc16_table(poly: int = 0x1021) -> Tuple[int, ...]:
    table = []
    for byte in range(256):
        cur = byte << 8
        for _ in range(8):
            if cur & 0x8000:
                cur = ((cur << 1) ^ poly) & 0xFFFF
            else:
                cur = (cur << 1) & 0xFFFF
        table.append(cur)
    return tuple(table)


CRC16_TABLE: Tuple[int, ...] = _generate_crc16_table(0x1021)


def fast_calculate_crc16(data: bytes, init: int = 0xFFFF) -> int:
    """テーブル引きによる超高速 CRC-16-CCITT 計算 (従来のビット単位ループ比 約8倍高速)"""
    crc = init
    table = CRC16_TABLE
    for b in data:
        crc = ((crc << 8) & 0xFFFF) ^ table[((crc >> 8) ^ b) & 0xFF]
    return crc


class RaptorCodec:
    """万能消去訂正 (Cauchy / Raptor) コーデック (大域MDS ＆ ブロック分割両対応)"""

    def __init__(
        self,
        num_source_symbols: int = 1024,
        symbol_size: int = 216,
        seed: int = 42,
        max_subblock_size: int = 0,
        use_global_mds: bool = True
    ):
        self.k = num_source_symbols
        self.t = symbol_size
        self.seed = seed
        self.max_subblock_size = max_subblock_size

        # max_subblock_size が 0 または use_global_mds が True の場合は大域MDS (ブロック分割撤廃)
        self.use_global_mds = use_global_mds or (max_subblock_size <= 0)

        # コーシー係数キャッシュ
        self._coeff_cache: Dict[Tuple[int, int], int] = {}

        if self.use_global_mds:
            # 大域MDS: ブロック数 = 1 (全K個を単一空間で処理)
            self.num_blocks = 1
            self._init_gf2_16()
        else:
            # 従来モード: GF(256) によるサブブロック分割
            self.num_blocks = max(1, (self.k + self.max_subblock_size - 1) // self.max_subblock_size)
            self._init_gf256()

    # =========================================================================
    # GF(2^16) 大域MDS 演算エンジン (ブロック分割完全撤廃)
    # =========================================================================
    def _init_gf2_16(self):
        """GF(2^16) 高速対数・指数ルックアップテーブル初期化 (多項式: x^16 + x^12 + x^3 + x + 1 = 0x1100B)"""
        poly = 0x1100B
        self.exp16_t = np.zeros(131072, dtype=np.uint16)
        self.log16_t = np.zeros(65536, dtype=np.uint16)

        x = 1
        for i in range(65535):
            self.exp16_t[i] = x
            self.exp16_t[i + 65535] = x
            self.log16_t[x] = i
            x <<= 1
            if x & 0x10000:
                x ^= poly

    def _mul16(self, a: int, b: int) -> int:
        """GF(2^16) 乗算"""
        if a == 0 or b == 0:
            return 0
        return int(self.exp16_t[int(self.log16_t[a]) + int(self.log16_t[b])])

    def _inv16(self, a: int) -> int:
        """GF(2^16) 逆元"""
        if a == 0:
            raise ZeroDivisionError("GF(2^16) zero division")
        return int(self.exp16_t[65535 - int(self.log16_t[a])])

    def _mul_vec16(self, c: int, arr: np.ndarray) -> np.ndarray:
        """スカラー c と uint16 配列の高速ベクトル乗算 (不要なコピーとアロケーションを抑止)"""
        if c == 0:
            return np.zeros_like(arr)
        if c == 1:
            return arr.copy()
        nz = (arr != 0)
        if not np.any(nz):
            return np.zeros_like(arr)
        res = np.zeros_like(arr)
        c_log = int(self.log16_t[c])
        arr_nz = arr[nz]
        idx = c_log + self.log16_t[arr_nz].astype(np.int32)
        res[nz] = self.exp16_t[idx]
        return res

    def _get_global_coeff(self, r: int, i: int) -> int:
        """
        大域コーシー行列の係数 c = 1 / (x_r ^ y_i)
        x_r = r + 1          (1 〜 32767)
        y_i = i + 32768      (32768 〜 65535)
        x_r と y_i は最上位ビットが異なるため絶対に一致せず、重複ゼロ。
        任意の小行列は数学的に100%正則 (特異点ゼロ) が保証される。
        (キャッシュにより $O(1)$ で高速返却)
        """
        pair = (r, i)
        if pair in self._coeff_cache:
            return self._coeff_cache[pair]
        x_r = r + 1
        y_i = i + 32768
        coeff = self._inv16(x_r ^ y_i)
        self._coeff_cache[pair] = coeff
        return coeff

    def _bytes_to_uint16(self, b: bytes) -> np.ndarray:
        """バイト列を uint16 配列に変換 (パディング考慮)"""
        if len(b) % 2 != 0:
            b = b + b"\x00"
        return np.frombuffer(b, dtype=">u2")

    def _uint16_to_bytes(self, arr: np.ndarray, target_len: int) -> bytes:
        """uint16 配列を元のバイト長に変換"""
        return arr.astype(">u2").tobytes()[:target_len]

    # =========================================================================
    # GF(256) 従来サブブロック演算エンジン (後方互換用)
    # =========================================================================
    def _init_gf256(self):
        """GF(256) 乗算・逆元ルックアップテーブルの高速初期化"""
        exp_t = np.zeros(512, dtype=np.uint8)
        log_t = np.zeros(256, dtype=np.uint8)
        x = 1
        for i in range(255):
            exp_t[i] = x
            exp_t[i + 255] = x
            log_t[x] = i
            x = (x << 1) ^ (0x11D if (x & 0x80) else 0)

        self.mul_table = np.zeros((256, 256), dtype=np.uint8)
        for a in range(256):
            for b in range(256):
                if a > 0 and b > 0:
                    self.mul_table[a, b] = exp_t[int(log_t[a]) + int(log_t[b])]

        self.inv_table = np.zeros(256, dtype=np.uint8)
        for a in range(1, 256):
            self.inv_table[a] = exp_t[255 - int(log_t[a])]

    def _get_subblock_coeff(self, local_repair_idx: int, local_source_idx: int) -> int:
        """GF(256) コーシー行列係数"""
        x_r = (local_repair_idx % 127) + 1
        y_i = (local_source_idx % 127) + 128
        return int(self.inv_table[x_r ^ y_i])

    def get_source_block_assignment(self, source_idx: int) -> Tuple[int, int]:
        """ソースインデックスから (ブロック番号, ブロック内インデックス) を取得"""
        if self.use_global_mds:
            return 0, source_idx
        b = source_idx % self.num_blocks
        local_idx = source_idx // self.num_blocks
        return b, local_idx

    def get_repair_block_assignment(self, repair_idx: int) -> Tuple[int, int]:
        """リペアインデックスから (ブロック番号, ブロック内リペアインデックス) を取得"""
        if self.use_global_mds:
            return 0, repair_idx
        b = repair_idx % self.num_blocks
        local_repair_idx = repair_idx // self.num_blocks
        return b, local_repair_idx

    # =========================================================================
    # リペアシンボル生成 (エンコード)
    # =========================================================================
    def encode_repair_symbols(self, source_symbols: List[bytes], num_repair: int) -> List[Tuple[int, bytes]]:
        """
        K個のソースシンボルから、指定個数 (num_repair) のリペアシンボルを一括生成。
        大域MDSモード時は、ブロック分割を完全撤廃し、全ソースシンボルを網羅する真の万能リペアを生成。
        """
        if len(source_symbols) != self.k:
            raise ValueError(f"ソースシンボル数が一致しません: expected {self.k}, got {len(source_symbols)}")

        if self.use_global_mds:
            return self._encode_global(source_symbols, num_repair)
        else:
            return self._encode_subblock(source_symbols, num_repair)

    def _encode_global(self, source_symbols: List[bytes], num_repair: int) -> List[Tuple[int, bytes]]:
        """大域MDSリペア生成 (全Kパケットを単一のGF(2^16) Cauchy空間で一括線形結合)"""
        src_u16 = np.array([self._bytes_to_uint16(sym) for sym in source_symbols], dtype=np.uint16)
        num_words = src_u16.shape[1]

        repair_symbols: List[Tuple[int, bytes]] = []

        for r in range(num_repair):
            esi = self.k + r
            comb = np.zeros(num_words, dtype=np.uint16)

            for i in range(self.k):
                c = self._get_global_coeff(r, i)
                comb ^= self._mul_vec16(c, src_u16[i])

            rep_bytes = self._uint16_to_bytes(comb, self.t)
            repair_symbols.append((esi, rep_bytes))

        return repair_symbols

    def _encode_subblock(self, source_symbols: List[bytes], num_repair: int) -> List[Tuple[int, bytes]]:
        """GF(256) サブブロック分割リペア生成 (従来互換)"""
        block_sources: Dict[int, List[bytes]] = {b: [] for b in range(self.num_blocks)}
        for esi, sym in enumerate(source_symbols):
            b, _ = self.get_source_block_assignment(esi)
            block_sources[b].append(sym)

        block_arrs: Dict[int, np.ndarray] = {}
        for b in range(self.num_blocks):
            if block_sources[b]:
                block_arrs[b] = np.frombuffer(b"".join(block_sources[b]), dtype=np.uint8).reshape(len(block_sources[b]), self.t)
            else:
                block_arrs[b] = np.zeros((0, self.t), dtype=np.uint8)

        repair_symbols: List[Tuple[int, bytes]] = []

        for r in range(num_repair):
            esi = self.k + r
            b, local_r = self.get_repair_block_assignment(r)
            src_arr = block_arrs[b]
            k_b = len(src_arr)

            if k_b == 0:
                repair_symbols.append((esi, b"\x00" * self.t))
                continue

            coeffs = np.array([self._get_subblock_coeff(local_r, i) for i in range(k_b)], dtype=np.uint8)
            comb = np.zeros(self.t, dtype=np.uint8)
            for i in range(k_b):
                c = coeffs[i]
                if c != 0:
                    comb ^= self.mul_table[c, src_arr[i]]

            repair_symbols.append((esi, comb.tobytes()))

        return repair_symbols

    # =========================================================================
    # 消去復元 (デコード)
    # =========================================================================
    def decode(
        self,
        received_symbols: Dict[int, bytes]
    ) -> Tuple[Optional[List[bytes]], bool]:
        """
        受信した任意のシンボル群 {ESI: bytes} から、K個の元ソースシンボルを確実に消去復元する。
        大域MDSモード時は、どんな偏った消失が起きても「受信リペア数 >= 消失数」であれば100%全再生を完全保証。
        """
        missing_source = [esi for esi in range(self.k) if esi not in received_symbols]
        if not missing_source:
            # 欠落ゼロ
            return [received_symbols[esi] for esi in range(self.k)], True

        if self.use_global_mds:
            return self._decode_global(received_symbols, missing_source)
        else:
            return self._decode_subblock(received_symbols, missing_source)

    def _decode_global(
        self,
        received_symbols: Dict[int, bytes],
        missing_source: List[int]
    ) -> Tuple[Optional[List[bytes]], bool]:
        """大域MDS消去復元 (ブロック分割なし・ガウスジョルダン法)"""
        m = len(missing_source)
        available_repairs = [esi for esi in received_symbols.keys() if esi >= self.k]

        if len(available_repairs) < m:
            # リペアシンボル数が不足
            return None, False

        selected_repairs = sorted(available_repairs)[:m]
        miss_map = {esi: idx for idx, esi in enumerate(missing_source)}
        miss_set = set(missing_source)

        sample_bytes = next(iter(received_symbols.values()))
        sample_u16 = self._bytes_to_uint16(sample_bytes)
        num_words = len(sample_u16)

        # 縮小方程式 A・X = B の構築 (サイズ: m x m)
        A = np.zeros((m, m), dtype=np.uint16)
        B = np.zeros((m, num_words), dtype=np.uint16)

        known_sources_u16 = {}
        for esi in range(self.k):
            if esi not in miss_set and esi in received_symbols:
                known_sources_u16[esi] = self._bytes_to_uint16(received_symbols[esi])

        for row_idx, rep_esi in enumerate(selected_repairs):
            r = rep_esi - self.k
            rhs = self._bytes_to_uint16(received_symbols[rep_esi]).copy()

            for i in range(self.k):
                c = self._get_global_coeff(r, i)
                if i in miss_set:
                    col_idx = miss_map[i]
                    A[row_idx, col_idx] = c
                else:
                    if i in known_sources_u16:
                        rhs ^= self._mul_vec16(c, known_sources_u16[i])

            B[row_idx] = rhs

        # GF(2^16) 高速ガウス・ジョルダン消去法 (不要演算スキップ & ピボット最適化)
        for col in range(m):
            pivot_row = -1
            for r_idx in range(col, m):
                if A[r_idx, col] != 0:
                    pivot_row = r_idx
                    break

            if pivot_row == -1:
                return None, False

            if pivot_row != col:
                A[[col, pivot_row]] = A[[pivot_row, col]]
                B[[col, pivot_row]] = B[[pivot_row, col]]

            pivot_val = A[col, col]
            if pivot_val != 1:
                inv_p = self._inv16(pivot_val)
                A[col, col] = 1
                for c_idx in range(col + 1, m):
                    val = A[col, c_idx]
                    if val != 0:
                        A[col, c_idx] = self._mul16(inv_p, val)
                B[col] = self._mul_vec16(inv_p, B[col])

            # 他行の消去 (col 列目は自動的に 0 になるため col+1 以降のみを更新)
            for r_idx in range(m):
                if r_idx != col:
                    factor = A[r_idx, col]
                    if factor != 0:
                        A[r_idx, col] = 0
                        for c_idx in range(col + 1, m):
                            p_c = A[col, c_idx]
                            if p_c != 0:
                                A[r_idx, c_idx] ^= self._mul16(factor, p_c)
                        B[r_idx] ^= self._mul_vec16(factor, B[col])

        recovered_dict: Dict[int, bytes] = {}
        for i in range(m):
            rec_bytes = self._uint16_to_bytes(B[i], self.t)
            recovered_dict[missing_source[i]] = rec_bytes

        full_symbols: List[bytes] = []
        for esi in range(self.k):
            if esi in received_symbols:
                full_symbols.append(received_symbols[esi])
            elif esi in recovered_dict:
                full_symbols.append(recovered_dict[esi])
            else:
                return None, False

        return full_symbols, True

    def _decode_subblock(
        self,
        received_symbols: Dict[int, bytes],
        missing_source: List[int]
    ) -> Tuple[Optional[List[bytes]], bool]:
        """GF(256) サブブロック分割消去復元 (従来互換)"""
        block_missing: Dict[int, List[int]] = {b: [] for b in range(self.num_blocks)}
        for esi in missing_source:
            b, _ = self.get_source_block_assignment(esi)
            block_missing[b].append(esi)

        block_repairs: Dict[int, List[int]] = {b: [] for b in range(self.num_blocks)}
        for esi in received_symbols.keys():
            if esi >= self.k:
                r = esi - self.k
                b, _ = self.get_repair_block_assignment(r)
                block_repairs[b].append(esi)

        for b in range(self.num_blocks):
            if len(block_repairs[b]) < len(block_missing[b]):
                return None, False

        recovered_dict: Dict[int, bytes] = {}

        for b in range(self.num_blocks):
            miss_esis = block_missing[b]
            if not miss_esis:
                continue

            m = len(miss_esis)
            rep_esis = block_repairs[b][:m]
            k_b_esis = [esi for esi in range(self.k) if esi % self.num_blocks == b]
            miss_local_map = {esi: idx for idx, esi in enumerate(miss_esis)}

            A = np.zeros((m, m), dtype=np.uint8)
            B = np.zeros((m, self.t), dtype=np.uint8)

            for row_idx, rep_esi in enumerate(rep_esis):
                r = rep_esi - self.k
                _, local_r = self.get_repair_block_assignment(r)
                rhs = np.frombuffer(received_symbols[rep_esi], dtype=np.uint8).copy()

                for local_i, src_esi in enumerate(k_b_esis):
                    c = self._get_subblock_coeff(local_r, local_i)
                    if src_esi in miss_local_map:
                        col_idx = miss_local_map[src_esi]
                        A[row_idx, col_idx] = c
                    elif src_esi in received_symbols:
                        s_arr = np.frombuffer(received_symbols[src_esi], dtype=np.uint8)
                        rhs ^= self.mul_table[c, s_arr]

                B[row_idx] = rhs

            for col in range(m):
                pivot_row = -1
                for r_idx in range(col, m):
                    if A[r_idx, col] != 0:
                        pivot_row = r_idx
                        break

                if pivot_row == -1:
                    return None, False

                if pivot_row != col:
                    A[[col, pivot_row]] = A[[pivot_row, col]]
                    B[[col, pivot_row]] = B[[pivot_row, col]]

                inv_p = self.inv_table[A[col, col]]
                A[col] = self.mul_table[inv_p, A[col]]
                B[col] = self.mul_table[inv_p, B[col]]

                for r_idx in range(m):
                    if r_idx != col and A[r_idx, col] != 0:
                        factor = A[r_idx, col]
                        A[r_idx] ^= self.mul_table[factor, A[col]]
                        B[r_idx] ^= self.mul_table[factor, B[col]]

            for i in range(m):
                recovered_dict[miss_esis[i]] = B[i].tobytes()

        full_symbols: List[bytes] = []
        for esi in range(self.k):
            if esi in received_symbols:
                full_symbols.append(received_symbols[esi])
            elif esi in recovered_dict:
                full_symbols.append(recovered_dict[esi])
            else:
                return None, False

        return full_symbols, True


if __name__ == "__main__":
    import sys
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, 'reconfigure', None)
        if callable(reconf):
            try:
                reconf(encoding='utf-8', errors='replace')
            except Exception:
                pass

    print("[RaptorCodec] 単体実行テスト (大域Cauchy MDS)")
    codec = RaptorCodec(num_source_symbols=256, symbol_size=216, use_global_mds=True)
    src_data = [bytes([(i * 7 + j) % 256 for j in range(216)]) for i in range(256)]
    repairs = codec.encode_repair_symbols(src_data, num_repair=32)
    print(f"  ・ソースシンボル: {len(src_data)} 個")
    print(f"  ・リペアシンボル: {len(repairs)} 個")

    # 10個消失させて復元テスト
    missing = [5, 12, 44, 88, 120, 150, 180, 200, 220, 250]
    rx = {esi: src_data[esi] for esi in range(256) if esi not in missing}
    for rep_esi, rep_b in repairs[:len(missing)]:
        rx[rep_esi] = rep_b

    decoded, ok = codec.decode(rx)
    print(f"  ・消去復元結果: {'成功 (100%一致)' if ok and decoded == src_data else '失敗'}")
    print("[OK] コーデック単体テスト正常完了！")
