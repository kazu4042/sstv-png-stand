"""SSTV Turbo FEC (Raptor + RS) Webプラットフォーム統合アグリゲータ (aggregator_turbo.py)
【sstv-png-stand BaseAggregator 準拠】
受信テキストログのDB同期、複数局ビット多数決、Raptor消去MDS全再生、多段画像復元、
および Web静的画像・プレビューの出力。
"""

import glob
import io
import math
import os
import re
import shutil
import struct
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageFile

setattr(ImageFile, 'LOAD_TRUNCATED_IMAGES', True)

_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_CURRENT_DIR, "../"))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from core.base_interfaces import BaseAggregator
from digital_turbo_fec import config_turbo as config
from digital_turbo_fec.database_turbo import PacketDatabaseRaptorFEC
from digital_turbo_fec.raptor_aggregator import RaptorRSAggregator
from digital_turbo_fec.config import RAPTOR_CFG


class TurboFECAggregator(BaseAggregator):
    """SSTV Turbo FEC アグリゲータ (BaseAggregator 準拠)"""

    def __init__(self, log_dir: Optional[str] = None):
        if log_dir is None:
            self.log_dir = os.path.join(_PROJECT_ROOT, config.TEXT_LOG_DIR)
        else:
            self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)
        self.db = PacketDatabaseRaptorFEC(self.log_dir)

    def bits_to_bytes(self, bits_str: str) -> bytes:
        """ビット文字列をバイト列に変換"""
        n = (len(bits_str) // 8) * 8
        if n == 0:
            return b""
        byte_list = []
        for i in range(0, n, 8):
            chunk = bits_str[i:i + 8]
            val = 0
            for b in chunk:
                val = (val << 1) | (1 if b == '1' else 0)
            byte_list.append(val)
        return bytes(byte_list)

    def load_all_logs(self) -> bool:
        """ログディレクトリ内の新規テキストログをDBに同期取り込み"""
        log_files = glob.glob(os.path.join(self.log_dir, f"{config.TEXT_LOG_PREFIX}_*.txt"))
        if not log_files:
            return False

        new_files = [f for f in log_files if not self.db.is_file_imported(os.path.basename(f))]
        if not new_files:
            return True

        for file_path in new_files:
            file_name = os.path.basename(file_path)
            user_id = None
            m = re.search(r'_user_(\d+)', file_name)
            if m:
                try:
                    user_id = int(m.group(1))
                except Exception:
                    pass

            file_packets = []
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    # 純2進数形式のパース
                    if all(c in '01' for c in line):
                        hdr_len = (
                            config.BIT_IMAGE_CRC
                            + config.BIT_TILE_X
                            + config.BIT_TILE_Y
                            + config.BIT_PAYLOAD_LENGTH
                        )
                        if len(line) < hdr_len + 4:
                            continue
                        try:
                            idx = 0
                            img_id = int(line[idx : idx + config.BIT_IMAGE_CRC], 2)
                            idx += config.BIT_IMAGE_CRC
                            tile_x = int(line[idx : idx + config.BIT_TILE_X], 2)
                            idx += config.BIT_TILE_X
                            tile_y = int(line[idx : idx + config.BIT_TILE_Y], 2)
                            idx += config.BIT_TILE_Y
                            payload_len = int(line[idx : idx + config.BIT_PAYLOAD_LENGTH], 2)
                            idx += config.BIT_PAYLOAD_LENGTH

                            payload_bits = line[idx : idx + payload_len * 8]
                            snr_str = line[idx + payload_len * 8 :]
                            snr_val = float(int(snr_str, 2)) if len(snr_str) == 4 else 1.0

                            esi = None
                            pkt_type = 0
                            if tile_x == 0xFF:
                                pkt_type = 1  # リペアパケット
                                if tile_y < 0xFF:
                                    esi = config.TOTAL_TILES + tile_y
                            else:
                                pkt_type = 0  # データパケット
                                esi = tile_y * config.TILE_COUNT_X + tile_x

                            file_packets.append((img_id, tile_x, tile_y, payload_len, payload_bits, snr_val, esi, pkt_type))
                        except Exception:
                            continue

            if file_packets:
                self.db.insert_packets_bulk(file_name, file_packets, user_id=user_id)

        return True

    def process_and_save_images(self, min_tile_ratio: float = 0.0, user_id: Optional[int] = None) -> List[str]:
        """
        DBからパケットを読み出し、多数決 ＆ RaptorMDS消去全再生 ＆ タイル復元を実行して画像を保存。
        戻り値: 保存された画像パスのリスト
        """
        self.load_all_logs()

        image_counts = self.db.get_all_image_ids_with_counts(user_id=user_id)
        if not image_counts:
            return []

        out_dir = os.path.join(_PROJECT_ROOT, config.IMAGE_OUT_DIR)
        os.makedirs(out_dir, exist_ok=True)

        web_static_out = os.path.join(_PROJECT_ROOT, "web_turbo_png", "static", "output")
        if os.path.exists(os.path.dirname(web_static_out)):
            os.makedirs(web_static_out, exist_ok=True)

        saved_paths = []

        for img_id in sorted(image_counts.keys()):
            img_path = self.process_single_image(img_id, target_user_id=user_id)
            if img_path and os.path.exists(img_path):
                saved_paths.append(img_path)

        return saved_paths

    def process_single_image(self, img_id: int, target_user_id: Optional[int] = None) -> str:
        """
        指定された1枚の画像IDに対し、Raptor+RS多段アグリゲータを完全適用して復元画像を保存する。
        """
        tiles_data = self.db.get_packets_for_image(img_id, user_id=target_user_id)
        if not tiles_data:
            return ""

        img_hex = f"{img_id:04X}"
        out_dir = os.path.join(_PROJECT_ROOT, config.IMAGE_OUT_DIR)
        os.makedirs(out_dir, exist_ok=True)
        img_filename = f"restored_ID_{img_hex}.jpg"
        img_out_path = os.path.join(out_dir, img_filename)

        # Web静的出力先
        web_static_out = os.path.join(_PROJECT_ROOT, "web_turbo_png", "static", "output")
        web_out_path = os.path.join(web_static_out, img_filename)

        agg = RaptorRSAggregator(RAPTOR_CFG, total_source_packets=config.TOTAL_TILES)
        from digital_turbo_fec.fec_engine import fast_bit_majority_vote

        for ty in sorted(tiles_data.keys()):
            for tx in sorted(tiles_data[ty].keys()):
                for plen, pkts in tiles_data[ty][tx].items():
                    cand_payloads = []
                    cand_snrs = []
                    last_esi_val = None

                    for p in pkts:
                        pbits, snr_val, fname, uid, imp_at, esi_val, ptype_val = p
                        payload_bytes = self.bits_to_bytes(pbits[:plen * 8])
                        if not payload_bytes:
                            continue
                        cand_payloads.append(payload_bytes)
                        cand_snrs.append(max(0.1, float(snr_val) + 1.0))
                        if esi_val is not None:
                            last_esi_val = int(esi_val)

                        if tx < config.TILE_COUNT_X and ty < config.TILE_COUNT_Y:
                            tile_id = ty * config.TILE_COUNT_X + tx
                            tile_seq = 0
                            tile_num_pkts = 1
                            tile_len = len(payload_bytes) - config.TAG_SIZE if len(payload_bytes) > config.TAG_SIZE else len(payload_bytes)
                            if len(payload_bytes) >= 5:
                                try:
                                    _, t_s, t_np, t_l = struct.unpack(">BBBH", payload_bytes[:5])
                                    if 1 <= t_np <= 8 and 1 <= t_l <= config.RS_DATA_CHUNK_SIZE * t_np:
                                        tile_seq = t_s
                                        tile_num_pkts = t_np
                                        tile_len = t_l
                                except Exception:
                                    pass

                            pkt_info = {
                                "is_valid": True,
                                "corrected_bytes": 0,
                                "packet_id": tile_id,
                                "img_id": img_id,
                                "pkt_type": config.TYPE_DATA if hasattr(config, "TYPE_DATA") else 0,
                                "esi": tile_id,
                                "tile_id": tile_id,
                                "tile_seq": tile_seq,
                                "tile_num_pkts": tile_num_pkts,
                                "tile_data_len": tile_len,
                                "payload": payload_bytes,
                                "raw_rs_block": payload_bytes + b"\x00" * config.RS_NSYM,
                                "snr_db": float(snr_val)
                            }
                            agg.add_packet(pkt_info)

                    if not cand_payloads:
                        continue

                    # 同一座標または同一リペアESIに対して複数局の受信がある場合、SNR重み付けビット多数決で最適シンボルを確定
                    if len(cand_payloads) >= 2:
                        voted_bytes = fast_bit_majority_vote(cand_payloads, weights=cand_snrs)
                    else:
                        voted_bytes = cand_payloads[0]
                    best_snr = float(max(cand_snrs))

                    if tx < config.TILE_COUNT_X and ty < config.TILE_COUNT_Y:
                        tile_id = ty * config.TILE_COUNT_X + tx
                        agg.raptor_symbols[tile_id] = voted_bytes
                    elif tx == 0xFF:
                        rep_esi = None
                        if ty < 0xFF:
                            rep_esi = config.TOTAL_TILES + ty
                        elif last_esi_val is not None and last_esi_val >= config.TOTAL_TILES:
                            rep_esi = last_esi_val

                        if rep_esi is not None:
                            pkt_info = {
                                "is_valid": True,
                                "corrected_bytes": 0,
                                "packet_id": rep_esi,
                                "img_id": img_id,
                                "pkt_type": 1,
                                "esi": rep_esi,
                                "tile_id": 0xFF,
                                "tile_seq": -1,
                                "tile_num_pkts": -1,
                                "tile_data_len": -1,
                                "payload": voted_bytes,
                                "raw_rs_block": voted_bytes + b"\x00" * config.RS_NSYM,
                                "snr_db": best_snr
                            }
                            agg.add_packet(pkt_info)

        # 第4防壁: ラプター消去復元
        agg.run_raptor_recovery()

        # 第5防壁: RSTスキップ補間
        agg.finalize_tiles()

        # 全体統合時は確定・ラプター復元済みタイルを DB の finalized_tiles テーブルへ同期保存
        if target_user_id is None:
            finalized_rows = []
            for esi_idx in range(config.TOTAL_TILES):
                tx = esi_idx % config.TILE_COUNT_X
                ty = esi_idx // config.TILE_COUNT_X
                if (tx, ty) in agg.rendered_tiles and esi_idx in agg.raptor_symbols:
                    is_direct = (ty in tiles_data and tx in tiles_data[ty])
                    stage_label = "DIRECT_RS" if is_direct else "RAPTOR_MDS"
                    finalized_rows.append((tx, ty, agg.raptor_symbols[esi_idx], stage_label))
            if finalized_rows:
                try:
                    self.db.upsert_finalized_tiles(img_id, finalized_rows)
                except Exception:
                    pass

        # 保存
        agg.save_canvas_image(img_out_path)

        if os.path.exists(web_static_out):
            try:
                shutil.copy2(img_out_path, web_out_path)
            except Exception:
                pass

        return img_out_path

    def decode_tile_bytes_safely(self, p_bytes: bytes, tile_w: int = 16, tile_h: int = 16) -> Optional[Image.Image]:
        """
        RSTマーカー間スキップ復元 ＆ ヘッダ自動補間安全デコーダ (第5防壁)
        自己完結タグ(5B)やヘッダ省略エントロピーデータから、ダミー空MCU補間を駆使して安全にタイル画像を復元する。
        """
        if not p_bytes or len(p_bytes) < 4:
            return None

        raw_payload = bytes(p_bytes)
        # 自己完結タグ (5B: tile_id, seq, num_pkts, t_len) が含まれているかチェック
        tag_size = getattr(config, "TAG_SIZE", 5)
        if len(raw_payload) > tag_size:
            chunk_data = raw_payload[tag_size:]
            try:
                t_id, seq, num_pkts, t_len = struct.unpack(">BBBH", raw_payload[:tag_size])
            except Exception:
                seq = 0
                num_pkts = 1
                t_len = len(chunk_data)
        else:
            chunk_data = raw_payload
            seq = 0
            num_pkts = 1
            t_len = len(chunk_data)

        # 1. 直接 JPEG として開ける場合
        try:
            target_data = chunk_data if chunk_data.startswith(b'\xff\xd8') else raw_payload
            im = Image.open(io.BytesIO(target_data)).convert("RGB")
            im.load()
            return im
        except Exception:
            pass

        # 2. RaptorRSAggregator の RSTスキップ補間 (reconstruct_with_rst_skip)
        try:
            agg = RaptorRSAggregator(RAPTOR_CFG, total_source_packets=getattr(config, "TOTAL_TILES", 256))
            res = agg.reconstruct_with_rst_skip({seq: chunk_data}, num_pkts=num_pkts, t_len=t_len)
            if res is not None:
                tile_arr, valid_ratio, _ = res
                return Image.fromarray(tile_arr)
        except Exception:
            pass

        return None

    def reset_database(self) -> None:
        """データベースのリセット"""
        self.db.clear_imported_files()
        with self.db.conn:
            self.db.conn.execute("DELETE FROM packets")
            self.db.conn.execute("DELETE FROM finalized_tiles")

    def close(self) -> None:
        """データベース接続クローズ"""
        self.db.close()
