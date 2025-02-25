import json
import re
import tomllib
import traceback
from pathlib import Path
import aiohttp
import filetype
from loguru import logger
import requests
from WechatAPI import WechatAPIClient
from database.XYBotDB import XYBotDB
from utils.decorators import *
from utils.plugin_base import PluginBase
from other_apis.tts_http_demo import gen_voice

class CharacterAgents(PluginBase):
    description = "CharacterAgents插件"
    author = "WYC"
    version = "0.0.0"

    def __init__(self):
        super().__init__()

        with open("main_config.toml", "rb") as f:
            config = tomllib.load(f)

        self.admins = config["XYBot"]["admins"]

        with open("plugins/ChatAgents/config.toml", "rb") as f:
            config = tomllib.load(f)

        self.enable = config["enable"]
        self.chat_role = config["chat_role"]
        self.api_base = config["api_base"]

        # 记录上次在某个群或者跟哪个人说话的时间
        self.last_group_chat_time = {}
        self.db = XYBotDB()

    async def chat(self, chat_id, msg_id, msg_content, nick_name, chat_role=None, cite_msg_id=None,
             model=None, max_tokens: int = 4096, temperature: float = 0.7, stream: bool = False, *args, **kwargs):
        url = f"{self.api_base}/api/v1/chat"
        data = {
            "chat_id": chat_id,
            "msg_id": msg_id,
            "msg_content": msg_content,
            "nick_name": nick_name,
            "chat_role": chat_role or self.chat_role,
            "cite_msg_id": cite_msg_id,
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream
        }
        
        headers = {"Content-Type": "application/json"}
        payload = json.dumps(data)
        
        ai_resp = ""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url=url, headers=headers, data=payload) as resp:
                    if resp.status == 200:
                        response = await resp.json()
                        try:
                            ai_resp = response["choices"][0]["message"]["content"]
                        except Exception as e:
                            logger.error(f"API返回格式错误: {response}")
                    else:
                        logger.error(f"API请求失败,状态码: {resp.status}")
                        error_text = await resp.text()
                        logger.error(f"错误信息: {error_text}")
        except Exception as e:
            logger.error(f"请求发生异常: {str(e)}")
            logger.error(traceback.format_exc())
        
        return ai_resp

    @on_text_message(priority=20)
    async def handle_text(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return

        if message["IsGroup"]:  # 是群聊
            return
        else:
            if message["MsgType"] == 1:
                try:
                    from_wxid = message["FromWxid"]
                    sender_wxid = message["SenderWxid"]
                    msg_id = message["MsgId"]
                    sender_nickname = await bot.get_nickname(sender_wxid)
                    reply = await self.chat(
                        chat_id=from_wxid,
                        msg_id=msg_id,
                        msg_content=message["Content"],
                        nick_name=sender_nickname
                    )
                    if reply:
                        # 去掉括号里的
                        reply = re.sub(r'\(.*?\)', '', reply)
                        voice_reply = gen_voice(reply)
                        # await bot.send_text_message(message["FromWxid"], reply)
                        await bot.send_voice_message(message["FromWxid"], voice_reply, "mp3")
                    else:
                        logger.error("API 返回内容为空")
                        await bot.send_text_message(message["FromWxid"], "抱歉,系统出现错误")
                except Exception as e:
                    logger.error(f"处理消息时发生异常: {str(e)}")
                    logger.error(traceback.format_exc())
                    await bot.send_text_message(message["FromWxid"], "抱歉,系统出现错误")
        
        return False

    @on_at_message(priority=20)
    async def handle_at(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return
        return False

    @on_voice_message(priority=20)
    async def handle_voice(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return

        if message["IsGroup"]:
            return
            
        return False

    @on_image_message(priority=20)
    async def handle_image(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return

        if message["IsGroup"]:
            return
        
        img_bs64 = message["Content"]
            
        return False

    @on_video_message(priority=20)
    async def handle_video(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return

        if message["IsGroup"]:
            return
            
        return False

    @on_file_message(priority=20)
    async def handle_file(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return

        if message["IsGroup"]:
            return
            
        return False
