import sys
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

import os
import glob
import re
import numpy as np
from PIL import Image, ImageFile, ImageFilter
import io
from collections import defaultdict

# 破損・途切れ・ノイズ混じりJPEGでも例外で捨てずに部分描画する設定
setattr(ImageFile, 'LOAD_TRUNCATED_IMAGES', True)

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

from digital_turbo_jpeg import config_turbo as config
from dataclasses import dataclass
from digital_turbo_jpeg.database_turbo import PacketDatabaseTurboJPEG
from core.base_interfaces import BaseAggregator


@dataclass
class ParsedJPEGPacket:
    header: bytes
    segments: list[bytes]
    markers: list[int]
    has_eoi: bool
    snr: float
    raw_bytes: bytes | bytearray


class TurboJPEGAggregator(BaseAggregator):
    """SSTV Turbo JPEG アグリゲータ (BaseAggregator 準拠・超耐ノイズ仕様)
    ノイズや欠損のあるパケットから、ビット多数決・ピクセル領域SNR加重平均・欠損インペインティングを駆使して極限まで高品質に画像を復元する。
    """
    def __init__(self, log_dir=None):
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
        if log_dir is None:
            self.log_dir = os.path.join(root_dir, getattr(config, "TEXT_LOG_DIR", "data/digital_turbo_jpeg/logs"))
        else:
            self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)
        self.db = PacketDatabaseTurboJPEG(self.log_dir)
        self._header_templates = {}

    def load_all_logs(self) -> bool:
        log_files = glob.glob(os.path.join(self.log_dir, f"{config.TEXT_LOG_PREFIX}_*.txt"))
        if not log_files:
            return False

        new_files = [f for f in log_files if not self.db.is_file_imported(os.path.basename(f))]
        if not new_files:
            return True

        for file_path in new_files:
            file_name = os.path.basename(file_path)
            user_id = None
            m = re.search(r'_user_(\d+)_', file_name)
            if m:
                user_id = int(m.group(1))

            file_packets = []
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    if all(c in '01' for c in line):
                        try:
                            hdr = (config.BIT_IMAGE_CRC + config.BIT_TILE_X
                                   + config.BIT_TILE_Y + config.BIT_PAYLOAD_LENGTH)
                            if len(line) < hdr + 4:
                                continue
                            idx = 0
                            img_id         = int(line[idx : idx + config.BIT_IMAGE_CRC], 2);  idx += config.BIT_IMAGE_CRC
                            tile_x         = int(line[idx : idx + config.BIT_TILE_X],    2);  idx += config.BIT_TILE_X
                            tile_y         = int(line[idx : idx + config.BIT_TILE_Y],    2);  idx += config.BIT_TILE_Y
                            payload_length = int(line[idx : idx + config.BIT_PAYLOAD_LENGTH], 2); idx += config.BIT_PAYLOAD_LENGTH
                            payload_bits   = line[idx : idx + payload_length * 8]
                            snr_str        = line[idx + payload_length * 8 :]
                            snr_val        = float(int(snr_str, 2)) if len(snr_str) == 4 else 1.0
                            file_packets.append((img_id, tile_x, tile_y, payload_length, payload_bits, snr_val))
                        except (ValueError, IndexError):
                            continue
                    else:
                        parts = line.split(",")
                        if len(parts) >= 6:
                            try:
                                img_id         = int(parts[0])
                                tile_x         = int(parts[1])
                                tile_y         = int(parts[2])
                                payload_length = int(parts[3])
                                payload_bits   = parts[4]
                                snr_str        = parts[5].strip()
                                snr_val = float(int(snr_str, 2)) if (all(c in '01' for c in snr_str) and len(snr_str) == 4) else float(snr_str)
                                file_packets.append((img_id, tile_x, tile_y, payload_length, payload_bits, snr_val))
                            except ValueError:
                                continue

            if file_packets:
                self.db.insert_packets_bulk(file_name, file_packets, user_id=user_id)
        return True

    def bits_to_bytearray(self, bits_str):
        """NumPy ベクトル化: ビット文字列を一括でバイト配列に変換"""
        n = (len(bits_str) // 8) * 8
        if n == 0:
            return bytearray()
        bit_arr = np.frombuffer(bits_str[:n].encode('ascii'), dtype=np.uint8) - ord('0')
        byte_weights = np.array([128, 64, 32, 16, 8, 4, 2, 1], dtype=np.uint8)
        byte_arr = bit_arr.reshape(-1, 8) @ byte_weights
        return bytearray(byte_arr.astype(np.uint8).tobytes())

    def bit_majority_vote(self, packets, payload_bit_len):
        """複数パケットのビットごとのSNR加重多数決により、ビット誤りを消滅させる"""
        if not packets:
            return ""
        if len(packets) == 1:
            return packets[0][0][:payload_bit_len]

        vote_sums = np.zeros(payload_bit_len, dtype=np.float64)
        total_weights = 0.0

        for item in packets:
            p_str = item[0]
            snr = item[1]
            if len(p_str) < payload_bit_len:
                continue
            weight = snr + 1.0
            bits = (np.frombuffer(p_str[:payload_bit_len].encode('ascii'), dtype=np.uint8) - ord('0')).astype(np.float64)
            vote_sums += (bits * 2.0 - 1.0) * weight
            total_weights += weight

        voted_bits = (vote_sums >= 0).astype(np.uint8)
        return "".join(str(b) for b in voted_bits)

    def split_jpeg_into_segments(self, p_bytes, tile_w=None, tile_h=None):
        """JPEGバイト列を解析し、ヘッダ(SOI〜SOS)、各RST区間データ(セグメント)、マーカー、EOI有無に分割。
        ヘッダ破損時でもテンプレート補正により区間分割を試行する。"""
        if not p_bytes or len(p_bytes) < 4:
            return None
        raw = bytes(p_bytes)
        soi_idx = raw.find(b'\xff\xd8')
        sos_idx = raw.find(b'\xff\xda', (soi_idx + 2) if soi_idx != -1 else 0)
        
        header_bytes = None
        header_end = -1

        if soi_idx != -1 and sos_idx != -1 and sos_idx + 4 <= len(raw):
            sos_len = (raw[sos_idx + 2] << 8) | raw[sos_idx + 3]
            header_end = sos_idx + 2 + sos_len
            if header_end <= len(raw):
                header_bytes = raw[soi_idx:header_end]

        # ヘッダが壊れている場合、テンプレートを試行
        if header_bytes is None and tile_w and tile_h:
            tmpl_hdr, tmpl_off = self._get_jpeg_header_template(tile_w, tile_h)
            if tmpl_hdr and len(raw) > tmpl_off:
                header_bytes = tmpl_hdr
                header_end = tmpl_off

        if header_bytes is None or header_end < 0:
            return None

        segments = []
        rst_markers = []
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

    def rst_aligned_majority_vote(self, packets, best_plen, cur_tile_w, cur_tile_h):
        """リスタートマーカー (0xFF 0xD0〜0xD7) 単位でセグメント分割し、区間ごとの独立アライメント多数決でJPEGを再構築。
        完全復元に成功した場合は (perfect_img, rebuilt_bytes) を返す。"""
        if len(packets) < 2:
            return None

        parsed_packets: list[ParsedJPEGPacket] = []
        for row in packets:
            p_str = row[0]
            snr = float(row[1])
            if len(p_str) < best_plen * 8:
                continue
            p_bytes = self.bits_to_bytearray(p_str[:best_plen * 8])
            parsed = self.split_jpeg_into_segments(p_bytes, cur_tile_w, cur_tile_h)
            if parsed is not None:
                header_bytes, segments, rst_markers, has_eoi = parsed
                if len(segments) > 1:
                    parsed_packets.append(ParsedJPEGPacket(
                        header=header_bytes,
                        segments=segments,
                        markers=rst_markers,
                        has_eoi=has_eoi,
                        snr=snr,
                        raw_bytes=bytes(p_bytes)
                    ))

        if len(parsed_packets) < 2:
            return None

        # タイルサイズ 16*16 の場合、RSTマーカーは3個でセグメント数は4区間に分割される
        expected_seg_count = 4 if (cur_tile_w == 16 and cur_tile_h == 16) else None
        
        # 期待セグメント数を持つパケットがあれば最優先、なければ最頻出セグメント数
        target_packets = []
        if expected_seg_count:
            target_packets = [p for p in parsed_packets if len(p.segments) == expected_seg_count]
        
        if not target_packets:
            seg_count_freq: dict[int, int] = defaultdict(int)
            for p in parsed_packets:
                seg_count_freq[len(p.segments)] += 1
            target_seg_count = int(max(seg_count_freq.keys(), key=lambda k: seg_count_freq[k]))
            target_packets = [p for p in parsed_packets if len(p.segments) == target_seg_count]
        else:
            target_seg_count = int(expected_seg_count)

        if not target_packets or target_seg_count <= 0:
            return None

        best_header_packet = max(target_packets, key=lambda p: p.snr)
        
        # 各セグメント区間ごとに、多数決セグメントおよび各パケットの候補セグメントを収集
        seg_candidates: list[list[bytes]] = []
        majority_segments: list[bytes] = []

        for seg_idx in range(target_seg_count):
            candidates: list[tuple[bytes, float]] = []
            for p in target_packets:
                if seg_idx < len(p.segments):
                    candidates.append((p.segments[seg_idx], p.snr))

            if not candidates:
                return None

            # 長さごとの候補グループ
            len_groups: dict[int, list[tuple[bytes, float]]] = defaultdict(list)
            for c_bytes, c_snr in candidates:
                len_groups[len(c_bytes)].append((c_bytes, c_snr))

            best_len = max(len_groups.keys(), key=lambda l: sum(s + 1.0 for _, s in len_groups[l]))
            same_len_cand = len_groups[best_len]

            cur_seg_cand_list: list[bytes] = []

            # 1. 同一長セグメント間でSNR加重ビット多数決
            if len(same_len_cand) >= 2:
                bit_len = best_len * 8
                vote_sums = np.zeros(bit_len, dtype=np.float64)
                for c_bytes, c_snr in same_len_cand:
                    w = c_snr + 1.0
                    b_bits = np.unpackbits(np.frombuffer(c_bytes, dtype=np.uint8))
                    vote_sums += (b_bits.astype(np.float64) * 2.0 - 1.0) * w
                voted_bits = (vote_sums >= 0).astype(np.uint8)
                voted_seg_bytes = np.packbits(voted_bits).tobytes()
                majority_segments.append(voted_seg_bytes)
                cur_seg_cand_list.append(voted_seg_bytes)
            else:
                best_cand = max(same_len_cand, key=lambda x: x[1])
                majority_segments.append(best_cand[0])
                cur_seg_cand_list.append(best_cand[0])

            # 2. 各パケットの生セグメント（SNR順、重複除去）も最良データ候補として追加
            for c_bytes, _ in sorted(candidates, key=lambda x: x[1], reverse=True):
                if c_bytes not in cur_seg_cand_list:
                    cur_seg_cand_list.append(c_bytes)
                if len(cur_seg_cand_list) >= 3:
                    break

            seg_candidates.append(cur_seg_cand_list)

        # マーカー列の確定
        markers_to_use = []
        for idx in range(target_seg_count - 1):
            if idx < len(best_header_packet.markers):
                markers_to_use.append(best_header_packet.markers[idx])
            else:
                markers_to_use.append(0xD0 + (idx % 8))

        def assemble_tile(segs):
            buf = bytearray(best_header_packet.header)
            for i, s in enumerate(segs):
                buf.extend(s)
                if i < len(markers_to_use):
                    buf.extend(bytes([0xFF, markers_to_use[i]]))
            buf.extend(b'\xff\xd9')
            return bytes(buf)

        # 試行1: 多数決セグメントでの合体
        rebuilt_maj_bytes = assemble_tile(majority_segments)
        perf_img = self.is_perfect_jpeg_tile(rebuilt_maj_bytes, cur_tile_w, cur_tile_h)
        if perf_img is not None:
            return perf_img, rebuilt_maj_bytes

        # 試行2: 各区間の最良データ（SNR順）候補の組み合わせ合体探索
        import itertools
        for combo in itertools.product(*seg_candidates):
            rebuilt_combo_bytes = assemble_tile(combo)
            if rebuilt_combo_bytes == rebuilt_maj_bytes:
                continue
            perf_img = self.is_perfect_jpeg_tile(rebuilt_combo_bytes, cur_tile_w, cur_tile_h)
            if perf_img is not None:
                return perf_img, rebuilt_combo_bytes

        return None

    def _get_jpeg_header_template(self, tile_w, tile_h):
        """指定タイルサイズ用の正常な JPEG ヘッダテンプレート (SOI〜SOS) をキャッシュ"""
        key = (tile_w, tile_h)
        if key not in self._header_templates:
            dummy = Image.new("RGB", (tile_w, tile_h), color=(0, 0, 0))
            bio = io.BytesIO()
            restart_int = getattr(config, "JPEG_RESTART_MARKER", 1)
            dummy.save(bio, format="JPEG", quality=config.JPEG_QUALITY, restart_marker_blocks=restart_int, subsampling=0)
            raw = bio.getvalue()
            sos_pos = raw.find(b'\xff\xda')
            if sos_pos != -1:
                sos_len = (raw[sos_pos+2] << 8) | raw[sos_pos+3]
                self._header_templates[key] = (raw[:sos_pos + 2 + sos_len], sos_pos + 2 + sos_len)
            else:
                self._header_templates[key] = (None, 0)
        return self._header_templates[key]

    def decode_tile_bytes_safely(self, p_bytes, tile_w=None, tile_h=None):
        """JPEGバイト列を安全にデコード（ノイズ・破損・RSTマーカー付きでも部分描画を徹底試行）"""
        if not p_bytes or len(p_bytes) < 4:
            return None

        # 1. そのまま一度デコードを試みる
        try:
            tile_img = Image.open(io.BytesIO(p_bytes)).convert("RGB")
            tile_img.load()
            return tile_img
        except Exception:
            pass

        # 2. SOI (0xFF 0xD8) の位置を探索して位置補正 ＋ 末尾 (EOI: 0xFF 0xD9) 修復
        raw_bytes = bytes(p_bytes)
        soi_idx = raw_bytes.find(b'\xff\xd8')
        if soi_idx != -1:
            fixed_bytes = bytearray(raw_bytes[soi_idx:])
        else:
            fixed_bytes = bytearray(b'\xff\xd8' + raw_bytes)

        if not fixed_bytes.endswith(b'\xff\xd9'):
            fixed_bytes.extend(b'\xff\xd9')

        try:
            tile_img = Image.open(io.BytesIO(fixed_bytes)).convert("RGB")
            tile_img.load()
            return tile_img
        except Exception:
            pass

        # 3. 高度な修復: ヘッダ全壊時、正常な JPEG 構造テンプレート (DQT/DHT/SOF/SOS) で差し替え
        if tile_w and tile_h:
            try:
                hdr_tmpl, sos_off = self._get_jpeg_header_template(tile_w, tile_h)
                if hdr_tmpl and len(p_bytes) > sos_off:
                    repaired_bytes = bytearray(hdr_tmpl + p_bytes[sos_off:])
                    if not repaired_bytes.endswith(b'\xff\xd9'):
                        repaired_bytes.extend(b'\xff\xd9')
                    tile_img = Image.open(io.BytesIO(repaired_bytes)).convert("RGB")
                    tile_img.load()
                    return tile_img
            except Exception:
                pass

        return None

    def is_perfect_jpeg_tile(self, p_bytes, tile_w, tile_h):
        """1箇所の破損もなく完全・無傷に開けたJPEGタイルであるかを厳密に判定"""
        if not p_bytes or len(p_bytes) < 4:
            return None
        raw_bytes = bytes(p_bytes).rstrip(b'\x00')
        # SOI (0xFFD8) で始まり EOI (0xFFD9) で終わる完全な構造であるか
        if not (raw_bytes.startswith(b'\xff\xd8') and raw_bytes.endswith(b'\xff\xd9')):
            return None

        sos_idx = raw_bytes.find(b'\xff\xda')
        if sos_idx == -1:
            return None
        sos_len = (raw_bytes[sos_idx+2] << 8) | raw_bytes[sos_idx+3]
        entropy_start = sos_idx + 2 + sos_len
        if entropy_start > len(raw_bytes):
            return None

        # スキャンデータ内の不正マーカー（0xFFスタッフィング違反）を厳密検査
        idx = entropy_start
        n = len(raw_bytes)
        rst_markers = []
        while idx < n:
            if raw_bytes[idx] == 0xFF:
                if idx + 1 >= n:
                    return None
                m = raw_bytes[idx+1]
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
                    return None  # ハフマンデータ破損による不正マーカー
            else:
                idx += 1

        # 16x16 タイル（RSTマーカー3個＝4区間）の場合、マーカー構造の完全性を厳密検証
        if tile_w == 16 and tile_h == 16:
            if rst_markers != [0xD0, 0xD1, 0xD2]:
                return None

        old_truncated: bool = bool(getattr(ImageFile, 'LOAD_TRUNCATED_IMAGES', True))
        try:
            setattr(ImageFile, 'LOAD_TRUNCATED_IMAGES', False)
            tile_img = Image.open(io.BytesIO(raw_bytes))
            tile_img.load()
            tile_rgb = tile_img.convert("RGB")
            if tile_rgb.size != (tile_w, tile_h):
                tile_rgb = tile_rgb.resize((tile_w, tile_h))

            tile_arr = np.array(tile_rgb, dtype=np.float32)

            # 1. 最下部の黒落ち判定
            bottom_strip = tile_arr[-min(4, tile_h):, :]
            if np.max(bottom_strip) < 3.0 and np.mean(tile_arr) > 10.0:
                return None

            # 2. 未復号グレー落ち (128, 128, 128) の検出
            gray_pixels = (np.abs(tile_arr[:, :, 0] - 128.0) < 1.0) & \
                          (np.abs(tile_arr[:, :, 1] - 128.0) < 1.0) & \
                          (np.abs(tile_arr[:, :, 2] - 128.0) < 1.0)
            if np.sum(gray_pixels) > 4:
                return None

            # 3. 極端な高周波チェッカーボード破損ノイズの検出
            diff_y = np.abs(np.diff(tile_arr, axis=0))
            diff_x = np.abs(np.diff(tile_arr, axis=1))
            if np.max(diff_y) > 235.0 or np.max(diff_x) > 235.0:
                # 隣接画素が0と240等で極端に破綻している
                return None

            return tile_rgb
        except Exception:
            return None
        finally:
            setattr(ImageFile, 'LOAD_TRUNCATED_IMAGES', old_truncated)

    def resolve_tile(self, packets, best_plen, cur_tile_w, cur_tile_h):
        """
        ユーザー要件のフローチャート（No.1〜No.5）に従い、対象座標のタイル画像を多段階復元する。

        No.1 受信タイル単体での無傷チェック・即時採用
             届いたオリジナルのパケットの中に、最初から100%完全な無傷JPEGがあるかを検査。
             (完全なパケットが1つでもあれば、他と混ぜずに即座に描画確定して終了。失敗すればNo.2へ)

        No.2 全ビット加重多数決
             複数タイルのビット列全体で加重多数決を行い、ノイズを相殺して完全なJPEGの復元を試みる。
             (完璧に復元できれば描画確定して終了。これ以降の同x,y座標では多数決を実施しない。失敗すればNo.3へ)

        No.3 RSTマーカ区間分割による再構築・多数決
             各パケットをRSTマーカー単位で区間分割し、区間ごとに多数決や最良データを抽出。
             それらを繋ぎ直して1枚のJPEGタイルを再構築（合体）する。
             (再構築したタイル全体が完璧に復元できれば描画確定して終了。失敗すればNo.4へ)

             ※No.2およびNo.3は、データベースに対象座標のパケットが複数存在するときのみ実行。
             　パケットが1つしかない場合は、No.2とNo.3をスキップしてNo.4へ進む。

        No.4 最高SNRタイルのクリア単体採用
             有効画素率（黒落ちしていない面積）が85%以上のタイルがあれば、変色ノイズを防ぐため他と混ぜずに
             電波（SNR）が最も良かった1枚をそのまま単体採用して終了。
             (※ただし100%完全復元ではないため、次回以降に新しいパケットが届いた際は再びNo.1から再挑戦される)

        No.5 RGBピクセル空間での加重平均合成
             すべてのタイルが有効画素率85%未満（半分以上真っ黒など）の場合、同座標データベースにある各タイルを
             画像展開し、生き残っているピクセル同士を画面上で半透明合成（SNR加重平均）して合作する。
             (また、そのタイルのパケットが1つしか存在しない場合(初受信の1枚だけの場合)は、
              有効画素率にかかわらずここでその1枚を描画して処理を終了する。)
             (※ただし100%完全復元ではないため、次回以降に新しいパケットが届いた際は再びNo.1から再挑戦される)

        戻り値: (tile_img, final_bytes, stage_name)
          - tile_img: 復元された PIL Image (RGB)
          - final_bytes: 完全復元時 (No.1〜No.3) のみ bytes、それ以外 (No.4, No.5) は None
          - stage_name: "NO1", "NO2", "NO3", "NO4", "NO5"
        """
        if not packets:
            return None, None, None

        payload_bit_len = best_plen * 8
        sorted_packets = sorted(packets, key=lambda p: p[1], reverse=True)

        # パケットごとのビット列出現頻度（同一パケットの受信重複チェック）
        bit_counts = defaultdict(int)
        for row in sorted_packets:
            bit_counts[row[0][:payload_bit_len]] += 1

        # =========================================================================
        # No.1 受信タイル単体での無傷チェック・即時採用
        # 届いたオリジナルのパケットの中に、最初から100%完全な無傷JPEGがあるかを検査。
        # (完全なパケットが1つでもあれば、他と混ぜずに即座に描画確定して終了。失敗すればNo.2へ)
        # =========================================================================
        for row in sorted_packets:
            payload_bits_str = row[0]
            if len(payload_bits_str) >= payload_bit_len:
                p_str = payload_bits_str[:payload_bit_len]
                p_bytes = self.bits_to_bytearray(p_str)
                chk_img = self.is_perfect_jpeg_tile(p_bytes, cur_tile_w, cur_tile_h)
                if chk_img is not None:
                    return chk_img, bytes(p_bytes), "NO1"

        # ※No.2およびNo.3は、データベースに対象座標のパケットが複数存在するときのみ実行。
        # 　パケットが1つしかない場合は、No.2とNo.3をスキップしてNo.4へ進む。
        if len(packets) > 1:
            # =========================================================================
            # No.2 全ビット加重多数決
            # 複数タイルのビット列全体で加重多数決を行い、ノイズを相殺して完全なJPEGの復元を試みる。
            # (完璧に復元できれば描画確定して終了。これ以降の同x,y座標では多数決を実施しない。失敗すればNo.3へ)
            # =========================================================================
            voted_bits_str = self.bit_majority_vote(packets, payload_bit_len)
            v_bytes = self.bits_to_bytearray(voted_bits_str)
            chk_img = self.is_perfect_jpeg_tile(v_bytes, cur_tile_w, cur_tile_h)
            if chk_img is not None:
                return chk_img, bytes(v_bytes), "NO2"

            # =========================================================================
            # No.3 RSTマーカ区間分割による再構築・多数決
            # 各パケットをRSTマーカー単位で区間分割し、区間ごとに多数決や最良データを抽出。
            # それらを繋ぎ直して1枚のJPEGタイルを再構築（合体）する。
            # (再構築したタイル全体が完璧に復元できれば描画確定して終了。失敗すればNo.4へ)
            # =========================================================================
            rst_res = self.rst_aligned_majority_vote(packets, best_plen, cur_tile_w, cur_tile_h)
            if rst_res is not None:
                chk_img, rst_bytes = rst_res
                return chk_img, rst_bytes, "NO3"

        # =========================================================================
        # No.4 最高SNRタイルのクリア単体採用
        # 有効画素率（黒落ちしていない面積）が85%以上のタイルがあれば、変色ノイズを防ぐため
        # 他と混ぜずに電波（SNR）が最も良かった1枚をそのまま単体採用して終了。
        # (※ただし100%完全復元ではないため、次回以降に新しいパケットが届いた際は再びNo.1から再挑戦される)
        # =========================================================================
        for row in sorted_packets:
            payload_bits_str = row[0]
            p_slice = payload_bits_str[:payload_bit_len] if len(payload_bits_str) >= payload_bit_len else payload_bits_str
            p_bytes = self.bits_to_bytearray(p_slice)
            cand_img = self.decode_tile_bytes_safely(p_bytes, cur_tile_w, cur_tile_h)
            if cand_img is not None:
                cand_arr = np.array(cand_img.resize((cur_tile_w, cur_tile_h)), dtype=np.float32)
                valid_ratio = np.mean(np.sum(cand_arr, axis=2) > 3.0)
                if valid_ratio >= 0.85:
                    return cand_img, None, "NO4"

        # =========================================================================
        # No.5 RGBピクセル空間での加重平均合成
        # =========================================================================
        # また、そのタイルのパケットが1つしか存在しない場合(初受信の1枚だけの場合)は、
        # 有効画素率にかかわらずここでその1枚を描画して処理を終了する。
        if len(packets) == 1:
            p_bits = sorted_packets[0][0]
            p_slice = p_bits[:payload_bit_len] if len(p_bits) >= payload_bit_len else p_bits
            p_bytes = self.bits_to_bytearray(p_slice)
            one_img = self.decode_tile_bytes_safely(p_bytes, cur_tile_w, cur_tile_h)
            if one_img is not None:
                return one_img, None, "NO5"

        # すべてのタイルが有効画素率85%未満の場合:
        # 同座標データベースにある各タイルを画像展開し、生き残っているピクセル同士を画面上で半透明合成（SNR加重平均）
        pixel_sum = np.zeros((cur_tile_h, cur_tile_w, 3), dtype=np.float64)
        weight_sum = np.zeros((cur_tile_h, cur_tile_w, 1), dtype=np.float64)
        decoded_count = 0

        for row in sorted_packets:
            payload_bits_str = row[0]
            snr_val = float(row[1])
            p_slice = payload_bits_str[:payload_bit_len] if len(payload_bits_str) >= payload_bit_len else payload_bits_str
            p_bytes = self.bits_to_bytearray(p_slice)
            tile_img = self.decode_tile_bytes_safely(p_bytes, cur_tile_w, cur_tile_h)
            if tile_img is None:
                continue

            tile_arr = np.array(tile_img.resize((cur_tile_w, cur_tile_h)), dtype=np.float64)
            weight = snr_val + 1.0

            # 生き残っているピクセル（黒落ちしていない面積）のみをマスクして加算
            pixel_brightness = np.sum(tile_arr, axis=2, keepdims=True)
            valid_mask = (pixel_brightness > 3.0).astype(np.float64)

            pixel_sum += tile_arr * weight * valid_mask
            weight_sum += weight * valid_mask
            decoded_count += 1

        if decoded_count > 0:
            safe_weight = np.where(weight_sum > 0, weight_sum, 1.0)
            averaged_pixels = (pixel_sum / safe_weight).clip(0, 255).astype(np.uint8)
            res_img = Image.fromarray(averaged_pixels)
            return res_img, None, "NO5"

        # フォールバック: 最高SNRタイルの安全デコード
        for row in sorted_packets:
            p_bytes = self.bits_to_bytearray(row[0][:payload_bit_len])
            fallback_img = self.decode_tile_bytes_safely(p_bytes, cur_tile_w, cur_tile_h)
            if fallback_img is not None:
                return fallback_img, None, "NO5_FALLBACK"

        return None, None, None

    def inpaint_missing_tiles(self, canvas, tile_count_x, tile_count_y, tile_w, tile_h, rendered_mask):
        """受信できなかった欠損タイルを、周囲の正常タイルの色からスマート補間"""
        canvas_arr = np.array(canvas, dtype=np.float64)

        for ty in range(tile_count_y):
            for tx in range(tile_count_x):
                if rendered_mask[ty, tx]:
                    continue  # 正常に描画済み

                # 周囲（上下左右）の正常タイルから平均色を算出
                neighbor_colors = []
                # 上
                if ty > 0 and rendered_mask[ty - 1, tx]:
                    neighbor_colors.append(np.mean(canvas_arr[(ty-1)*tile_h : ty*tile_h, tx*tile_w : (tx+1)*tile_w], axis=(0,1)))
                # 下
                if ty < tile_count_y - 1 and rendered_mask[ty + 1, tx]:
                    neighbor_colors.append(np.mean(canvas_arr[(ty+1)*tile_h : (ty+2)*tile_h, tx*tile_w : (tx+1)*tile_w], axis=(0,1)))
                # 左
                if tx > 0 and rendered_mask[ty, tx - 1]:
                    neighbor_colors.append(np.mean(canvas_arr[ty*tile_h : (ty+1)*tile_h, (tx-1)*tile_w : tx*tile_w], axis=(0,1)))
                # 右
                if tx < tile_count_x - 1 and rendered_mask[ty, tx + 1]:
                    neighbor_colors.append(np.mean(canvas_arr[ty*tile_h : (ty+1)*tile_h, (tx+1)*tile_w : (tx+2)*tile_w], axis=(0,1)))

                if neighbor_colors:
                    fill_color = np.mean(neighbor_colors, axis=0)
                    y1 = ty * tile_h
                    y2 = min(canvas_arr.shape[0], (ty + 1) * tile_h)
                    x1 = tx * tile_w
                    x2 = min(canvas_arr.shape[1], (tx + 1) * tile_w)
                    canvas_arr[y1:y2, x1:x2] = fill_color

        return Image.fromarray(canvas_arr.astype(np.uint8))

    def reset_database(self):
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
        db_path = os.path.join(self.log_dir, "sstv_packets_turbo_jpeg.db")
        self.db.close()
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except Exception:
                pass
        self.db = PacketDatabaseTurboJPEG(self.log_dir)

    def process_and_save_images(self, min_tile_ratio=0.0, user_id=None) -> list[str]:
        """テキストログをDBに蓄積し、完全タイル即時確定 ＋ ビット多数決 ＋ ピクセル領域SNR加重平均 ＋ インペインティングで画像を復元"""
        self.load_all_logs()

        image_counts = self.db.get_all_image_ids_with_counts(user_id=user_id)
        if not image_counts:
            return []

        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
        output_dir = os.path.join(root_dir, getattr(config, "IMAGE_OUT_DIR", "data/digital_turbo_jpeg/images"))
        os.makedirs(output_dir, exist_ok=True)
        saved_files = []

        for main_id, count in sorted(image_counts.items(), key=lambda x: x[1], reverse=True):
            if main_id is None:
                continue

            tiles_data = self.db.get_packets_for_image(main_id, user_id=user_id)
            if not tiles_data:
                continue

            # タイル構造の自動判定
            all_ty = [ty for ty in tiles_data.keys() if ty < 32]
            if not all_ty:
                continue
            max_ty = max(all_ty)
            max_tx = 0
            for ty in all_ty:
                tx_list = [tx for tx in tiles_data[ty].keys() if tx < 32]
                if tx_list:
                    max_tx = max(max_tx, max(tx_list))

            # パケットから実際のタイルサイズを判定
            detected_tile_w: int = int(getattr(config, "TILE_SIZE", 16))
            detected_tile_h: int = int(getattr(config, "TILE_SIZE", 16))
            found_size = False
            for ty in all_ty:
                for tx in tiles_data[ty]:
                    for plen, pkts in tiles_data[ty][tx].items():
                        for p in pkts:
                            p_bits = p[0]
                            if len(p_bits) >= plen * 8:
                                p_bytes = self.bits_to_bytearray(p_bits[:plen * 8])
                                t_img = self.decode_tile_bytes_safely(p_bytes)
                                if t_img:
                                    detected_tile_w, detected_tile_h = t_img.size
                                    found_size = True
                                    break
                        if found_size:
                            break
                    if found_size:
                        break
                if found_size:
                    break

            canvas_w = int(config.WIDTH)
            canvas_h = int(config.HEIGHT)
            tile_count_x = max(1, canvas_w // detected_tile_w)
            tile_count_y = max(1, canvas_h // detected_tile_h)
            canvas = Image.new("RGB", (canvas_w, canvas_h), color="black")
            rendered_mask = np.zeros((tile_count_y, tile_count_x), dtype=bool)

            # DBから完全復元確定済みタイルを読み出し (これ以降の同x,y座標では多数決を実施しない)
            finalized_tiles = self.db.get_finalized_tiles(main_id) if not user_id else {}

            for ty in range(tile_count_y):
                for tx in range(tile_count_x):
                    cur_tile_w: int = min(detected_tile_w, canvas_w - tx * detected_tile_w)
                    cur_tile_h: int = min(detected_tile_h, canvas_h - ty * detected_tile_h)
                    if cur_tile_w <= 0 or cur_tile_h <= 0:
                        continue

                    # ★ 確定済みタイルの即時採用 (これ以降の同x,y座標では多数決を実施しない)
                    if (tx, ty) in finalized_tiles:
                        fin_bytes, fin_stage = finalized_tiles[(tx, ty)]
                        fin_img = self.decode_tile_bytes_safely(fin_bytes, cur_tile_w, cur_tile_h)
                        if fin_img is not None:
                            paste_x = tx * detected_tile_w
                            paste_y = ty * detected_tile_h
                            tw, th = fin_img.size
                            if paste_x + tw <= canvas_w and paste_y + th <= canvas_h:
                                canvas.paste(fin_img, (paste_x, paste_y))
                            else:
                                cropped = fin_img.crop((0, 0, min(tw, canvas_w - paste_x), min(th, canvas_h - paste_y)))
                                canvas.paste(cropped, (paste_x, paste_y))
                            rendered_mask[ty, tx] = True
                            continue  # 多数決を実施せず即座に描画確定して終了

                    len_dict = tiles_data.get(ty, {}).get(tx, {})
                    if not len_dict:
                        continue

                    best_plen = max(len_dict.keys(), key=lambda k: sum((p[1] + 1.0) for p in len_dict[k]))
                    packets = len_dict[best_plen]

                    # ユーザー要件フローチャート (No.1〜No.5) による多段階復元
                    tile_img, final_bytes, stage_name = self.resolve_tile(packets, best_plen, cur_tile_w, cur_tile_h)
                    if tile_img is None:
                        continue

                    # ★ 完全復元 (No.1〜No.3) が達成された場合は、DBに確定保存！
                    # (これ以降の同x,y座標では多数決を実施せず、次回以降も即座に採用して終了)
                    # ※No.4およびNo.5の場合は final_bytes が None のため確定保存されず、次回再挑戦される
                    if final_bytes is not None:
                        if not user_id:
                            self.db.save_finalized_tile(main_id, tx, ty, final_bytes, stage=stage_name)
                            finalized_tiles[(tx, ty)] = (final_bytes, stage_name)

                    paste_x = tx * detected_tile_w
                    paste_y = ty * detected_tile_h
                    tw, th = tile_img.size
                    if paste_x + tw <= canvas_w and paste_y + th <= canvas_h:
                        canvas.paste(tile_img, (paste_x, paste_y))
                    else:
                        cropped = tile_img.crop((0, 0, min(tw, canvas_w - paste_x), min(th, canvas_h - paste_y)))
                        canvas.paste(cropped, (paste_x, paste_y))
                    rendered_mask[ty, tx] = True

            # 3. 欠損タイル補間（インペインティング）は周囲の平均色ベタ塗りによる「曇り・モザイク」の原因となるため無効化
            # if not np.all(rendered_mask):
            #     canvas = self.inpaint_missing_tiles(canvas, tile_count_x, tile_count_y, detected_tile_w, detected_tile_h, rendered_mask)

            def _safe_save(img_obj, path):
                try:
                    img_obj.save(path, format="JPEG", quality=95)
                    try:
                        os.chmod(path, 0o664)
                    except Exception:
                        pass
                except PermissionError:
                    if os.path.exists(path):
                        try:
                            os.remove(path)
                        except Exception:
                            pass
                    img_obj.save(path, format="JPEG", quality=95)
                    try:
                        os.chmod(path, 0o664)
                    except Exception:
                        pass

            clean_id_str = f"{main_id:04X}"
            out_filename = f"restored_ID_{clean_id_str}.jpg" if not user_id else f"user_{user_id}_ID_{clean_id_str}.jpg"
            out_path = os.path.join(output_dir, out_filename)
            _safe_save(canvas, out_path)
            saved_files.append(out_path)

            static_out = os.path.join(root_dir, "web_turbo_png", "static", "output")
            os.makedirs(static_out, exist_ok=True)
            static_path = os.path.join(static_out, out_filename)
            _safe_save(canvas, static_path)

            # Web用: 全員多数決画像とともに、ユーザー単体・累積画像 (user_1 / user_cumulative_1) も同期生成
            if not user_id:
                for fallback_uid in (1,):
                    u_single = os.path.join(static_out, f"user_{fallback_uid}_ID_{clean_id_str}.jpg")
                    u_cumul = os.path.join(static_out, f"user_cumulative_{fallback_uid}_ID_{clean_id_str}.jpg")
                    _safe_save(canvas, u_single)
                    _safe_save(canvas, u_cumul)
            else:
                u_cumul = os.path.join(static_out, f"user_cumulative_{user_id}_ID_{clean_id_str}.jpg")
                _safe_save(canvas, u_cumul)

        return saved_files

    def close(self):
        self.db.close()

if __name__ == "__main__":
    try:
        print("=== SSTV Turbo JPEG アグリゲータ (画像復元) 開始 ===")
        aggregator = TurboJPEGAggregator()
        saved_files = aggregator.process_and_save_images()
        if saved_files:
            print("🎉 復元完了！ 生成画像:")
            for f in saved_files:
                print(f"  -> {f}")
        else:
            print("⚠️ 復元対象のパケットログが見つかりませんでした。")
            print("   先に decoder_turbo.py を実行してください。")
    except KeyboardInterrupt:
        print("\n[停止] プログラムを終了しました。")
