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
import xml.etree.ElementTree as ET
import copy
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
        self.character_tag = config["character_tag"]
        self.uid = config["uid"]
        self.base_url = config["base_url"]
        self.auto_send_interval = config.get("auto_send_interval", 7)  # 消息发送间隔(秒)
        self.active_interval = config.get("active_interval", 300)  # 活跃间隔(秒)
        self.max_voice_length = config.get("max_voice_length", 28)  # 最大语音长度
        self.whitelist = config.get("whitelist", [])  # 白名单
        self.chat_rooms = [id for id in self.whitelist if "@chatroom" in id]  # 聊天室列表
        
        # 初始化API客户端
        self.api_client = APIClient(config["base_url"])
        
        self.uid = config["uid"]
        self.character_tag = config["character_tag"]
        self.agent_type = config["agent_type"]

        # 获取角色名字
        try:
            response = self.api_client.get_agent_names(
                character_tag=self.character_tag,
                agent_type=self.agent_type,
                uid=self.uid
            )
            self.agent_names = response["names"]
            self.main_name = response["main_name"]
            self.nick_names = response["nicknames"]
        except Exception as e:
            logger.error(f"获取角色名字请求发生异常: {str(e)}")
            logger.error(traceback.format_exc())
            self.main_name = self.character_tag
            self.nick_names = [self.character_tag]

        # 初始化状态记录
        self.last_group_chat_time = {}  # 群聊最后交互时间记录
        self.db = XYBotDB()

        # 初始化消息队列和发送任务
        self.message_queue = Queue()  # 消息发送队列
        create_task(self._message_sender())  # 启动消息发送处理器

    ###################
    # 消息处理器部分 #
    ###################

    async def _split_content_by_punctuation(self, content: str) -> list:
        """根据标点符号切分内容
        
        Args:
            content (str): 要切分的内容
            
        Returns:
            list: 切分后的内容列表
        """
        result = []
        content_buffer = content
        last_check_length = 0
        
        while len(content_buffer) >= 10 and len(content_buffer) > last_check_length:
            last_check_length = len(content_buffer)
            
            # 查找所有可能的终止符位置
            punctuation_positions = []
            i = 0
            while i < len(content_buffer):
                # 检查连续的标点符号
                if content_buffer[i] in ['。', '！', '？', '.', '!', '?', '…']:
                    # 检查是否是浮点数中的小数点
                    if content_buffer[i] == '.':
                        # 向前看是否是数字
                        j = i - 1
                        while j >= 0 and content_buffer[j].isspace():
                            j -= 1
                        has_prev_digit = j >= 0 and content_buffer[j].isdigit()
                        
                        # 向后看是否是数字
                        j = i + 1
                        while j < len(content_buffer) and content_buffer[j].isspace():
                            j += 1
                        has_next_digit = j < len(content_buffer) and content_buffer[j].isdigit()
                        
                        # 如果是浮点数的一部分，跳过
                        if has_prev_digit and has_next_digit:
                            i += 1
                            continue
                    
                    start_pos = i
                    # 向后查找连续的相同或相似标点
                    while i + 1 < len(content_buffer) and content_buffer[i+1] in ['。', '！', '？', '.', '!', '?', '…']:
                        i += 1
                    punctuation_positions.append((start_pos, i))
                i += 1

            # 如果找到终止符
            if punctuation_positions:
                last_pos = punctuation_positions[-1][1]
                
                # 处理代码块
                next_backtick = content_buffer.find('`', 0, last_pos)
                if next_backtick != -1:
                    pair_backtick = content_buffer.find('`', next_backtick + 1)
                    if pair_backtick != -1 and pair_backtick > last_pos:
                        break  # 等待代码块结束

                # 切分并更新缓存
                to_send = content_buffer[:last_pos + 1].strip()
                if to_send:  # 确保不发送空消息
                    result.append(to_send)
                    content_buffer = content_buffer[last_pos + 1:]
                    last_check_length = 0  # 重置检查长度

        # 处理剩余内容
        if content_buffer.strip():
            result.append(content_buffer.strip())
            
        return result

    async def _process_message(self, bot: WechatAPIClient, message: dict):
        """处理消息的通用函数
        
        Args:
            bot (WechatAPIClient): 微信API客户端
            message (dict): 消息字典
        """ 
        from_wxid = message["FromWxid"]
        sender_wxid = message["SenderWxid"]
        msg_id = message["NewMsgId"]
        content = message["Content"]
        sender_nickname = await bot.get_nickname(sender_wxid)
        
        # 准备API调用参数
        api_params = {
            "chat_id": from_wxid,
            "msg_id": msg_id,
            "msg_content": content,
            "nick_name": sender_nickname,
            "uid": self.uid,
            "agent_type": self.agent_type,
            "character_tag": self.character_tag,
            "stream": True
        }
        
        # 如果是引用消息,添加cite_msg_id参数
        if "Quote" in message:
            api_params["cite_msg_id"] = int(message["Quote"]["NewMsgId"])
        
        # 调用API获取响应
        reply = self.api_client.uni_chat(**api_params)
        
        if not reply:
            logger.error("API 返回内容为空")
            await bot.send_text_message(from_wxid, "抱歉,系统出现错误")
            return False
            
        # 判断是否为流式返回
        if isinstance(reply, Generator):
            # 创建新任务处理流式响应
            create_task(self._handle_stream_response(bot, from_wxid, reply))
        else:
            try:
                # 处理非流式返回
                if isinstance(reply, dict) and "choices" in reply:
                    content = reply["choices"][0].get("message", {}).get("content", "")
                    if content:
                        # 使用相同的切分逻辑处理内容
                        content_parts = await self._split_content_by_punctuation(content)
                        for part in content_parts:
                            await self.message_queue.put((bot, from_wxid, part))
                    else:
                        logger.error("API返回内容格式不正确")
                        await bot.send_text_message(from_wxid, "抱歉,系统出现错误")
                else:
                    logger.error(f"未知的API返回格式: {type(reply)}")
                    await bot.send_text_message(from_wxid, "抱歉,系统出现错误")
            except Exception as e:
                logger.error(f"处理非流式响应时发生异常: {str(e)}")
                logger.error(traceback.format_exc())
                await bot.send_text_message(from_wxid, "抱歉,系统出现错误")
        
        return False

    @on_text_message(priority=20)
    async def handle_text(self, bot: WechatAPIClient, message: dict):
        """处理文本消息"""
        if not self.enable:
            return

        if message["MsgType"] == 1:
            from_wxid = message["FromWxid"]
            content = message["Content"]
            if from_wxid == "wxid_yaymhyamx8kq22":
                if content.startswith("/"):
                    # 处理命令
                    command = content[1:]
                    if command == "help":
                        await bot.send_text_message(from_wxid, "帮助")
                    elif "add_white:" in command:
                        wxid = command.split(":")[1].strip()
                        if wxid:
                            self.whitelist.append(wxid)
                            await bot.send_text_message(from_wxid, f"已添加白名单: {wxid}")
                        return False
                    elif command == "exit":
                        await bot.send_text_message(from_wxid, "退出")
                        logger.info("收到退出命令，退出")
                        logout_result = await bot.logout()
                        logger.info(f"退出结果: {logout_result}")
                        return False
                    elif command == "restart":
                        await bot.send_text_message(from_wxid, "重启")
                        logger.info("收到重启命令，重启")
                        restart_result = await bot.restart()
                        logger.info(f"重启结果: {restart_result}")
                        return False
                    elif command == "log_out":
                        await bot.send_text_message(from_wxid, "登出")
                        logger.info("收到登出命令，登出")
                        log_out_result = await bot.log_out()
                        logger.info(f"登出结果: {log_out_result}")
                        return False
                    elif command == "QR":
                        qr_code = await bot.get_qr_code()
                        await bot.send_text_message(from_wxid, "二维码")
                        logger.info("收到二维码命令，发送二维码")
                        await bot.send_image_message(from_wxid, qr_code)
                        logger.info(f"二维码: {qr_code}")
                        return False
                    else:
                        await bot.send_text_message(from_wxid, "未知命令")
            try:
                return await self._process_message(bot, message)
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
            return await self._process_message(bot, message)
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
        try:
            return await self._process_message(bot, message)
        except Exception as e:
            logger.error(f"处理消息时发生异常: {str(e)}")
            logger.error(traceback.format_exc())
            await bot.send_text_message(message["FromWxid"], "抱歉,系统出现错误")
        return False

    @on_voice_message(priority=20)
    async def handle_voice(self, bot: WechatAPIClient, message: dict):
        """处理语音消息"""
        if not self.enable:
            return
        
        voice_bytes = message["Content"]
        if voice_bytes:
            try:
                # 添加日志以便调试
                logger.info(f"收到语音消息，大小: {len(voice_bytes)} 字节")
                
                response = self.api_client.transcribe_audio(media_bytes=voice_bytes)
                if not response:
                    logger.error("语音转写失败，返回为空")
                    return False
                    
                logger.info(f"语音转写响应: {response}")
                
                if "response" in response:
                    response_data = response["response"]
                    if response_data:
                        # 提取转写文本
                        if "segments" in response_data:                            
                            # 格式化为XML
                            xml_content = self._format_transcription_to_xml(response_data)
                            logger.info(f"语音转写文本: \n{xml_content}")
                            # 更新消息内容
                            message["Content"] = f"[发了一条语音，内容如下] \n{xml_content}" 
                            
                            return await self._process_message(bot, message)
                        else:
                            logger.warning("语音转写结果不包含文本")
                    else:
                        logger.warning("语音转写响应数据为空")
                else:
                    logger.warning("语音转写响应格式不正确")
            except Exception as e:
                logger.error(f"处理语音消息时发生异常: {str(e)}")
                logger.error(traceback.format_exc())
        
        return False

    @on_image_message(priority=20)
    async def handle_image(self, bot: WechatAPIClient, message: dict):
        """处理图片消息"""
        if not self.enable:
            return
        msg_id = message["NewMsgId"]
        from_wxid = message["FromWxid"]
        sender_wxid = message["SenderWxid"]
        sender_nickname = await bot.get_nickname(sender_wxid)
        # 更新聊天历史
        await self.api_client.append_msg2hist(
            chat_id=from_wxid,
            msg_id=msg_id,
            msg_content=f"[图片加载中...] base64: {message['Content']}",
            nick_name=sender_nickname,
            character_tag=self.character_tag,
            uid=self.uid,
            agent_type=self.agent_type,
            role="user"
        )
        return False

    @on_video_message(priority=20)
    async def handle_video(self, bot: WechatAPIClient, message: dict):
        """处理视频消息"""
        if not self.enable:
            return
        
        video_base64 = message["Video"]
        logger.info(f"[{message['FromWxid']}] 收到视频:{video_base64[:10]} ... {video_base64[-10:]}")
        # message["Content"] = "[视频在加载中...]，base64: " + video_base64
        message["Content"] = "[发了一条视频，但视频你是不想看的，小红书笔记你倒是可以看看]"
        sender_wxid = message["SenderWxid"] 
        sender_nickname = await bot.get_nickname(sender_wxid)
        self.api_client.append_msg2hist(
            chat_id=message["FromWxid"],
            msg_id=message["NewMsgId"],
            msg_content=message["Content"],
            nick_name=sender_nickname,
            character_tag=self.character_tag,
            uid=self.uid,
            agent_type=self.agent_type,
            role="user"
        )
        return False

    @on_file_message(priority=20)
    async def handle_file(self, bot: WechatAPIClient, message: dict):
        """处理文件消息"""
        if not self.enable or message["IsGroup"]:
            return
        return False

    @on_link_message(priority=20)
    async def handle_link(self, bot: WechatAPIClient, message: dict):
        """处理分享链接消息"""
        if not self.enable:
            return
        
        try:            
            if message["Link"]["AppInfo"]["AppName"] == "小红书":
                sender_wxid = message["SenderWxid"] 
                sender_nickname = await bot.get_nickname(sender_wxid)
                message["Content"] = f'[分享了一个小红书链接，简要描述如下，多媒体文件正在加载中...请等一会儿] \n{message["Content"]}'
                # 更新信息到后端
                self.api_client.append_msg2hist(
                    chat_id=message["FromWxid"],
                    msg_id=message["NewMsgId"],
                    msg_content=message["Content"],
                    nick_name=sender_nickname,
                    character_tag=self.character_tag,
                    uid=self.uid,
                    agent_type=self.agent_type,
                    role="user"
                )
            
        except Exception as e:
            logger.error(f"处理链接消息时发生异常: {str(e)}")
            logger.error(traceback.format_exc())
            
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
            last_check_length = 0  # 上次检查的长度

            async for chunk in self._async_iter(reply):
                # 处理推理内容
                if chunk["choices"][0]["delta"].get("reasoning_content"):
                    content = chunk["choices"][0]["delta"]["reasoning_content"]
                    reasoning_buffer.append(content)
                    print(content, end="", flush=True)
                    await asyncio.sleep(0)

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
                    
                    # 只在内容增加时才进行检查，避免重复检查相同的内容
                    if len(content_buffer) >= 10 and len(content_buffer) > last_check_length:
                        last_check_length = len(content_buffer)
                        
                        # 查找所有可能的终止符位置
                        punctuation_positions = []
                        i = 0
                        while i < len(content_buffer):
                            # 检查连续的标点符号
                            if content_buffer[i] in ['。', '！', '？', '.', '!', '?', '…']:
                                # 检查是否是浮点数中的小数点
                                if content_buffer[i] == '.':
                                    # 向前看是否是数字
                                    j = i - 1
                                    while j >= 0 and content_buffer[j].isspace():
                                        j -= 1
                                    has_prev_digit = j >= 0 and content_buffer[j].isdigit()
                                    
                                    # 向后看是否是数字
                                    j = i + 1
                                    while j < len(content_buffer) and content_buffer[j].isspace():
                                        j += 1
                                    has_next_digit = j < len(content_buffer) and content_buffer[j].isdigit()
                                    
                                    # 如果是浮点数的一部分，跳过
                                    if has_prev_digit and has_next_digit:
                                        i += 1
                                        continue
                                
                                start_pos = i
                                # 向后查找连续的相同或相似标点
                                while i + 1 < len(content_buffer) and content_buffer[i+1] in ['。', '！', '？', '.', '!', '?', '…']:
                                    i += 1
                                punctuation_positions.append((start_pos, i))
                            i += 1

                        # 如果找到终止符
                        if punctuation_positions:
                            last_pos = punctuation_positions[-1][1]
                            
                            # 处理代码块
                            next_backtick = content_buffer.find('`', 0, last_pos)
                            if next_backtick != -1:
                                pair_backtick = content_buffer.find('`', next_backtick + 1)
                                if pair_backtick != -1 and pair_backtick > last_pos:
                                    continue  # 等待代码块结束

                            # 发送消息并更新缓存
                            to_send = content_buffer[:last_pos + 1].strip()
                            if to_send:  # 确保不发送空消息
                                await self.message_queue.put((bot, from_wxid, to_send))
                                content_buffer = content_buffer[last_pos + 1:]
                                last_check_length = 0  # 重置检查长度
                    
                    await asyncio.sleep(0)

            # 发送剩余内容
            if content_buffer.strip():
                await self.message_queue.put((bot, from_wxid, content_buffer.strip()))

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
                
                bot, from_wxid, content = msg
                content = re.sub(f'\[msg_id.*?{self.main_name}：', '', content)
                content = content.strip()

                # 根据内容长度计算语音概率
                content_wo_bracket = re.sub(r'[\(\[\{（].*?[\)\]\}）]', '', content)
                content_wo_bracket_len = len(content_wo_bracket)
                p = 1 - (content_wo_bracket_len / self.max_voice_length)
                voice_probability = min(max(p, 0.2), 0.6)

                if random.random() < voice_probability and content_wo_bracket_len >= 4:
                    logger.info(f"向[{from_wxid}] 发送语音消息 (概率:{voice_probability:.2f})")
                    voice_content = gen_voice(content_wo_bracket)
                    if voice_content:
                        logger.info(f"语音消息内容type: type:{type(voice_content)}")
                        remsg_ids = await bot.send_voice_message(from_wxid, voice_content, "mp3")
                    else:
                        logger.info(f"语音消息内容为空")
                        remsg_ids = await bot.send_text_message(from_wxid, content)
                else:
                    logger.info(f"向[{from_wxid}] 发送文本消息")
                    remsg_ids = await bot.send_text_message(from_wxid, content)

                # 更新聊天历史
                self.api_client.append_msg2hist(
                    chat_id=from_wxid, 
                    msg_id=remsg_ids[2],
                    msg_content=content,
                    character_tag=self.character_tag,
                    uid=self.uid,
                    agent_type=self.agent_type,
                    role="assistant"
                )
                
                await asyncio.sleep(self.auto_send_interval * (0.8 + random.random() * 0.4))
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
            api_params (dict): API参数
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

    # def should_reason(self, content: str) -> bool:
    #     """判断是否应该使用推理模式
        
    #     Args:
    #         content (str): 消息内容
            
    #     Returns:
    #         bool: 是否应该使用推理模式
    #     """
    #     content = content.lower()
    #     to_reason = any(keyword in content for keyword in self.reasoning_keywords)
    #     logger.info(f"来自消息[{content}] 是否应该使用推理模式: {to_reason}")
    #     return to_reason

    def _format_transcription_to_xml(self, transcription):
        """将转写结果格式化为XML格式
        
        Args:
            transcription (dict): 转写结果字典
            
        Returns:
            str: 格式化后的XML字符串
        """
        if not transcription or "segments" not in transcription:
            return ""
            
        segments = transcription.get("segments", [])
        if not segments:
            return ""
            
        xml_parts = ["<subs>"]
        
        for segment in segments:
            start = segment.get("start", 0)
            end = segment.get("end", 0)
            text = segment.get("text", "").strip()
            speaker = segment.get("speaker", "")
            
            if text:
                time_range = f"{start:.2f}-{end:.2f}"
                xml_parts.append(f"<sub><t>{time_range}</t>{speaker}<txt>{text}</txt></sub>")
        
        xml_parts.append("</subs>")
        return "\n".join(xml_parts)
