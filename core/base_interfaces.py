from abc import ABC, abstractmethod
from typing import Tuple, List, Optional, Callable


class BaseDecoder(ABC):
    """SSTV Turbo デコーダの共通抽象基底クラス"""
    def __init__(self, user_id: Optional[int] = None):
        self.user_id = user_id

    @abstractmethod
    def run(self, wav_path: str, progress_callback: Optional[Callable[[float], None]] = None) -> Tuple[int, str]:
        """WAVファイルをデコードし、(success_count, log_path) を返す"""
        pass


class BaseAggregator(ABC):
    """SSTV Turbo アグリゲータの共通抽象基底クラス"""
    @abstractmethod
    def load_all_logs(self) -> bool:
        """未取り込みのテキストログをDBに同期"""
        pass

    @abstractmethod
    def process_and_save_images(self, min_tile_ratio: float = 0.0, user_id: Optional[int] = None) -> List[str]:
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

