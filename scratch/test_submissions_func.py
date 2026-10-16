import os
import sys
import sqlite3
import re

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from web_turbo_png.services.auth_db import get_auth_db

def test_func():
    auth_db_obj = get_auth_db()
    users_dict = {}
    users_list = []
    try:
        cursor_u = auth_db_obj.conn.cursor()
        cursor_u.execute("SELECT id, email, created_at FROM users ORDER BY id ASC")
        for uid, uemail, ucreated in cursor_u.fetchall():
            users_dict[uid] = uemail
            users_list.append({"id": uid, "email": uemail})
    except Exception as e:
        print(f"[Admin Submissions] User query error: {e}")

    submissions = []
    modes_to_query = ['PNG', 'JPEG']
    static_out = os.path.join(ROOT_DIR, "web_turbo_png", "static", "output")

    for mode in modes_to_query:
        if mode == 'JPEG':
            db_path = os.path.join(ROOT_DIR, "data/digital_turbo_jpeg/logs/sstv_packets_turbo_jpeg.db")
            conf_tiles = 256
            ext = ".jpg"
        else:
            db_path = os.path.join(ROOT_DIR, "data/digital_turbo_png/logs/sstv_packets_turbo_png.db")
            conf_tiles = 100
            ext = ".png"

        if not os.path.exists(db_path):
            continue

        try:
            conn = sqlite3.connect(db_path, timeout=10.0)
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='packets'")
            if not cur.fetchone():
                conn.close()
                continue

            query = """
                SELECT file_name, user_id, image_id, COUNT(*) as pkt_count,
                       COUNT(DISTINCT tile_y || '_' || tile_x) as tile_count,
                       AVG(snr) as avg_snr, MIN(imported_at) as first_imp, MAX(imported_at) as last_imp
                FROM packets
                GROUP BY file_name, user_id, image_id
                ORDER BY MAX(imported_at) DESC
                LIMIT 400
            """
            cur.execute(query)
            rows = cur.fetchall()
            conn.close()

            for r in rows:
                fn, uid, img_id, pkts, tiles, snr, f_imp, l_imp = r
                m = re.search(r'_(\d{8})_(\d{6})', fn or '')
                if m:
                    d_str, t_str = m.group(1), m.group(2)
                    timestamp = f"{d_str[:4]}-{d_str[4:6]}-{d_str[6:8]} {t_str[:2]}:{t_str[2:4]}:{t_str[4:6]}"
                else:
                    timestamp = l_imp or f_imp or "---"

                img_hex = f"{img_id:04X}" if img_id is not None else "UNKNOWN"
                score = min(100.0, round((tiles / conf_tiles) * 100, 1)) if conf_tiles > 0 else 0.0

                user_email = users_dict.get(uid) if uid else "ゲスト局 (未ログイン)"
                is_guest = (uid is None)

                thumbnail_url = None
                cand_files = []
                if uid:
                    cand_files.append(f"user_{uid}_ID_{img_hex}{ext}")
                    cand_files.append(f"user_cumulative_{uid}_ID_{img_hex}{ext}")
                cand_files.append(f"restored_ID_{img_hex}{ext}")

                for cf in cand_files:
                    full_p = os.path.join(static_out, cf)
                    if os.path.exists(full_p):
                        try:
                            v = int(os.path.getmtime(full_p))
                            thumbnail_url = f"/static/output/{cf}?v={v}"
                        except Exception:
                            thumbnail_url = f"/static/output/{cf}"
                        break

                submissions.append({
                    "id": f"{mode}_{fn}_{img_hex}_{uid}",
                    "mode": mode,
                    "file_name": fn,
                    "user_id": uid,
                    "email": user_email,
                    "is_guest": is_guest,
                    "image_id_hex": img_hex,
                    "packet_count": pkts,
                    "tile_count": tiles,
                    "total_tiles": conf_tiles,
                    "restoration_score": score,
                    "avg_snr": round(snr or 0.0, 1),
                    "timestamp": timestamp,
                    "thumbnail_url": thumbnail_url
                })
        except Exception as e:
            print(f"Error {mode}: {e}")

    submissions.sort(key=lambda s: s["timestamp"], reverse=True)
    print(f"Submissions count: {len(submissions)}")
    for s in submissions[:3]:
        print(s)

if __name__ == '__main__':
    test_func()
