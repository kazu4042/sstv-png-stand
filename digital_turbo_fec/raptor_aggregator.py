"""ラプター符号 ＋ RS符号 4段統合アグリゲータ (raptor_aggregator.py)
【4段の多段復元アーキテクチャ】
1. 第1防壁: RS単局自動誤り訂正 (最大16B誤り完治 ➔ ランクS即時確定)
2. 第2防壁: 複数地上局のSNR加重ビット多数決 (SIMD)
3. 第3防壁: 多数決 ＋ RS再適用 (合わせ技蘇生)
4. 【即時タイル描画】データパケットが届き次第、画面に即座に描画
5. 第4防壁: ラプター符号消去復元 (全局消失パケットをリペアシンボルで100%全再生)
6. No.1〜No.5 タイル復元パイプライン (RST合体・部分描画フォールバック)
"""

import io
import itertools
import os
import struct
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

# プロジェクトルートのパス解決 (直接実行時でも親ディレクトリの rs_fec や core を確実にインポート可能にする)
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import numpy as np
from PIL import Image, ImageFile

setattr(ImageFile, 'LOAD_TRUNCATED_IMAGES', True)

try:
    from digital_turbo_fec.fec_engine import fast_bit_majority_vote, fast_bit_majority_vote_with_erasures
    from digital_turbo_fec.rs_codec import RSCodec
    from digital_turbo_fec.config import RAPTOR_CFG, RaptorConfig
    from digital_turbo_fec.raptor_codec import RaptorCodec, fast_calculate_crc16
except ImportError:
    from core.fec_engine import fast_bit_majority_vote, fast_bit_majority_vote_with_erasures
    from rs_fec.rs_codec import RSCodec
    from config import RAPTOR_CFG, RaptorConfig
    from raptor_rs_fec.raptor_codec import RaptorCodec, fast_calculate_crc16



