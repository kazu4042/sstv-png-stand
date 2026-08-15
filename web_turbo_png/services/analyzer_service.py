import os
import sys
import numpy as np

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "../../"))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import digital_turbo_png.config_turbo as config
from digital_turbo_png.aggregator_turbo import TurboPNGAggregator


class TurboPNGAnalyzerService:
    """TurboPNG 専用アナライザーサービス（SQLite DB を利用）"""

    def __init__(self):
        log_dir = getattr(config, "TEXT_LOG_DIR", "data/digital_turbo_png/logs")
        if not os.path.isabs(log_dir):
            self.log_directory = os.path.join(ROOT_DIR, log_dir)
        else:
            self.log_directory = log_dir

        self.aggregator = TurboPNGAggregator(log_dir=self.log_directory)
        self.aggregator.load_all_logs()

    def get_available_image_ids(self):
        """DB に存在する全画像IDを16進数文字列のリストで返す"""
        image_counts = self.aggregator.db.get_all_image_ids_with_counts()
        return sorted([f"{img_id:04X}" for img_id in image_counts.keys()])

    def get_merge_stats(self, target_image_id_hex):
        """TurboPNG ではクラスタリングを行わないため空を返す"""
        return {"total_merged": 0, "details": []}

    def _get_tiles_data(self, target_id_int):
        """指定画像IDのタイルデータを DB から取得"""
        return self.aggregator.db.get_packets_for_image(target_id_int)

    def find_missing_packets(self, target_image_id_hex, max_limit=100):
        """不足・低品質なタイルを検出して返す"""
        missing_list = []
        try:
            target_id_int = int(target_image_id_hex, 16)
        except ValueError:
            return missing_list

        tiles_data = self._get_tiles_data(target_id_int)
        tile_count_x = config.TILE_COUNT_X
        tile_count_y = config.TILE_COUNT_Y
        poor_threshold = getattr(config, 'POOR_BLOCK_SNR_THRESHOLD', 5.0)

        for ty in range(tile_count_y):
            for tx in range(tile_count_x):
                if ty not in tiles_data or tx not in tiles_data[ty] or not tiles_data[ty][tx]:
                    missing_list.append({"ty": ty, "tx": tx, "status": "MISSING"})
                else:
                    len_dict = tiles_data[ty][tx]

                    best_plen = None
                    max_weight_sum = -1
                    for plen, pkts in len_dict.items():
                        w_sum = sum(p[1] for p in pkts)
                        if w_sum > max_weight_sum:
                            max_weight_sum = w_sum
                            best_plen = plen

                    if not best_plen:
                        missing_list.append({"ty": ty, "tx": tx, "status": "MISSING"})
                        continue

                    packets = len_dict[best_plen]
                    total_files = len(packets)
                    total_weight = sum(weight for _, weight, _, _ in packets)
                    avg_snr = (total_weight / total_files) - 1.0 if total_files > 0 else 0

                    if avg_snr <= poor_threshold:
                        missing_list.append({
                            "ty": ty,
                            "tx": tx,
                            "status": "POOR",
                            "avg_snr": round(avg_snr, 1)
                        })

                if len(missing_list) >= max_limit:
                    return missing_list

        return missing_list

    def calculate_reliability_scores(self, target_image_id_hex, current_user_id=None):
        """指定画像のタイルごとの信頼度（0-100%）を算出し、配列で返す"""
        reliability_map = []
        try:
            target_id_int = int(target_image_id_hex, 16)
        except ValueError:
            return reliability_map

        tiles_data = self._get_tiles_data(target_id_int)
        if not tiles_data:
            return reliability_map

        tile_count_x = config.TILE_COUNT_X
        tile_count_y = config.TILE_COUNT_Y

        for ty in range(tile_count_y):
            for tx in range(tile_count_x):
                if ty not in tiles_data or tx not in tiles_data[ty]:
                    continue

                len_dict = tiles_data[ty][tx]
                if not len_dict:
                    continue

                best_plen = None
                max_weight_sum = -1
                for plen, pkts in len_dict.items():
                    w_sum = sum(p[1] for p in pkts)
                    if w_sum > max_weight_sum:
                        max_weight_sum = w_sum
                        best_plen = plen

                if not best_plen:
                    continue

                packets = len_dict[best_plen]
                payload_len = best_plen * 8

                total_files = len(packets)
                total_weight_sum = sum(weight for _, weight, _, _ in packets)

                score_0 = np.zeros(payload_len, dtype=float)
                score_1 = np.zeros(payload_len, dtype=float)
                
                is_contributed = False

                for payload_bits_str, weight, file_name, p_user_id in packets:
                    if current_user_id and p_user_id == current_user_id:
                        is_contributed = True
                        
                    if len(payload_bits_str) < payload_len:
                        continue
                    for i, bit_char in enumerate(payload_bits_str[:payload_len]):
                        if bit_char == '1':
                            score_1[i] += weight
                        elif bit_char == '0':
                            score_0[i] += weight

                confidences = []
                for i in range(payload_len):
                    diff = abs(score_1[i] - score_0[i])
                    confidence = min(100.0, (diff / max(1.0, total_weight_sum)) * 100)
                    confidences.append(confidence)

                avg_confidence = sum(confidences) / len(confidences) if confidences else 0
                avg_snr = (total_weight_sum / total_files) - 1.0 if total_files > 0 else 0

                sources = []
                for idx_pkt, pkt_info in enumerate(packets):
                    sources.append({
                        "sender": "Local Demo",
                        "location": "Local",
                        "received_at": "Now",
                        "file_name": f"local_data_{idx_pkt}"
                    })

                reliability_map.append({
                    "line": ty,
                    "block": tx,
                    "score": round(avg_confidence, 1),
                    "avg_snr": round(avg_snr, 1),
                    "samples": total_files,
                    "total_weight": int(total_weight_sum),
                    "sources": sources,
                    "is_contributed": is_contributed
                })

        return reliability_map
