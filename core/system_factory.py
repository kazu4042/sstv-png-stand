import os
import threading

class SystemFactory:
    """SSTV Turbo プラットフォームのエンジン切り替えファクトリ"""
    _lock = threading.Lock()
    _mode = os.getenv("ACTIVE_DECODER_MODE", "PNG").upper()

    @classmethod
    def get_mode(cls):
        with cls._lock:
            return cls._mode

    @classmethod
    def set_mode(cls, mode):
        mode = mode.upper().strip()
        if mode not in ("PNG", "JPEG"):
            raise ValueError(f"Invalid mode: '{mode}'. Must be 'PNG' or 'JPEG'.")
        with cls._lock:
            cls._mode = mode
            os.environ["ACTIVE_DECODER_MODE"] = mode

    @classmethod
    def get_config(cls, mode=None):
        target_mode = (mode or cls.get_mode()).upper()
        if target_mode == "JPEG":
            import digital_turbo_jpeg.config_turbo as config_jpeg
            return config_jpeg
        else:
            import digital_turbo_png.config_turbo as config_png
            return config_png

    @classmethod
    def get_decoder(cls, user_id=None, mode=None):
        target_mode = (mode or cls.get_mode()).upper()
        if target_mode == "JPEG":
            from digital_turbo_jpeg.decoder_turbo import DigitalTurboJPEGDecoder
            return DigitalTurboJPEGDecoder(user_id=user_id)
        else:
            from digital_turbo_png.decoder_turbo import DigitalTurboPNGDecoder
            return DigitalTurboPNGDecoder(user_id=user_id)

    @classmethod
    def get_aggregator(cls, log_dir=None, mode=None):
        target_mode = (mode or cls.get_mode()).upper()
        if target_mode == "JPEG":
            from digital_turbo_jpeg.aggregator_turbo import TurboJPEGAggregator
            return TurboJPEGAggregator(log_dir=log_dir)
        else:
            from digital_turbo_png.aggregator_turbo import TurboPNGAggregator
            return TurboPNGAggregator(log_dir=log_dir)
