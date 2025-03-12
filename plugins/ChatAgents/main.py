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
from .media.online_img_url import excel_logo_url
import threading
from queue import Queue as ThreadQueue
from concurrent.futures import ThreadPoolExecutor

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
        self.auto_send_interval = config.get("auto_send_interval", 3)  # 消息发送间隔(秒)
        self.active_interval = config.get("active_interval", 300)  # 活跃间隔(秒)
        self.max_voice_length = config.get("max_voice_length", 28)  # 最大语音长度
        self.enable_voice = config.get("enable_voice", False)  # 是否启用语音功能
        self.whitelist = config.get("whitelist", [])  # 白名单
        self.chat_rooms = [id for id in self.whitelist if "@chatroom" in id]  # 聊天室列表
        self.stream_split_sentence = config.get("stream_split_sentence", True)  # 是否在流式输出中断句发送
        
        # 编译断句正则表达式
        self.split_pattern = re.compile(r'(?<!\.)\s+\w+[.。！？!?…]+(?![.。！？!?…])|(?<![:：])\n\n')
        
        # 配对符号映射
        self.paired_symbols = {
            "'": "'",
            '"': '"',
            "“": "”",
            "‘": "’"
        }

        self.uid = config["uid"]
        self.character_tag = config["character_tag"]
        self.agent_type = config["agent_type"]

        # 初始化API客户端
        self.api_client = APIClient(config["base_url"], self.uid, self.character_tag, self.agent_type)
        
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

        # 初始化线程安全的消息队列
        self.message_queue = ThreadQueue()
        
        # 创建事件循环对象供消息发送线程使用
        self.sender_loop = asyncio.new_event_loop()
        
        # 创建线程池
        self.thread_pool = ThreadPoolExecutor(max_workers=2)
        
        # 启动消息发送线程
        self.sender_thread = threading.Thread(target=self._run_sender_thread, daemon=True)
        self.sender_thread.start()
        logger.info("消息发送线程已启动")

    def _run_sender_thread(self):
        """运行消息发送线程"""
        # 设置线程的事件循环
        asyncio.set_event_loop(self.sender_loop)
        
        # 在新线程中运行事件循环
        self.sender_loop.run_until_complete(self._message_sender())

    ###################
    # 消息处理器部分 #
    ###################

    def _check_chunk_end_for_split(self, text: str, max_look_back: int = 50) -> tuple:
        """检查文本末尾是否存在断句位置
        
        智能检测断句位置，处理特殊区域（引号内、代码块内）和英文句号
        
        Args:
            text (str): 要检查的文本
            max_look_back (int): 最大向前检查的字符数
            
        Returns:
            tuple: (是否找到断句位置, 断句位置索引, 是否在特殊区域中)
        """
        if not text:
            return False, -1, False
            
        # 只检查文本末尾部分
        check_start = max(0, len(text) - max_look_back)
        tail_text = text[check_start:]
        
        # 检查是否在代码块内
        code_blocks = tail_text.count('```')
        if code_blocks % 2 != 0:
            extended_text = text[max(0, check_start - 100):] 
            if extended_text.count('```') % 2 != 0:
                return False, -1, True
        
        # 检查引号配对状态
        stack = []
        for i, char in enumerate(tail_text):
            if char in self.paired_symbols or char in self.paired_symbols.values():
                if not stack or (char != self.paired_symbols.get(stack[-1])):
                    stack.append(char)
                else:
                    stack.pop()
        
        if stack:  # 如果栈不为空，说明在引号内
            return False, -1, True
            
        # 查找最后一个有效的断句位置
        matches = list(self.split_pattern.finditer(tail_text))
        if matches:
            match = matches[-1]
            global_pos = check_start + match.end()
            # 验证不是连续的标点
            next_char = text[global_pos:global_pos + 1]
            if not next_char or next_char not in '.。！？!?…':
                return True, global_pos, False
                
        return False, -1, False

    async def _split_content_by_punctuation(self, content: str) -> list:
        """根据标点符号切分内容"""
        result = []
        content_buffer = content
        last_check_length = 0
        
        while len(content_buffer) >= 10 and len(content_buffer) > last_check_length:
            last_check_length = len(content_buffer)
            
            found, pos, in_code = self._check_chunk_end_for_split(content_buffer)
            if found and not in_code:
                to_send = content_buffer[:pos].strip()
                if to_send:  # 确保不发送空消息
                    result.append(to_send)
                content_buffer = content_buffer[pos:].strip()
                last_check_length = 0  # 重置检查长度
            elif in_code:
                break

        # 处理剩余内容
        remaining = content_buffer.strip()
        if remaining:
            result.append(remaining)
            
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
        
        # 如果是引用消息,添加quote_msg_id参数
        if "Quote" in message:
            api_params["quote_msg_id"] = int(message["Quote"]["NewMsgId"])
        
        # 调用API获取响应
        reply = self.api_client.uni_chat(**api_params)
        
        if not reply:
            logger.error("API 返回内容为空")
            await bot.send_text_message(from_wxid, "抱歉,系统出现错误")
            return False
            
        # 判断是否为流式返回
        if isinstance(reply, Generator):
            # 在线程池中处理流式响应
            self.thread_pool.submit(self._run_stream_handler, bot, from_wxid, reply)
            logger.info("已在新线程中启动流式响应处理")
        else:
            try:
                # 处理非流式返回
                if isinstance(reply, dict) and "choices" in reply:
                    content = reply["choices"][0].get("message", {}).get("content", "")
                    if content:
                        # 使用相同的切分逻辑处理内容
                        content_parts = await self._split_content_by_punctuation(content)
                        for part in content_parts:
                            self.message_queue.put((bot, from_wxid, part))
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

    def _run_stream_handler(self, bot: WechatAPIClient, from_wxid: str, reply):
        """在新线程中运行流式处理
        
        Args:
            bot (WechatAPIClient): 微信API客户端
            from_wxid (str): 发送者ID
            reply (Generator): 流式响应生成器
        """
        try:
            # 直接调用同步处理方法
            self._handle_stream_response(bot, from_wxid, reply)
        except Exception as e:
            logger.error(f"流式处理线程发生异常: {str(e)}")
            logger.error(traceback.format_exc())
            # 将错误消息放入队列
            self.message_queue.put((bot, from_wxid, "抱歉,系统出现错误"))

    def _handle_stream_response(self, bot: WechatAPIClient, from_wxid: str, reply):
        """处理流式响应
        
        同步处理AI的流式输出，不涉及任何异步操作
        """
        try:
            if not reply:
                logger.error("API 返回内容为空")
                self.message_queue.put((bot, from_wxid, "抱歉,系统出现错误"))
                return

            content_buffer = ""  # 正式回答的缓存
            reasoning_buffer = []  # 推理过程的缓存
            reasoning_finished = False  # 推理阶段标记

            for chunk in reply:  # 同步迭代处理
                if not isinstance(chunk, dict):
                    raise ValueError("流式响应内容不是字典类型, 而是: {}, \n{}".format(type(chunk), chunk))
                
                # 处理推理内容
                if chunk["choices"][0]["delta"].get("reasoning_content"):
                    content = chunk["choices"][0]["delta"]["reasoning_content"]
                    reasoning_buffer.append(content)
                    print(content, end="", flush=True)

                # 处理正式回答
                if chunk["choices"][0]["delta"].get("content"):
                    if not reasoning_finished:
                        reasoning_finished = True
                        reason_content = ''.join(reasoning_buffer)
                        if reason_content:
                            logger.info(f"完整推理过程:\n{reason_content}")
                        print("\n正式回答：")
                        reasoning_buffer = []

                    content = chunk["choices"][0]["delta"]["content"]
                    
                    # 如果content是字典类型，直接放入队列
                    if isinstance(content, dict):
                        self.message_queue.put((bot, from_wxid, content))
                        continue

                    if chunk.get("chunk_type") != "big_chunk":
                        print(content, end="", flush=True)                        
                    
                    # 将新chunk添加到缓冲区
                    if chunk.get("chunk_type") != "connect":
                        # print(content, end="", flush=True)
                        prev_length = len(content_buffer)
                        content_buffer += content

                    # 高效断句检测 - 只检查最新添加内容附近的断句可能性
                    if self.stream_split_sentence and len(content_buffer) >= 10:
                        # 记录检测开始时间（用于性能监控）
                        detect_start = time.time()
                        
                        # 使用末端检测函数
                        found, pos, in_special = self._check_chunk_end_for_split(content_buffer)
                        
                        # 记录检测耗时
                        detect_duration = time.time() - detect_start
                        
                        if found and not in_special:
                            to_send = content_buffer[:pos].strip()
                            if to_send:
                                self.message_queue.put((bot, from_wxid, to_send))
                                # 更新缓冲区，保留未发送部分
                                content_buffer = content_buffer[pos:].strip()

            # 发送剩余内容
            if content_buffer.strip():
                self.message_queue.put((bot, from_wxid, content_buffer))

        except Exception as e:
            logger.error(f"处理流式响应时发生异常: {str(e)}")
            logger.error(traceback.format_exc())
            self.message_queue.put((bot, from_wxid, "抱歉,系统出现错误"))

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
                    elif command == "test_file":
                        # 文件分享数据
                        file_data = {
                            "title": "副本选题 2.17-2.24.xlsx",
                            "fileext": "xlsx",
                            "totallen": "26672",
                            "attachid": "@cdn_3057020100044b30490201000204e2d905f802032f559502047b42f7df020467c91f83042465376461376563342d373764622d346334302d613036632d6161343432393537316630640204051400050201000405004c53d900_31343363333937636530363035316466_1",
                            "cdnattachurl": "3057020100044b30490201000204e2d905f802032f559502047b42f7df020467c91f83042465376461376563342d373764622d346334302d613036632d6161343432393537316630640204051400050201000405004c53d900",
                            "aeskey": "31343363333937636530363035316466",
                            "filekey": "wxid_m9lhdlutblw812_356_1741234050",
                            "md5": "789223cd3c3a23cbc725255c24c1381a",
                            "fileuploadtoken": "v1_e9+VSimc4jevzsqeYNSPRlJRjCjwBPRWfmF0/Msm1SSeS3dVhWzFdDCtVl36Oh4HkWKn18n0qfPWs0UGUR2nOAG5VcMsuZehOcYFdITNvu0AvaNht6kB6C7+SCv/lyq2kuSQK55ZkUmLGiUBXiiVbtqxv7X1XvqFG3jTtU1CmVxiyg9NBJOisBwt94Ll1VaNmgz7Kv7fnLDORRAQRU6VJolwuABLItqAI7U="
                        }

                        xml_content = f"""<appmsg appid="" sdkver="0"><title>{file_data['title']}</title><type>6</type><appattach><totallen>{file_data['totallen']}</totallen><fileext>{file_data['fileext']}</fileext><attachid>{file_data['attachid']}</attachid><cdnattachurl>{file_data['cdnattachurl']}</cdnattachurl><cdnthumbaeskey/><aeskey>{file_data['aeskey']}</aeskey><encryver>0</encryver><filekey>{file_data['filekey']}</filekey><fileuploadtoken>{file_data['fileuploadtoken']}</fileuploadtoken></appattach><md5>{file_data['md5']}</md5></appmsg><fromusername>{bot.wxid}</fromusername><scene>0</scene><appinfo><version>1</version><appname/></appinfo><commenturl/>"""
                        await bot.send_cdn_file_msg(from_wxid, xml_content)
                    elif command == "test_cdn":
                        # 文件分享数据
                        file_data = {
                            "title": "爆款选题总结.xlsx",
                            "fileext": "xlsx",
                            "totallen": "9227",
                            "attachid": "@cdn_3057020100044b30490201000204e2d905f802032f55950204e941f7df020467c94541042462343739353536612d623665322d346564382d626466652d6361646539383932643136650204051400050201000405004c4dfd00_5bfcf0a194a34ae0a37f93f5a01f05e5_1",
                            "cdnattachurl": "3057020100044b30490201000204e2d905f802032f55950204e941f7df020467c94541042462343739353536612d623665322d346564382d626466652d6361646539383932643136650204051400050201000405004c4dfd00",
                            "aeskey": "5bfcf0a194a34ae0a37f93f5a01f05e5",
                            "filekey": "wxid_m9lhdlutblw812_375_1741243713",
                            "md5": "24738268095109cd1e06a4836e9d8350",
                            "fileuploadtoken": "v1_zZQVjrScWMbgGjAHKV6hRBHDiZ0NmFYuq26XIoHV8ScVCCHtUFdULbaQ6muFH94Lkpmd7fhSlwEvzoKRM3C/wYPaoDeUsyXCGoUo3mpr+Ffk4guRbRH2p1cUxkbX//xqDyCeRErRnFFimsk9220O9vBsIZuuEghzGeMCu9O6q4/I5Iw5wp6R+Zp6cy2WN9rxa0M4xFWUAjXJAbtxBIQGn3YGkHBnQGU="
                        }

                        xml_content = f"""<appmsg appid="" sdkver="0"><title>{file_data['title']}</title><type>6</type><appattach><totallen>{file_data['totallen']}</totallen><fileext>{file_data['fileext']}</fileext><attachid>{file_data['attachid']}</attachid><cdnattachurl>{file_data['cdnattachurl']}</cdnattachurl><cdnthumbaeskey/><aeskey>{file_data['aeskey']}</aeskey><encryver>0</encryver><filekey>{file_data['filekey']}</filekey><fileuploadtoken>{file_data['fileuploadtoken']}</fileuploadtoken></appattach><md5>{file_data['md5']}</md5></appmsg><fromusername>{bot.wxid}</fromusername><scene>0</scene><appinfo><version>1</version><appname/></appinfo><commenturl/>"""
                        await bot.send_cdn_file_msg(from_wxid, xml_content)
                        return False
                    elif "test_app" in command:
                        # 小红书分享数据
                        data = {
                            "title": "什么才是2025年的好生意？3个布局思路",
                            "des": "去年我对谈了上百位赚到钱的创业者和投资人，给大家总结3个在新的一年把生意做好的方法。2025，一起赚到钱，留在牌桌上",
                            "url": "https://www.xiaohongshu.com/discovery/item/6787c41e000000001b00b6cd?app_platform=ios&app_version=8.71&share_from_user_hidden=true&xsec_source=app_share&type=video&xsec_token=CBH_uK4MFwialONNSZdhaHtEWcW4JwlZknmlRFgbEvaGo=&author_share=1&xhsshare=WeixinSession&shareRedId=ODY2REVGSkA2NzUyOTgwNjY0OThKN0hO&apptime=1741213891&share_id=dcf728eac66d4eaf8b0a3925d72864bb",
                            "cdnthumburl": "3057020100044b30490201000204583797ab02032f559502045541f7df020467c8d078042464623536343566632d646363302d343961642d623439382d3334356137376563353530360204051808030201000405004c505500",
                            "cdnthumbmd5": "a53916ad552a3855da14149ca4c87351",
                            "cdnthumbaeskey": "6421fe26e35c92f8696574fc4e4c2e7d",
                            "filekey": "wxid_m9lhdlutblw812_362_1741236090"
                        }

                        xml_content = f"""<appmsg appid="wxd8a2750ce9d46980" sdkver="0"><title>{data['title']}</title><des>{data['des']}</des><type>4</type><url>{data['url']}</url><lowurl>{data['url']}</lowurl><appattach><cdnthumburl>{data['cdnthumburl']}</cdnthumburl><cdnthumbmd5>{data['cdnthumbmd5']}</cdnthumbmd5><cdnthumblength>31878</cdnthumblength><cdnthumbwidth>360</cdnthumbwidth><cdnthumbheight>480</cdnthumbheight><cdnthumbaeskey>{data['cdnthumbaeskey']}</cdnthumbaeskey><aeskey>{data['cdnthumbaeskey']}</aeskey><encryver>0</encryver><filekey>{data['filekey']}</filekey></appattach><md5>{data['cdnthumbmd5']}</md5><statextstr>GhQKEnd4ZDhhMjc1MGNlOWQ0Njk4MA==</statextstr></appmsg><fromusername>{bot.wxid}</fromusername><scene>0</scene><appinfo><version>46</version><appname>小红书</appname></appinfo><commenturl/>"""
                        await bot.send_app_message(from_wxid, xml_content, 4)
                        song_name = "海阔天空"

                        async with aiohttp.ClientSession() as session:
                            async with session.get(
                                    f"https://www.hhlqilongzhu.cn/api/dg_wyymusic.php?gm={song_name}&n=1&br=2&type=json") as resp:
                                data = await resp.json()

                        title = data["title"]
                        singer = data["singer"]
                        url = data["link"]
                        music_url = data["music_url"].split("?")[0]
                        cover_url = data["cover"]
                        lyric = data["lrc"]

                        xml = f"""<appmsg appid="wx79f2c4418704b4f8" sdkver="0"><title>{title}</title><des>{singer}</des><action>view</action><type>3</type><showtype>0</showtype><content/><url>{url}</url><dataurl>{music_url}</dataurl><lowurl>{url}</lowurl><lowdataurl>{music_url}</lowdataurl><recorditem/><thumburl>{cover_url}</thumburl><messageaction/><laninfo/><extinfo/><sourceusername/><sourcedisplayname/><songlyric>{lyric}</songlyric><commenturl/><appattach><totallen>0</totallen><attachid/><emoticonmd5/><fileext/><aeskey/></appattach><webviewshared><publisherId/><publisherReqId>0</publisherReqId></webviewshared><weappinfo><pagepath/><username/><appid/><appservicetype>0</appservicetype></weappinfo><websearch/><songalbumurl>{cover_url}</songalbumurl></appmsg><fromusername>{bot.wxid}</fromusername><scene>0</scene><appinfo><version>1</version><appname/></appinfo><commenturl/>"""
                        await bot.send_app_message(message["FromWxid"], xml, 3)
                        return False
                    elif "test_link" in command:
                        excel_link = "https://w1-1256118424.cos.ap-shanghai.myqcloud.com/scripts/video_script.xlsx"
                        excel_logo_url = "https://image.baidu.com/search/down?tn=download&word=download&ie=utf8&fr=detail&url=https%3A%2F%2Fbkimg.cdn.bcebos.com%2Fpic%2Ffaedab64034f78f0f736f5a2fa631d55b319ebc4b61a&thumburl=https%3A%2F%2Fimg1.baidu.com%2Fit%2Fu%3D1254359585%2C398839493%26fm%3D253%26fmt%3Dauto%26app%3D138%26f%3DJPEG%3Fw%3D648%26h%3D500"
                        await bot.send_link_message(from_wxid, 
                                                    excel_link, 
                                                    "视频脚本", 
                                                    "11KB", 
                                                    excel_logo_url)
                        return False
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
                        return False
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
            msg_content = message["Content"]
            if msg_content == "下载":
                from_wxid = message["FromWxid"]
                result = self.api_client.download_media(chat_id=from_wxid,
                                                      quote_msg_id=message["Quote"]["NewMsgId"],
                                                      character_tag=self.character_tag,
                                                      agent_type=self.agent_type)
                logger.info(f"获得下载返回！")
                download_type = ""
                if isinstance(result, Generator):
                    # 流式处理进度
                    async for progress in self._async_iter(result):
                        if "progress" in progress:
                            # 发送最新的一批进度信息
                            progress_list = progress["progress"]
                            logger.info(f"最新进度: {' -> '.join(progress_list)}")
                            await bot.send_text_message(from_wxid, f"最新进度: {' -> '.join(progress_list)}")
                        else:
                            # 最终结果
                            assert "type" in progress
                            download_type = progress["type"]
                            download_info = progress
                            logger.info(f"下载完成: {progress}")
                            await bot.send_text_message(from_wxid, f"下载完成！")
                            break
                else:
                    assert isinstance(result, dict)
                    download_info = result["response"]
                
                # 发送下载结果
                if download_type == "xhs_note_excel":
                    title = download_info.get("title", "")
                    desc = download_info.get("desc", "")
                    url = download_info.get("url", "")
                    file_size = download_info.get("excel_size", "")
                    logger.info(f"向[{from_wxid}] 发送视频脚本消息")
                    await bot.send_link_message(from_wxid, 
                                              url, 
                                              title, 
                                              desc[:18] + "\n" + str(file_size) + "KB",
                                              excel_logo_url)

                return False
            else:
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
        if not self.enable:
            return

        file_name = message["Filename"]
        file_ext = message["FileExtend"]
        file_base64 = message["File"]
        sender_nickname = await bot.get_nickname(message["SenderWxid"])
        self.api_client.append_msg2hist(
            chat_id=message["FromWxid"],
            msg_id=message["NewMsgId"],
            msg_content=f"[文件加载中...] 文件名：【{file_name}】, base64: {file_base64}",
            nick_name=sender_nickname,
            role="user"
        )
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
                message["Content"] = f'[分享了一个小红书链接，标题和描述如下，而多媒体文件正在加载中...请等一会儿] \n{message["Content"]}'
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
    # 消息发送队列部分 #
    ###################

    async def _message_sender(self):
        """异步消息发送处理器
        
        在独立线程中运行，处理消息队列中的消息
        """
        while True:
            try:
                # 使用阻塞方式获取消息
                msg = self.message_queue.get()
                if msg is None:
                    continue

                # 记录队列大小
                queue_size = self.message_queue.qsize()
                if queue_size > 0:
                    logger.info(f"当前消息队列大小: {queue_size}")

                logger.info(f"从队列获取消息，准备发送...")
                bot, from_wxid, content = msg
                
                if isinstance(content, dict):
                    logger.info(f"消息内容为字典类型: {content}")
                    if content.get("chunk_type") == "script_finished":
                        title = content.get("title", "")
                        desc = content.get("desc", "")
                        url = content.get("url", "")
                        file_size = content.get("excel_size", "")
                        full_script = content.get("full_xml", "")
                        logger.info(f"向[{from_wxid}] 发送视频脚本消息")
                        remsg_ids = await bot.send_link_message(from_wxid, 
                                                    url, 
                                                    title, 
                                                    desc[:18] + "\n" + str(file_size) + "KB",
                                                    excel_logo_url)
                        self.api_client.append_msg2hist(
                            chat_id=from_wxid, 
                            msg_id=remsg_ids[2],
                            msg_content=f"[发了一篇视频脚本]\n{full_script}",
                            character_tag=self.character_tag,
                            uid=self.uid,
                            agent_type=self.agent_type,
                            role="assistant"
                        )
                    self.message_queue.task_done()
                    continue

                # 以下是处理纯文本content
                content = re.sub(f'\[msg_id.*?{self.main_name}：', '', content)
                content = content.strip()

                # 记录发送前的时间戳
                send_start_time = time.time()
                
                # 只有在启用语音功能时才考虑发送语音
                if self.enable_voice:
                    # 根据内容长度计算语音概率
                    content_wo_bracket = re.sub(r'[\(\[\{（].*?[\)\]\}）]', '', content)
                    content_wo_bracket_len = len(content_wo_bracket)
                    p = 1 - (content_wo_bracket_len / self.max_voice_length)
                    voice_probability = min(max(p, 0.2), 0.6)

                    if random.random() < voice_probability and content_wo_bracket_len >= 4:
                        logger.info(f"向[{from_wxid}] 发送语音消息 (概率:{voice_probability:.2f})")
                        voice_content = gen_voice(content_wo_bracket)
                        if voice_content:
                            logger.debug(f"语音消息内容type: type:{type(voice_content)}")
                            remsg_ids = await bot.send_voice_message(from_wxid, voice_content, "mp3")
                        else:
                            logger.debug(f"语音消息内容为空")
                            remsg_ids = await bot.send_text_message(from_wxid, content)
                    else:
                        logger.debug(f"向[{from_wxid}] 发送文本消息")
                        remsg_ids = await bot.send_text_message(from_wxid, content)
                else:
                    # 语音功能未启用，直接发送文本消息
                    remsg_ids = await bot.send_text_message(from_wxid, content)
                    logger.debug(f"向[{from_wxid}] 发送文本消息")


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
                
                # 根据队列大小动态调整发送间隔
                if queue_size > 3:
                    # 队列中消息较多时，减少等待时间
                    wait_time = self.auto_send_interval * 0.5
                else:
                    wait_time = self.auto_send_interval * (0.8 + random.random() * 0.4)
                
                await asyncio.sleep(wait_time)
                self.message_queue.task_done()
                
            except Exception as e:
                logger.error(f"消息发送异常: {str(e)}")
                logger.error(traceback.format_exc())
                if 'msg' in locals():
                    self.message_queue.task_done()

    ###################
    # 工具函数部分 #
    ###################

    async def _async_iter(self, sync_iter):
        """将同步迭代器转换为异步迭代器
        
        优化版本：在迭代过程中周期性地让出更多控制权
        以便消息发送任务有机会执行
        """
        chunk_count = 0
        for item in sync_iter:
            yield item
            chunk_count += 1
            
            # 每处理10个数据块，让出更多时间给消息发送任务
            if chunk_count % 10 == 0:
                # 主动清空一次消息队列，确保消息能及时发送
                await asyncio.sleep(0.1)
                
                # 提高消息处理的优先级
                if not self.message_queue.empty():
                    # 如果消息队列非空，多让出一些时间
                    await asyncio.sleep(0.1)
            else:
                # 正常让出少量时间
                await asyncio.sleep(0.01)

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
