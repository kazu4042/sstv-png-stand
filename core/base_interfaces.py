from abc import ABC, abstractmethod


class BaseDecoder(ABC):
    """SSTV Turbo デコーダの共通抽象基底クラス"""
    def __init__(self, user_id=None):
        self.user_id = user_id

    @abstractmethod
    def run(self, wav_path, progress_callback=None):
        """WAVファイルをデコードし、(success_count, log_path) を返す"""
        pass


class BaseAggregator(ABC):
    """SSTV Turbo アグリゲータの共通抽象基底クラス"""
    @abstractmethod
    def load_all_logs(self) -> bool:
        """未取り込みのテキストログをDBに同期"""
        pass

    @abstractmethod
    def process_and_save_images(self, min_tile_ratio=0.0, user_id=None) -> list:
        """DBから多数決および画像復元を実行し、保存されたファイルパスリストを返す"""
        pass

    @abstractmethod
    def reset_database(self) -> None:
        """データベースを初期化"""
        pass

    @abstractmethod
    def close(self) -> None:
        """DB接続をクローズ"""
        pass
