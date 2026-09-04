import os
import json
import tempfile
import time

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "../../"))
JOB_DIR = os.path.join(ROOT_DIR, "data", "jobs")
os.makedirs(JOB_DIR, exist_ok=True)

def _get_job_file(job_id):
    return os.path.join(JOB_DIR, f"{job_id}.json")

def create_job(job_id=None):
    """新しいジョブを初期化"""
    import uuid
    if not job_id:
        job_id = uuid.uuid4().hex
    data = {
        "progress": 0,
        "status": "初期化中...",
        "error": "",
        "result_data": {},
        "updated_at": time.time()
    }
    _write_job(job_id, data)
    return job_id

def update_job(job_id, progress=None, status=None, error=None, result_data=None, result=None):
    """ジョブの状態を更新"""
    data = get_job(job_id)
    if not data:
        data = {
            "progress": 0,
            "status": "",
            "error": "",
            "result_data": {},
            "updated_at": 0.0
        }
    
    if progress is not None:
        data["progress"] = progress
    if status is not None:
        data["status"] = status
    if error is not None:
        data["error"] = error
    if result_data is not None:
        data["result_data"] = result_data
    elif result is not None:
        data["result_data"] = result
        
    data["updated_at"] = time.time()
    _write_job(job_id, data)

def get_job(job_id, retries=1):
    """ジョブの状態を取得（書き込み直後の競合に備え最大数回リトライ）"""
    filepath = _get_job_file(job_id)
    for attempt in range(max(1, retries)):
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                if attempt < retries - 1:
                    time.sleep(0.05)
                    continue
        elif attempt < retries - 1:
            time.sleep(0.05)
    return None

def _write_job(job_id, data):
    """アトミック書き込みで読み込み側のJSONパースエラーを防ぐ"""
    filepath = _get_job_file(job_id)
    temp_filepath = f"{filepath}.tmp.{time.time()}"
    try:
        with open(temp_filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        # Windowsでも上書き可能な安全な置換
        if os.path.exists(filepath):
            try:
                os.replace(temp_filepath, filepath)
            except OSError:
                # 代替手法
                os.remove(filepath)
                os.rename(temp_filepath, filepath)
        else:
            os.rename(temp_filepath, filepath)
    except Exception as e:
        print(f"Error writing job {job_id}: {e}")
        try:
            if os.path.exists(temp_filepath):
                os.remove(temp_filepath)
        except Exception:
            pass

def cleanup_old_jobs(max_age_seconds=86400):
    """古いジョブファイルを削除"""
    current_time = time.time()
    try:
        if not os.path.exists(JOB_DIR):
            return
        for filename in os.listdir(JOB_DIR):
            if not filename.endswith('.json'):
                continue
            filepath = os.path.join(JOB_DIR, filename)
            try:
                if os.path.getmtime(filepath) < current_time - max_age_seconds:
                    os.remove(filepath)
            except Exception:
                pass
    except Exception:
        pass