class RaptorRSAggregator:
    """ラプター符号 ＆ RS符号 統合アグリゲータ"""

    def __init__(self, config: RaptorConfig = RAPTOR_CFG, total_source_packets: Optional[int] = None):
        self.cfg = config
        self.rs_codec = RSCodec(nsym=self.cfg.rs_nsym)
        if total_source_packets is None:
            if getattr(self.cfg, "enable_header_stripping", False):
                self.total_source_packets = self.cfg.total_tiles
            else:
                self.total_source_packets = 1024
        else:
            self.total_source_packets = total_source_packets
        self.raptor_codec = RaptorCodec(
            num_source_symbols=self.total_source_packets,
            symbol_size=self.cfg.symbol_size,
            seed=self.cfg.raptor_seed,
            max_subblock_size=getattr(self.cfg, "max_subblock_size", 0),
            use_global_mds=getattr(self.cfg, "use_global_mds", True)
        )

        # 描画キャンバス (RGB)
        self.canvas = np.zeros((self.cfg.height, self.cfg.width, 3), dtype=np.uint8)
        self.rendered_tiles: Set[Tuple[int, int]] = set()

        # No.1〜No.5 多段タイル復元管理
        self.confirmed_tiles: Set[Tuple[int, int]] = set()
        self.provisional_tiles: Dict[Tuple[int, int], float] = {}

        # タイル単位の候補バッファ: {tile_id: {seq: [chunk_bytes, ...]}}
        self.tile_candidates: Dict[int, Dict[int, List[bytes]]] = defaultdict(lambda: defaultdict(list))
        self.tile_candidate_snrs: Dict[int, Dict[int, List[float]]] = defaultdict(lambda: defaultdict(list))
        self.tile_meta: Dict[int, Tuple[int, int]] = {}

        # ラプター符号用シンボルバッファ: {esi: payload_bytes}
        self.raptor_symbols: Dict[int, bytes] = {}

        # パケット単位の生RSブロック保管庫 (多数決用): {esi: [(raw_rs_block, snr_db), ...]}
        self.raw_rs_candidates: Dict[int, List[Tuple[bytes, float]]] = defaultdict(list)
        self.solved_esis: Set[int] = set()

        # 統計カウンタ
        self.intact_packet_count: int = 0
        self.rs_corrected_count: int = 0
        self.vote_recovered_count: int = 0
        self.erasure_vote_recovered_count: int = 0
        self.raptor_recovered_packets: int = 0
        self.vote_only_recovered_count: int = 0

        self._header_template_cache = {}

    @staticmethod
    def calculate_crc16(data: bytes, poly: int = 0x1021, init: int = 0xFFFF) -> int:
        """テーブル引きによる高速 CRC-16-CCITT 計算"""
        return fast_calculate_crc16(data, init=init)

    @staticmethod
    def calculate_valid_pixel_ratio(arr: np.ndarray) -> float:
        """タイルの有効画素率"""
        if arr.size == 0:
            return 0.0
        h = arr.shape[0]
        valid_rows = sum(1 for r in range(h) if not np.all(arr[r] == 0))
        return valid_rows / float(h)

    def _render_tile_to_canvas(self, tx: int, ty: int, t_arr: np.ndarray):
        """キャンバスにタイルを描画"""
        y0 = ty * self.cfg.tile_size
        y1 = min(y0 + self.cfg.tile_size, self.cfg.height)
        x0 = tx * self.cfg.tile_size
        x1 = min(x0 + self.cfg.tile_size, self.cfg.width)
        self.canvas[y0:y1, x0:x1] = t_arr
        self.rendered_tiles.add((tx, ty))

    def add_packet(self, pkt_info: Dict) -> List[Tuple[int, int]]:
        """
        復調された1パケット情報をアグリゲータに追加し、即時タイル復元を試行する。
        """
        esi = pkt_info.get("esi", -1)
        pkt_type = pkt_info.get("pkt_type", self.cfg.TYPE_DATA)
        snr_db = pkt_info.get("snr_db", 0.0)
        raw_rs_block = pkt_info.get("raw_rs_block", b"")
        is_valid = pkt_info.get("is_valid", False)
        corr_bytes = pkt_info.get("corrected_bytes", 0)

        # -------------------------------------------------------------
        # 第1防壁: RS単局自動復号に成功している場合 (ランクS)
        # -------------------------------------------------------------
        if is_valid and esi >= 0:
            if esi not in self.solved_esis:
                self.solved_esis.add(esi)
                if corr_bytes == 0:
                    self.intact_packet_count += 1
                else:
                    self.rs_corrected_count += 1

            payload = pkt_info["payload"]
            self.raptor_symbols[esi] = payload

            # データパケットの場合: 即時タイル復元
            if pkt_type == self.cfg.TYPE_DATA:
                tile_id = pkt_info["tile_id"]
                seq = pkt_info["tile_seq"]
                num_pkts = pkt_info["tile_num_pkts"]
                t_len = pkt_info["tile_data_len"]
                chunk = payload[self.cfg.tag_size:]

                self.tile_meta[tile_id] = (num_pkts, t_len)
                self.tile_candidates[tile_id][seq].append(chunk)
                self.tile_candidate_snrs[tile_id][seq].append(snr_db)

                return self._resolve_tile(tile_id, num_pkts, t_len)
            else:
                # リペアパケットの場合はシンボルバッファに保存して終了
                return []

        # -------------------------------------------------------------
        # RS復号失敗 (>16バイト誤り) の場合
        # -------------------------------------------------------------
        if len(raw_rs_block) >= self.cfg.rs_protected_size and esi >= 0:
            self.raw_rs_candidates[esi].append((raw_rs_block, snr_db))

            # 第3防壁: 複数局生RSブロックの多数決 ＋ RS再適用 (票割れErasure連携)
            if esi not in self.solved_esis and len(self.raw_rs_candidates[esi]) >= 2:
                cand_blocks = [b for b, s in self.raw_rs_candidates[esi]]
                cand_snrs = [max(0.1, s + 10.0) for b, s in self.raw_rs_candidates[esi]]

                # 票割れビット検出付き加重多数決
                voted_rs_block, erase_positions = fast_bit_majority_vote_with_erasures(cand_blocks, weights=cand_snrs)

                # 1. まず通常RS復号 (誤り訂正のみ: 最大16B)
                dec_msg, corr_cnt, ok = self.rs_codec.decode_after_vote(voted_rs_block)
                used_erasure = False

                # 2. 通常復号失敗時: 票割れErasure連携復号
                # 【安全設計】消失指定数(e_limit)は最大16バイトに制限し、残り16バイト(最大8B訂正可能)の検算余力を確保して偽合格を防止
                if (not ok or dec_msg is None) and erase_positions:
                    for e_limit in (8, 12, 16):
                        test_erases = erase_positions[:min(len(erase_positions), e_limit)]
                        dec_msg, corr_cnt, ok = self.rs_codec.decode_with_erasures(voted_rs_block, erase_pos=test_erases)
                        if ok and dec_msg is not None:
                            used_erasure = True
                            break

                if ok and dec_msg is not None:
                    img_id, p_type, solved_esi = struct.unpack(">HBH", dec_msg[:5])
                    # 安全チェック: 復元されたESIが処理対象のESIと一致しているか
                    if solved_esi != esi:
                        return []

                    self.solved_esis.add(esi)
                    self.vote_recovered_count += 1
                    if used_erasure:
                        self.erasure_vote_recovered_count += 1

                    payload = dec_msg[7:]
                    self.raptor_symbols[solved_esi] = payload

                    if p_type == self.cfg.TYPE_DATA:
                        t_id, t_seq, t_num_pkts, t_len = struct.unpack(">BBBH", payload[:5])
                        chunk = payload[self.cfg.tag_size:]

                        self.tile_meta[t_id] = (t_num_pkts, t_len)
                        self.tile_candidates[t_id][t_seq].append(chunk)
                        self.tile_candidate_snrs[t_id][t_seq].append(float(np.mean(cand_snrs)))

                        return self._resolve_tile(t_id, t_num_pkts, t_len)

        return []

    def run_raptor_recovery(self) -> int:
        """
        【第4防壁: ラプター符号による消失パケット100%全再生】
        全パケット受信後、未だ欠落しているデータパケット (ESI < K) を
        届いたラプターリペアシンボルを用いて一括復元する。
        """
        missing_source = [esi for esi in range(self.total_source_packets) if esi not in self.raptor_symbols]
        if not missing_source:
            return 0  # 欠落なし

        available_repairs = [esi for esi in self.raptor_symbols.keys() if esi >= self.total_source_packets]
        if len(available_repairs) < len(missing_source):
            # リペアシンボル数が不足している場合は復元不可
            return 0

        # ラプター復号を実行
        decoded_sources, ok = self.raptor_codec.decode(self.raptor_symbols)
        if not ok or decoded_sources is None:
            return 0

        recovered_count = 0
        for esi in missing_source:
            payload = decoded_sources[esi]
            self.raptor_symbols[esi] = payload
            self.solved_esis.add(esi)

            # 欠落していたデータパケットからタイルを復元
            t_id, t_seq, t_num_pkts, t_len = struct.unpack(">BBBH", payload[:5])
            chunk = payload[self.cfg.tag_size:]

            self.tile_meta[t_id] = (t_num_pkts, t_len)
            self.tile_candidates[t_id][t_seq].append(chunk)
            self.tile_candidate_snrs[t_id][t_seq].append(10.0)

            self._resolve_tile(t_id, t_num_pkts, t_len)
            recovered_count += 1

        self.raptor_recovered_packets = recovered_count
        return recovered_count

    # =================================================================
    # No.1〜No.5 多段タイル復元パイプライン
    # =================================================================

    def reconstruct_with_rst_skip(
        self,
        chunks_by_seq: Dict[int, bytes],
        num_pkts: int,
        t_len: int
    ) -> Optional[Tuple[np.ndarray, float, bytes]]:
        """
        【RSTマーカー間スキップ復元 (SSDV方式の空MCUブロック補間)】
        
        一部のパケットが欠落していても、届いているパケット内の有効なMCUエントロピーセグメントを検出し、
        欠落区間にダミー空MCU (b'\x28\x03': グレー) を補間・挿入して正規JPEGバイナリを再構成する。
        
        戻り値:
            Optional[Tuple[np.ndarray, float, bytes]]: 
                (復元画像配列(16x16x3), 有効MCU比率(0.0〜1.0), 再構成JPEGバイナリ)
        """
        ts = self.cfg.tile_size
        header_template, header_len = self._get_jpeg_header_template(ts, ts)
        if header_template is None or header_len <= 0:
            return None

        chunk_size = self.cfg.rs_data_chunk_size
        DUMMY_MCU = b'\x28\x03'  # 16x16 4:4:4 Q75 におけるグレー (128,128,128) の空MCU

        # チャンクがヘッダを含まないエントロピーのみのデータか判定
        # (ヘッダ省略モード時は t_len がエントロピー長(約100B)のため header_len(629B) 未満となる)
        is_entropy_only = (t_len < header_len) and not any(c.startswith(b'\xff\xd8') for c in chunks_by_seq.values() if len(c) >= 2)

        # 1. エントロピーバッファの初期化
        if is_entropy_only:
            entropy_len = t_len
            entropy_buf: List[Optional[int]] = [None] * entropy_len
            for s, chunk in chunks_by_seq.items():
                if not chunk:
                    continue
                c_start = s * chunk_size
                c_end = min(c_start + len(chunk), t_len)
                for idx in range(c_start, c_end):
                    entropy_buf[idx] = chunk[idx - c_start]
        else:
            effective_t_len = max(t_len, header_len + 4)
            entropy_len = max(0, effective_t_len - header_len)
            entropy_buf = [None] * entropy_len
            for s, chunk in chunks_by_seq.items():
                if not chunk:
                    continue
                c_start = s * chunk_size
                c_end = min(c_start + len(chunk), effective_t_len)
                e_start = max(0, c_start - header_len)
                e_end = max(0, c_end - header_len)
                if e_end > e_start:
                    chunk_offset = (header_len - c_start) if c_start < header_len else 0
                    for idx in range(e_start, e_end):
                        src_idx = chunk_offset + (idx - e_start)
                        if src_idx < len(chunk):
                            entropy_buf[idx] = chunk[src_idx]

        # 2. バイト配列と有効マスクの構築
        raw_entropy = bytearray()
        valid_mask: List[bool] = []
        for b in entropy_buf:
            if b is not None:
                raw_entropy.append(b)
                valid_mask.append(True)
            else:
                raw_entropy.append(0x00)
                valid_mask.append(False)

        # 3. RSTマーカー (0xFFD0, 0xFFD1, 0xFFD2) および EOI (0xFFD9) の探索
        markers: Dict[int, int] = {}
        last_found_pos = 0
        for m_byte, m_code in [(0xD0, b'\xff\xd0'), (0xD1, b'\xff\xd1'), (0xD2, b'\xff\xd2'), (0xD9, b'\xff\xd9')]:
            search_start = last_found_pos
            found = False
            while search_start < len(raw_entropy) - 1:
                pos = raw_entropy.find(m_code, search_start)
                if pos == -1:
                    break
                if valid_mask[pos] and valid_mask[pos + 1]:
                    markers[m_byte] = pos
                    last_found_pos = pos + 2
                    found = True
                    break
                search_start = pos + 1
            if not found:
                # 以降のマーカー順序整合性を保つため探索開始位置は維持
                pass

        # 4. 4つのMCUセグメント境界を決定
        # seg0: 0 .. pos(0xD0)
        # seg1: pos(0xD0)+2 .. pos(0xD1)
        # seg2: pos(0xD1)+2 .. pos(0xD2)
        # seg3: pos(0xD2)+2 .. pos(0xD9)
        boundaries: List[Optional[int]] = [0]
        for m in (0xD0, 0xD1, 0xD2):
            boundaries.append(markers.get(m, None))
        
        # 末尾 (EOI) の位置
        if 0xD9 in markers:
            boundaries.append(markers[0xD9])
        else:
            # EOIが見つからなくても、末尾までデータが届いていればそこを終端とする
            boundaries.append(len(raw_entropy) if (valid_mask and valid_mask[-1]) else None)

        segs: List[bytes] = []
        valid_mcu_count = 0

        for i in range(4):
            b_start = boundaries[i]
            b_end = boundaries[i + 1]
            if b_start is not None and b_end is not None and b_start <= b_end:
                actual_start = b_start if i == 0 else b_start + 2
                if actual_start <= b_end and all(valid_mask[actual_start:b_end]):
                    segs.append(bytes(raw_entropy[actual_start:b_end]))
                    valid_mcu_count += 1
                else:
                    segs.append(DUMMY_MCU)
            else:
                segs.append(DUMMY_MCU)

        # 5. JPEGバイナリを再構成
        rebuilt = (header_template +
                   segs[0] + b'\xff\xd0' +
                   segs[1] + b'\xff\xd1' +
                   segs[2] + b'\xff\xd2' +
                   segs[3] + b'\xff\xd9')

        try:
            im = Image.open(io.BytesIO(rebuilt))
            im.load()
            t_arr = np.array(im.convert("RGB").resize((ts, ts)))
            valid_ratio = float(valid_mcu_count) / 4.0
            return t_arr, valid_ratio, rebuilt
        except Exception:
            return None

    def _resolve_tile(self, tile_id: int, num_pkts: int, t_len: int) -> List[Tuple[int, int]]:
        """タイル単位の即時解決パイプライン (RSTマーカー間スキップ復元対応)"""
        tx = tile_id % self.cfg.tile_count_x
        ty = tile_id // self.cfg.tile_count_x

        if (tx, ty) in self.confirmed_tiles:
            return []

        cands_dict = self.tile_candidates.get(tile_id, {})
        has_all_packets = (len(cands_dict) >= num_pkts and all(bool(cands_dict.get(s)) for s in range(num_pkts)))

        # -------------------------------------------------------------
        # 全パケットが揃っている場合: No.1〜No.3 の完全復元パイプラインを実行
        # -------------------------------------------------------------
        if has_all_packets:
            max_cands = max(len(cands_dict[s]) for s in range(num_pkts))
            has_multiple_candidates = any(len(cands_dict[s]) >= 2 for s in range(num_pkts))

            # No.1 受信タイル単体での無傷チェック・即時採用
            for c_idx in range(max_cands):
                chunk_list = [cands_dict[s][min(c_idx, len(cands_dict[s]) - 1)] for s in range(num_pkts)]
                full_bytes = b"".join(chunk_list)[:t_len]
                full_jpeg = self._ensure_jpeg_header(full_bytes)

                perf_arr = self._is_perfect_jpeg_tile(full_jpeg)
                if perf_arr is not None:
                    self._render_tile_to_canvas(tx, ty, perf_arr)
                    self.confirmed_tiles.add((tx, ty))
                    self.provisional_tiles.pop((tx, ty), None)
                    return [(tx, ty)]

            # No.2 全ビット加重多数決
            voted_full_bytes = None
            if has_multiple_candidates:
                voted_chunks = []
                for s in range(num_pkts):
                    c_list = cands_dict[s]
                    snr_list = self.tile_candidate_snrs.get(tile_id, {}).get(s, [1.0] * len(c_list))
                    if len(c_list) >= 2:
                        voted_chunks.append(fast_bit_majority_vote(c_list, weights=snr_list))
                    else:
                        voted_chunks.append(c_list[0])
                voted_full_bytes = b"".join(voted_chunks)[:t_len]
                voted_jpeg = self._ensure_jpeg_header(voted_full_bytes)

                perf_arr = self._is_perfect_jpeg_tile(voted_jpeg)
                if perf_arr is not None:
                    self._render_tile_to_canvas(tx, ty, perf_arr)
                    self.confirmed_tiles.add((tx, ty))
                    self.provisional_tiles.pop((tx, ty), None)
                    self.vote_only_recovered_count += 1
                    return [(tx, ty)]

                # No.3 RSTマーカー区間分割による再構築・合体
                rst_result = self._rst_aligned_rebuild(cands_dict, num_pkts, t_len, voted_full_bytes)
                if rst_result is not None:
                    rst_arr, rst_bytes = rst_result
                    self._render_tile_to_canvas(tx, ty, rst_arr)
                    self.confirmed_tiles.add((tx, ty))
                    self.provisional_tiles.pop((tx, ty), None)
                    self.vote_only_recovered_count += 1
                    return [(tx, ty)]

        # -------------------------------------------------------------
        # 一部パケット欠落時 or 完全合体に失敗した場合:
        # 【RSTマーカー間スキップ復元】による即時暫定描画
        # -------------------------------------------------------------
        best_chunks_by_seq = {}
        for s in range(num_pkts):
            if s in cands_dict and cands_dict[s]:
                best_chunks_by_seq[s] = cands_dict[s][0]

        if best_chunks_by_seq:
            rst_skip_res = self.reconstruct_with_rst_skip(best_chunks_by_seq, num_pkts, t_len)
            if rst_skip_res is not None:
                rst_arr, valid_ratio, rebuilt_bytes = rst_skip_res
                # もし全MCUが有効かつ完全無傷チェックに合格すれば確定昇格
                if valid_ratio >= 1.0:
                    perf_arr = self._is_perfect_jpeg_tile(rebuilt_bytes)
                    if perf_arr is not None:
                        self._render_tile_to_canvas(tx, ty, perf_arr)
                        self.confirmed_tiles.add((tx, ty))
                        self.provisional_tiles.pop((tx, ty), None)
                        return [(tx, ty)]

                # 部分復元の場合: 前回の暫定比率より高ければ即時描画更新
                prev_ratio = self.provisional_tiles.get((tx, ty), -1.0)
                if valid_ratio > prev_ratio and valid_ratio > 0.0:
                    self._render_tile_to_canvas(tx, ty, rst_arr)
                    self.provisional_tiles[(tx, ty)] = valid_ratio
                    return [(tx, ty)]

        return []

    def finalize_tiles(self) -> int:
        """
        全パケット受信後に未確定のタイルに対して、
        【RSTマーカー間スキップ復元】を用いて可能な限り高品質に復元・描画する。
        """
        recovered_count = 0
        for tile_id in range(self.cfg.total_tiles):
            tx = tile_id % self.cfg.tile_count_x
            ty = tile_id // self.cfg.tile_count_x

            if (tx, ty) in self.confirmed_tiles:
                continue

            cands_dict = self.tile_candidates.get(tile_id, {})
            if not cands_dict:
                continue

            meta = self.tile_meta.get(tile_id)
            num_pkts = meta[0] if meta else max(cands_dict.keys()) + 1
            t_len = meta[1] if meta else num_pkts * self.cfg.rs_data_chunk_size

            # 各シーケンスの代表チャンクを取得
            best_chunks = {s: cands_dict[s][0] for s in cands_dict if cands_dict[s]}
            if not best_chunks:
                continue

            # 1. まず RSTマーカー間スキップ復元 を試行
            rst_skip_res = self.reconstruct_with_rst_skip(best_chunks, num_pkts, t_len)
            if rst_skip_res is not None:
                rst_arr, valid_ratio, _ = rst_skip_res
                prev_ratio = self.provisional_tiles.get((tx, ty), -1.0)
                if valid_ratio > prev_ratio:
                    self._render_tile_to_canvas(tx, ty, rst_arr)
                    self.provisional_tiles[(tx, ty)] = valid_ratio
                    recovered_count += 1
                    continue

            # 2. フォールバック: 従来型ゼロ埋め結合
            chunks = []
            for s in range(num_pkts):
                if s in cands_dict and cands_dict[s]:
                    chunks.append(cands_dict[s][0])
                else:
                    chunks.append(b"\x00" * self.cfg.rs_data_chunk_size)
            raw_b = b"".join(chunks)[:t_len]
            raw_jpeg = self._ensure_jpeg_header(raw_b)

            if not raw_jpeg.endswith(b"\xff\xd9"):
                raw_jpeg = raw_jpeg + b"\xff\xd9"

            try:
                im = Image.open(io.BytesIO(raw_jpeg))
                t_arr = np.array(im.resize((self.cfg.tile_size, self.cfg.tile_size)).convert("RGB"))
                ratio = self.calculate_valid_pixel_ratio(t_arr)
                if ratio > 0.1:
                    self._render_tile_to_canvas(tx, ty, t_arr)
                    self.provisional_tiles[(tx, ty)] = ratio
                    recovered_count += 1
            except Exception:
                pass

        return recovered_count

    def _get_jpeg_header_template(self, tile_w: int, tile_h: int) -> Tuple[Optional[bytes], int]:
        key = (tile_w, tile_h)
        if key not in self._header_template_cache:
            dummy = Image.new("RGB", (tile_w, tile_h), color=(0, 0, 0))
            bio = io.BytesIO()
            restart_int = getattr(self.cfg, 'jpeg_restart_marker', 1)
            quality = getattr(self.cfg, 'jpeg_quality', 75)
            subsampling = getattr(self.cfg, 'jpeg_subsampling', 0)
            dummy.save(bio, format="JPEG", quality=quality,
                       restart_marker_blocks=restart_int, subsampling=subsampling)
            raw = bio.getvalue()
            sos_pos = raw.find(b'\xff\xda')
            if sos_pos != -1 and sos_pos + 3 < len(raw):
                sos_len = (raw[sos_pos + 2] << 8) | raw[sos_pos + 3]
                end = sos_pos + 2 + sos_len
                self._header_template_cache[key] = (raw[:end], end)
            else:
                self._header_template_cache[key] = (None, 0)
        return self._header_template_cache[key]

    def _ensure_jpeg_header(self, data: bytes) -> bytes:
        """
        バイト列がJPEGのSOIマーカー (0xFFD8) で始まっていない場合、
        固定JPEGヘッダテンプレートを先頭に結合して完全なJPEGバイナリにする。
        """
        if not data:
            return data
        if data.startswith(b'\xff\xd8'):
            return data
        header_tmpl, _ = self._get_jpeg_header_template(self.cfg.tile_size, self.cfg.tile_size)
        if header_tmpl:
            return header_tmpl + data
        return data

    @staticmethod
    def split_jpeg_into_segments(
        raw_bytes: bytes,
        header_template: Optional[bytes] = None,
        header_end_offset: int = 0
    ) -> Optional[Tuple[bytes, List[bytes], List[int], bool]]:
        if not raw_bytes or len(raw_bytes) < 4:
            return None
        raw = bytes(raw_bytes)
        soi_idx = raw.find(b'\xff\xd8')
        sos_idx = raw.find(b'\xff\xda', (soi_idx + 2) if soi_idx != -1 else 0)

        header_bytes = None
        header_end = -1
        if soi_idx != -1 and sos_idx != -1 and sos_idx + 3 < len(raw):
            sos_len = (raw[sos_idx + 2] << 8) | raw[sos_idx + 3]
            header_end = sos_idx + 2 + sos_len
            if header_end <= len(raw):
                header_bytes = raw[soi_idx:header_end]

        if header_bytes is None and header_template is not None and header_end_offset > 0:
            if len(raw) > header_end_offset:
                header_bytes = header_template
                header_end = header_end_offset

        if header_bytes is None or header_end < 0:
            return None

        segments: List[bytes] = []
        rst_markers: List[int] = []
        has_eoi = False
        idx = header_end
        n = len(raw)
        seg_start = header_end

        while idx < n:
            if raw[idx] == 0xFF:
                if idx + 1 < n:
                    m = raw[idx + 1]
                    if m == 0x00:
                        idx += 2
                        continue
                    elif 0xD0 <= m <= 0xD7:
                        segments.append(raw[seg_start:idx])
                        rst_markers.append(m)
                        idx += 2
                        seg_start = idx
                        continue
                    elif m == 0xD9:
                        segments.append(raw[seg_start:idx])
                        has_eoi = True
                        idx += 2
                        break
                    else:
                        idx += 1
                else:
                    idx += 1
            else:
                idx += 1

        if not has_eoi and seg_start < n:
            segments.append(raw[seg_start:n])

        return header_bytes, segments, rst_markers, has_eoi

    def _is_perfect_jpeg_tile(self, raw_bytes: bytes) -> Optional[np.ndarray]:
        if not raw_bytes or len(raw_bytes) < 4:
            return None
        raw = bytes(raw_bytes).rstrip(b'\x00')
        if not (raw.startswith(b'\xff\xd8') and raw.endswith(b'\xff\xd9')):
            return None

        sos_idx = raw.find(b'\xff\xda')
        if sos_idx == -1 or sos_idx + 3 >= len(raw):
            return None
        sos_len = (raw[sos_idx + 2] << 8) | raw[sos_idx + 3]
        entropy_start = sos_idx + 2 + sos_len
        if entropy_start > len(raw):
            return None

        idx = entropy_start
        n = len(raw)
        rst_markers = []
        while idx < n:
            if raw[idx] == 0xFF:
                if idx + 1 >= n:
                    return None
                m = raw[idx + 1]
                if m == 0x00:
                    idx += 2
                    continue
                elif 0xD0 <= m <= 0xD7:
                    rst_markers.append(m)
                    idx += 2
                    continue
                elif m == 0xD9:
                    idx += 2
                    break
                else:
                    return None
            else:
                idx += 1

        ts = self.cfg.tile_size
        if ts == 16 and rst_markers != [0xD0, 0xD1, 0xD2]:
            return None

        old_flag = bool(getattr(ImageFile, 'LOAD_TRUNCATED_IMAGES', True))
        try:
            setattr(ImageFile, 'LOAD_TRUNCATED_IMAGES', False)
            im = Image.open(io.BytesIO(raw))
            im.load()
            t_arr = np.array(im.convert("RGB").resize((ts, ts)))

            bottom_strip = t_arr[-min(4, ts):, :]
            if np.max(bottom_strip) < 3.0 and np.mean(t_arr) > 10.0:
                return None

            if ts == 16 and np.mean(t_arr) > 10.0:
                for by in (0, 8):
                    for bx in (0, 8):
                        block = t_arr[by:by+8, bx:bx+8]
                        if np.max(block) < 3.0:
                            return None

            gray_mask = (np.abs(t_arr.astype(np.float32) - 128.0) < 1.0)
            if np.sum(np.all(gray_mask, axis=2)) > 4:
                return None

            return t_arr
        except Exception:
            return None
        finally:
            setattr(ImageFile, 'LOAD_TRUNCATED_IMAGES', old_flag)

    def _rst_aligned_rebuild(
        self,
        cands_dict: Dict[int, List[bytes]],
        num_pkts: int,
        t_len: int,
        voted_full_bytes: Optional[bytes] = None
    ) -> Optional[Tuple[np.ndarray, bytes]]:
        ts = self.cfg.tile_size
        header_tmpl, header_off = self._get_jpeg_header_template(ts, ts)

        full_binaries: List[bytes] = []
        if voted_full_bytes is not None:
            v_jpeg = self._ensure_jpeg_header(voted_full_bytes)
            if v_jpeg not in full_binaries:
                full_binaries.append(v_jpeg)

        max_cands = max(len(cands_dict[s]) for s in range(num_pkts))
        for c_idx in range(max_cands):
            chunk_list = [cands_dict[s][min(c_idx, len(cands_dict[s]) - 1)] for s in range(num_pkts)]
            b = b"".join(chunk_list)[:t_len]
            b_jpeg = self._ensure_jpeg_header(b)
            if b_jpeg not in full_binaries:
                full_binaries.append(b_jpeg)

        parsed_list = []
        for full_bytes in full_binaries:
            parsed = self.split_jpeg_into_segments(full_bytes, header_tmpl, header_off)
            if parsed is not None:
                header_bytes, segments, rst_markers, has_eoi = parsed
                if len(segments) > 1:
                    parsed_list.append(parsed)

        if len(parsed_list) < 2:
            return None

        expected_seg_count = 4 if ts == 16 else None
        target_parsed = [p for p in parsed_list if len(p[1]) == expected_seg_count] if expected_seg_count else parsed_list
        if not target_parsed:
            return None

        best_header = target_parsed[0][0]
        majority_segments = []

        for seg_idx in range(expected_seg_count or len(target_parsed[0][1])):
            candidates = [p[1][seg_idx] for p in target_parsed if seg_idx < len(p[1])]
            if not candidates:
                return None
            len_groups = defaultdict(list)
            for c_bytes in candidates:
                len_groups[len(c_bytes)].append(c_bytes)
            best_len = max(len_groups.keys(), key=lambda l: len(len_groups[l]))
            same_len = len_groups[best_len]
            majority_segments.append(fast_bit_majority_vote(same_len) if len(same_len) >= 2 else same_len[0])

        best_markers = target_parsed[0][2]
        buf = bytearray(best_header)
        for i, s in enumerate(majority_segments):
            buf.extend(s)
            if i < len(best_markers):
                buf.extend(bytes([0xFF, best_markers[i]]))
        buf.extend(b'\xff\xd9')

        rebuilt = bytes(buf)
        perf_arr = self._is_perfect_jpeg_tile(rebuilt)
        if perf_arr is not None:
            return perf_arr, rebuilt
        return None

    def save_canvas_image(self, output_path: str):
        """キャンバスを画像ファイルとして保存"""
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        img = Image.fromarray(self.canvas)
        img.save(output_path)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, 'reconfigure', None)
        if callable(reconf):
            try:
                reconf(encoding='utf-8', errors='replace')
            except Exception:
                pass

    print("[RaptorRSAggregator] 単体実行テスト")
    agg = RaptorRSAggregator(RAPTOR_CFG, total_source_packets=RAPTOR_CFG.total_tiles)
    print(f"  ・キャンバス解像度: {agg.canvas.shape[1]}x{agg.canvas.shape[0]}")
    print(f"  ・総ソースパケット数: {agg.total_source_packets}")
    print("[OK] アグリゲータ単体テスト正常完了！")
