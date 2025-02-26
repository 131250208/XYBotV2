import json
import requests
import aiohttp
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

    def chat(self, 
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
        
        return self._make_request("chat/chat", payload)

    def reason(self, 
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
        
        return self._make_request("chat/reason", payload)

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

    async def chat_parse_images(self, image_urls: list, detail: str = "low", 
                                max_images: int = 1, max_size_mb: int = 5,
                                chat_role: str = None,**kwargs):
        """聊天模式分析图片"""
        url = f"{self.base_url}/chat/parse_img"
        data = {
            "image_urls": image_urls,
            "detail": detail,
            "max_images": max_images,
            "max_size_mb": max_size_mb,
            "chat_role": chat_role
        }
        
        logger.info(f"准备发送图片分析请求到: {url}")
        logger.info(f"请求数据: {data}")
        
        try:
            async with aiohttp.ClientSession() as session:
                logger.info("创建 aiohttp 会话成功")
                async with session.post(url=url, json=data) as resp:
                    logger.info(f"收到响应状态码: {resp.status}")
                    if resp.status == 200:
                        result = await resp.json()
                        logger.info(f"成功获取响应: {result}")
                        return result
                    else:
                        response_text = await resp.text()
                        logger.error(f"聊天图片分析API请求失败,状态码: {resp.status}")
                        logger.error(f"错误信息: {response_text}")
                        return None
        except aiohttp.ClientError as e:
            logger.error(f"网络请求错误: {str(e)}")
            return None
        except Exception as e:
            logger.error(f"聊天图片分析请求发生异常: {str(e)}")
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

    async def chat_parse_video(self, video_url: str, detail: bool = True, frame_num_per_group: int = 5):
        """聊天模式分析视频"""
        url = f"{self.base_url}/chat/parse_vid"
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
                        logger.error(f"聊天视频分析API请求失败,状态码: {resp.status}")
                        logger.error(f"错误信息: {await resp.text()}")
                        return None
        except Exception as e:
            logger.error(f"聊天视频分析请求发生异常: {str(e)}")
            return None

    async def transcribe_audio(self, media_path: str, language: str = "zh", parallel: bool = True, gpu_fallback: bool = True):
        """音频转写"""
        url = f"{self.base_url}/aud/trans"
        data = {
            "media_path": media_path,
            "language": language,
            "parallel": parallel,
            "gpu_fallback": gpu_fallback
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url=url, json=data) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        logger.error(f"音频转写API请求失败,状态码: {resp.status}")
                        logger.error(f"错误信息: {await resp.text()}")
                        return None
        except Exception as e:
            logger.error(f"音频转写请求发生异常: {str(e)}")
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

    # def parse_image(self,
    #                image_urls: Union[str, list],
    #                detail: str = "low",
    #                max_images: int = 3,
    #                max_size_mb: int = 5,
    #                **kwargs) -> Dict[str, Any]:
    #     """图片解析函数"""
    #     payload = {
    #         "chat_role": kwargs.get("chat_role", "ljs"),
    #         "image_urls": image_urls if isinstance(image_urls, list) else [image_urls],
    #         "detail": detail,
    #         "max_images": max_images,
    #         "max_size_mb": max_size_mb
    #     }
        
    #     return self._make_request("chat/parse_img", payload) 