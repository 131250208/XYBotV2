import tomllib
import xml.etree.ElementTree as ET
from typing import Dict, Any
import hashlib
import os
from pathlib import Path
import traceback

from loguru import logger

from WechatAPI import WechatAPIClient
from WechatAPI.Client.protect import protector
from database.messsagDB import MessageDB
from utils.event_manager import EventManager


class XYBot:
    def __init__(self, bot_client: WechatAPIClient):
        self.bot = bot_client
        self.wxid = None
        self.nickname = None
        self.alias = None
        self.phone = None

        with open("main_config.toml", "rb") as f:
            main_config = tomllib.load(f)

        self.ignore_protection = main_config.get("XYBot", {}).get("ignore-protection", False)

        self.ignore_mode = main_config.get("XYBot", {}).get("ignore-mode", "")
        self.whitelist = main_config.get("XYBot", {}).get("whitelist", [])
        self.blacklist = main_config.get("XYBot", {}).get("blacklist", [])

        self.msg_db = MessageDB()

        # 创建临时文件夹
        self.temp_dir = Path("temp/message_xml")
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def update_profile(self, wxid: str, nickname: str, alias: str, phone: str):
        """更新机器人信息"""
        self.wxid = wxid
        self.nickname = nickname
        self.alias = alias
        self.phone = phone

    async def process_message(self, message: Dict[str, Any]):
        """处理接收到的消息"""
        msg_type = message.get("MsgType")
        content = message["Content"]["string"]
        
        # # 计算内容的md5
        # content_md5 = hashlib.md5(content.encode()).hexdigest()
        
        # # 保存消息内容到临时文件
        # filename = f"{content_md5}_{msg_type}.xml"
        # file_path = self.temp_dir / filename
        # with open(file_path, "w", encoding="utf-8") as f:
        #     f.write(content)

        # 预处理消息
        message["FromWxid"] = message.get("FromUserName").get("string")
        message.pop("FromUserName")
        message["ToWxid"] = message.get("ToWxid").get("string")

        # 处理一下自己发的消息
        if message["FromWxid"] == self.wxid and message["ToWxid"].endswith("@chatroom"):  # 自己发发到群聊
            # 由于是自己发送的消息，所以对于自己来说，From和To是反的
            message["FromWxid"], message["ToWxid"] = message["ToWxid"], message["FromWxid"]


        # 根据消息类型触发不同的事件
        if msg_type == 1:  # 文本消息
            await self.process_text_message(message)

        elif msg_type == 3:  # 图片消息
            await self.process_image_message(message)

        elif msg_type == 34:  # 语音消息
            await self.process_voice_message(message)

        elif msg_type == 43:  # 视频消息
            await self.process_video_message(message)

        elif msg_type == 49:  # xml消息
            await self.process_xml_message(message)

        elif msg_type == 10002:  # 系统消息
            await self.process_system_message(message)

        elif msg_type == 37:  # 好友请求
            if self.ignore_protection or not protector.check(14400):
                await EventManager.emit("friend_request", self.bot, message)
            else:
                logger.warning("风控保护: 新设备登录后4小时内请挂机")

        elif msg_type == 51:
            pass

        else:
            logger.info("未知的消息类型: {}", message)

        # 可以继续添加更多消息类型的处理

    async def process_text_message(self, message: Dict[str, Any]):
        """处理文本消息"""
        # 预处理消息
        message["Content"] = message.get("Content").get("string")

        if message["FromWxid"].endswith("@chatroom"):  # 群聊消息
            message["IsGroup"] = True
            split_content = message["Content"].split(":\n", 1)
            if len(split_content) > 1:
                message["Content"] = split_content[1]
                message["SenderWxid"] = split_content[0]
            else:  # 绝对是自己发的消息! qwq
                message["Content"] = split_content[0]
                message["SenderWxid"] = self.wxid

        else:# 私聊消息
            message["SenderWxid"] = message["FromWxid"]
            if message["FromWxid"] == self.wxid:  # 自己发的消息
                message["FromWxid"] = message["ToWxid"]
            message["IsGroup"] = False
        try:
            root = ET.fromstring(message["MsgSource"])
            ats = root.find("atuserlist").text if root.find("atuserlist") is not None else ""
        except Exception as e:
            logger.error("解析文本消息失败: {}", e)
            return

        if ats:
            ats = ats.strip(",").split(",")
        else:  # 修复
            ats = []
        message["Ats"] = ats if ats and ats[0] != "" else []

        # 保存消息到数据库
        await self.msg_db.save_message(
            msg_id=int(message["MsgId"]),
            sender_wxid=message["SenderWxid"],
            from_wxid=message["FromWxid"],
            msg_type=int(message["MsgType"]),
            content=message["Content"],
            is_group=message["IsGroup"]
        )

        if self.wxid in ats:
            logger.info("收到被@消息: 消息ID:{} 来自:{} 发送人:{} @:{} 内容:{}",
                        message["MsgId"],
                        message["FromWxid"],
                        message["SenderWxid"],
                        message["Ats"],
                        message["Content"])

            if self.ignore_check(message["FromWxid"], message["SenderWxid"]):
                if self.ignore_protection or not protector.check(14400):
                    await EventManager.emit("at_message", self.bot, message)
                else:
                    logger.warning("风控保护: 新设备登录后4小时内请挂机")
            return

        logger.info("收到文本消息: 消息ID:{} 来自:{} 发送人:{} @:{} 内容:{}",
                    message["MsgId"],
                    message["FromWxid"],
                    message["SenderWxid"],
                    message["Ats"],
                    message["Content"])

        if self.ignore_check(message["FromWxid"], message["SenderWxid"]):
            if self.ignore_protection or not protector.check(14400):
                await EventManager.emit("text_message", self.bot, message)
            else:
                logger.warning("风控保护: 新设备登录后4小时内请挂机")

    async def process_image_message(self, message: Dict[str, Any]):
        """处理图片消息"""
        # 预处理消息
        message["Content"] = message.get("Content").get("string").replace("\n", "").replace("\t", "")

        if message["FromWxid"].endswith("@chatroom"):  # 群聊消息
            message["IsGroup"] = True
            split_content = message["Content"].split(":", 1)
            if len(split_content) > 1:
                message["Content"] = split_content[1]
                message["SenderWxid"] = split_content[0]
            else:  # 绝对是自己发的消息! qwq
                message["Content"] = split_content[0]
                message["SenderWxid"] = self.wxid
        else:
            message["SenderWxid"] = message["FromWxid"]
            if message["FromWxid"] == self.wxid:  # 自己发的消息
                message["FromWxid"] = message["ToWxid"]
            message["IsGroup"] = False

        logger.info("收到图片消息: 消息ID:{} 来自:{} 发送人:{} XML:{}",
                    message["MsgId"],
                    message["FromWxid"],
                    message["SenderWxid"],
                    message["Content"])

        await self.msg_db.save_message(
            msg_id=int(message["MsgId"]),
            sender_wxid=message["SenderWxid"],
            from_wxid=message["FromWxid"],
            msg_type=int(message["MsgType"]),
            content=message["MsgSource"],
            is_group=message["IsGroup"]
        )

        # 解析图片消息
        aeskey, cdnmidimgurl = None, None
        try:
            root = ET.fromstring(message["Content"])
            img_element = root.find('img')
            if img_element is not None:
                aeskey = img_element.get('aeskey')
                cdnmidimgurl = img_element.get('cdnmidimgurl')
        except Exception as e:
            logger.error("解析图片消息失败: {}", e)
            return

        # 下载图片
        if aeskey and cdnmidimgurl:
            message["Content"] = await self.bot.download_image(aeskey, cdnmidimgurl)

        if self.ignore_check(message["FromWxid"], message["SenderWxid"]):
            if self.ignore_protection or not protector.check(14400):
                await EventManager.emit("image_message", self.bot, message)
            else:
                logger.warning("风控保护: 新设备登录后4小时内请挂机")

    async def process_voice_message(self, message: Dict[str, Any]):
        """处理语音消息"""
        # 预处理消息
        message["Content"] = message.get("Content").get("string").replace("\n", "").replace("\t", "")

        if message["FromWxid"].endswith("@chatroom"):  # 群聊消息
            message["IsGroup"] = True
            split_content = message["Content"].split(":", 1)
            if len(split_content) > 1:
                message["Content"] = split_content[1]
                message["SenderWxid"] = split_content[0]
            else:  # 绝对是自己发的消息! qwq
                message["Content"] = split_content[0]
                message["SenderWxid"] = self.wxid
        else:
            message["SenderWxid"] = message["FromWxid"]
            if message["FromWxid"] == self.wxid:  # 自己发的消息
                message["FromWxid"] = message["ToWxid"]
            message["IsGroup"] = False

        logger.info("收到语音消息: 消息ID:{} 来自:{} 发送人:{} XML:{}",
                    message["MsgId"],
                    message["FromWxid"],
                    message["SenderWxid"],
                    message["Content"])

        await self.msg_db.save_message(
            msg_id=int(message["MsgId"]),
            sender_wxid=message["SenderWxid"],
            from_wxid=message["FromWxid"],
            msg_type=int(message["MsgType"]),
            content=message["Content"],
            is_group=message["IsGroup"]
        )

        if message["IsGroup"] or not message.get("ImgBuf", {}).get("buffer", ""):
            # 解析语音消息
            voiceurl, length = None, None
            try:
                root = ET.fromstring(message["Content"])
                voicemsg_element = root.find('voicemsg')
                if voicemsg_element is not None:
                    voiceurl = voicemsg_element.get('voiceurl')
                    length = int(voicemsg_element.get('length'))
            except Exception as e:
                logger.error("解析语音消息失败: {}", e)
                return

            # 下载语音
            if voiceurl and length:
                silk_base64 = await self.bot.download_voice(message["MsgId"], voiceurl, length)
                message["Content"] = await self.bot.silk_base64_to_wav_byte(silk_base64)
        else:
            silk_base64 = message["ImgBuf"]["buffer"]
            message["Content"] = await self.bot.silk_base64_to_wav_byte(silk_base64)

        if self.ignore_check(message["FromWxid"], message["SenderWxid"]):
            if self.ignore_protection or not protector.check(14400):
                await EventManager.emit("voice_message", self.bot, message)
            else:
                logger.warning("风控保护: 新设备登录后4小时内请挂机")

    async def process_xml_message(self, message: Dict[str, Any]):
        """处理xml消息"""
        message["Content"] = message.get("Content").get("string").replace("\n", "").replace("\t", "")

        if message["FromWxid"].endswith("@chatroom"):  # 群聊消息
            message["IsGroup"] = True
            split_content = message["Content"].split(":", 1)
            if len(split_content) > 1:
                message["Content"] = split_content[1]
                message["SenderWxid"] = split_content[0]
            else:  # 绝对是自己发的消息! qwq
                message["Content"] = split_content[0]
                message["SenderWxid"] = self.wxid
        else:
            message["SenderWxid"] = message["FromWxid"]
            if message["FromWxid"] == self.wxid:  # 自己发的消息
                message["FromWxid"] = message["ToWxid"]
            message["IsGroup"] = False

        await self.msg_db.save_message(
            msg_id=int(message["MsgId"]),
            sender_wxid=message["SenderWxid"],
            from_wxid=message["FromWxid"],
            msg_type=int(message["MsgType"]),
            content=message["Content"],
            is_group=message["IsGroup"]
        )

        try:
            root = ET.fromstring(message["Content"])
            type = int(root.find("appmsg").find("type").text)
        except Exception as e:
            logger.error(f"解析xml消息失败: {e}")
            return

        if type == 57:
            await self.process_quote_message(message)
        elif type in {4, 5}:  # 分享链接，小红书等
            await self.process_link_message(message)
        elif type == 6:
            await self.process_file_message(message)
        elif type == 74:  # 文件消息，但还在上传，不用管
            pass

        else:
            logger.info("未知的xml消息类型: {}", message)

    async def process_link_message(self, message: Dict[str, Any]):
        """处理分享链接消息"""
        try:
            # 解析XML获取链接信息
            root = ET.fromstring(message["Content"])
            appmsg = root.find("appmsg")
            if appmsg is not None:
                # 基本信息
                title = appmsg.find("title").text if isinstance(appmsg.find("title"), ET.Element) else ""
                desc = appmsg.find("des").text if isinstance(appmsg.find("des"), ET.Element) else ""
                url = appmsg.find("url").text if isinstance(appmsg.find("url"), ET.Element) else ""
                appid = appmsg.get("appid", "")
                
                # 应用信息
                appinfo = root.find("appinfo")
                if appinfo is not None:
                    app_version = appinfo.find("version").text if isinstance(appinfo.find("version"), ET.Element) else ""
                    app_name = appinfo.find("appname").text if isinstance(appinfo.find("appname"), ET.Element) else ""
                
                # 缩略图信息
                appattach = appmsg.find("appattach")
                if appattach is not None:
                    thumb = {
                        "cdnthumburl": appattach.find("cdnthumburl").text if isinstance(appattach.find("cdnthumburl"), ET.Element) else "",
                        "cdnthumbmd5": appattach.find("cdnthumbmd5").text if isinstance(appattach.find("cdnthumbmd5"), ET.Element) else "",
                        "cdnthumblength": int(appattach.find("cdnthumblength").text) if isinstance(appattach.find("cdnthumblength"), ET.Element) else 0,
                        "cdnthumbwidth": int(appattach.find("cdnthumbwidth").text) if isinstance(appattach.find("cdnthumbwidth"), ET.Element) else 0,
                        "cdnthumbheight": int(appattach.find("cdnthumbheight").text) if isinstance(appattach.find("cdnthumbheight"), ET.Element) else 0,
                        "aeskey": appattach.find("aeskey").text if isinstance(appattach.find("aeskey"), ET.Element) else ""
                    }
                
                # 构建链接消息结构
                message["Link"] = {
                    "Title": title,
                    "Description": desc,
                    "Url": url,
                    "AppId": appid,
                    "AppInfo": {
                        "Version": app_version,
                        "AppName": app_name
                    },
                    "Thumbnail": thumb if appattach is not None else {}
                }
                
                # 从链接检测APPname，因为appname会缺省
                # 检查是否为小红书链接
                is_xhs = any([
                    "xiaohongshu.com/discovery/item/" in url,
                    "xiaohongshu.com/explore/" in url,
                    "xhslink.com/" in url
                ])
                if is_xhs:
                    message["Link"]["AppInfo"]["AppName"] = "小红书"
                # 构建可读的链接描述作为消息内容
                message["Content"] = f'标题: {title}\n描述: {desc}\n链接: {url}'

                logger.info("收到链接消息: 消息ID:{} 来自:{} 发送人:{} 标题:{}",
                            message["MsgId"],
                            message["FromWxid"],
                            message["SenderWxid"],
                            title)

                if self.ignore_check(message["FromWxid"], message["SenderWxid"]):
                    if self.ignore_protection or not protector.check(14400):
                        await EventManager.emit("link_message", self.bot, message)
                    else:
                        logger.warning("风控保护: 新设备登录后4小时内请挂机")

        except Exception as e:
            logger.error(f"解析链接消息失败: {e}")
            logger.error(traceback.format_exc())

    async def process_quote_message(self, message: Dict[str, Any]):
        """处理引用消息"""
        quote_messsage = {}
        try:
            root = ET.fromstring(message["Content"])
            appmsg = root.find("appmsg")
            text = appmsg.find("title").text
            refermsg = appmsg.find("refermsg")

            # 统一处理基础信息
            quote_messsage["MsgType"] = int(refermsg.find("type").text)
            quote_messsage["NewMsgId"] = int(refermsg.find("svrid").text)
            quote_messsage["ToWxid"] = refermsg.find("fromusr").text
            quote_messsage["FromWxid"] = refermsg.find("chatusr").text
            quote_messsage["Nickname"] = refermsg.find("displayname").text
            quote_messsage["MsgSource"] = refermsg.find("msgsource").text
            quote_messsage["Content"] = refermsg.find("content").text
            quote_messsage["Createtime"] = refermsg.find("createtime").text

            if quote_messsage["MsgType"] == 1:  # 文本消息
                # 文本消息直接使用content作为内容
                pass

            elif quote_messsage["MsgType"] == 3:  # 图片消息
                # 解析图片信息
                img_content = ET.fromstring(quote_messsage["Content"])
                img_element = img_content.find('img')
                if img_element is not None:
                    quote_messsage["aeskey"] = img_element.get('aeskey')
                    quote_messsage["cdnmidimgurl"] = img_element.get('cdnmidimgurl') 
                    quote_messsage["cdnthumburl"] = img_element.get('cdnthumburl')
                    quote_messsage["length"] = img_element.get('length')
                    quote_messsage["md5"] = img_element.get('md5')

            elif quote_messsage["MsgType"] == 49:  # 引用消息
                quote_root = ET.fromstring(quote_messsage["Content"])
                quote_appmsg = quote_root.find("appmsg")

                # 处理基本信息
                quote_messsage["Content"] = quote_appmsg.find("title").text if isinstance(quote_appmsg.find("title"), ET.Element) else ""
                quote_messsage["destination"] = quote_appmsg.find("des").text if isinstance(quote_appmsg.find("des"), ET.Element) else ""
                quote_messsage["action"] = quote_appmsg.find("action").text if isinstance(quote_appmsg.find("action"), ET.Element) else ""
                quote_messsage["XmlType"] = int(quote_appmsg.find("type").text) if isinstance(quote_appmsg.find("type"), ET.Element) else 0
                quote_messsage["showtype"] = int(quote_appmsg.find("showtype").text) if isinstance(quote_appmsg.find("showtype"), ET.Element) else 0
                quote_messsage["soundtype"] = int(quote_appmsg.find("soundtype").text) if isinstance(quote_appmsg.find("soundtype"), ET.Element) else 0
                
                # 处理URL相关
                quote_messsage["url"] = quote_appmsg.find("url").text if isinstance(quote_appmsg.find("url"), ET.Element) else ""
                quote_messsage["lowurl"] = quote_appmsg.find("lowurl").text if isinstance(quote_appmsg.find("lowurl"), ET.Element) else ""
                quote_messsage["dataurl"] = quote_appmsg.find("dataurl").text if isinstance(quote_appmsg.find("dataurl"), ET.Element) else ""
                quote_messsage["lowdataurl"] = quote_appmsg.find("lowdataurl").text if isinstance(quote_appmsg.find("lowdataurl"), ET.Element) else ""
                quote_messsage["songlyric"] = quote_appmsg.find("songlyric").text if isinstance(quote_appmsg.find("songlyric"), ET.Element) else ""
                
                # 处理额外信息
                quote_messsage["extinfo"] = quote_appmsg.find("extinfo").text if isinstance(quote_appmsg.find("extinfo"), ET.Element) else ""
                quote_messsage["sourceusername"] = quote_appmsg.find("sourceusername").text if isinstance(quote_appmsg.find("sourceusername"), ET.Element) else ""
                quote_messsage["sourcedisplayname"] = quote_appmsg.find("sourcedisplayname").text if isinstance(quote_appmsg.find("sourcedisplayname"), ET.Element) else ""
                quote_messsage["thumburl"] = quote_appmsg.find("thumburl").text if isinstance(quote_appmsg.find("thumburl"), ET.Element) else ""
                quote_messsage["md5"] = quote_appmsg.find("md5").text if isinstance(quote_appmsg.find("md5"), ET.Element) else ""
                quote_messsage["statextstr"] = quote_appmsg.find("statextstr").text if isinstance(quote_appmsg.find("statextstr"), ET.Element) else ""
                quote_messsage["directshare"] = int(quote_appmsg.find("directshare").text) if isinstance(quote_appmsg.find("directshare"), ET.Element) else 0
                
                # 处理appattach信息
                appattach = quote_appmsg.find("appattach")
                if appattach is not None:
                    quote_messsage["appattach"] = {
                        "totallen": int(appattach.find("totallen").text) if isinstance(appattach.find("totallen"), ET.Element) else 0,
                        "attachid": appattach.find("attachid").text if isinstance(appattach.find("attachid"), ET.Element) else "",
                        "emoticonmd5": appattach.find("emoticonmd5").text if isinstance(appattach.find("emoticonmd5"), ET.Element) else "",
                        "fileext": appattach.find("fileext").text if isinstance(appattach.find("fileext"), ET.Element) else "",
                        "cdnthumbaeskey": appattach.find("cdnthumbaeskey").text if isinstance(appattach.find("cdnthumbaeskey"), ET.Element) else "",
                        "aeskey": appattach.find("aeskey").text if isinstance(appattach.find("aeskey"), ET.Element) else ""
                    }

        except Exception as e:
            logger.error(f"解析引用消息失败: {e}")
            return

        message["Content"] = text
        message["Quote"] = quote_messsage

        logger.info("收到引用消息: 消息ID:{} 来自:{} 发送人:{}  内容:{} 引用:{}",
                    message.get("MsgId", ""),
                    message["FromWxid"],
                    message["SenderWxid"], 
                    message["Content"],
                    message["Quote"])

        if self.ignore_check(message["FromWxid"], message["SenderWxid"]):
            if self.ignore_protection or not protector.check(14400):
                await EventManager.emit("quote_message", self.bot, message)
            else:
                logger.warning("风控保护: 新设备登录后4小时内请挂机")

    async def process_video_message(self, message):
        # 预处理消息
        message["Content"] = message.get("Content").get("string")

        if message["FromWxid"].endswith("@chatroom"):  # 群聊消息
            message["IsGroup"] = True
            split_content = message["Content"].split(":", 1)
            if len(split_content) > 1:
                message["Content"] = split_content[1]
                message["SenderWxid"] = split_content[0]
            else:  # 绝对是自己发的消息! qwq
                message["Content"] = split_content[0]
                message["SenderWxid"] = self.wxid
        else:
            message["SenderWxid"] = message["FromWxid"]
            if message["FromWxid"] == self.wxid:  # 自己发的消息
                message["FromWxid"] = message["ToWxid"]
            message["IsGroup"] = False

        logger.info("收到视频消息: 消息ID:{} 来自:{} 发送人:{} XML:{}",
                    message["MsgId"],
                    message["FromWxid"],
                    message["SenderWxid"],
                    str(message["Content"]).replace("\n", ""))

        await self.msg_db.save_message(
            msg_id=int(message["MsgId"]),
            sender_wxid=message["SenderWxid"],
            from_wxid=message["FromWxid"],
            msg_type=int(message["MsgType"]),
            content=message["Content"],
            is_group=message["IsGroup"]
        )

        message["Video"] = await self.bot.download_video(message["MsgId"], f"video_{message['MsgId']}.mp4")

        if self.ignore_check(message["FromWxid"], message["SenderWxid"]):
            if self.ignore_protection or not protector.check(14400):
                await EventManager.emit("video_message", self.bot, message)
            else:
                logger.warning("风控保护: 新设备登录后4小时内请挂机")

    async def process_file_message(self, message: Dict[str, Any]):
        """处理文件消息"""
        try:
            root = ET.fromstring(message["Content"])
            filename = root.find("appmsg").find("title").text
            attach_id = root.find("appmsg").find("appattach").find("attachid").text
            file_extend = root.find("appmsg").find("appattach").find("fileext").text
        except Exception as error:
            logger.error(f"解析文件消息失败: {error}")
            return

        message["Filename"] = filename
        message["FileExtend"] = file_extend

        logger.info("收到文件消息: 消息ID:{} 来自:{} 发送人:{} XML:{}",
                    message["MsgId"],
                    message["FromWxid"],
                    message["SenderWxid"],
                    message["Content"])

        await self.msg_db.save_message(
            msg_id=int(message["MsgId"]),
            sender_wxid=message["SenderWxid"],
            from_wxid=message["FromWxid"],
            msg_type=int(message["MsgType"]),
            content=message["Content"],
            is_group=message["IsGroup"]
        )

        message["File"] = await self.bot.download_attach(attach_id)

        if self.ignore_check(message["FromWxid"], message["SenderWxid"]):
            if self.ignore_protection or not protector.check(14400):
                await EventManager.emit("file_message", self.bot, message)
            else:
                logger.warning("风控保护: 新设备登录后4小时内请挂机")

    async def process_system_message(self, message: Dict[str, Any]):
        """处理系统消息"""
        # 预处理消息
        message["Content"] = message.get("Content").get("string")

        if message["FromWxid"].endswith("@chatroom"):  # 群聊消息
            message["IsGroup"] = True
            split_content = message["Content"].split(":", 1)
            if len(split_content) > 1:
                message["Content"] = split_content[1]
                message["SenderWxid"] = split_content[0]
            else:  # 绝对是自己发的消息! qwq
                message["Content"] = split_content[0]
                message["SenderWxid"] = self.wxid
        else:
            message["SenderWxid"] = message["FromWxid"]
            if message["FromWxid"] == self.wxid:  # 自己发的消息
                message["FromWxid"] = message["ToWxid"]
            message["IsGroup"] = False

        try:
            root = ET.fromstring(message["Content"])
            msg_type = root.attrib["type"]
        except Exception as e:
            logger.error(f"解析系统消息失败: {e}")
            return

        if msg_type == "pat":
            await self.process_pat_message(message)
        elif msg_type == "ClientCheckGetExtInfo":
            pass
        else:
            logger.info("收到系统消息: {}", message)
            if self.ignore_check(message["FromWxid"], message["SenderWxid"]):
                if self.ignore_protection or not protector.check(14400):
                    await EventManager.emit("system_message", self.bot, message)
                else:
                    logger.warning("风控保护: 新设备登录后4小时内请挂机")

    async def process_pat_message(self, message: Dict[str, Any]):
        """处理拍一拍请求消息"""
        try:
            root = ET.fromstring(message["Content"])
            pat = root.find("pat")
            patter = pat.find("fromusername").text
            patted = pat.find("pattedusername").text
            pat_suffix = pat.find("patsuffix").text
        except Exception as e:
            logger.error(f"解析拍一拍消息失败: {e}")
            return

        message["Patter"] = patter
        message["Patted"] = patted
        message["PatSuffix"] = pat_suffix

        logger.info("收到拍一拍消息: 消息ID:{} 来自:{} 发送人:{} 拍者:{} 被拍:{} 后缀:{}",
                    message["MsgId"],
                    message["FromWxid"],
                    message["SenderWxid"],
                    message["Patter"],
                    message["Patted"],
                    message["PatSuffix"])

        await self.msg_db.save_message(
            msg_id=int(message["MsgId"]),
            sender_wxid=message["SenderWxid"],
            from_wxid=message["FromWxid"],
            msg_type=int(message["MsgType"]),
            content=f"{message['Patter']} 拍了拍 {message['Patted']} {message['PatSuffix']}",
            is_group=message["IsGroup"]
        )

        if self.ignore_check(message["FromWxid"], message["SenderWxid"]):
            if self.ignore_protection or not protector.check(14400):
                await EventManager.emit("pat_message", self.bot, message)
            else:
                logger.warning("风控保护: 新设备登录后4小时内请挂机")

    def ignore_check(self, FromWxid: str, SenderWxid: str):
        if self.ignore_mode == "Whitelist":
            return (FromWxid in self.whitelist) or (SenderWxid in self.whitelist)
        elif self.ignore_mode == "blacklist":
            return (FromWxid not in self.blacklist) and (SenderWxid not in self.blacklist)
        else:
            return True
