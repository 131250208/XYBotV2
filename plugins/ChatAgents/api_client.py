import json
import requests
import aiohttp
import base64
from typing import Dict, Any, Union, Generator, Optional
from loguru import logger
from dataclasses import dataclass, asdict
from tenacity import retry, stop_after_attempt, wait_exponential

class APIClientException(Exception):
    """API客户端异常基类"""
    pass

@dataclass
class ChatRequest:
    chat_id: str
    msg_id: int
    msg_content: str
    nick_name: str
    uid: str
    agent_type: str = "base"
    character_tag: str = "ljs"
    quick_query: str = None
    quote_msg_id: Optional[str] = None
    model: Optional[str] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    stream: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

class APIClient:
    def __init__(self, base_url: str, uid: str, character_tag: str, agent_type: str = "base"):
        self.uid = uid
        self.character_tag = character_tag
        self.agent_type = agent_type
        self.base_url = base_url
        self._session: Optional[aiohttp.ClientSession] = None
        
    async def __aenter__(self):
        self._session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._session:
            await self._session.close()
            
    def _handle_error(self, e: Exception, operation: str) -> None:
        """统一的错误处理"""
        error_msg = f"{operation}失败: {str(e)}"
        logger.error(error_msg)
        raise APIClientException(error_msg)

    def _make_request(self, 
                    endpoint: str, 
                    payload: Dict[str, Any]) -> Union[Dict[str, Any], Generator[Dict[str, Any], None, None]]:
        """统一的请求处理函数"""
        try:
            url = f"{self.base_url}/{endpoint}"
            payload["uid"] = self.uid
            response = requests.post(url, json=payload, stream=payload.get('stream', False))
            
            if response.status_code != 200:
                raise APIClientException(f"API请求失败: {response.status_code} - {response.text}")
            
            # 检查响应的内容类型
            content_type = response.headers.get('content-type', '').lower()
            if 'application/x-ndjson' in content_type:
                return self._handle_stream_response(response)
            
            res_json = response.json()
            return res_json
            
        except Exception as e:
            self._handle_error(e, f"请求 {endpoint}")
            
    def _handle_stream_response(self, response) -> Generator[Dict[str, Any], None, None]:
        """处理流式响应"""
        for line in response.iter_lines(decode_unicode=True):
            if line:
                try:
                    chunk = json.loads(line)
                    yield chunk
                except json.JSONDecodeError as e:
                    self._handle_error(e, "解析流式响应")

    @retry(stop=stop_after_attempt(3), 
           wait=wait_exponential(multiplier=1, min=4, max=10))
    async def _make_async_request(self, 
                                endpoint: str, 
                                payload: Dict[str, Any]) -> Dict[str, Any]:
        """带重试机制的异步请求"""
        if not self._session:
            self._session = aiohttp.ClientSession()
            
        try:
            url = f"{self.base_url}/{endpoint}"
            async with self._session.post(url, json=payload) as response:
                if response.status != 200:
                    text = await response.text()
                    raise APIClientException(f"API请求失败: {response.status} - {text}")
                return await response.json()
        except Exception as e:
            self._handle_error(e, f"异步请求 {endpoint}")

    def chat_w_memory(self, 
                     msg_content: str, 
                     character_tag: str = None,
                     stream: bool = False,
                     **kwargs) -> Union[Dict[str, Any], Generator[Dict[str, Any], None, None]]:
        """聊天函数，支持流式和同步响应"""
        logger.debug(f"chat_w_memory kwargs: {kwargs}")  # 添加日志
        
        request_params = {
            "chat_id": kwargs.get("chat_id", "test_chat"),
            "msg_id": kwargs.get("msg_id", 1),
            "msg_content": msg_content,
            "nick_name": kwargs.get("nick_name", "test_user"),
            "uid": kwargs.get("uid", "test_user"),
            "character_tag": character_tag,
            "agent_type": kwargs.get("agent_type", "base"),
            "stream": stream,
            "quote_msg_id": kwargs.get("quote_msg_id"),
            "model": kwargs.get("model"),
            "max_tokens": kwargs.get("max_tokens"),
            "temperature": kwargs.get("temperature")
        }
        
        logger.debug(f"request_params: {request_params}")  # 添加日志
        
        # 移除None值
        request_params = {k: v for k, v in request_params.items() if v is not None}
        
        logger.debug(f"filtered request_params: {request_params}")  # 添加日志
        
        # 创建请求对象
        request = ChatRequest(**request_params)
        
        return self._make_request("chat/chat_w_memory", request.to_dict())

    def reason_w_memory(self, 
                       msg_content: str, 
                       character_tag: str = None,
                       stream: bool = False,
                       **kwargs) -> Union[Dict[str, Any], Generator[Dict[str, Any], None, None]]:
        """推理函数，支持流式和同步响应"""
        request_params = {
            "chat_id": kwargs.get("chat_id", "test_reason"),
            "msg_id": kwargs.get("msg_id", 1),
            "msg_content": msg_content,
            "nick_name": kwargs.get("nick_name", "test_user"),
            "uid": kwargs.get("uid", "test_user"),
            "character_tag": character_tag,
            "agent_type": kwargs.get("agent_type", "base"),
            "quote_msg_id": kwargs.get("quote_msg_id"),
            "model": kwargs.get("model"),
            "max_tokens": kwargs.get("max_tokens"),
            "temperature": kwargs.get("temperature"),
            "stream": stream
        }
        
        # 移除None值
        request_params = {k: v for k, v in request_params.items() if v is not None}
        
        # 创建请求对象
        request = ChatRequest(**request_params)
        
        return self._make_request("chat/reason_w_memory", request.to_dict())

    def parse_xhs_note(self, note_data_or_url: dict, creator_profile: dict = None, res_style: str = None) -> Dict[str, Any]:
        """解析小红书笔记"""
        payload = {
            "note_data_or_url": note_data_or_url,
            "creator_profile": creator_profile,
            "res_style": res_style
        }
        
        try:
            return self._make_request("creator/parse_note", payload)
        except Exception as e:
            logger.error(f"小红书笔记解析请求发生异常: {str(e)}")
            return None

    def desc_images(self, 
                    image_urls: list, 
                    detail: str = "low", 
                    max_images: int = 1, 
                    max_size_mb: int = 5,
                    **kwargs) -> Dict[str, Any]:
        """分析图片"""
        payload = {
            "image_urls": image_urls,
            "detail": detail,
            "max_images": max_images,
            "max_size_mb": max_size_mb
        }
        
        return self._make_request("vis/desc_images", payload)

    def desc_video_frames(self, 
                          video_url: str, 
                          detail: str = "low", 
                          frame_num_per_group: int = 5) -> Dict[str, Any]:
        """分析视频帧"""
        payload = {
            "video_url": video_url,
            "detail": detail,
            "frame_num_per_group": frame_num_per_group
        }
        
        return self._make_request("vis/desc_video_frames", payload)

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

    def append_msg2hist(self,
                       chat_id: str,
                       msg_id: int,
                       msg_content: str,
                       role: str = "user",
                       nick_name: str = "test_user",
                       quote_msg_id: str = None,
                       **kwargs) -> Dict[str, Any]:
        """更新聊天历史"""
        payload = {
            "chat_id": chat_id,
            "msg_id": msg_id,
            "msg_content": msg_content,
            "nick_name": nick_name,
            "role": role,
            "quote_msg_id": quote_msg_id,
            "uid":  self.uid,
            "character_tag": self.character_tag,
            "agent_type": self.agent_type,
        }
        
        return self._make_request("chat/append_msg2hist", payload)

    def get_agent_names(self, 
                      character_tag: str = "ljs", 
                      agent_type: str = "base",
                      uid: str = "default") -> Dict[str, Any]:
        """获取指定角色的所有名字（主名字和昵称）"""
        try:
            response = requests.get(f"{self.base_url}/chat/names", 
                                 params={
                                     "character_tag": character_tag,
                                     "agent_type": agent_type,
                                     "uid": uid
                                 })
            
            if response.status_code != 200:
                logger.error(f"获取角色名字API请求失败,状态码: {response.status_code}")
                logger.error(f"错误信息: {response.text}")
                return None
                
            return response.json()
        except Exception as e:
            logger.error(f"获取角色名字请求发生异常: {str(e)}")
            return None

    def to_oral_style(self, text: str, character_tag: str = None) -> Dict[str, Any]:
        """将文本转换为口语化风格"""
        try:
            payload = {
                "text": text,
                "character_tag": character_tag
            }
            
            return self._make_request("chat/to_oral", payload)
        except Exception as e:
            logger.error(f"转换口语化风格请求发生异常: {str(e)}")
            return None

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

    def quick_chat(self, 
                  msg_content: str,
                  quick_query: str,
                  character_tag: str = None,
                  stream: bool = False,
                  **kwargs) -> Union[Dict[str, Any], Generator[Dict[str, Any], None, None]]:
        """快速聊天函数，不保存聊天历史"""
        request_params = {
            "chat_id": kwargs.get("chat_id", "test_chat"),
            "msg_id": kwargs.get("msg_id", 1),
            "msg_content": msg_content,
            "quick_query": quick_query,
            "nick_name": kwargs.get("nick_name", "test_user"),
            "uid": kwargs.get("uid", "test_user"),
            "character_tag": character_tag,
            "agent_type": kwargs.get("agent_type", "base"),
            "stream": stream,
            "quote_msg_id": kwargs.get("quote_msg_id"),
            "model": kwargs.get("model"),
            "max_tokens": kwargs.get("max_tokens"),
            "temperature": kwargs.get("temperature")
        }
        
        # 移除None值
        request_params = {k: v for k, v in request_params.items() if v is not None}
        
        # 创建请求对象
        request = ChatRequest(**request_params)
        
        return self._make_request("chat/quick_chat", request.to_dict())

    def uni_chat(self, 
                 msg_content: str,
                 character_tag: str = "ljs",
                 stream: bool = False,
                 **kwargs) -> Union[Dict[str, Any], Generator[Dict[str, Any], None, None]]:
        """统一聊天接口，自动根据聊天历史切换聊天函数"""
        request_params = {
            "chat_id": kwargs.get("chat_id", "test_chat"),
            "msg_id": kwargs.get("msg_id", 1),
            "msg_content": msg_content,
            "nick_name": kwargs.get("nick_name", "test_user"),
            "uid": kwargs.get("uid", "test_user"),
            "character_tag": character_tag,
            "agent_type": kwargs.get("agent_type", "base"),
            "stream": stream,
            "quote_msg_id": kwargs.get("quote_msg_id"),
            "model": kwargs.get("model"),
            "max_tokens": kwargs.get("max_tokens"),
            "temperature": kwargs.get("temperature"),
            "meet_respond_condition": kwargs.get("meet_respond_condition")
        }
        
        # 移除None值
        request_params = {k: v for k, v in request_params.items() if v is not None}
        
        # 创建请求对象
        request = ChatRequest(**request_params)
        
        return self._make_request("chat/uni_chat", request.to_dict())

    def download_media(self,
                      chat_id: str,
                      quote_msg_id: str,
                      character_tag: str = None,
                      agent_type: str = "base") -> Dict[str, Any]:
        """下载媒体消息
        
        Args:
            chat_id (str): 聊天ID
            quote_msg_id (str): 引用的消息ID
            character_tag (str, optional): 角色标签. 默认为 None
            agent_type (str, optional): 代理类型. 默认为 "base"
            
        Returns:
            Dict[str, Any]: 包含下载媒体内容的响应
        """
        payload = {
            "chat_id": chat_id,
            "quote_msg_id": quote_msg_id,
            "character_tag": character_tag,
            "agent_type": agent_type,
            "uid": self.uid,
            "stream": True
        }
        
        # 移除None值
        payload = {k: v for k, v in payload.items() if v is not None}
        
        return self._make_request("chat/download_media", payload)