import os, subprocess
from pathlib import Path
from urllib.parse import urlparse

class AcceptanceError(ValueError): pass

def check_media(path, expected_width=None, expected_height=None):
    if not Path(path).is_file() or Path(path).stat().st_size == 0: raise AcceptanceError('VIDEO_INVALID')
    try:
        out=subprocess.run(['ffprobe','-v','error','-show_streams','-of','json',str(path)],capture_output=True,text=True,check=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as e: raise AcceptanceError('FFPROBE_UNAVAILABLE_OR_INVALID') from e
    import json
    streams=json.loads(out.stdout).get('streams',[])
    video=next((s for s in streams if s.get('codec_type')=='video'),None)
    if not video: raise AcceptanceError('VIDEO_INVALID')
    if expected_width and video.get('width') != expected_width: raise AcceptanceError('VIDEO_DIMENSION_MISMATCH')
    if expected_height and video.get('height') != expected_height: raise AcceptanceError('VIDEO_DIMENSION_MISMATCH')
    return {'valid':True,'has_audio':any(s.get('codec_type')=='audio' for s in streams)}

def validate_download_url(url, allowed_hosts):
    parsed=urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in set(allowed_hosts): raise AcceptanceError('DOWNLOAD_HOST_NOT_ALLOWED')
    return True
