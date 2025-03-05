#coding=utf-8

'''
requires Python 3.6 or later
pip install requests
'''
import base64
import json
import uuid
import requests
import logging
logger = logging.getLogger(__name__)

def gen_voice(text=None, save_path=None):
    if text is None:
        logger.warn("text must be provided!")
        return
    # 填写平台申请的appid, access_token以及cluster
    appid = "1443310224"
    access_token= "tFw-uvmaKWbX7utBJNRhyyT3IXXCTJ4L"
    cluster = "volcano_icl"

    voice_type = "S_laDJ9eek1"
    host = "openspeech.bytedance.com"
    api_url = f"https://{host}/api/v1/tts"

    header = {"Authorization": f"Bearer;{access_token}"}

    request_json = {
        "app": {
            "appid": appid,
            "token": "access_token",
            "cluster": cluster
        },
        "user": {
            "uid": "388808087185088"
        },
        "audio": {
            "voice_type": voice_type,
            "encoding": "mp3",
            "speed_ratio": 1.0,
            "volume_ratio": 1.0,
            "pitch_ratio": 1.0,
        },
        "request": {
            "reqid": str(uuid.uuid4()),
            "text": text,
            "text_type": "plain",
            "operation": "query",
            "with_frontend": 1,
            "frontend_type": "unitTson"

        }
    }
    try:
        logger.info("请求TTS API")
        resp = requests.post(api_url, json.dumps(request_json), headers=header)
        logger.info(f"resp body: \n{resp.json()}")
        if "data" in resp.json():
            data = resp.json()["data"]
            if save_path:
                file_to_save = open(save_path, "wb")
                file_to_save.write(base64.b64decode(data))
            return data
    except Exception as e:
        e.with_traceback()
if __name__ == '__main__':
    gen_voice("我是刘爽，留几手的留，一个东北老爷们儿", "E:/download/test_submit111.mp3")
