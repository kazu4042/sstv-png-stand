import os
import sys
import json
import time
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime
from PIL import Image

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

AUDIO_DIR = os.path.join(ROOT_DIR, "web_turbo_png", "static", "audio")
MANIFEST_PATH = os.path.join(AUDIO_DIR, "audio_manifest.json")


def _get_ffmpeg_bin() -> Optional[str]:
    return shutil.which("ffmpeg") or ("/usr/bin/ffmpeg" if os.path.exists("/usr/bin/ffmpeg") else None)


class AudioManager:
    """実験用音声トラックの登録、アップロード、画像からの自動エンコード、アクティブ管理"""

    @classmethod
    def get_manifest(cls) -> Dict[str, Any]:
        """マニフェストを取得し、存在しない既存ファイルを自動同期して返す"""
        os.makedirs(AUDIO_DIR, exist_ok=True)
        manifest: Dict[str, Any] = {"active": {"JPEG": "turbo_256_256.wav", "PNG": "turbo_png_256_256.wav", "FEC": "turbo_fec_256_256.wav"}, "tracks": {}}

        if os.path.exists(MANIFEST_PATH):
            try:
                with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
            except Exception as e:
                print(f"[AudioManager] Manifest read error: {e}")

        # ディレクトリ内の WAV ファイルをスキャンして自動同期
        cls._sync_with_directory(manifest)
        return manifest

    @classmethod
    def _sync_with_directory(cls, manifest: Dict[str, Any]) -> None:
        """ディレクトリ内の実際の .wav ファイルとマニフェストを同期"""
        tracks: Dict[str, Any] = manifest.setdefault("tracks", {})
        active: Dict[str, str] = manifest.setdefault("active", {"JPEG": "turbo_256_256.wav", "PNG": "turbo_png_256_256.wav"})

        # デフォルトトラックの初期登録
        default_defs: Dict[str, Dict[str, Any]] = {
            "turbo_256_256.wav": {
                "id": "turbo_256_256.wav",
                "filename": "turbo_256_256.wav",
                "label": "標準 JPEG テスト音声 (デフォルト)",
                "mode": "JPEG",
                "image_id": "0x0001",
                "description": "システム初期搭載の JPEG 段階的復元テスト音声",
                "is_system": True
            },
            "turbo_png_256_256.wav": {
                "id": "turbo_png_256_256.wav",
                "filename": "turbo_png_256_256.wav",
                "label": "標準 PNG テスト音声 (デフォルト)",
                "mode": "PNG",
                "image_id": "0x0001",
                "description": "システム初期搭載の PNG 多数決復元テスト音声",
                "is_system": True
            },
            "turbo_fec_256_256.wav": {
                "id": "turbo_fec_256_256.wav",
                "filename": "turbo_fec_256_256.wav",
                "label": "標準 FEC テスト音声 (デフォルト)",
                "mode": "FEC",
                "image_id": "0x0001",
                "description": "システム初期搭載の FEC (Raptor+RS) 復元テスト音声",
                "is_system": True
            },
            "space_jpeg_test.wav": {
                "id": "space_jpeg_test.wav",
                "filename": "space_jpeg_test.wav",
                "label": "宇宙画像 (JPEG 256x256)",
                "mode": "JPEG",
                "image_id": "0x6798",
                "description": "宇宙画像.jpg をエンコードした実験用音声",
                "is_system": False
            }
        }

        updated = False
        if os.path.exists(AUDIO_DIR):
            for fname in os.listdir(AUDIO_DIR):
                if fname.lower().endswith(".wav"):
                    full_path = os.path.join(AUDIO_DIR, fname)
                    file_size = os.path.getsize(full_path)
                    mtime = os.path.getmtime(full_path)

                    if fname not in tracks:
                        inferred_mode = "FEC" if "fec" in fname.lower() else ("JPEG" if "jpeg" in fname.lower() else "PNG")
                        base_info: Dict[str, Any] = dict(default_defs.get(fname, {
                            "id": fname,
                            "filename": fname,
                            "label": fname,
                            "mode": inferred_mode,
                            "image_id": "---",
                            "description": "アップロードされた音声",
                            "is_system": False
                        }))
                        base_info["size_bytes"] = file_size
                        base_info["size_mb"] = round(file_size / (1024 * 1024), 2)
                        base_info["created_at"] = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
                        tracks[fname] = base_info
                        updated = True
                    else:
                        # サイズや日時を更新
                        tracks[fname]["size_bytes"] = file_size
                        tracks[fname]["size_mb"] = round(file_size / (1024 * 1024), 2)

        # 存在しないファイルの整理
        to_delete = []
        for track_id, track_info in list(tracks.items()):
            fname = track_info.get("filename", track_id) if isinstance(track_info, dict) else track_id
            if not os.path.exists(os.path.join(AUDIO_DIR, fname)):
                to_delete.append(track_id)

        for tid in to_delete:
            del tracks[tid]
            updated = True

        # active が無効ならフォールバック
        for mode, default_fname in [("JPEG", "turbo_256_256.wav"), ("PNG", "turbo_png_256_256.wav"), ("FEC", "turbo_fec_256_256.wav")]:
            if active.get(mode) not in tracks:
                mode_tracks = [t for t in tracks.values() if isinstance(t, dict) and t.get("mode") == mode]
                if mode_tracks:
                    active[mode] = mode_tracks[0]["filename"]
                else:
                    active[mode] = default_fname
                updated = True

        if updated or not os.path.exists(MANIFEST_PATH):
            cls.save_manifest(manifest)

    @classmethod
    def save_manifest(cls, manifest: Dict[str, Any]) -> None:
        """マニフェストを JSON に保存"""
        os.makedirs(AUDIO_DIR, exist_ok=True)
        with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

    @classmethod
    def list_tracks(cls, mode: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
        """トラック一覧を取得（mode でフィルタ可能）"""
        manifest = cls.get_manifest()
        raw_tracks = manifest.get("tracks", {})
        tracks: List[Dict[str, Any]] = list(raw_tracks.values()) if isinstance(raw_tracks, dict) else []
        if mode:
            mode_upper = mode.upper()
            tracks = [t for t in tracks if isinstance(t, dict) and t.get("mode") == mode_upper]
        
        active_map: Dict[str, str] = manifest.get("active", {}) if isinstance(manifest.get("active"), dict) else {}
        for t in tracks:
            t_mode = t.get("mode", "JPEG")
            t["is_active"] = (active_map.get(t_mode) == t.get("filename"))

        # 新しい順にソート (システムデフォルトは先頭)
        tracks.sort(key=lambda x: (not x.get("is_active", False), not x.get("is_system", False), x.get("created_at", "")), reverse=True)
        return tracks, active_map

    @classmethod
    def set_active_track(cls, mode: str, filename: str) -> Dict[str, str]:
        """特定モードのアクティブトラックを設定"""
        mode = mode.upper()
        manifest = cls.get_manifest()
        if filename not in manifest.get("tracks", {}):
            raise ValueError(f"トラックが見つかりません: {filename}")
        manifest.setdefault("active", {})[mode] = filename
        cls.save_manifest(manifest)
        return manifest["active"]

    @classmethod
    def get_active_track(cls, mode: str) -> Dict[str, Any]:
        """現在アクティブなトラック情報を取得"""
        mode = mode.upper()
        manifest = cls.get_manifest()
        tracks_dict: Dict[str, Any] = manifest.get("tracks", {}) if isinstance(manifest.get("tracks"), dict) else {}
        active_dict: Dict[str, str] = manifest.get("active", {}) if isinstance(manifest.get("active"), dict) else {}
        active_filename = active_dict.get(mode)
        if not active_filename or active_filename not in tracks_dict:
            tracks_list = [t for t in tracks_dict.values() if isinstance(t, dict) and t.get("mode") == mode]
            if tracks_list:
                return tracks_list[0]
            # フォールバック
            if mode == "JPEG":
                fname = "turbo_256_256.wav"
            elif mode == "FEC":
                fname = "turbo_fec_256_256.wav"
            else:
                fname = "turbo_png_256_256.wav"
            return {"filename": fname, "label": f"{mode} デフォルト音声", "mode": mode}
        result = tracks_dict[active_filename]
        return result if isinstance(result, dict) else {"filename": active_filename, "label": active_filename, "mode": mode}

    @classmethod
    def add_audio_file(cls, src_path: str, filename: str, label: str, mode: str, description: str = "") -> Dict[str, Any]:
        """アップロードされた音声ファイルを 44.1kHz WAV として正規化して保存"""
        mode = mode.upper()
        safe_name = os.path.splitext(os.path.basename(filename))[0].replace(" ", "_")
        safe_name = "".join(c for c in safe_name if c.isalnum() or c in ("-", "_"))
        if not safe_name:
            safe_name = f"custom_track_{int(time.time())}"
        dest_filename = f"{safe_name}.wav"
        dest_path = os.path.join(AUDIO_DIR, dest_filename)

        # ffmpeg 変換またはコピー
        ffmpeg_bin = _get_ffmpeg_bin()
        is_wav = src_path.lower().endswith(".wav")
        duration_sec = 0.0

        if is_wav:
            try:
                import wave
                with wave.open(src_path, 'rb') as wf:
                    frames = wf.getnframes()
                    rate = wf.getframerate()
                    duration_sec = round(frames / float(rate), 1)
                    # 既に44.1kHz 16bit モノラルなら直接コピー
                    if rate == 44100 and wf.getnchannels() == 1 and wf.getsampwidth() == 2:
                        shutil.copy2(src_path, dest_path)
                        ffmpeg_bin = None
            except Exception:
                pass

        if ffmpeg_bin and not os.path.exists(dest_path):
            cmd = [
                ffmpeg_bin, "-y", "-i", src_path,
                "-ar", "44100",
                "-ac", "1",
                "-c:a", "pcm_s16le",
                dest_path
            ]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
            if res.returncode != 0 or not os.path.exists(dest_path):
                shutil.copy2(src_path, dest_path)
        elif not os.path.exists(dest_path):
            shutil.copy2(src_path, dest_path)

        # duration_sec が未算出なら算出
        if duration_sec <= 0.0 and os.path.exists(dest_path):
            try:
                import wave
                with wave.open(dest_path, 'rb') as wf:
                    duration_sec = round(wf.getnframes() / float(wf.getframerate()), 1)
            except Exception:
                pass

        file_size = os.path.getsize(dest_path)
        manifest = cls.get_manifest()
        new_track_info: Dict[str, Any] = {
            "id": dest_filename,
            "filename": dest_filename,
            "label": label or dest_filename,
            "mode": mode,
            "image_id": "Custom",
            "description": description or f"{mode} アップロード音声 ({round(duration_sec/60, 1) if duration_sec > 60 else duration_sec}{'分' if duration_sec > 60 else '秒'})",
            "duration_sec": duration_sec,
            "is_system": False,
            "size_bytes": file_size,
            "size_mb": round(file_size / (1024 * 1024), 2),
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M")
        }
        manifest["tracks"][dest_filename] = new_track_info
        cls.save_manifest(manifest)
        return new_track_info

    @classmethod
    def encode_image_to_audio(cls, image_path: str, label: str, mode: str, description: str = "") -> Dict[str, Any]:
        """画像（JPG/PNG）から SSTV エンコードを実行して音声 WAV を生成し登録"""
        mode = mode.upper()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_label = "".join(c for c in (label or "image") if c.isalnum() or c in ("-", "_")).strip()
        if not base_label:
            base_label = "image_track"
        dest_filename = f"{mode.lower()}_{base_label}_{timestamp}.wav"
        dest_path = os.path.join(AUDIO_DIR, dest_filename)

        if mode == "JPEG":
            from digital_turbo_jpeg.encoder_turbo import DigitalTurboJPEGEncoder
            encoder = DigitalTurboJPEGEncoder()
        elif mode == "FEC":
            from digital_turbo_fec.encoder_turbo import DigitalTurboFECEncoder
            encoder = DigitalTurboFECEncoder()
        else:
            from digital_turbo_png.encoder_turbo import DigitalTurboPNGEncoder
            encoder = DigitalTurboPNGEncoder()

        total_time_sec, total_pkts, saved_path, image_id_val = encoder.encode(image_path, dest_path)
        file_size = os.path.getsize(dest_path)

        manifest = cls.get_manifest()
        encoded_track_info: Dict[str, Any] = {
            "id": dest_filename,
            "filename": dest_filename,
            "label": label or dest_filename,
            "mode": mode,
            "image_id": f"0x{image_id_val:04X}",
            "description": description or f"画像から自動エンコード (全{total_pkts}パケット / {round(total_time_sec, 1)}秒)",
            "packets": total_pkts,
            "duration_sec": round(total_time_sec, 1),
            "is_system": False,
            "size_bytes": file_size,
            "size_mb": round(file_size / (1024 * 1024), 2),
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M")
        }
        manifest["tracks"][dest_filename] = encoded_track_info
        cls.save_manifest(manifest)
        return encoded_track_info

    @classmethod
    def delete_track(cls, filename: str) -> bool:
        """トラックを削除（システムデフォルトは削除不可）"""
        manifest = cls.get_manifest()
        tracks_dict: Dict[str, Any] = manifest.get("tracks", {}) if isinstance(manifest.get("tracks"), dict) else {}
        if filename not in tracks_dict:
            raise ValueError("指定されたトラックが見つかりません")
        
        track = tracks_dict[filename]
        if isinstance(track, dict) and track.get("is_system"):
            raise ValueError("システム初期音声は削除できません")

        full_path = os.path.join(AUDIO_DIR, filename)
        if os.path.exists(full_path):
            try:
                os.remove(full_path)
            except Exception as e:
                print(f"[AudioManager] File remove error: {e}")

        del tracks_dict[filename]

        # もしアクティブだった場合は別のトラックに切り替え
        active = manifest.setdefault("active", {})
        t_mode = track.get("mode", "JPEG") if isinstance(track, dict) else "JPEG"
        if active.get(t_mode) == filename:
            remaining = [t["filename"] for t in tracks_dict.values() if isinstance(t, dict) and t.get("mode") == t_mode]
            active[t_mode] = remaining[0] if remaining else ("turbo_256_256.wav" if t_mode == "JPEG" else "turbo_png_256_256.wav")

        cls.save_manifest(manifest)
        return True
