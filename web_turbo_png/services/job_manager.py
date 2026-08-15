import os
import json
import tempfile
import time

JOB_DIR = os.path.join(tempfile.gettempdir(), 'sstv_turbo_png_jobs')
os.makedirs(JOB_DIR, exist_ok=True)

def _get_job_file(job_id):
    return os.path.join(JOB_DIR, f"{job_id}.json")

def create_job(job_id):
    """新しいジョブを初期化"""
    data = {
        "progress": 0,
        "status": "初期化中...",
        "error": "",
        "result_data": {},
        "updated_at": time.time()
    }
    _write_job(job_id, data)

def update_job(job_id, progress=None, status=None, error=None, result_data=None):
    """ジョブの状態を更新"""
    data = get_job(job_id)
    if not data:
        data = {
            "progress": 0,
            "status": "",
            "error": "",
            "result_data": {}
        }
    
    if progress is not None:
        data["progress"] = progress
    if status is not None:
        data["status"] = status
    if error is not None:
        data["error"] = error
    if result_data is not None:
        data["result_data"] = result_data
        
    data["updated_at"] = time.time()
    _write_job(job_id, data)

def get_job(job_id):
    """ジョブの状態を取得"""
    filepath = _get_job_file(job_id)
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None

def _write_job(job_id, data):
    filepath = _get_job_file(job_id)
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"Error writing job {job_id}: {e}")

def cleanup_old_jobs(max_age_seconds=86400):
    """古いジョブファイルを削除"""
    current_time = time.time()
    try:
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
