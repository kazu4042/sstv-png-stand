import sys
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

import os
import glob
import numpy as np
from PIL import Image
import io
from collections import defaultdict

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

from digital_turbo_png import config_turbo as config
from digital_turbo_png.database_turbo import PacketDatabaseTurboPNG

class TurboPNGAggregator:
    def __init__(self, log_dir=None):
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
        if log_dir is None:
            self.log_dir = os.path.join(root_dir, config.TEXT_LOG_DIR)
        else:
            self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)
        self.db = PacketDatabaseTurboPNG(self.log_dir)

    def load_all_logs(self):
        log_files = glob.glob(os.path.join(self.log_dir, f"{config.TEXT_LOG_PREFIX}_*.txt"))
        if not log_files:
            print("[Info] ログファイルが見つかりません。")
            return False

        new_files = [f for f in log_files if not self.db.is_file_imported(os.path.basename(f))]
        if not new_files:
            return True

        print(f"[Sync] {len(new_files)} 個の新規ログファイルをDBに同期中...")
        for file_path in new_files:
            file_name = os.path.basename(file_path)
            
            user_id = None
            if "_user_" in file_name:
                try:
                    user_id = int(file_name.split("_user_")[1].split("_")[0])
                except Exception:
                    pass
            
            file_packets = []
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    # ===== 新形式: 0と1のみで構成された純2進数行 =====
                    # 形式: [image_id:16bit][tile_x:8bit][tile_y:8bit][payload_len:16bit][payload_bits][snr:4bit]
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
                            weight         = snr_val + 1.0
                            file_packets.append((img_id, tile_x, tile_y, payload_length, payload_bits, weight))
                        except (ValueError, IndexError):
                            continue

                    # ===== 旧形式(後方互換): CSV カンマ区切り =====
                    else:
                        parts = line.split(",")
                        if len(parts) >= 6:
                            try:
                                img_id         = int(parts[0])
                                tile_x         = int(parts[1])
                                tile_y         = int(parts[2])
                                payload_length = int(parts[3])
                                payload_bits   = parts[4]
                                snr_str        = str(parts[5]).strip()
                                if all(c in '01' for c in snr_str) and len(snr_str) == 4:
                                    snr_val = float(int(snr_str, 2))
                                else:
                                    snr_val = float(snr_str)
                                weight = snr_val + 1.0
                                file_packets.append((img_id, tile_x, tile_y, payload_length, payload_bits, weight))
                            except ValueError:
                                continue

            if file_packets:
                self.db.insert_packets_bulk(file_name, file_packets, user_id=user_id)
                print(f"  [Loaded] {file_name}: {len(file_packets)} パケット (User: {user_id})")
        return True

    def bits_to_bytearray(self, bits_str):
        byte_list = []
        for i in range(0, len(bits_str), 8):
            chunk = bits_str[i:i+8]
            if len(chunk) == 8:
                val = 0
                for b in chunk:
                    val = (val << 1) | (1 if b == '1' else 0)
                byte_list.append(val)
        return bytearray(byte_list)

    def reset_database(self):
        """DBを削除して初期化する（古いデータをすべてクリア）"""
        db_path = os.path.join(self.log_dir, "sstv_packets_turbo_png.db")
        self.db.close()
        if os.path.exists(db_path):
            os.remove(db_path)
            print(f"[Reset] 旧データベースを削除しました: {db_path}")
        self.db = PacketDatabaseTurboPNG(self.log_dir)

    def process_and_save_images(self, min_tile_ratio=0.0, user_id=None):
        """
        テキストログをDBに蓄積し、多数決投票で最終画像を復元する。

        Args:
            min_tile_ratio: 復元対象の最低タイル充填率 (0.0~1.0)
                            デフォルト 0.0 = 全ての画像IDを復元対象とする
            user_id: フィルタリングするユーザーID
        """
        if not self.load_all_logs():
            return []

        image_counts = self.db.get_all_image_ids_with_counts(user_id=user_id)
        
        tile_count_x = config.TILE_COUNT_X
        tile_count_y = config.TILE_COUNT_Y
        total_required_packets = tile_count_x * tile_count_y
        
        print(f"\n[Info] 期待タイル数: {total_required_packets}  最低パケット数フィルタ: {int(total_required_packets * min_tile_ratio)}")
        print(f"[Info] DB内画像ID数: {len(image_counts)}")
        
        saved_files = []
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
        output_dir = os.path.join(root_dir, config.IMAGE_OUT_DIR)
        os.makedirs(output_dir, exist_ok=True)
        
        for img_id, count in image_counts.items():
            if count < total_required_packets * min_tile_ratio:
                continue
                
            print(f"\n[Restore] 画像ID 0x{img_id:04X} (パケット数: {count}) を多数決投票で復元中...")
            tiles_data = self.db.get_packets_for_image(img_id, user_id=user_id)

            canvas = Image.new("RGB", (config.WIDTH, config.HEIGHT), color="black")
            success_tiles = 0
            error_tiles = 0

            for ty in range(config.TILE_COUNT_Y):
                for tx in range(config.TILE_COUNT_X):
                    len_dict = tiles_data[ty][tx]
                    if not len_dict:
                        continue

                    # ===== Fast Path: 個別パケットの直接検証 =====
                    # すべての payload_length におけるパケットを試し、PNGとしてデコードできるか確認する
                    valid_tile_img = None
                    total_packets = 0
                    for plen, pkt_list in len_dict.items():
                        total_packets += len(pkt_list)
                        for payload_bits_str, weight, _, _, _ in pkt_list:
                            try:
                                p_bytes = self.bits_to_bytearray(payload_bits_str)
                                temp_img = Image.open(io.BytesIO(p_bytes)).convert("RGB")
                                valid_tile_img = temp_img
                                break
                            except Exception:
                                pass
                        if valid_tile_img:
                            break
                    
                    if valid_tile_img:
                        # 個別のパケットが完璧だった場合、多数決はスキップ
                        tw, th = valid_tile_img.size
                        paste_x = tx * tw
                        paste_y = ty * th
                        if paste_x + tw <= config.WIDTH and paste_y + th <= config.HEIGHT:
                            canvas.paste(valid_tile_img, (paste_x, paste_y))
                            success_tiles += 1
                        else:
                            # キャンバスに収まらない場合はクロップして貼り付け
                            valid_tile_img = valid_tile_img.crop((0, 0,
                                min(tw, config.WIDTH - paste_x),
                                min(th, config.HEIGHT - paste_y)))
                            canvas.paste(valid_tile_img, (paste_x, paste_y))
                            success_tiles += 1
                        continue

                    # ===== Fallback: SNR重み付き多数決投票 =====
                    # どの個別パケットも単独ではデコードできなかった場合
                    # SNR重みの合計が最大のpayload_lengthを選択
                    best_plen = max(len_dict.keys(), key=lambda k: sum(p[1] for p in len_dict[k]))
                    packets = len_dict[best_plen]
                    payload_bit_len = best_plen * 8

                    score_0 = np.zeros(payload_bit_len, dtype=np.float64)
                    score_1 = np.zeros(payload_bit_len, dtype=np.float64)

                    valid_count = 0
                    for payload_bits_str, weight, _, _, _ in packets:
                        if len(payload_bits_str) < payload_bit_len:
                            continue
                        valid_count += 1
                        for i, bit_char in enumerate(payload_bits_str[:payload_bit_len]):
                            if bit_char == '1':
                                score_1[i] += weight
                            else:
                                score_0[i] += weight  # '0' を確実に加算

                    if valid_count == 0:
                        error_tiles += 1
                        continue

                    # 各ビット: 得票数が多い方を採用
                    voted_payload = "".join(
                        '1' if score_1[i] >= score_0[i] else '0'
                        for i in range(payload_bit_len)
                    )

                    p_bytes = self.bits_to_bytearray(voted_payload)

                    try:
                        # PNGとして復元（JPEGではなくPNG）
                        tile_img = Image.open(io.BytesIO(p_bytes)).convert("RGB")
                        tw, th = tile_img.size  # PNGの実際のタイルサイズを使う
                        paste_x = tx * tw
                        paste_y = ty * th
                        if paste_x + tw <= config.WIDTH and paste_y + th <= config.HEIGHT:
                            canvas.paste(tile_img, (paste_x, paste_y))
                            success_tiles += 1
                        else:
                            # キャンバスに収まらない場合はクロップして貼り付け
                            tile_img = tile_img.crop((0, 0,
                                min(tw, config.WIDTH - paste_x),
                                min(th, config.HEIGHT - paste_y)))
                            canvas.paste(tile_img, (paste_x, paste_y))
                            success_tiles += 1
                    except Exception as e:
                        error_tiles += 1
                        if error_tiles <= 5:
                            print(f"  [ERROR] タイル({tx},{ty}) PNG復元失敗 (受信パケット総数:{total_packets}, 多数決投票数:{valid_count}): {e}")

            # PNG形式で保存（JPEGではなくPNG）
            if user_id is not None:
                filename = f"user_cumulative_{user_id}_ID_{img_id:04X}.png"
            else:
                filename = f"restored_ID_{img_id:04X}.png"
                
            out_path = os.path.join(output_dir, filename)
            canvas.save(out_path, format="PNG", compress_level=config.PNG_COMPRESS)

            # Webシステム用に static/output にもコピー保存
            static_out = os.path.join(root_dir, "web_turbo_png", "static", "output")
            os.makedirs(static_out, exist_ok=True)
            static_path = os.path.join(static_out, filename)
            canvas.save(static_path, format="PNG", compress_level=config.PNG_COMPRESS)

            saved_files.append(out_path)
            print(f"[Done] 復元完了: {success_tiles}/{total_required_packets} タイル合成"
                  f" (失敗:{error_tiles}) -> {out_path}")

        if not saved_files:
            print("\n[Warn] 復元できた画像がありません。")
            print("       min_tile_ratio を下げるか、デコーダを再実行してください。")

        return saved_files

    def close(self):
        self.db.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="TurboPNG アグリゲータ - 多数決投票で画像復元")
    parser.add_argument("--reset", action="store_true",
                        help="旧DBを削除して新規ログのみ処理する")
    parser.add_argument("--min-ratio", type=float, default=0.3,
                        help="復元対象の最低タイル充填率 (デフォルト: 0.3)")
    args = parser.parse_args()

    agg = TurboPNGAggregator()
    if args.reset:
        agg.reset_database()
    restored = agg.process_and_save_images(min_tile_ratio=args.min_ratio)
    agg.close()

    if restored:
        print(f"\n✅ 復元完了: {len(restored)} 枚保存")
        for p in restored:
            print(f"   {p}")
    else:
        print("\n❌ 画像復元なし。")
