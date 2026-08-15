import sys
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

import numpy as np
from PIL import Image
from scipy.io import wavfile
import io
import os
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

from digital_turbo_png import config_turbo as config

class DigitalTurboPNGEncoder:
    def __init__(self):
        self.current_phase = 0.0
        self.samples_per_symbol = max(1, int(config.SAMPLE_RATE * config.MS_SYMBOL / 1000))

    def get_tone_samples(self, freq, num_samples):
        """指定サンプル数で位相連続なサイン波を生成"""
        if num_samples <= 0:
            return np.array([], dtype=np.float32)
        t = np.arange(num_samples) / config.SAMPLE_RATE
        phase = 2 * np.pi * freq * t + self.current_phase
        wave = np.sin(phase)
        self.current_phase = phase[-1] % (2 * np.pi)
        return wave.astype(np.float32)

    def get_tone(self, freq, duration_ms):
        num_samples = max(1, int(config.SAMPLE_RATE * duration_ms / 1000))
        return self.get_tone_samples(freq, num_samples)

    def bits_to_wave(self, bit_stream):
        self.samples_per_symbol = max(1, int(config.SAMPLE_RATE * config.MS_SYMBOL / 1000))
        wave_segments = []
        for i in range(0, len(bit_stream), 2):
            b1 = bit_stream[i]
            b2 = bit_stream[i+1] if i+1 < len(bit_stream) else 0
            two_bits = (b1 << 1) | b2
            freq = config.FREQ_MAP[two_bits]
            wave_segments.append(self.get_tone_samples(freq, self.samples_per_symbol))
        if not wave_segments:
            return np.array([], dtype=np.float32)
        return np.concatenate(wave_segments)

    def bytes_to_bitstream(self, data_bytes, bit_lengths):
        stream = []
        for val, length in zip(data_bytes, bit_lengths):
            for i in reversed(range(length)):
                stream.append((val >> i) & 1)
        return stream

    def bytearray_to_bitstream(self, byte_array):
        stream = []
        for b in byte_array:
            for i in reversed(range(8)):
                stream.append((b >> i) & 1)
        return stream

    @staticmethod
    def calculate_crc16_bits(bit_list, poly=0x1021, init_val=0xFFFF):
        crc = init_val
        for bit in bit_list:
            inv = ((crc >> 15) ^ bit) & 1
            crc = (crc << 1) & 0xFFFF
            if inv:
                crc ^= poly
        return crc

    def encode(self, image_path, output_wav_path=None):
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
        if not os.path.exists(image_path):
            alt_path = os.path.join(root_dir, "data", "input", os.path.basename(image_path))
            if os.path.exists(alt_path):
                image_path = alt_path
            else:
                raise FileNotFoundError(f"画像が見つかりません: {image_path}")

        img = Image.open(image_path).convert('RGB').resize((config.WIDTH, config.HEIGHT))
        pixels = np.array(img)
        packet_list = []

        print(f"[Encode] Starting TURBO PNG: {config.WIDTH}x{config.HEIGHT} (Tile: {config.TILE_SIZE}x{config.TILE_SIZE} => {config.TILE_COUNT_X}x{config.TILE_COUNT_Y} tiles)")

        print("  -> Calculating 16-bit Image ID...")
        all_pixel_bits = self.bytearray_to_bitstream(pixels.tobytes())
        image_id_val = self.calculate_crc16_bits(all_pixel_bits)
        image_id_bits = self.bytes_to_bitstream([image_id_val], [config.BIT_IMAGE_CRC])
        print(f"  -> Image ID Generated: 0x{image_id_val:04X}")

        total_time_ms = 0
        total_payload_bytes = 0

        for ty in range(config.TILE_COUNT_Y):
            for tx in range(config.TILE_COUNT_X):
                y_start = ty * config.TILE_SIZE
                y_end = min(y_start + config.TILE_SIZE, config.HEIGHT)
                x_start = tx * config.TILE_SIZE
                x_end = min(x_start + config.TILE_SIZE, config.WIDTH)
                tile_pixels = pixels[y_start:y_end, x_start:x_end]

                # PNG圧縮（JPEGの代わりにPNGを使用）
                tile_img = Image.fromarray(tile_pixels)
                with io.BytesIO() as bio:
                    tile_img.save(bio, format="PNG", compress_level=config.PNG_COMPRESS, optimize=True)
                    png_data = bio.getvalue()

                payload_length = len(png_data)
                total_payload_bytes += payload_length
                max_len = (1 << config.BIT_PAYLOAD_LENGTH) - 1
                if payload_length > max_len:
                    raise ValueError(f"Tile ({tx},{ty}) PNG data ({payload_length} bytes) exceeds limit ({max_len} bytes).")

                header_info_bits = image_id_bits + self.bytes_to_bitstream(
                    [tx, ty, payload_length],
                    [config.BIT_TILE_X, config.BIT_TILE_Y, config.BIT_PAYLOAD_LENGTH]
                )

                header_crc_val = self.calculate_crc16_bits(header_info_bits)
                header_crc_bits = self.bytes_to_bitstream([header_crc_val], [config.BIT_HEADER_CRC])
                full_header_bits = header_info_bits + header_crc_bits

                if len(full_header_bits) % 2 != 0:
                    full_header_bits.append(0)

                payload_bits = self.bytearray_to_bitstream(png_data)
                if len(payload_bits) % 2 != 0:
                    payload_bits.append(0)

                sync_wave = self.get_tone(config.FREQ_SYNC, config.MS_SYNC)
                data_wave = self.bits_to_wave(full_header_bits + payload_bits)

                single_packet = np.concatenate([sync_wave, data_wave])
                packet_list.append(single_packet)

                packet_time = config.MS_SYNC + ((len(full_header_bits) + len(payload_bits)) // 2) * config.MS_SYMBOL
                total_time_ms += packet_time

        print(f"  -> 全 {len(packet_list)} パケット生成完了！ (総データ量: {total_payload_bytes} Bytes)")
        print(f"  -> 音声伝送時間: {total_time_ms / 1000.0:.2f} 秒 ({total_time_ms / 60000.0:.2f} 分)")

        final_wave = np.concatenate(packet_list)
        final_wave = final_wave / np.max(np.abs(final_wave)) * 0.8

        if config.NOISE_LEVEL > 0.0:
            noise = np.random.normal(0, config.NOISE_LEVEL, final_wave.shape).astype(np.float32)
            final_wave = final_wave + noise
            max_amp = np.max(np.abs(final_wave))
            if max_amp > 1.0:
                final_wave = final_wave / max_amp * 0.9

        if output_wav_path is None:
            output_wav_path = os.path.join(root_dir, config.OUTPUT_WAV)

        os.makedirs(os.path.dirname(output_wav_path), exist_ok=True)
        wavfile.write(output_wav_path, config.SAMPLE_RATE, (final_wave * 32767).astype(np.int16))
        print(f"  -> 音声ファイル保存完了: {output_wav_path}\n")

        return total_time_ms / 1000.0, len(packet_list), output_wav_path, image_id_val

if __name__ == "__main__":
    encoder = DigitalTurboPNGEncoder()
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
    # config.INPUT_IMAGE を使用して一貫性を保証する
    input_img = os.path.join(root_dir, config.INPUT_IMAGE)
    if not os.path.exists(input_img):
        print(f"[Error] 入力画像が見つかりません: {input_img}")
        print("        data/input/ に test.jpg を配置してください。")
        sys.exit(1)
    encoder.encode(input_img)
