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
from typing import Dict, Any, Union, Generator
import random
import asyncio
from .api_client import APIClient
from asyncio import Queue, create_task
import time

class CharacterAgents(PluginBase):
    """角色代理插件
    
    处理与AI角色的对话交互，支持流式输出和异步消息处理。
    """
    description = "CharacterAgents插件"
    author = "WYC"
    version = "0.0.0"

    def __init__(self):
        """初始化插件
        
        加载配置文件，初始化API客户端和消息队列。
        """
        super().__init__()

        # 加载主配置
        with open("main_config.toml", "rb") as f:
            config = tomllib.load(f)
        self.admins = config["XYBot"]["admins"]

        # 加载插件配置
        with open("plugins/ChatAgents/config.toml", "rb") as f:
            config = tomllib.load(f)

        # 初始化基本配置
        self.enable = config["enable"]
        self.chat_role = config["chat_role"]
        self.base_url = config["base_url"]
        self.auto_send_interval = config.get("auto_send_interval", 7)  # 消息发送间隔(秒)
        self.active_interval = config.get("active_interval", 300)  # 活跃间隔(秒)
        self.reasoning_keywords = config.get("reasoning_keywords", [])  # 推理关键词列表

        # 初始化API客户端
        self.api_client = APIClient(config["base_url"])
        # 获取角色名字
        try:
            response = self.api_client.get_agent_names(self.chat_role)
            self.agent_names = response["names"]
            self.main_name = response["main_name"]
            self.nick_names = response["nicknames"]
        except Exception as e:
            logger.error(f"获取角色名字请求发生异常: {str(e)}")
            logger.error(traceback.format_exc())
            self.main_name = self.chat_role
            self.nick_names = [self.chat_role]

        # 初始化状态记录
        self.last_group_chat_time = {}  # 群聊最后交互时间记录
        self.db = XYBotDB()

        # 初始化消息队列和发送任务
        self.message_queue = Queue()  # 消息发送队列
        create_task(self._message_sender())  # 启动消息发送处理器

    ###################
    # 消息处理器部分 #
    ###################

    @on_text_message(priority=20)
    async def handle_text(self, bot: WechatAPIClient, message: dict):
        """处理文本消息"""
        if not self.enable:
            return

        if message["MsgType"] == 1:
            try:
                # 判断是否需要响应
                current_time = time.time()
                if not self.should_respond(message, current_time):
                    return False
                    
                from_wxid = message["FromWxid"]
                sender_wxid = message["SenderWxid"]
                msg_id = message["MsgId"]
                content = message["Content"]
                sender_nickname = await bot.get_nickname(sender_wxid)
                
                # 判断是否使用推理模式
                api_method = self.api_client.reason if self.should_reason(content) else self.api_client.chat
                
                # 创建新任务处理流式响应
                create_task(self._handle_stream_response(
                    bot, from_wxid, api_method(
                        chat_id=from_wxid,
                        msg_id=msg_id,
                        msg_content=content,
                        nick_name=sender_nickname,
                        stream=True
                    )
                ))
                
            except Exception as e:
                logger.error(f"处理消息时发生异常: {str(e)}")
                logger.error(traceback.format_exc())
                await bot.send_text_message(message["FromWxid"], "抱歉,系统出现错误")
        
        return False
    
    @on_quote_message(priority=25)
    async def handle_quote(self, bot: WechatAPIClient, message: dict):
        """处理引用消息"""
        if not self.enable:
            return
        try:
            # 判断是否需要响应
            current_time = time.time()
            if not self.should_respond(message, current_time):
                return False
            
            from_wxid = message["FromWxid"]
            sender_wxid = message["SenderWxid"]
            msg_id = message["NewMsgId"]
            content = message["Content"]
            sender_nickname = await bot.get_nickname(sender_wxid)
            cite_msg_id = int(message["Quote"]["NewMsgId"])
            
            # 判断是否使用推理模式
            api_method = self.api_client.reason if self.should_reason(content) else self.api_client.chat
            
            # 创建新任务处理流式响应
            create_task(self._handle_stream_response(
                bot, from_wxid, api_method(
                    chat_id=from_wxid,
                    msg_id=msg_id,
                    msg_content=content,
                    nick_name=sender_nickname,
                    cite_msg_id=cite_msg_id,
                    stream=True
                )
            ))
        except Exception as e:
            logger.error(f"处理消息时发生异常: {str(e)}")
            logger.error(traceback.format_exc())
            await bot.send_text_message(message["FromWxid"], "抱歉,系统出现错误")
        return False

    @on_at_message(priority=20)
    async def handle_at(self, bot: WechatAPIClient, message: dict):
        """处理@消息"""
        if not self.enable:
            return
        return False

    @on_voice_message(priority=20)
    async def handle_voice(self, bot: WechatAPIClient, message: dict):
        """处理语音消息"""
        if not self.enable or message["IsGroup"]:
            return
        return False

    @on_image_message(priority=20)
    async def handle_image(self, bot: WechatAPIClient, message: dict):
        """处理图片消息"""
        if not self.enable or message["IsGroup"]:
            return
        logger.info(f"[{message['FromWxid']}] 收到图片消息，已发送解析")
        response = await self.api_client.chat_parse_images(
            message["Content"],
            chat_role=self.chat_role
        )
        img_desc = response["response"]["content"]
        logger.info(f"[{message['FromWxid']}] 图片描述: {img_desc}")
        msg_id = message["NewMsgId"]
        from_wxid = message["FromWxid"]
        sender_wxid = message["SenderWxid"]
        sender_nickname = await bot.get_nickname(sender_wxid)
        # 更新聊天历史
        await self.api_client.update_chat_history(
            chat_id=from_wxid,
            msg_id=msg_id,
            msg_content=img_desc,
            nick_name=sender_nickname,
            chat_role=self.chat_role,
            role="user"
        )
        logger.info(f"[{message['FromWxid']}] 图片描述已更新到历史记录")
        return False

    @on_video_message(priority=20)
    async def handle_video(self, bot: WechatAPIClient, message: dict):
        """处理视频消息"""
        if not self.enable or message["IsGroup"]:
            return
        return False

    @on_file_message(priority=20)
    async def handle_file(self, bot: WechatAPIClient, message: dict):
        """处理文件消息"""
        if not self.enable or message["IsGroup"]:
            return
        return False

    ###################
    # 流式响应处理部分 #
    ###################

    async def _handle_stream_response(self, bot: WechatAPIClient, from_wxid: str, reply):
        """处理流式响应
        
        处理AI的流式输出，包括推理过程和正式回答。
        使用异步迭代和消息队列实现非阻塞的消息处理。
        """
        try:
            if not reply:
                logger.error("API 返回内容为空")
                await bot.send_text_message(from_wxid, "抱歉,系统出现错误")
                return

            content_buffer = ""  # 正式回答的缓存
            reasoning_buffer = []  # 推理过程的缓存
            reasoning_finished = False  # 推理阶段标记

            async for chunk in self._async_iter(reply):
                # 处理推理内容
                if chunk["choices"][0]["delta"].get("reasoning_content"):
                    content = chunk["choices"][0]["delta"]["reasoning_content"]
                    reasoning_buffer.append(content)
                    print(content, end="", flush=True)
                    await asyncio.sleep(0)  # 让出控制权

                # 处理正式回答
                if chunk["choices"][0]["delta"].get("content"):
                    if not reasoning_finished:
                        reasoning_finished = True
                        logger.info(f"完整推理过程:\n{''.join(reasoning_buffer)}")
                        print("\n正式回答：")
                        reasoning_buffer = []

                    content = chunk["choices"][0]["delta"]["content"]
                    print(content, end="", flush=True)
                    content_buffer += content
                    await asyncio.sleep(0)  # 让出控制权

                    # 检查是否需要发送消息
                    if len(content_buffer) >= 10:
                        for i, char in enumerate(content_buffer):
                            if char in ['。', '！', '？', '.', '!', '?']:
                                # 处理代码块(反引号包围的内容)
                                next_backtick = content_buffer.find('`', i)
                                if next_backtick != -1 and next_backtick > i:
                                    pair_backtick = content_buffer.find('`', next_backtick + 1)
                                    if pair_backtick != -1:
                                        await self.message_queue.put((bot, from_wxid, content_buffer[:pair_backtick + 1]))
                                        content_buffer = content_buffer[pair_backtick + 1:]
                                        break
                                    continue

                                # 发送普通文本
                                await self.message_queue.put((bot, from_wxid, content_buffer[:i + 1]))
                                content_buffer = content_buffer[i + 1:]
                                break

            # 发送剩余内容
            if content_buffer:
                await self.message_queue.put((bot, from_wxid, content_buffer))

        except Exception as e:
            logger.error(f"处理流式响应时发生异常: {str(e)}")
            logger.error(traceback.format_exc())
            await bot.send_text_message(from_wxid, "抱歉,系统出现错误")

    ###################
    # 消息发送队列部分 #
    ###################

    async def _message_sender(self):
        """异步消息发送处理器
        
        持续监听消息队列，按序发送消息并控制发送间隔。
        消息格式: (bot, to_wxid, content)
        """
        while True:
            try:
                msg = await self.message_queue.get()
                if msg is None:
                    continue
                
                # 发送消息
                # 消息预处理
                # re去除开头的[时间] [昵称]：
                bot, to_wxid, content = msg
                content = re.sub(r'^\[msg_id=\d+\] \[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] .*?：', '', content)
                content = content.strip()
                client_msgid, create_time, new_msg_id = await bot.send_text_message(to_wxid, content)
                
                # 更新聊天历史
                self.api_client.update_chat_history(
                    chat_id=to_wxid,
                    msg_id=new_msg_id,
                    msg_content=content,
                    chat_role=self.chat_role,
                    role="assistant"
                )
                
                # 添加随机延迟(0.8-1.2倍基础间隔)
                delay = self.auto_send_interval * (0.8 + random.random() * 0.4)
                await asyncio.sleep(delay)
                
                self.message_queue.task_done()
            except Exception as e:
                logger.error(f"消息发送异常: {str(e)}")
                logger.error(traceback.format_exc())

    ###################
    # 工具函数部分 #
    ###################

    async def _async_iter(self, sync_iter):
        """将同步迭代器转换为异步迭代器
        
        在每次迭代后让出控制权，避免阻塞事件循环。
        """
        for item in sync_iter:
            yield item
            await asyncio.sleep(0)  # 让出控制权

    def should_respond(self, message: dict, current_time: float) -> bool:
        """判断是否应该响应消息
        
        Args:
            message (dict): 消息字典
            current_time (float): 当前时间戳
            
        Returns:
            bool: 是否应该响应
        """
        # 如果不是群聊，100%回复
        if not message["IsGroup"]:
            return True
        
        from_wxid = message["FromWxid"]
        # 检查消息内容是否包含角色名字
        content = message.get("Content", "").lower()
        include_name = any(name.lower() in content for name in self.agent_names)
        logger.info(f"[{from_wxid}] 检查消息内容是否包含角色名字: {include_name}")
        if include_name:
            # 更新最后交互时间
            self.last_group_chat_time[from_wxid] = current_time
            return True
        
        last_time = self.last_group_chat_time.get(from_wxid, 0)
        gap = current_time - last_time
        logger.info(f"[{from_wxid}] 最后交互时间: {last_time}, 当前时间: {current_time}, 间隔: {gap}秒")
        if gap > (self.active_interval * 1000):
            # 更新最后交互时间
            self.last_group_chat_time[from_wxid] = current_time
            logger.info(f"[{from_wxid}] 超过活跃间隔，已过去{gap}秒，开始回复")
            return True
        
        return False

    def should_reason(self, content: str) -> bool:
        """判断是否应该使用推理模式
        
        Args:
            content (str): 消息内容
            
        Returns:
            bool: 是否应该使用推理模式
        """
        content = content.lower()
        return any(keyword in content for keyword in self.reasoning_keywords)
