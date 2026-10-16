import sqlite3, os, re

users = {}
if os.path.exists('data/auth.db'):
    u_conn = sqlite3.connect('data/auth.db')
    for uid, email in u_conn.execute('SELECT id, email FROM users').fetchall():
        users[uid] = email

results = []
for mode, db_path, conf_tiles in [('PNG', 'data/digital_turbo_png/logs/sstv_packets_turbo_png.db', 100), ('JPEG', 'data/digital_turbo_jpeg/logs/sstv_packets_turbo_jpeg.db', 256)]:
    if not os.path.exists(db_path):
        continue
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='packets'")
    if not cur.fetchone():
        continue
    rows = cur.execute('''
        SELECT file_name, user_id, image_id, COUNT(*) as pkt_count, 
               COUNT(DISTINCT tile_y || '_' || tile_x) as tile_count,
               AVG(snr) as avg_snr, MIN(imported_at) as first_imp, MAX(imported_at) as last_imp
        FROM packets
        GROUP BY file_name, user_id, image_id
        ORDER BY last_imp DESC
    ''').fetchall()
    for r in rows:
        fn, uid, img_id, pkts, tiles, snr, f_imp, l_imp = r
        m = re.search(r'_(\d{8})_(\d{6})', fn)
        ts = f_imp
        if m:
            d, t = m.group(1), m.group(2)
            ts = f'{d[:4]}-{d[4:6]}-{d[6:8]} {t[:2]}:{t[2:4]}:{t[4:6]}'
        score = min(100.0, round((tiles / conf_tiles) * 100, 1))
        results.append({
            'mode': mode,
            'file_name': fn,
            'user_id': uid,
            'email': users.get(uid, 'ゲスト局 (未ログイン)' if uid is None else f'User #{uid}'),
            'image_id_hex': f'{img_id:04X}' if img_id is not None else 'UNKNOWN',
            'packet_count': pkts,
            'tile_count': tiles,
            'total_tiles': conf_tiles,
            'restoration_score': score,
            'avg_snr': round(snr or 0.0, 1),
            'timestamp': ts
        })

print(f'Total submission sessions: {len(results)}')
for item in results[:10]:
    print(item)
