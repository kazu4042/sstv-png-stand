"""Reed-Solomon (RS) 符号コーデック (digital_turbo_fec/rs_codec.py)
reedsolo ライブラリを活用し、パケット単位での誤り検出・自動訂正機能を提供する。
"""

from typing import Optional, Tuple
import reedsolo


class RSCodec:
    """Reed-Solomon (RS) 誤り訂正符号のエンコード・デコードを行うラッパークラス"""

    def __init__(self, nsym: int = 32):
        """
        RSコーデックの初期化
        Args:
            nsym: RSパリティバイト数 (デフォルト: 32B → 最大16バイトの誤りを訂正可能)
        """
        self.nsym = nsym
        self._codec = reedsolo.RSCodec(nsym=nsym)

    @property
    def max_correctable_bytes(self) -> int:
        """訂正可能な最大バイト数 (nsym // 2)"""
        return self.nsym // 2

    def encode(self, data: bytes) -> bytes:
        """
        データにRSパリティを付加して符号化ブロックを生成する。
        """
        return bytes(self._codec.encode(data))

    def is_clean(self, rs_block: bytes) -> bool:
        """
        RSシンドローム検査により、ブロックが無傷（誤りゼロ）であるかを高速判定する。
        """
        try:
            res = self._codec.check(rs_block)
            return bool(res and all(res))
        except Exception:
            return False

    def decode(self, rs_block: bytes) -> Tuple[Optional[bytes], int, bool]:
        """
        RS符号化ブロックから誤りを自動検知・訂正し、元データを復元する。
        """
        if self.is_clean(rs_block):
            return rs_block[:-self.nsym], 0, True

        try:
            dec_msg, _, errata_pos = self._codec.decode(rs_block)
            corrected_count = len(errata_pos)
            return bytes(dec_msg), corrected_count, True
        except (reedsolo.ReedSolomonError, ValueError, Exception):
            return None, 0, False

    def decode_after_vote(self, voted_block: bytes) -> Tuple[Optional[bytes], int, bool]:
        """
        複数地上局の多数決によって粗く修復されたブロックに対してRS復号を適用する。
        """
        return self.decode(voted_block)

    def decode_with_erasures(
        self,
        rs_block: bytes,
        erase_pos: Optional[list] = None
    ) -> Tuple[Optional[bytes], int, bool]:
        """
        RSイレイジャ (消失) 訂正復号
        """
        if self.is_clean(rs_block):
            return rs_block[:-self.nsym], 0, True

        try:
            dec_msg, _, errata_pos = self._codec.decode(rs_block, erase_pos=erase_pos)
            return bytes(dec_msg), len(errata_pos), True
        except (reedsolo.ReedSolomonError, ValueError, Exception):
            return None, 0, False
