import os
import glob
import shutil

import sys
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

ROOT_DIR = os.path.abspath(os.path.dirname(__file__))

def clean_data():
    print("[Clean] Cleaning data files...")
    
    # 1. data/logs/ 以下のログとDB
    logs_dir = os.path.join(ROOT_DIR, "data", "logs")
    if os.path.exists(logs_dir):
        for f in glob.glob(os.path.join(logs_dir, "*")):
            try:
                if os.path.isfile(f) or os.path.islink(f):
                    os.remove(f)
                    print(f"  Deleted: {f}")
                elif os.path.isdir(f):
                    shutil.rmtree(f)
                    print(f"  Deleted dir: {f}")
            except Exception as e:
                print(f"  Error deleting {f}: {e}")

    # 2. data/images/ 以下の復元画像
    images_dir = os.path.join(ROOT_DIR, "data", "images")
    if os.path.exists(images_dir):
        for f in glob.glob(os.path.join(images_dir, "*")):
            try:
                if os.path.isfile(f) or os.path.islink(f):
                    os.remove(f)
                    print(f"  Deleted: {f}")
            except Exception as e:
                print(f"  Error deleting {f}: {e}")

    # 3. data/audio/ 以下のWAVファイル
    audio_dir = os.path.join(ROOT_DIR, "data", "audio")
    if os.path.exists(audio_dir):
        for f in glob.glob(os.path.join(audio_dir, "*")):
            try:
                if os.path.isfile(f) or os.path.islink(f):
                    os.remove(f)
                    print(f"  Deleted: {f}")
            except Exception as e:
                print(f"  Error deleting {f}: {e}")

    # 4. web_turbo_png/static/output/ 以下の出力画像
    output_dir = os.path.join(ROOT_DIR, "web_turbo_png", "static", "output")
    if os.path.exists(output_dir):
        for f in glob.glob(os.path.join(output_dir, "*")):
            try:
                if os.path.isfile(f) or os.path.islink(f):
                    os.remove(f)
                    print(f"  Deleted: {f}")
            except Exception as e:
                print(f"  Error deleting {f}: {e}")

    # 5. web_turbo_png/static/uploads/ 以下のアップロードファイル
    uploads_dir = os.path.join(ROOT_DIR, "web_turbo_png", "static", "uploads")
    if os.path.exists(uploads_dir):
        for f in glob.glob(os.path.join(uploads_dir, "*")):
            try:
                if os.path.isfile(f) or os.path.islink(f):
                    os.remove(f)
                    print(f"  Deleted: {f}")
            except Exception as e:
                print(f"  Error deleting {f}: {e}")

    # 6. data/digital_turbo_png/ ディレクトリの中身
    dt_dir = os.path.join(ROOT_DIR, "data", "digital_turbo_png")
    if os.path.exists(dt_dir):
        for root, dirs, files in os.walk(dt_dir):
            for f in files:
                try:
                    os.remove(os.path.join(root, f))
                    print(f"  Deleted: {os.path.join(root, f)}")
                except Exception as e:
                    print(f"  Error deleting {f}: {e}")

    print("[Clean] Data cleaning completed successfully!")

if __name__ == "__main__":
    clean_data()
