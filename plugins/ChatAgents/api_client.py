import json
import requests
import aiohttp
import base64
from typing import Dict, Any, Union, Generator
from loguru import logger

class APIClient:
    def __init__(self, base_url: str):
        self.base_url = base_url

    def _make_request(self, 
                    endpoint: str, 
                    payload: Dict[str, Any]) -> Union[Dict[str, Any], Generator[Dict[str, Any], None, None]]:
        """统一的请求处理函数"""
        url = f"{self.base_url}/{endpoint}"
        
        response = requests.post(url, json=payload, stream=payload.get('stream', False))
        
        if response.status_code != 200:
            raise Exception(f"请求失败: {response.status_code} - {response.text}")
        
        if payload.get('stream', False):
            def generate():
                for line in response.iter_lines(decode_unicode=True):
                    if line:
                        chunk = json.loads(line)
                        yield chunk
            return generate()
        else:
            return response.json()

    def chat_w_memory(self, 
             msg_content: str, 
             chat_role: str = "ljs",
             stream: bool = False,
             **kwargs) -> Union[Dict[str, Any], Generator[Dict[str, Any], None, None]]:
        """聊天函数，支持流式和同步响应"""
        payload = {
            "chat_id": kwargs.get("chat_id", "test_chat"),
            "msg_id": kwargs.get("msg_id", 1),
            "msg_content": msg_content,
            "nick_name": kwargs.get("nick_name", "test_user"),
            "chat_role": chat_role,
            "cite_msg_id": kwargs.get("cite_msg_id", None),
            "model": kwargs.get("model", None),
            "max_tokens": kwargs.get("max_tokens", None),
            "temperature": kwargs.get("temperature", None),
            "stream": stream
        }
        
        return self._make_request("chat/chat_w_memory", payload)

    def reason_w_memory(self, 
               msg_content: str, 
               chat_role: str = "ljs",
               stream: bool = False,
               **kwargs) -> Union[Dict[str, Any], Generator[Dict[str, Any], None, None]]:
        """推理函数，支持流式和同步响应"""
        payload = {
            "chat_id": kwargs.get("chat_id", "test_reason"),
            "msg_id": kwargs.get("msg_id", 1),
            "msg_content": msg_content,
            "nick_name": kwargs.get("nick_name", "test_user"),
            "chat_role": chat_role,
            "cite_msg_id": kwargs.get("cite_msg_id", None),
            "model": kwargs.get("model", None),
            "max_tokens": kwargs.get("max_tokens", None),
            "temperature": kwargs.get("temperature", None),
            "stream": stream
        }
        
        return self._make_request("chat/reason_w_memory", payload)

    async def parse_xhs_note(self, note_data: dict, creator_profile: dict = None, tmp_dir: str = None):
        """解析小红书笔记"""
        url = f"{self.base_url}/creator/parse_note"
        data = {
            "note_data": note_data,
            "creator_profile": creator_profile,
            "tmp_dir": tmp_dir
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url=url, json=data) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        logger.error(f"小红书笔记解析API请求失败,状态码: {resp.status}")
                        logger.error(f"错误信息: {await resp.text()}")
                        return None
        except Exception as e:
            logger.error(f"小红书笔记解析请求发生异常: {str(e)}")
            return None

    async def parse_images(self, image_urls: list, detail: bool = True, max_images: int = 10, max_size_mb: int = 10):
        """分析图片"""
        url = f"{self.base_url}/vis/parse_img"
        data = {
            "image_urls": image_urls,
            "detail": detail,
            "max_images": max_images,
            "max_size_mb": max_size_mb
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url=url, json=data) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        logger.error(f"图片分析API请求失败,状态码: {resp.status}")
                        logger.error(f"错误信息: {await resp.text()}")
                        return None
        except Exception as e:
            logger.error(f"图片分析请求发生异常: {str(e)}")
            return None

    async def parse_video(self, video_url: str, detail: bool = True, frame_num_per_group: int = 5):
        """分析视频"""
        url = f"{self.base_url}/vis/parse_vid"
        data = {
            "video_url": video_url,
            "detail": detail,
            "frame_num_per_group": frame_num_per_group
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url=url, json=data) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        logger.error(f"视频分析API请求失败,状态码: {resp.status}")
                        logger.error(f"错误信息: {await resp.text()}")
                        return None
        except Exception as e:
            logger.error(f"视频分析请求发生异常: {str(e)}")
            return None

    async def separate_vocal(self, media_path: str):
        """分离人声"""
        url = f"{self.base_url}/aud/sep_vocal"
        data = {
            "media_path": media_path
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url=url, json=data) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        logger.error(f"人声分离API请求失败,状态码: {resp.status}")
                        logger.error(f"错误信息: {await resp.text()}")
                        return None
        except Exception as e:
            logger.error(f"人声分离请求发生异常: {str(e)}")
            return None

    def update_chat_history(self,
                           msg_content: str,
                           chat_role: str = "ljs",
                           role: str = "user",
                           **kwargs) -> Dict[str, Any]:
        """更新聊天历史"""
        payload = {
            "chat_id": kwargs.get("chat_id", "test_chat"),
            "msg_id": kwargs.get("msg_id", 1),
            "msg_content": msg_content,
            "nick_name": kwargs.get("nick_name", "test_user"),
            "chat_role": chat_role,
            "role": role,
            "cite_msg_id": kwargs.get("cite_msg_id", None)
        }
        response = self._make_request("chat/update_hist", payload)
        return response

    def get_agent_names(self, chat_role: str = "ljs") -> Dict[str, Any]:
        """获取指定角色的所有名字（主名字和昵称）"""
        try:
            response = requests.get(f"{self.base_url}/chat/names/{chat_role}")
            
            if response.status_code != 200:
                logger.error(f"获取角色名字API请求失败,状态码: {response.status_code}")
                logger.error(f"错误信息: {response.text}")
                return None
                
            return response.json()
        except Exception as e:
            logger.error(f"获取角色名字请求发生异常: {str(e)}")
            return None

    def to_oral_style(self, text: str, chat_role: str = "ljs") -> Dict[str, Any]:
        """将文本转换为口语化风格"""
        try:
            payload = {
                "text": text,
                "chat_role": chat_role
            }
            
            return self._make_request("chat/to_oral", payload)
        except Exception as e:
            logger.error(f"转换口语化风格请求发生异常: {str(e)}")
            return None

    def parse_images(self, 
                    image_urls: list, 
                    detail: str = "low", 
                    max_images: int = 1, 
                    max_size_mb: int = 5,
                    **kwargs) -> Dict[str, Any]:
        """聊天模式分析图片"""
        payload = {
            "image_urls": image_urls,
            "detail": detail,
            "max_images": max_images,
            "max_size_mb": max_size_mb
        }
        
        return self._make_request("vis/parse_img", payload)

    def transcribe_audio(self, 
                        media_path: str = None, 
                        media_bytes = None, 
                        language: str = "zh", 
                        parallel: bool = True, 
                        gpu_fallback: bool = True, 
                        model_size: str = "medium") -> Dict[str, Any]:
        """音频转写"""
        # 如果提供了二进制数据，进行Base64编码
        encoded_bytes = None
        if media_bytes:
            if isinstance(media_bytes, bytes):
                encoded_bytes = base64.b64encode(media_bytes).decode('utf-8')
            else:
                encoded_bytes = media_bytes  # 如果已经是字符串，假设已经编码
        
        payload = {
            "media_path": media_path,
            "media_base64": encoded_bytes,
            "language": language,
            "parallel": parallel,
            "gpu_fallback": gpu_fallback,
            "model_size": model_size
        }
        
        return self._make_request("aud/trans", payload)