"""SSTV Turbo FEC (Raptor + RS) Webプラットフォーム統合エンコーダ (encoder_turbo.py)
【sstv-png-stand SystemFactory 互換】
入力画像からタイル化、RS(255, 223)パリティ付与、ラプターCauchy MDSリペア生成、
均等サンドイッチ変調を経て 44.1kHz 4-FSK 音声 WAV を一括生成。
"""

import os
import sys
from typing import Optional, Tuple

import numpy as np
from PIL import Image
from scipy.io import wavfile

_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_CURRENT_DIR, "../"))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from digital_turbo_fec import config_turbo as config
from digital_turbo_fec.raptor_encoder import RaptorRSEncoder
from digital_turbo_fec.config import RAPTOR_CFG


class DigitalTurboFECEncoder:
    """SSTV Turbo FEC エンコーダ (SystemFactory 互換)"""

    def __init__(self):
        self.cfg = RAPTOR_CFG
        self.encoder = RaptorRSEncoder(self.cfg)
        self.max_packets: Optional[int] = None

    def encode(
        self,
        image_path: str,
        output_wav_path: Optional[str] = None
    ) -> Tuple[float, int, str, int]:
        """
        画像ファイルからパケット化・ラプター生成・4-FSK変調を行い、WAV音声ファイルを生成する。
        戻り値: (total_time_sec, num_packets, output_wav_path, image_id)
        """
        if not os.path.exists(image_path):
            alt_path = os.path.join(_PROJECT_ROOT, "data", "input", os.path.basename(image_path))
            if os.path.exists(alt_path):
                image_path = alt_path
            else:
                raise FileNotFoundError(f"画像が見つかりません: {image_path}")

        if output_wav_path is None:
            output_wav_path = os.path.join(_PROJECT_ROOT, config.OUTPUT_WAV)

        os.makedirs(os.path.dirname(os.path.abspath(output_wav_path)), exist_ok=True)

        # 1. 16bit 画像IDの生成 (画像のCRC-16)
        img = Image.open(image_path).convert('RGB').resize((config.WIDTH, config.HEIGHT))
        pixels = np.array(img)
        from digital_turbo_fec.raptor_codec import fast_calculate_crc16
        image_id = fast_calculate_crc16(pixels.tobytes())

        # 2. タイルJPEG化
        tile_jpegs, _ = self.encoder.build_tile_jpegs(image_path)

        # 3. パケット化 ＆ ラプター生成 ＆ 均等サンドイッチ配置
        raw_packets, pkt_objects, _ = self.encoder.packetize(
            tile_jpegs,
            image_id=image_id,
            shuffle_tiles=self.cfg.enable_tile_clustered_shuffle,
            seed=self.cfg.tile_shuffle_seed
        )

        if self.max_packets is not None and self.max_packets > 0:
            pkt_objects = pkt_objects[:self.max_packets]

        # 4. 4-FSK 変調
        raw_pkts = [p.raw_packet for p in pkt_objects]
        audio_data = self.encoder.modulate_4fsk(raw_pkts)


        # 音量正規化
        max_amp = np.max(np.abs(audio_data))
        if max_amp > 1e-4:
            audio_data = (audio_data / max_amp) * 0.85

        wavfile.write(output_wav_path, self.cfg.sample_rate, (audio_data * 32767).astype(np.int16))

        total_time_sec = len(audio_data) / float(self.cfg.sample_rate)
        return total_time_sec, len(pkt_objects), output_wav_path, image_id

    def encode_image_to_wav(
        self,
        image_path: str,
        output_wav_path: Optional[str] = None,
        image_id: int = 0x1234
    ) -> str:
        """後方互換用メソッド"""
        _, _, wav_path, _ = self.encode(image_path, output_wav_path=output_wav_path)
        return wav_path
