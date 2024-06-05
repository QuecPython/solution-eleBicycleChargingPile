# Copyright (c) Quectel Wireless Solution, Co., Ltd.All Rights Reserved.
# 
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# 
#     http://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import _thread
import math
import usys
import uos
from machine import UART
import audio
import checkNet
import modem
import net
import osTimer
import request
import sim
import sys_bus
import ubinascii
import uhashlib
import usocket
import ustruct
from machine import Pin
from machine import UART
from machine import ExtInt
from usr.MFRC522 import MFRC522_SPI
import ql_fs
import utime
import dataCall
from misc import Power
import app_fota_download

"""------------------------------------------------------------------------------"""
"""设备相关配置 包括MCU的版本, 每次升级需要更新MCU版本后升级"""
VERSION = 103
DEV_TYPE = 68
UPGRADE_FILE = "/usr/main.py"
LOCAL_UPDATER_FILE = app_fota_download.get_updater_dir() + UPGRADE_FILE
DEVICE_ACCESS_URI = "http://center.lvcchong.com/api/"
"""------------------------------------------------------------------------------"""


#################################################
# 获取真实版本信息, 103会转化为16进制对应 0x0103

#################################################

def get_truth_version():
    """获取真实版本号"""
    version = str(VERSION)
    if len(version) == 4:
        return int(version[0]) * (16 ** 3) + int(version[1]) * (16 ** 2) + int(version[2]) * (16 ** 1) + int(version[3])
    elif len(version) == 3:
        return int(version[0]) * (16 ** 2) + int(version[1]) * (16 ** 1) + int(version[2])
    elif len(version) == 2:
        return int(version[0]) * (16 ** 1) + int(version[1])
    else:
        return version


"""---------------------------------获取真实版本号----------------------------------"""
MCU_VERSION = get_truth_version()
"""------------------------------------------------------------------------------"""

# 公共参数获取
ic_cid = sim.getIccid()
imei = modem.getDevImei()
imsi = sim.getImsi()
phone_num = sim.getPhoneNumber()

# 用于读取的参数
write_buf = 0
read_buf = 0
vt = 0
vtp = 0

# BL09系列有 A/B两个槽
SOCKET_A = "A"
SOCKET_B = "B"
NUM_SOCKET_A = 0
NUM_SOCKET_B = 1
SOCKET_COUNT = 2
config = {
    SOCKET_A: {
        "name": SOCKET_A,
        "insert": Pin.GPIO7,
        "relay": Pin.GPIO6,
        "red": Pin.GPIO9,
        "green": Pin.GPIO21,
    },
    SOCKET_B: {
        "name": SOCKET_B,
        "insert": Pin.GPIO3,
        "relay": Pin.GPIO4,
        "red": Pin.GPIO23,
        "green": Pin.GPIO22,
    }
}

# 默认的配置
DEFAULT_CONFIG = {
    "media": {
        "res": (0x01, 0x00, 0x07)
    },
    "default_device_config": {
        "res": (20, 3000, 100, 1800, 60, 600, 230, 12 * 3600)
    }
}


## 公共枚举
class RESULT(object):
    """返回结果"""
    OK = 1
    ERR = 2


class FailureReportCode(object):
    ERROR_IC = 0x06
    ERROR_OVER_VOLTAGE = 0X09


class OTA_INITIATOR(object):
    """OTA发起方"""
    DEVICE = 0x00
    PLATFORM = 0X01


class OPEN_SOCKET_MODE(object):
    """
    打开socket模式,
    0为卡片
    1位手机
    """
    CARD = 0
    PHONE = 1


class Lock(object):
    """全局锁对象"""

    def __init__(self):
        self.lock = _thread.allocate_lock()

    def __enter__(self):
        self.lock.acquire()

    def __exit__(self, *args, **kwargs):
        self.lock.release()


class ConfStore(object):
    """配置存储"""

    def __init__(self):
        self.lock = Lock()
        self.f = "/usr/charge_bak.json"
        self.data = dict()

    def init(self):
        if ql_fs.path_exists(self.f):
            self.data = ql_fs.read_json(self.f)
        else:
            ql_fs.touch(self.f, DEFAULT_CONFIG)
            self.data = DEFAULT_CONFIG

    def get(self, key):
        return self.data.get(key)

    def include(self, key):
        return key in self.data

    def update(self, kwargs):
        with self.lock:
            self.data.update(kwargs)
            print("------------------update data begin -------------------------")
            print("data = {}".format(self.data))
            ql_fs.touch(self.f, self.data)
            print("------------------update data end ---------------------------")

    def delete(self, key):
        with self.lock:
            if key in self.data:
                del self.data[key]
                ql_fs.touch(self.f, self.data)


class SecurityMsgMap(object):
    """主要保证每次的刷卡订单和回来的消息是一个"""

    def __init__(self):
        self.map = dict()
        self.lock = Lock()

    def init(self, data):
        if data:
            self.map = data

    def get(self, msg_id):
        with self.lock:
            return self.map.get(msg_id)["card_id"]

    def set(self, msg_id, msg):
        with self.lock:
            self.map[msg_id] = msg
            conf_store.update(dict(security_msg_map=self.map))

    def delete(self, msg_id):
        with self.lock:
            if msg_id in self.map:
                del self.map[msg_id]
            if str(msg_id) in self.map:
                del self.map[str(msg_id)]
            print("-----------------------------security_msg_map {} ----------------".format(self.map))
            conf_store.update(dict(security_msg_map=self.map))

    def update(self):
        with self.lock:
            conf_store.update(dict(security_msg_map=self.map))

    def exist(self, msg_id):
        if str(msg_id) in self.map or msg_id in self.map:
            return True
        else:
            return False

    def check_upload(self):
        with self.lock:
            del_msg_id = []
            print("----------------check_upload----------- {}".format(self.map))
            for k, v in self.map.items():
                """retry to upload charge end info"""
                v["count"] = v.get('count', -1) + 1
                if v['count'] > 4:
                    del_msg_id.append(k)
                else:
                    print("check upload = {}".format(self.map))
                    app.send(v.get('data'))
            for msg_id in del_msg_id:
                del self.map[msg_id]
            conf_store.update(dict(security_msg_map=self.map))


class CreditCardInfo(object):
    def __init__(self):
        self.map = dict()
        self.lock = Lock()

    def get(self, msg_id):
        with self.lock:
            return self.map.get(msg_id)["card_id"]

    def set(self, msg_id, msg):
        with self.lock:
            self.map[msg_id] = msg

    def exist(self, msg_id):
        if msg_id in self.map:
            return True
        else:
            return False

    def delete(self, msg_id):
        with self.lock:
            if msg_id in self.map:
                del self.map[msg_id]


security_msg_map = SecurityMsgMap()
credit_card_info = CreditCardInfo()


class TAGORDER(object):
    """TAG 的枚举"""
    GATWAY_REQ = 0x00
    GATWAY_RESP = 0X01
    DEVICE_REQ = 0X10
    DEVICE_RESP = 0X11
    DEVICE_HEART = 0X12


class ORDER(object):
    """主要用于协议方面的接收指令"""
    DEVICE_REGISTER = 0x0001
    DEVICE_HEART = 0x0002
    PHONE_OPEN_CHARGING = 0x0005
    SEARCH_DEVICE_INFO = 0x0011
    SEARCH_COMMUNICATION_MODULE_INFO = 0x0012
    CHARGING_END = 0X0010
    SEARCH_SOCKET_STATUS = 0x0019
    SEARCH_LBS_INFO = 0x0022
    SEARCH_MEDIA_INFO = 0x0028
    DEVICE_RESTART = 0x0017
    SET_MEDIA_VOLUME = 0x0027
    STOP_CHARGING = 0x0007
    UPLOAD_CHARGING_STATUS = 0x0048
    UPLOAD_POWER_STATUS = 0x0030
    SEARCH_DEFAULT_CONFIG_INFO = 0x0013
    SET_DEFAULT_CONFIG_INFO = 0x0014
    SET_MEDIA_INFO = 0x0027
    CREDIT_CARD_CHARGE_REQUEST = 0x0008
    UPLOAD_CREDIT_CARD_ORDER = 0x0009
    SEARCH_OTA_VERSION = 0x0025
    REQUEST_OTA_DATA = 0x0026
    PLATFORM_ISSUED_OTA = 0x0015
    FAILURE_REPORT = 0x0016
    DLT_INFO_REPORT = 0x0034
    SEARCH_CHARGING_STATE = 0x0006
    SEARCH_OTA_UPGRADE_VERSION = 0x0025


class DefaultDeviceState(object):
    """默认的设备状态"""
    REGISTER = False
    DEVICE_UN_ONLINE = False
    CONNECT_SERVER_ERROR = False
    METERING_CHIP_FAILURE = False
    FIRST_ONLINE = False

    @classmethod
    def set_first_online(cls, status=True):
        cls.FIRST_ONLINE = status

    @classmethod
    def set_connect_server_error(cls, status):
        if status != cls.CONNECT_SERVER_ERROR:
            cls.CONNECT_SERVER_ERROR = status

    @classmethod
    def set_device_un_online(cls, status):
        if status != cls.DEVICE_UN_ONLINE:
            cls.DEVICE_UN_ONLINE = status
            if not status:
                sys_bus.publish(INTERNAL_TOPIC.DEVICE_RECONNECT, None)

    @classmethod
    def set_metering_chip_failure(cls, status):
        if status != cls.METERING_CHIP_FAILURE:
            cls.METERING_CHIP_FAILURE = status

    @classmethod
    def set_register(cls, status):
        if status != cls.METERING_CHIP_FAILURE:
            cls.REGISTER = status


# 默认配置
class DefaultDeviceConfig(object):
    """默认的设备相关配置"""
    MIN_POWER = 15
    MAX_POWER = 750
    # TODO
    NO_LOAD_WAIT_TIME = 100
    FULL_WAIT_TIME = 1800
    HEART_TIME = 3
    CHARGING_DATA_TELL_CHILL_TIME = 60
    WORKING_VOLTAGE = 230
    DEVICE_INFO_UPLOAD = 12 * 3600
    TEMPERATURE_ALARM = 70
    SMOKE_ALARM = 0
    MIN_CURRENT = 20
    BACK_TIME = 60 * 1000

    @classmethod
    def init(cls, conf):
        res = conf['res']
        cls.set_min_power(res[0])
        cls.set_max_power(res[1])
        cls.set_no_load_wait_time(res[2])
        cls.set_full_wait_time(res[3])
        cls.set_heart_time(res[4])
        cls.set_charging_data_tell_chill_time(res[5])
        cls.set_working_voltage(res[6])
        cls.set_device_info_upload(res[7])

    @classmethod
    def set_min_power(cls, min_power):
        if min_power != cls.MIN_POWER:
            print("set_min_power = {}".format(min_power))
            cls.MIN_POWER = min_power

    @classmethod
    def set_max_power(cls, max_power):
        if max_power != cls.MAX_POWER:
            print("set_max_power = {}".format(max_power))
            cls.MAX_POWER = max_power

    @classmethod
    def set_no_load_wait_time(cls, no_load_wait_time):
        if no_load_wait_time != cls.NO_LOAD_WAIT_TIME:
            print("set_no_load_wait_time = {}".format(no_load_wait_time))
            cls.NO_LOAD_WAIT_TIME = no_load_wait_time

    @classmethod
    def set_full_wait_time(cls, full_wait_time):
        if full_wait_time != cls.FULL_WAIT_TIME:
            print("set_full_wait_time = {}".format(full_wait_time))
            cls.FULL_WAIT_TIME = full_wait_time

    @classmethod
    def set_heart_time(cls, heart_time):
        if heart_time != cls.HEART_TIME:
            print("set_heart_time = {}".format(heart_time))
            cls.HEART_TIME = heart_time

    @classmethod
    def set_charging_data_tell_chill_time(cls, charging_data_tell_chill_time):
        if charging_data_tell_chill_time != cls.CHARGING_DATA_TELL_CHILL_TIME:
            print("set_charging_data_tell_chill_time = {}".format(charging_data_tell_chill_time))
            cls.CHARGING_DATA_TELL_CHILL_TIME = charging_data_tell_chill_time

    @classmethod
    def set_working_voltage(cls, working_voltage):
        if working_voltage != cls.WORKING_VOLTAGE:
            print("set_working_voltage = {}".format(working_voltage))
            cls.WORKING_VOLTAGE = working_voltage

    @classmethod
    def set_device_info_upload(cls, device_info_upload):
        if device_info_upload * 3600 != cls.DEVICE_INFO_UPLOAD and device_info_upload:
            print("set_device_info_upload = {}".format(device_info_upload))
            cls.DEVICE_INFO_UPLOAD = device_info_upload

    @classmethod
    def set_temperature_alarm(cls, temperature_alarm):
        if temperature_alarm != cls.TEMPERATURE_ALARM:
            print("set_temperature_alarm = {}".format(temperature_alarm))
            cls.TEMPERATURE_ALARM = temperature_alarm

    @classmethod
    def set_smoke_alarm(cls, smoke_alarm):
        if smoke_alarm != cls.SMOKE_ALARM:
            print("set_smoke_alarm = {}".format(smoke_alarm))
            cls.SMOKE_ALARM = smoke_alarm

    @classmethod
    def set_config(cls, conf):
        cls.init(conf)
        conf_store.update({"default_device_config": conf})


# 订阅的topic
class INTERNAL_TOPIC(object):
    """主要维护内部发布指令用"""
    SET_HEART_TIME = "set-heart-time"
    DEVICE_HEART = "keep-heart"  # 心跳保持
    DEVICE_REGISTER = "device-register"
    PROTOCOL_ANALYSIS = "protocol-analysis"
    SEARCH_DEVICE_INFO = "search-device_info"
    SEARCH_COMMUNICATION_MODULE_INFO = "search-communication-module-info"
    SEARCH_SOCKET_STATUS = "search-socket-status"
    SEARCH_LBS_INFO = "search-lbs-info"
    SEARCH_MEDIA_INFO = "opt-media-info"
    PHONE_OPEN_CHARGING = "open-charging"
    DEVICE_RESTART = "device-restart"
    SET_MEDIA_VOLUME = "set-media-volume"
    STOP_CHARGING = "stop-charging"
    GET_EFFVO_DATA = "get-effvo-data"
    UPLOAD_CHARGING_STATUS = "upload-charging-status"
    A_CHARGING = SOCKET_A + "-charging"
    B_CHARGING = SOCKET_B + "-charging"
    A_STOP_CHARGING = SOCKET_A + "-stop-charging"
    B_STOP_CHARGING = SOCKET_B + "-stop-charging"
    CHARGING_UPDATER_STATUS_START = "charging-updater-status-start"
    CHARGING_UPDATER_STATUS_STOP = "charging-updater-status-stop"
    UPLOAD_POWER_STATUS = "UPLOAD-POWER-STATUS"
    CHARGING_UPDATER_STATUS = "CHARGING-UPDATER-STATUS"
    CHARGING_END = "CHARGING-END"
    SEARCH_DEFAULT_CONFIG_INFO = "search-default-config-info"
    SET_DEFAULT_CONFIG_INFO = "set-default-config-info"
    SEARCH_DEVICE_TEMPERATURE = "search-device-temperature"
    SEARCH_SOCKET_INFO = "search-socket-info"
    SET_MEDIA_INFO = "set-media-info"
    SET_MEDIA_CONF = "set-media-config"
    GET_MEDIA_CONF = "get-media-config"
    DISCOVER_CARD = "discovery-card"
    CREDIT_CARD_CHARGING = "credit-card-charging"
    CREDIT_CARD_CHARGE_REQUEST = "credit-card-charge-request"
    CREDIT_CARD_CHOSE_SOCKET_CHARGING = "credit-card-socket-charging"
    UPLOAD_CREDIT_CARD_ORDER = "upload-credit-card-order"
    DEVICE_RECONNECT = "device-reconnect"
    SEARCH_OTA_VERSION = "search-ota-version"
    REQUEST_OTA_DATA = "request-ota-data"
    START_DEVICE_OTA = "START-DEVICE-OTA"
    PLATFORM_ISSUED_OTA = "PLATFORM-ISSUED-OTA"
    FAILURE_REPORT = "FAILURE-REPORT"
    DLT_INFO_REPORT = "dlt-info-report"
    SEARCH_CHARGING_STATE = "search-charging-state"
    SEARCH_CHARGING_SOCKET_STATUS = "search-charging-socket-status"
    SUCCESS_ONLINE = "success-online"
    SEARCH_OTA_UPGRADE_VERSION = "search-ota-upgrade-version"
    RequestOTAData = "request-ota-data"
    STOP_ALL_CHARGING = "stop-all-charging"


# 充电模式
class STOP_CHARGING_MODE(object):
    """
    1：计时结束
    2：充满结束
    3：手动结束
    4：功率过大
    5：空载结束
    6：中途异常拔掉插座
    15：负载异常-电压过高
    """
    END_OF_TIMING = 0X01
    FULL_CHARGING = 0X02
    FINISH_BY_USER = 0X03
    EXCEED_MAX_POWER = 0X04
    NO_LOADER = 0X05
    EXCEPTION_UNPLUG_SOCKET = 0X06
    ABNORMAL_LOAD = 0x0f


# 播报系列
class MEDIA_PLAY_DATA(object):
    """
    媒体播放信息
    """
    NO_LOADER = "未检测到充电插头，请连接充电器"
    START_CHARGING = "{}号插座开始充电"
    FULL_CHARGING = "充电完成，感谢使用"
    EXCEED_MAX_POWER = "功率过大"
    CREDIT_CARD_CHARGING_BALANCE = "刷卡成功, 卡余额为{}元, 开始充电"
    START_CHARGING_MEDIA = "开始充电"
    CREDIT_CARD_CHARGING_TIME = "时长为{}分钟"
    CREDIT_CARD_CHARGING_ELECTRIC = "电量为{}度"
    ALL_SOCKET_NO_LOADER = "卡余额为{}元，请连接充电器"
    CARD_EXCEPTION = "卡异常"
    NOT_SUFFICIENT_FUNDS = "卡余额为{}元，账户余额不足，请及时充值"
    CARD_NOT_INTO_SYSTEM = "该卡未录入系统"
    SWIPE_CARD_OFTEN = "刷卡过于频繁，请稍后再试"
    EXIST_NO_FINISH_ORDER = "存在未结束订单"
    EXCEED_ROUND = "卡超出了使用范围"


# CDZ的版本号
class CDZ_VERSION(object):
    # 主控版本
    MASTER = MCU_VERSION
    # 刷卡器版本
    SOCKET = 0x00
    # 通讯模块版本
    PROTOCOL = 0x0001


class SWIPE_CARD_RETURN_STATUS(object):
    """
    刷卡返回状态判断
    """
    SUCCESS = 0X01
    EXCEPTION = 0x02
    NOT_SUFFICIENT_FUNDS = 0x03
    CARD_NOT_INTO_SYSTEM = 0x04
    CARD_STATUS_ILLEGALITY = 0x05
    SWIPE_CARD_OFTEN = 0x06
    EXIST_NO_FINISH_ORDER = 0X07
    EXCEED_ROUND = 0x08


class Media(object):
    """媒体对象"""
    COMPENSATE_VOLUME = 4

    def __init__(self):
        self.tts = audio.TTS(0)
        self.aud = audio.Audio(0)
        self.lock = Lock()
        self.tts.setVolume(7)
        # 播报状态
        self.broadcast_state = 0x01
        # 防打扰开关
        self.disturb_proof_switch = 0x00
        sys_bus.subscribe(INTERNAL_TOPIC.SET_MEDIA_CONF, self._set_media_conf)
        sys_bus.subscribe(INTERNAL_TOPIC.GET_MEDIA_CONF, self._get_media_conf)
        self.set_volume(7)

    def init(self, msg):
        self.broadcast_state = msg["res"][0]
        self.disturb_proof_switch = msg["res"][1]
        self.set_volume(msg["res"][2])

    def _set_media_conf(self, topic, msg):
        self.init(msg)
        conf_store.update(
            {"media": msg}
        )
        sys_bus.publish(INTERNAL_TOPIC.SET_MEDIA_INFO, msg)

    def _get_media_conf(self, topic, msg):
        msg["res"] = [self.broadcast_state, self.disturb_proof_switch, self.get_volume()]
        sys_bus.publish(INTERNAL_TOPIC.SEARCH_MEDIA_INFO, msg)

    def play(self, priority=4, breakin=0, mode=2, data=None):
        """
            策略可以支持媒体是否播放
            播放情景下:
                1. 设置后不允许 20:00 ~ 凌晨8点的播报
        """
        if self.broadcast_state:
            if self.disturb_proof_switch:
                local_time = utime.localtime()
                if local_time[3] >= 20 or local_time[3] <= 8:
                    return
            self.tts.play(priority, breakin, mode, data)

    def get_volume(self):
        return self.aud.getVolume() - self.COMPENSATE_VOLUME

    def _set_volume(self, volume):
        print("_set_volume = {}".format(self.COMPENSATE_VOLUME + volume))
        return self.aud.setVolume(self.COMPENSATE_VOLUME + volume)

    def set_volume(self, volume):
        with self.lock:
            if volume != self.get_volume():
                if 7 >= volume > 0:
                    self._set_volume(volume)
                elif volume > 7:
                    self._set_volume(7)
                else:
                    self._set_volume(0)


class GPIO(object):
    """GPIO对象"""

    def __init__(self, gpio, arg2=Pin.OUT, arg3=Pin.PULL_DISABLE, mode=0):
        self.gpio = Pin(gpio, arg2, arg3, mode)

    def on(self):
        if not self.gpio.read():
            self.gpio.write(1)

    def read(self):
        return self.gpio.read()

    def off(self):
        if self.gpio.read():
            self.gpio.write(0)

    def enable(self):
        self.on()

    def disable(self):
        self.off()


# LED灯
class LED(object):
    """三色灯 绿色、红色、黄色"""

    def __init__(self, name, gpios):
        self.color = name
        self.tubes = []
        self.freq = 0.3
        self.init_tubes(gpios)

    def init_tubes(self, gpios):
        if isinstance(gpios, list):
            for tube in gpios:
                self.tubes.append(tube)
        else:
            self.tubes.append(gpios)

    def read(self):
        i = 0
        for l in self.tubes:
            if l.read():
                i += 1
        return True if i == len(self.tubes) else False

    def on(self):
        for bute in self.tubes:
            bute.on()

    def off(self):
        for bute in self.tubes:
            bute.off()

    def blink(self, *args):
        self.off()
        self.on()
        utime.sleep(self.freq)
        self.off()


class EffVo(object):
    """电池信息类"""

    def __init__(self, name, *args):
        self.name = name
        self.current_eff_value = args[0] * 100 / 26601.031363
        self.device_voltage_data = args[1]
        self.current_channel_power_value = args[2]
        self.current_channel_pulse_meter_value = args[3]
        self.device_internal_temperature = args[4]
        self.device_external_temperature = args[5]


class VoltaProto(object):
    """计量器协议解析器"""
    FORMAT = "B3s3s3s3s3s3s3s3s3s3s3sB"
    BYTEORDER = "little"

    def __init__(self, *args):
        # 电压有效值
        self.device_voltage_data = self.byte_order_transfer(args[4]) * 100 / 33636.48141 + vt
        self.device_internal_temperature = self.cal_truth_temp(self.byte_order_transfer(args[10]))
        self.device_external_temperature = self.byte_order_transfer(args[11])
        self.b = EffVo(
            SOCKET_B,
            self.byte_order_transfer(args[2]),
            self.device_voltage_data,
            self.byte_order_transfer(args[6]),
            self.byte_order_transfer(args[8]),
            self.device_internal_temperature,
            self.device_external_temperature
        )
        self.a = EffVo(
            SOCKET_A,
            self.byte_order_transfer(args[3]),
            self.device_voltage_data,
            self.byte_order_transfer(args[7]),
            self.byte_order_transfer(args[9]),
            self.device_internal_temperature,
            self.device_external_temperature
        )
        self.checksum = args[12]

    @staticmethod
    def byte_order_transfer(data):
        return int.from_bytes(data, "little")

    @staticmethod
    def cal_truth_temp(data):
        return (170 / 448) * (data / 2 - 32) - 45

    @classmethod
    def create(cls, data):
        res = ustruct.unpack(cls.FORMAT, data)
        return VoltaProto(*res)


# BL0939计量器 解析协议发送消息到app队列中/ app解析分配到对应队列
class VoltaMeter(object):
    """点亮器读取"""

    def __init__(self):
        self.uart = UART(UART.UART2, 4800, 8, 0, 1, 0)
        self.timer = osTimer()
        self.error_count = 0
        self.uart_err_count = 0
        self.lock = Lock()

    @staticmethod
    def uchar_checksum(data, byteorder='little'):
        """
        char_checksum 按字节计算校验和。每个字节被翻译为无符号整数
        @param data: 字节串
        @param byteorder: 大/小端
        """
        length = len(data)
        checksum = int.from_bytes(b'\x55', byteorder)
        checksum &= 0xFF
        checksum = int.from_bytes(b'\x56', byteorder)
        checksum &= 0xFF

        for i in range(0, length):
            checksum += int.from_bytes(data[i:i + 1], byteorder)
            checksum &= 0xFF  # 强制截断

        return checksum

    @staticmethod
    def resolve(data):
        return VoltaProto.create(data)

    def read(self) -> VoltaProto:
        with self.lock:
            try:
                self.uart.write(bytearray([0x55, 0xAA]))
                utime.sleep_ms(300)
                msg_len = self.uart.any()
                data = self.uart.read(msg_len)  # 读不到数据，做异常处理
                print("MSG_LEN = {} data = {}".format(msg_len, data))
                if len(data) == 35:
                    check_sum = self.uchar_checksum(data[:-1])
                    if 256 != check_sum + data[-1]:
                        raise Exception("check sum error")
                    res_d = self.resolve(data)
                    if self.error_count:
                        if self.error_count >= 60:
                            res = (0xff, FailureReportCode.ERROR_IC, 0, 1)
                            sys_bus.publish(INTERNAL_TOPIC.FAILURE_REPORT, dict(res=res))
                            res = (0xff, FailureReportCode.ERROR_IC, 0, 2)
                            sys_bus.publish(INTERNAL_TOPIC.FAILURE_REPORT, dict(res=res))
                        self.error_count = 0
                        DefaultDeviceState.set_metering_chip_failure(False)
                    return res_d
                else:
                    raise Exception("data length error")
            except Exception as e:
                print("except Exception as e = {}".format(e))
                self.error_count += 1
                if self.error_count == 60:
                    DefaultDeviceState.set_metering_chip_failure(True)
                    res = (0xff, FailureReportCode.ERROR_IC, 1, 1)
                    sys_bus.publish(INTERNAL_TOPIC.FAILURE_REPORT, dict(res=res))
                    res = (0xff, FailureReportCode.ERROR_IC, 1, 2)
                    sys_bus.publish(INTERNAL_TOPIC.FAILURE_REPORT, dict(res=res))
                    sys_bus.publish(INTERNAL_TOPIC.STOP_ALL_CHARGING, STOP_CHARGING_MODE.EXCEPTION_UNPLUG_SOCKET)


class LEDManage(object):
    """LED的管理器   主要管理灯"""

    class STATUS:
        NORMAL = 0
        NO_LOADING = 1
        CHARGING = 2

    def __init__(self, red, green):
        self.red = LED("RED", GPIO(red))
        self.green = LED("GREEN", GPIO(green))
        self.yellow = LED("YELLOW", [self.red, self.green])
        self.state_machine_id = None
        self.last_point = self.STATUS.NORMAL
        self.state_change = False
        self.lock = Lock()

    def put(self, p):
        self._put(p)

    def _put(self, p):
        """接受指令并处理"""
        if p is not None:
            if p <= self.last_point and p != self.STATUS.CHARGING and p != -1:
                if p == self.STATUS.NORMAL and self.last_point == self.STATUS.CHARGING:
                    """检测充电拔出现象"""
                    pass
                else:
                    return
            if p != self.last_point:
                self.state_change = True
            self.last_point = p
            if p == -1:
                self.last_point = self.STATUS.NORMAL

    def off(self):
        self.yellow.off()

    def check(self):
        self.yellow.off()
        self.red.blink()
        self.green.blink()
        self.yellow.blink()
        self.green.on()
        if not self.state_machine_id:
            self.state_machine_id = _thread.start_new_thread(self._state_machine, ())

    def _state_machine(self):
        while True:
            # 跳动导致跳出函数执行, 主要为了判断状态是否发生改变改变则立即调整led灯改变姿势
            p = self.last_point
            if p != self.STATUS.CHARGING:
                if DefaultDeviceState.DEVICE_UN_ONLINE:
                    self.yellow.on()
                    self.state_change = True
                    utime.sleep(0.5)
                    self.yellow.off()
                    utime.sleep(1)
                    continue
                if DefaultDeviceState.METERING_CHIP_FAILURE:
                    self.green_blinker()
                    utime.sleep(0.2)
                    self.yellow.blink()
                    utime.sleep(0.2)
                    self.yellow.blink()
                    utime.sleep(0.2)
                    self.state_change = True
                    continue
                if DefaultDeviceState.CONNECT_SERVER_ERROR:
                    self.green_blinker()
                    utime.sleep(0.2)
                    self.yellow.blink()
                    utime.sleep(0.2)
                    self.state_change = True
                    continue
            if self.state_change:
                """当状态机发生改变时会执行"""
                self.state_change = False
                self.off()
                if p == self.STATUS.NORMAL:
                    """绿灯常亮"""
                    self.green_all()
                elif p == self.STATUS.NO_LOADING:
                    """没接负载"""
                    self.green_blinker()
                elif p == self.STATUS.CHARGING:
                    """充电红灯常亮"""
                    self.red_all()
            else:
                """主要处理跳动的"""
                if p == self.STATUS.NO_LOADING:
                    self.green_blinker()
            utime.sleep(1)

    def green_blinker(self):
        self.green.blink()

    def green_all(self):
        if not self.green.read():
            self.off()
            self.green.on()

    def yellow_all(self):
        if not self.yellow.read():
            self.off()
            self.yellow.on()

    def red_all(self):
        if not self.red.read():
            self.off()
            self.red.on()


class NetManage(object):
    """网络的管理器"""
    OFFLINE_THRESHOLD = 15

    def __init__(self):
        self.PROJECT_NAME = "XXXXXXXX"
        self.PROJECT_VERSION = "XXXX"
        self.checknet = checkNet.CheckNetwork(self.PROJECT_NAME, self.PROJECT_VERSION)
        self.timer = osTimer()
        self.offline_count = 0

    def check(self):
        stagecode, subcode = self.checknet.wait_network_connected(30)
        # 注册拨号
        self.timer.start(60 * 1000, 1, self.nw_cb)
        if stagecode == 3 and subcode == 1:
            pass
        else:
            DefaultDeviceState.set_device_un_online(True)

    def nw_cb(self, *args):
        nw_sta = dataCall.getInfo(1, 0)[2][0]
        print(
            "------------------------ check net work nw_sta = {} minutes = {} min----------------------".format(nw_sta,
                                                                                                                self.offline_count))
        if nw_sta:
            DefaultDeviceState.set_device_un_online(False)
            if not DefaultDeviceState.CONNECT_SERVER_ERROR:
                self.offline_count = 0
                return
        else:
            DefaultDeviceState.set_device_un_online(True)
        self.offline_count += 1
        if self.offline_count == self.OFFLINE_THRESHOLD:
            """设备持续掉线5 + 10分钟, 设备自动重启"""
            print("device offline 5 + 10 = 15 min will be restart")
            Power.powerRestart()


"""==========================================充电======================================="""
# 逻辑
# server TLV发布数据到/刷卡 -> app中, app发给 -> 插座管理器 -> 插座集合插座集合选择对应插座 -> 插座设置一些参数 -> 插座充电/选择充电模式 -> 对应模式中充电

class OPEN_CHARGE_MODE(object):
    """开电 方式"""
    USE_TIME = 1
    USE_MONEY = 2
    USE_DEGREE = 3


class CHARGE_MODE(object):
    """充电方式"""
    PROPORTION_OF_TIME = 1
    MAX_POWER = 2
    BATTERY_CHARGING = 3


class ChargingMode(object):
    """充电模式类"""

    def __init__(self):
        self.sock = None
        # 充电状态
        self.charging = False
        self.min_power = None
        self.max_power = None
        self.no_load_wait_time = None
        self.full_wait_time = None
        self.total_charging_time = None
        self.order_number = None
        self.charge_mode = None
        self.open_mode = None
        self.full_of_power = None

        self.charging_max_power = 0
        self.start_time = 0
        self.end_time = None
        self.thread_id = None
        self.card_order = None
        self.charging_time = 0
        self.initial_charging_time = 0
        self.idx = 0

    def update_charging_time(self):
        self.charging_time = utime.mktime(utime.localtime()) - self.start_time + self.initial_charging_time
        print("charging_time = {} start_time {} initial_charging_time {}".format(self.charging_time, self.start_time,
                                                                                 self.initial_charging_time))

    def get_total_charging_time(self):
        return self.total_charging_time

    def get_idx(self):
        return self.idx

    def get_charging_time(self):
        return self.charging_time

    def get_charging_max_power(self):
        return self.charging_max_power

    @property
    def finish_charging_time(self):
        if self.end_time is None:
            return 0
        return (self.end_time - utime.mktime(utime.localtime())) // 60

    def get_full_stop_charging(self):
        return self.full_of_power

    def init(self, msg):
        res = msg["res"]
        self.min_power = res[1]
        self.max_power = res[2]
        self.no_load_wait_time = res[3]
        self.full_wait_time = res[4]
        self.order_number = res[6]
        self.charge_mode = res[7]
        self.open_mode = res[8]
        self.full_of_power = res[9]
        self.total_charging_time = res[5]
        return self

    def reset(self):
        self.min_power = None
        self.max_power = None
        self.no_load_wait_time = None
        self.full_wait_time = None
        self.total_charging_time = None
        self.order_number = None
        self.charge_mode = None
        self.open_mode = None
        self.full_of_power = None
        self.start_time = 0
        self.end_time = None
        self.charging = False
        self.thread_id = None
        self.card_order = None
        self.sock = None
        self.charging_time = 0
        self.idx = 0
        self.charging_max_power = 0
        self.initial_charging_time = 0

    def update(self, msg):
        """子类实现"""
        pass

    def charging_check(self, topic, power_value, capacity):
        """子类实现"""
        pass

    def restart_open_charge(self, socket_num, card_order, msg):
        """子类实现"""
        pass

    def no_restart_open_charge(self, socket_num, card_order, msg):
        """子类实现"""
        pass

    def open_charge(self, socket_num, card_order, msg):
        """
        1. 判断是否重启后开电
            2. 我们需要拿到已经充电的时间去计算剩余充电的时间
        """
        restart_flag = msg.get("restart", False)
        if restart_flag:
            self.restart_open_charge(socket_num, card_order, msg)
        else:
            self.no_restart_open_charge(socket_num, card_order, msg)

    def stop_charging(self):
        self.reset()

    def publish_credit_card_order(self, total_charging_time, msg):
        msg["res"] = [msg.get("socket"), total_charging_time, self.order_number]
        # 转换添加   由于micropython这块表达式会报错所以这样转换
        for tid in self.card_order["card_id"]:
            msg["res"].append(tid)
        sys_bus.publish(INTERNAL_TOPIC.UPLOAD_CREDIT_CARD_ORDER, msg)

    def already_charging_time(self):
        """TODO 需要重新计算"""
        return (utime.mktime(utime.localtime()) - self.start_time) // 60

    def model_to_list(self, socket_num):
        return [socket_num, self.min_power, self.max_power, self.no_load_wait_time, self.full_wait_time,
                self.total_charging_time,
                self.order_number, self.charge_mode, self.open_mode, self.full_of_power]


# 功率充电模式
class PowerCharging(ChargingMode):
    """功率充电"""
    MODE = CHARGE_MODE.MAX_POWER

    def __init__(self):
        super(PowerCharging, self).__init__()
        self.price_of_power = []
        self.idx_tamper_proof = []
        self.first_modify = False
        self.card_balance = 0

    def get_total_charging_time(self):
        return math.ceil((self.total_charging_time * 3600) / self.price_of_power[self.idx]) // 60

    def reset(self):
        super(PowerCharging, self).reset()
        self.price_of_power = []
        self.idx = 0
        self.idx_tamper_proof = []
        self.first_modify = False
        self.card_balance = 0

    def already_charging_time(self):
        return self.charging_time

    def init(self, msg):
        super(PowerCharging, self).init(msg)
        self.price_of_power = msg["res"][10:]
        return self

    def _finish_charging_time(self):
        try:
            total_time = math.ceil((self.total_charging_time * 3600) / self.price_of_power[self.idx]) // 60
        except Exception as e:
            print("calculate total_time error = {}".format(e))
            total_time = self.finish_charging_time
        print("total_time --------------------------------- {}".format(total_time))
        return math.ceil(total_time)

    def update(self, msg):
        """更新, 刷卡情况下允许更新, 这里我们只需要更新下总total_charging_time就可以了"""
        socket_open_mode = msg.get("mode", OPEN_SOCKET_MODE.PHONE)
        res = msg["res"]
        if socket_open_mode == OPEN_SOCKET_MODE.CARD:
            self.order_number = res[6]
            self.total_charging_time = self.total_charging_time + res[5]
            self.power_update_time()
        else:
            return
        self.publish_credit_card_order(res[5], msg)
        media.play(
            data=(MEDIA_PLAY_DATA.CREDIT_CARD_CHARGING_BALANCE + MEDIA_PLAY_DATA.CREDIT_CARD_CHARGING_TIME
                  ).format(math.ceil(msg["card_balance"] / 100), self._finish_charging_time()))

    def get_consumer_standard_index(self, power_value=None):
        if not power_value:
            power_value = self.sock.get_charge_power()
        consumer_standard_index = 0

        print("self.price_of_power = {}".format(self.price_of_power))
        power_list = list(self.price_of_power)[1:][::2]
        for i in range(len(power_list)):
            if not power_list[i]:
                consumer_standard_index = i - 1
                break
            if power_value < power_list[i]:
                consumer_standard_index = i
                break
            if i == len(power_list) - 1:
                consumer_standard_index = (len(power_list) - 1)

        return consumer_standard_index * 2

    def power_update_time(self):
        """根据功率去更新充电时间"""
        print("----------------------ENTER---------------------------power update time----------------------------")
        current_time = utime.mktime(utime.localtime())
        self.end_time = current_time + math.ceil(
            (self.total_charging_time * 3600) / self.price_of_power[self.idx]) - self.charging_time
        print(
            "start_time = {} current_time = {} end_time = {} idx = {} total_charging_time = {} charging_time = {} {}".format(
                self.start_time,
                current_time,
                self.end_time,
                self.idx,
                self.total_charging_time,
                self.charging_time,
                self.price_of_power
            ))
        print("---------------------EXIT-----------------------------power update time---------------------------")

    def charging_check(self, topic, power_value, capacity):
        """这里需要动态的计算出结束时间, 当每次阀值等于10, 取出平均功率"""
        print("-----------------ENTER------------------Power charging_check-----------------------")
        print("power_value -> {} capacity -> {} price_of_power = {} idx = {}".format(power_value,
                                                                                     capacity,
                                                                                     self.price_of_power,
                                                                                     self.idx
                                                                                     ))
        if self.end_time:
            self.update_charging_time()
            if len(self.idx_tamper_proof) > 5:
                print("power charging check -> {}".format(self.idx_tamper_proof))
                self.idx_tamper_proof.append(power_value)
                vag_power = math.ceil(sum(self.idx_tamper_proof) / len(self.idx_tamper_proof))
                idx = self.get_consumer_standard_index(vag_power)
                self.idx_tamper_proof = []
                if idx > self.idx:
                    print("---------------UPDATE------------ power has been update -> {}".format(idx))
                    self.idx = idx
                    self.power_update_time()
                    if not self.first_modify:
                        self.first_modify = True
                        media.play(
                            data=(MEDIA_PLAY_DATA.START_CHARGING_MEDIA + MEDIA_PLAY_DATA.CREDIT_CARD_CHARGING_TIME
                                  ).format(self._finish_charging_time()))
                    self.sock._bak_sock_info()
            else:
                self.idx_tamper_proof.append(power_value)
            current_time = utime.mktime(utime.localtime())
            print(
                "start_time = {} current_time = {} end_time = {}".format(self.start_time, current_time, self.end_time))
            if power_value > self.charging_max_power:
                self.charging_max_power = power_value
            if current_time > self.end_time:
                sys_bus.publish(topic, {"status": STOP_CHARGING_MODE.END_OF_TIMING})
        print("-----------------EXIT------------------Power charging_check-----------------------")

    def restart_open_charge(self, socket_num, card_order, msg):
        """
        重启启动  我们需要获取  总充电时间
        1. 设置充电开始时间
        后面
        我们需要更新几个值, 已充电的时间
        判断档位和原先充电的档位哪个大   选最大档位
        通过功率去更新时间
        2. 我们需要更新充电时间在功率充电之前(就是计算充电结束时间之前)
        """
        print("-------ENTER---------Power restart open charge----------------------")
        self.initial_charging_time = msg.get("charging_time")
        self.charging_max_power = msg.get("charging_max_power")
        idx = msg.get("idx")
        self.start_time = utime.mktime(utime.localtime())
        self.get_consumer_standard_index()
        if idx > self.idx:
            self.idx = idx
        self.update_charging_time()
        self.power_update_time()
        print("charging_time = {} charging_max_power = {} start_time = {} end_time = {}".format(self.charging_time,
                                                                                                self.charging_max_power,
                                                                                                self.start_time,
                                                                                                self.end_time))
        print("-------EXIT---------Power restart open charge----------------------")

    def no_restart_open_charge(self, socket_num, card_order, msg):
        """
        设置充电开始时间
        获取当前功率的所属档位
        更新充电启动时间
        1. 判断是否刷卡  刷卡可以累加充电
        2. 不刷卡直接开电
        """
        res = msg["res"]
        socket_open_mode = msg.get("mode", OPEN_SOCKET_MODE.PHONE)
        self.start_time = utime.mktime(utime.localtime())
        self.idx = self.get_consumer_standard_index()
        self.power_update_time()
        print("------------> self.sock.get_charge_power -> {} ================= ".format(self.sock.get_charge_power()))
        print(
            "---ENTER--Power no_restart_open_charge-------start_time {}-----end_time {}------- ".format(self.start_time,
                                                                                                        self.end_time))
        if socket_open_mode == OPEN_SOCKET_MODE.PHONE:
            print("----------------------Power Sweep code charge----------------------")
            msg["res"] = (1, msg.get("socket"), self._finish_charging_time())
            sys_bus.publish(INTERNAL_TOPIC.PHONE_OPEN_CHARGING, msg)
            media.play(data=MEDIA_PLAY_DATA.START_CHARGING.format(socket_num + 1))
        else:
            print("----------------------Power Credit card charge----------------------")
            self.card_balance = math.ceil(msg["card_balance"] / 100)
            self.publish_credit_card_order(res[5], msg)
            media.play(data=(
                    MEDIA_PLAY_DATA.CREDIT_CARD_CHARGING_BALANCE + MEDIA_PLAY_DATA.CREDIT_CARD_CHARGING_TIME
            ).format(self.card_balance, self._finish_charging_time()))

    def model_to_list(self, socket_num):
        models = super(PowerCharging, self).model_to_list(socket_num)
        models.extend(self.price_of_power)
        return models


# 时间充电模式
class TimerCharging(ChargingMode):
    """时间充电"""
    MODE = CHARGE_MODE.PROPORTION_OF_TIME

    def __init__(self):
        super(TimerCharging, self).__init__()

    def init(self, msg):
        print("-------ENTER---------Timer INIT----------------------msg {}".format(msg))
        super(TimerCharging, self).init(msg)
        res = msg["res"]
        self.total_charging_time = res[5]
        print("-------EXIT---------Timer INIT----------------------")
        return self

    def charging_check(self, topic, power_value, capacity):
        print("-----------------ENTER------------------Timer charging_check-----------------------")
        print("power_value -> {} capacity -> {}".format(power_value, capacity))
        if self.end_time:
            self.update_charging_time()
            current_time = utime.mktime(utime.localtime())
            print(
                "start_time = {} current_time = {} end_time = {}".format(self.start_time, current_time, self.end_time))
            if power_value > self.charging_max_power:
                self.charging_max_power = power_value
            if current_time > self.end_time:
                print(".................end charging...............")
                sys_bus.publish(topic, {"status": STOP_CHARGING_MODE.END_OF_TIMING})
        print("-----------------EXIT------------------Timer charging_check-----------------------")

    def update(self, msg):
        """
        更新订单标准
        1. 我们需要更新充电时间  还有开始时间
        """
        print("-------ENTER---------Timer update----------------------")
        res = msg["res"]
        socket_open_mode = msg.get("mode", OPEN_SOCKET_MODE.PHONE)
        if socket_open_mode == OPEN_SOCKET_MODE.CARD:
            self.order_number = res[6]
            self.total_charging_time += res[5]
            self.end_time = self.end_time + res[5] * 60
        else:
            return
        self.publish_credit_card_order(res[5], msg)
        media.play(
            data=(MEDIA_PLAY_DATA.CREDIT_CARD_CHARGING_BALANCE + MEDIA_PLAY_DATA.CREDIT_CARD_CHARGING_TIME
                  ).format(math.ceil(msg["card_balance"] / 100), self.finish_charging_time))
        print(
            "-------EXIT---------Timer update-----------self.total_charging_time {}----- self.end_time {}------".format(
                self.total_charging_time, self.end_time
            ))

    def restart_open_charge(self, socket_num, card_order, msg):
        """
        重启后的开电
        1. 获取已充电电量
        2. 我们需要更新下充电时间, 在计算结束时间之前
        """
        print("-------ENTER---------Timer restart open charge----------------------")
        self.initial_charging_time = msg.get("charging_time")
        self.charging_max_power = msg.get("charging_max_power")
        current_time = utime.mktime(utime.localtime())
        self.start_time = utime.mktime(utime.localtime())
        self.update_charging_time()
        self.end_time = current_time + (self.total_charging_time * 60 - self.charging_time)

        print("charging_time = {} charging_max_power = {} start_time = {} end_time = {}".format(self.charging_time,
                                                                                                self.charging_max_power,
                                                                                                self.start_time,
                                                                                                self.end_time))
        print("-------EXIT---------Timer restart open charge----------------------")

    def no_restart_open_charge(self, socket_num, card_order, msg):
        """非重启开电  需要实时上报相关信息"""

        res = msg["res"]
        socket_open_mode = msg.get("mode", OPEN_SOCKET_MODE.PHONE)

        self.start_time = utime.mktime(utime.localtime())
        self.end_time = self.start_time + self.total_charging_time * 60
        print(
            "---ENTER--Timer no_restart_open_charge-------start_time {}-----end_time {}------- ".format(self.start_time,
                                                                                                        self.end_time))
        if socket_open_mode == OPEN_SOCKET_MODE.PHONE:
            print("----------------------Timer Sweep code charge----------------------")
            msg["res"] = (1, msg.get("socket"), self.finish_charging_time)
            sys_bus.publish(INTERNAL_TOPIC.PHONE_OPEN_CHARGING, msg)
            media.play(data=MEDIA_PLAY_DATA.START_CHARGING.format(socket_num + 1))
        else:
            print("----------------------Timer Credit card charge----------------------")
            self.publish_credit_card_order(res[5], msg)
            media.play(data=(
                    MEDIA_PLAY_DATA.CREDIT_CARD_CHARGING_BALANCE + MEDIA_PLAY_DATA.CREDIT_CARD_CHARGING_TIME
            ).format(math.ceil(msg["card_balance"] / 100), self.finish_charging_time))
        print("---EXIT----Timer no_restart_open_charge-------")


# 电池电量充电模式
class BatteryCharging(ChargingMode):
    """度数充电"""
    MODE = CHARGE_MODE.BATTERY_CHARGING

    @property
    def finish_charging_time(self):
        """这里要返回可充电时间"""
        if self.end_time is None:
            return 0
        return math.ceil(self.end_time - self.sock.get_charged_capacity())

    def update_charging_time(self):
        return 0

    def update(self, msg):
        """更新订单标准"""
        print("---ENTER--Battery update-----end_time {}------- ".format(self.end_time))
        socket_open_mode = msg.get("mode", OPEN_SOCKET_MODE.PHONE)
        res = msg["res"]
        if socket_open_mode == OPEN_SOCKET_MODE.CARD:
            self.order_number = res[6]
            self.total_charging_time += res[5]
            self.end_time = self.total_charging_time
        else:
            return
        self.publish_credit_card_order(res[5], msg)
        media.play(
            data=(MEDIA_PLAY_DATA.CREDIT_CARD_CHARGING_BALANCE + MEDIA_PLAY_DATA.CREDIT_CARD_CHARGING_ELECTRIC
                  ).format(math.ceil(msg["card_balance"] / 100), self.finish_charging_time / 10))

    def restart_open_charge(self, socket_num, card_order, msg):
        """
        重启后的开电
        1. 获取已充电电量
        """
        print("-------------------ENTER-------------------Battery restart_open_charge------------")
        self.charging_time = msg.get("charging_time")
        self.charging_max_power = msg.get("charging_max_power")
        self.start_time = utime.mktime(utime.localtime())
        self.end_time = self.total_charging_time
        print("charging_time = {} charging_max_power = {} start_time = {} end_time = {}".format(self.charging_time,
                                                                                                self.charging_max_power,
                                                                                                self.start_time,
                                                                                                self.end_time))
        print("-------------------EXIT-------------------Battery restart_open_charge------------")

    def no_restart_open_charge(self, socket_num, card_order, msg):
        """非重启开电  需要实时上报相关信息"""
        res = msg["res"]
        socket_open_mode = msg.get("mode", OPEN_SOCKET_MODE.PHONE)

        self.start_time = utime.mktime(utime.localtime())
        self.end_time = self.total_charging_time
        print(
            "---ENTER--Battery no_restart_open_charge-----end_time {}------- ".format(
                self.end_time))
        if socket_open_mode == OPEN_SOCKET_MODE.PHONE:
            """手机支付情况"""
            print("----------------------Battery Sweep code charge----------------------")
            msg["res"] = (1, msg.get("socket"), self.finish_charging_time)
            sys_bus.publish(INTERNAL_TOPIC.PHONE_OPEN_CHARGING, msg)
            media.play(data=MEDIA_PLAY_DATA.START_CHARGING.format(socket_num + 1))
        else:
            print("----------------------Battery Credit card charge----------------------")
            """刷卡支付情况"""
            self.publish_credit_card_order(res[5], msg)
            media.play(data=(
                    MEDIA_PLAY_DATA.CREDIT_CARD_CHARGING_BALANCE + MEDIA_PLAY_DATA.CREDIT_CARD_CHARGING_ELECTRIC
            ).format(math.ceil(msg["card_balance"] / 100), self.finish_charging_time / 10))
        print("---EXIT----Battery no_restart_open_charge-------")

    def charging_check(self, topic, power_value, capacity):
        print("-----------------ENTER------------------Battery charging_check-----------------------")
        print("power_value -> {} capacity -> {}".format(power_value, capacity))
        if self.end_time:
            print("start_time = {} capacity = {} end_time = {} init_plus_count = {} initialize_capacity = {}".format(
                self.start_time, capacity,
                self.end_time,
                self.sock.init_plus_count,
                self.sock.initialize_capacity))
            if power_value > self.charging_max_power:
                self.charging_max_power = power_value
            if capacity > self.end_time:
                print(".................end charging...............")
                sys_bus.publish(topic, {"status": STOP_CHARGING_MODE.END_OF_TIMING})
        print("----------EXIT-----------------Battery charging_check--------------------------------")


# 插座充电
class SocketCharge(object):
    """充电"""
    METER_DATA_LENGTH = 35
    VREF = 1.218
    RL = 1
    R1 = 1
    R2 = 390 * 5
    RETRY_THRESHOLD = 3

    def __init__(self, name, socket):
        self.name = name
        self.socket = socket
        self.socket_num = NUM_SOCKET_A if name == SOCKET_A else NUM_SOCKET_B
        self.charge_current = 0  # 表⽰瞬时电流，单位是毫安，换算后即1.453安培
        self.charge_voltage = 0  # 表⽰瞬时功率，单位毫⽡，换算后即1130.458⽡.
        self.charge_power = 0
        self.charged_capacity = 0  # 瓦时
        self.current_plus_count = 0
        self.init_plus_count = 0
        self.initialize_capacity = 0
        self.volumor_count_capacity = 0
        self.last_charge_voltage = 0  # 注意此单位是V

        # 初始管道电容
        self.charge_count = 0
        self.lock = Lock()

        self.device_internal_temperature = 0
        self.device_external_temperature = 0
        # 三种模式清单
        self.charging = False
        self.mode: ChargingMode = None
        self.mode_list = [PowerCharging(), TimerCharging(), BatteryCharging()]
        self.start_charging_topic = None
        self.stop_charging_topic = None
        self.card_order = None
        self._bak_sock_timer = osTimer()
        # 记录每次平次
        self.no_loader_count = 0
        self.full_charging_count = 0
        self.socket_insert_status_count = 0
        self.charge_power_exceed_max_power_count = 0
        self.open_charging_voltage = 220

    def init(self, msg):
        self.set_initialize_capacity(msg["initialize_capacity"])

    def subscribe(self):
        sys_bus.subscribe(self.name, self.update)
        self.start_charging_topic = INTERNAL_TOPIC.A_CHARGING if self.name == SOCKET_A else INTERNAL_TOPIC.B_CHARGING
        sys_bus.subscribe(self.start_charging_topic,
                          self.open_charging)
        self.stop_charging_topic = INTERNAL_TOPIC.A_STOP_CHARGING if self.name == SOCKET_A else INTERNAL_TOPIC.B_STOP_CHARGING
        sys_bus.subscribe(
            self.stop_charging_topic,
            self.stop_charging
        )

    def get_open_charge_voltage(self):
        return self.open_charging_voltage

    def get_charged_capacity(self):
        return self.charged_capacity

    def get_charge_power(self):
        return self.charge_power

    def open_charging(self, topic, msg: dict):
        with self.lock:
            # 需要携带ID来访问, 每次到这里可以拿到开电状态
            if self.charge_voltage > 275 * 100:
                print("-------------------------charge_voltage > {}".format(self.charge_voltage))
                return
            if DefaultDeviceState.METERING_CHIP_FAILURE:
                print("----------METERING CHIP FAILURE not allow open charging -----------")
                return
            res = msg.get("res")
            print("msg = {}".format(msg))
            if not self.mode:
                """如果模式不存在, 证明是首次充电请求"""
                for mode in self.mode_list:
                    if mode.MODE == res[7]:
                        self.mode = mode.init(msg)
                        self.mode.sock = self
                if self.mode.card_order is None:
                    """设置给子属性"""
                    self.mode.card_order = self.card_order
            else:
                """不同模式不允许叠加"""
                if self.mode.MODE != res[7]:
                    return
            """模式存在证明充电, 二次订单请求, 这里要对比"""
            if not self.charging:
                """初始化脉冲值记录首次的脉冲值"""
                self.init_plus_count = self.current_plus_count
                print(
                    "-------------open charge ---------- self.init_plus_count == {} self.initialize_capacity = {} "
                    "self.volumor_count_capacity = {}".format(
                        self.init_plus_count, self.initialize_capacity, self.volumor_count_capacity))
                self.charging = True
                self.socket.relay.on()
                self.open_charging_voltage = math.ceil(self.charge_voltage / 100)
                """避免启动的时候是由于断电才导致的设备重新充电"""
                self.mode.open_charge(self.socket_num, self.card_order, msg)
                """线程记录当前情况"""
                print("open charging {}".format(self.card_order))
                self._bak_sock_info()
                self._bak_sock_timer.start(DefaultDeviceConfig.BACK_TIME, 1, self._bak_sock_info)
                sys_bus.publish(INTERNAL_TOPIC.CHARGING_UPDATER_STATUS_START, None)
            else:
                """刷卡情况允许，追加充电"""
                print("update charging {}".format(self.card_order))
                self.mode.update(msg)
                """更新操作提前触发"""
                self._bak_sock_info()
            print("open charging success ~~")

    def _bak_sock_info(self, *args, **kwargs):
        """备份订单信息"""
        print("--------ENTER----------------update _bak_sock_info--------------------")
        data = {
            self.socket.name: {
                "res": self.mode.model_to_list(self.socket_num),
                "card_id": self.card_order["card_id"] if self.card_order else self.card_order,
                "initialize_capacity": self.get_charged_capacity(),
                "start_timestamp": self.mode.start_time,
                "timestamp": utime.mktime(utime.localtime()),
                "idx": self.mode.get_idx(),
                "charging_time": self.mode.get_charging_time(),
                "charging_max_power": self.mode.get_charging_max_power()
            }
        }
        print("\n_bak_sock_info data = {}--------------------\n".format(data))
        print("----------EXIT--------------update----------------------")
        conf_store.update(data)

    def upload_charging_status(self, msg):
        if self.charging:
            res = dict(res=(self.socket_num,
                            math.ceil(self.charge_power),
                            math.ceil(
                                self.mode.get_total_charging_time()),
                            math.ceil(
                                self.mode.get_charging_time() // 60),
                            50,
                            self.mode.order_number,
                            math.ceil(self.charged_capacity * 100)
                            ), msg_id=msg.get("msg_id"))
            sys_bus.publish(INTERNAL_TOPIC.SEARCH_CHARGING_STATE, res)

    def stop_charging(self, topic, msg):
        """停止充电"""
        with self.lock:
            print("topic ={} msg = {}".format(topic, msg))
            if self.charging:
                print("{} stop charging ~~~".format(self.name))
                self.socket.relay.off()
                # 发送停止充电指令
                status = msg["status"]
                res = [status, self.socket_num, self.mode.order_number,
                       math.ceil(self.get_charged_capacity() * 100), math.ceil(self.charge_power),
                       utime.mktime(utime.localtime())
                       ]
                print("res = {}".format(res))
                """根据状态去判断  上报充电结束消息"""
                media.play(data=MEDIA_PLAY_DATA.FULL_CHARGING)
                if status == STOP_CHARGING_MODE.FINISH_BY_USER:
                    """用户主动结束"""
                    msg["res"] = (
                        1, self.socket_num, self.mode.already_charging_time(),
                        math.ceil(self.charge_power),
                        self.mode.order_number)
                    sys_bus.publish(INTERNAL_TOPIC.STOP_CHARGING, msg)
                sys_bus.publish(INTERNAL_TOPIC.CHARGING_END, res)
                self.reset()
                sys_bus.publish(INTERNAL_TOPIC.CHARGING_UPDATER_STATUS_STOP, None)

    def set_initialize_capacity(self, initialize_capacity):
        self.initialize_capacity = initialize_capacity

    def reset(self):
        """复位初始数值"""
        self.charging = False
        self.mode.stop_charging()
        self.initialize_capacity = 0
        self.volumor_count_capacity = 0
        # self.init_plus_count = 0
        self._bak_sock_timer.stop()
        self.charged_capacity = 0
        conf_store.delete(self.socket.name)
        self.card_order = None
        self.mode = None

    def update(self, topic, evo: EffVo):

        self.charge_current = evo.current_eff_value
        self.charge_voltage = evo.device_voltage_data

        # sign_wait = 0  # 0代表正工, 1代表负功  -1代表负载读取错误
        """
        1. 情况下如果充电需要
            1. 优先初始化
            2. 电能统计需要
                1. 是否进入了  从0 -> 0xffffff的循环
                2. 不是直接递减
        """
        channel_power_value = evo.current_channel_power_value
        if evo.current_channel_power_value < 0xff:
            self.charge_power = evo.current_channel_power_value / 10
            sign_wait = -1
        else:
            # 这里需要转换成byte字节
            channel_power_value_byte = ustruct.pack("I", channel_power_value)
            if channel_power_value_byte[2] > 0x7f:
                # 负功, 补码转换
                self.charge_power = (0x1000000 - evo.current_channel_power_value) * 10 / 13978.93329
                sign_wait = 1
            else:
                self.charge_power = evo.current_channel_power_value * 10 / 13978.93329
                sign_wait = 0

        self.device_internal_temperature = evo.device_internal_temperature
        channel_plus_meter = evo.current_channel_pulse_meter_value

        # 测试
        self.charge_power = vtp + self.charge_power

        if sign_wait == -1:
            volumor_count_capacity = 0
        else:
            # 脉冲差值
            if sign_wait == 1:
                """负功"""
                if self.init_plus_count:
                    pulse_difference_value = self.init_plus_count - channel_plus_meter
                    if pulse_difference_value < 0:
                        pulse_difference_value = 0x1000000 - channel_plus_meter + self.init_plus_count
                else:
                    pulse_difference_value = 0
            else:
                if self.init_plus_count:
                    pulse_difference_value = channel_plus_meter - self.init_plus_count
                    if pulse_difference_value < 0:
                        pulse_difference_value = 0x1000000 - self.init_plus_count + channel_plus_meter
                else:
                    pulse_difference_value = 0
            # if self.name == SOCKET_A:
            #     print("pulse_difference_value = {} init_plus_count = {} channel_plus_meter = {} sign_wait = {}".format(
            #         pulse_difference_value,
            #         self.init_plus_count, channel_plus_meter, sign_wait))
            volumor_count_capacity = (pulse_difference_value * 10 / 11998.21469) / 10

        self.current_plus_count = channel_plus_meter
        if self.charging:
            if not self.init_plus_count:
                self.init_plus_count = channel_plus_meter

        if volumor_count_capacity > 0 and self.charge_current > 10:
            self.volumor_count_capacity = volumor_count_capacity
            self.charged_capacity = self.initialize_capacity + self.volumor_count_capacity
        # print("self.name = {} self.volumor_count_capacity = {} self.initialize_capacity = {} self.volumor_count_capacity ={}"
        #       .format(self.name, self.volumor_count_capacity, self.initialize_capacity, self.volumor_count_capacity))
        # if self.name == SOCKET_A:
        #     print("name = {}\n"
        #           " charging = {} \n"
        #           " charge_current = {}\n"
        #           " charge_power = {}\n"
        #           " charge_voltage = {} \n"
        #           " volumor_count_capacity = {} \n"
        #           " charged_capacity = {} \n"
        #           " channel_power_value = {} \n"
        #           " current_plus_count = {} \n"
        #           .format(self.name, self.charging, self.charge_current, self.charge_power,
        #                   self.charge_voltage, volumor_count_capacity, self.charged_capacity, channel_power_value,
        #                   self.current_plus_count))

        self.charger_check_process()

    def charger_check_process(self):
        """检测是否充电状态"""
        if self.charging:
            if self.charge_current < self.mode.min_power:  # 无负载检测,如果充电电流小于10ma，那么认为是空载  替换成最小功率
                self.no_loader_charging()
            else:
                self.loader_charging()
        else:
            if self.charge_current < DefaultDeviceConfig.MIN_CURRENT:  # 无负载检测,如果充电电流小于10ma，那么认为是空载
                self.socket.led_manager.put(LEDManage.STATUS.NORMAL)
            # 无负载充电情况下
            self.no_loader_count = 0
            self.full_charging_count = 0
            self.socket_insert_status_count = 0
            self.charge_power_exceed_max_power_count = 0
        self.check_voltage()

    def loader_charging(self):
        """有负载充电"""
        self.socket.led_manager.put(LEDManage.STATUS.CHARGING)
        if self.socket_insert_status_count == self.RETRY_THRESHOLD:
            """当设备插入的时候主动检测, 避免检测错误"""
            sys_bus.publish(INTERNAL_TOPIC.CHARGING_UPDATER_STATUS, dict(count=1))
            """避免检测错误"""
        if self.socket_insert_status_count <= self.RETRY_THRESHOLD:
            self.socket_insert_status_count += 1
        if self.charge_power < self.mode.min_power:
            """如果充功率充满, 并且解除恢复charge"""
            self.full_charging_count += 1
            if self.full_charging_count == self.mode.full_wait_time:
                sys_bus.publish(self.stop_charging_topic, {"status": STOP_CHARGING_MODE.FULL_CHARGING})
        else:
            """没充满, 复位相关功能"""
            self.no_loader_count = 0
            self.full_charging_count = 0
            if self.charge_power > self.mode.max_power:
                if self.charge_power_exceed_max_power_count < self.RETRY_THRESHOLD:
                    """避免检测失误, 检测3次是否超过最大功率"""
                    self.charge_power_exceed_max_power_count += 1
                elif self.charge_power_exceed_max_power_count == self.RETRY_THRESHOLD:
                    """检测到了3次, 断开继电器, 结束充电"""
                    self.charge_power_exceed_max_power_count += 1
                    print("================exceed max power===============================")
                    media.play(data=MEDIA_PLAY_DATA.EXCEED_MAX_POWER)
                    sys_bus.publish(self.stop_charging_topic, {"status": STOP_CHARGING_MODE.EXCEED_MAX_POWER})
                else:
                    return
                return
        if self.mode:
            self.mode.charging_check(self.stop_charging_topic, self.charge_power, self.charged_capacity)
        self.charge_power_exceed_max_power_count = 0

    def no_loader_charging(self):
        """无负载充电"""
        self.no_loader_count += 1
        self.full_charging_count = 0

        if not self.no_loader_count % 10:
            """每10秒钟加播放一次"""
            print("self.no_loader_count = {}".format(self.no_loader_count))
            media.play(data=MEDIA_PLAY_DATA.NO_LOADER)
        if self.socket_insert_status_count < self.RETRY_THRESHOLD:
            """判断是否位空载异常, 空载把错误的频次自动归0, 重新计数"""
            self.socket_insert_status_count = 0
        if not self.no_loader_count % self.mode.no_load_wait_time:
            if self.socket_insert_status_count < self.RETRY_THRESHOLD:
                """当空载100s后依然没有负载, 结束充电"""
                print("self.no_loader_count = {}".format(self.mode.no_load_wait_time))
                sys_bus.publish(self.stop_charging_topic, {"status": STOP_CHARGING_MODE.NO_LOADER})
            else:
                """设备检测100S后先检查是否是拔出状态后空载, 如果是认识设备拔出异常断电"""
                sys_bus.publish(self.stop_charging_topic,
                                {"status": STOP_CHARGING_MODE.EXCEPTION_UNPLUG_SOCKET})

    def check_voltage(self):
        pass


# 插座
class Socket(object):
    THRESHOLD = 3

    def __init__(self, name=None, insert=None, relay=None, red=None, green=None):
        self.name = name
        self.socket_num = NUM_SOCKET_A if self.name == SOCKET_A else NUM_SOCKET_B
        self.insert = GPIO(insert, Pin.IN, Pin.PULL_DISABLE, 1)
        self.relay = GPIO(relay, Pin.OUT, Pin.PULL_DISABLE, 0)
        self.led_manager = LEDManage(red, green)
        self.charge = SocketCharge(self.name, self)
        self.subscribe()
        self.ext_int = ExtInt(insert, ExtInt.IRQ_FALLING, ExtInt.PULL_PU, None)
        self.insert_enable()
        self.lock = Lock()
        self.timestamp = None
        self.insert_status = 0
        _thread.start_new_thread(self.charge_handler, ())

    def get_insert_status(self):
        return self.insert_status == self.THRESHOLD

    def init(self, msg):
        msg["restart"] = True
        print("init msg = {}".format(msg))
        self.charge.init(msg)
        self.open_charging(msg)

    def open_charging(self, msg):
        card_id = msg.get("card_id", None)
        self.set_card_order(dict(card_id=card_id))
        msg["socket"] = self.socket_num
        print("name = {} socket = {}".format(self.name, self.socket_num))
        self.charge.open_charging(None, msg)

    def subscribe(self):
        self.charge.subscribe()

    def insert_enable(self):
        self.ext_int.enable()

    def insert_disable(self):
        self.ext_int.disable()

    def charging_status(self):
        """返回对应的状态信息"""
        return (
            math.ceil(self.charge.charged_capacity * 100),
            math.ceil(self.charge.charge_voltage),
            math.ceil(self.charge.charge_current),
            math.ceil(self.charge.device_internal_temperature * 10),
            math.ceil(self.charge.charge_power)
        )

    def charging_load(self):
        if self.timestamp is None:
            return False
        else:
            return self.timestamp

    def charge_handler(self, *args):
        while True:
            read_count = self.ext_int.read_count(1)
            if read_count == [0, 0]:
                if self.insert_status > 0:
                    self.insert_status -= 1
                else:
                    self.insert_status = 0
            else:
                if self.insert_status < self.THRESHOLD:
                    self.insert_status += 1
            if self.insert_status // self.THRESHOLD:
                """无负载接入"""
                self.led_manager.put(LEDManage.STATUS.NO_LOADING)
                if not self.timestamp:
                    self.timestamp = utime.mktime(utime.localtime())
            elif not self.insert_status:
                """设备拔出"""
                if self.charging():
                    self.led_manager.put(LEDManage.STATUS.CHARGING)
                else:
                    self.led_manager.put(-1)
                if self.timestamp:
                    self.timestamp = None
            utime.sleep_ms(300)

    def charging(self):
        return self.charge.charging

    def read(self):
        print("name {} insert read () {}".format(self.name, self.insert.read()))
        print("name {} relay read () {}".format(self.name, self.relay.read()))

    def check(self):
        self.led_manager.check()

    def get_card_order(self):
        return self.charge.card_order

    def set_card_order(self, order):
        self.charge.card_order = order

    def get_stop_charging_topic(self):
        return self.charge.stop_charging_topic

    def check_charging_voltage(self, charge_voltage):
        if self.charging():
            if charge_voltage - self.charge.get_open_charge_voltage() > 30:
                print(
                    "---------------------charge_voltage = {} - get_open_charge_voltage = {} > 30 stop charging------".format(
                        charge_voltage, self.charge.get_open_charge_voltage()
                    ))
                self.stop_charging(STOP_CHARGING_MODE.ABNORMAL_LOAD)

    def stop_charging(self, reason):
        if self.charging():
            sys_bus.publish(self.get_stop_charging_topic(), {"status": reason})

    def upload_charging_status(self, msg):
        self.charge.upload_charging_status(msg)

    def led_normal(self):
        """
            led 正常
        """
        self.led_manager.put(LEDManage.STATUS.NORMAL)


# 插座集合
class ChargeList(object):
    def __init__(self):
        self.data = []
        self.size = 2
        self.lock = Lock()
        self.expire_time = 60

    def add(self, msg):
        with self.lock:
            data = []
            if len(self.data) == self.size:
                """数组交换"""
                if self.data[0]["card_id"] == msg["card_id"]:
                    data.append(self.data[1])
                    self.data[0] = msg
                    self.data[0]["timestamp"] = utime.mktime(utime.localtime())
                    data.append(self.data[0])
                    self.data = data
                else:
                    if self.data[1]["card_id"] == msg["card_id"]:
                        self.data[1] = msg
                        self.data[1]["timestamp"] = utime.mktime(utime.localtime())
            else:
                if len(self.data) == 1:
                    if self.data[0]["card_id"] == msg["card_id"]:
                        self.data[0] = msg
                        self.data[0]["timestamp"] = utime.mktime(utime.localtime())
                        return
                msg["timestamp"] = utime.mktime(utime.localtime())
                self.data.append(msg)

    def check(self, socks):
        with self.lock:
            """
            这里逻辑是检测两个插座是否有在无充电情况下刷卡
            存在对比时间戳  然后进行充电动作
            """
            local_time = utime.mktime(utime.localtime())
            print("check charge_list = {}".format(self.data))
            for d in self.data[:][::-1]:
                if local_time - d['timestamp'] < self.expire_time:
                    for sock in socks:
                        if sock.get_insert_status() and not sock.charging():
                            d['res'][0] = sock.socket_num
                            sock.open_charging(d)
                            self.data.remove(d)
                            return
                else:
                    self.data.remove(d)


# 插座管理
class SocketManage(object):
    """插座状态..."""

    def __init__(self):
        self.socks = []
        self.charging = False
        self.tid = None
        self.timer = osTimer()
        self.lock = Lock()
        sys_bus.subscribe(INTERNAL_TOPIC.CHARGING_UPDATER_STATUS_START, self.upload_start)
        sys_bus.subscribe(INTERNAL_TOPIC.CHARGING_UPDATER_STATUS_STOP, self.upload_stop)
        sys_bus.subscribe(INTERNAL_TOPIC.CHARGING_UPDATER_STATUS, self._upload_status)
        sys_bus.subscribe(INTERNAL_TOPIC.SEARCH_SOCKET_INFO, self._search_socket_info)
        sys_bus.subscribe(INTERNAL_TOPIC.DISCOVER_CARD, self._discover_card)
        sys_bus.subscribe(INTERNAL_TOPIC.CREDIT_CARD_CHARGING, self._card_charging)
        sys_bus.subscribe(INTERNAL_TOPIC.CREDIT_CARD_CHOSE_SOCKET_CHARGING, self._chose_socket_charging)
        sys_bus.subscribe(INTERNAL_TOPIC.SEARCH_CHARGING_SOCKET_STATUS, self._search_charging_socket_status)
        sys_bus.subscribe(INTERNAL_TOPIC.STOP_ALL_CHARGING, self.stop_charging)
        self.history_card = dict()
        self.card_lock = Lock()
        self.chose_socket_lock = Lock()
        self.last_charge_voltage = 0
        self.charge_list = ChargeList()
        self.vol_than_250_upload_stat = False
        self.vol_than_260_upload_stat = False
        self.vol_than_270_upload_stat = False
        self.check_charge_timer = osTimer()
        self.check_charge_timer.start(2000, 1, self.check_socket)

    def publish(self, vp):
        sys_bus.publish(SOCKET_A, vp.a)
        sys_bus.publish(SOCKET_B, vp.b)
        self.check_voltage(vp.device_voltage_data)

    def check_voltage(self, voltage):
        """
        检查电压
        1. 判断电压是否  大于250V大于 上报告警
            2. 判断是否大于开电电压的 30 V 是断电
            3. 判断是否大于260V持续报警, 每过1格都需要告警
            4. 判断大于265V时 会主动断开
        2. 判断电压小于250V时候  我们需要检测然后取消告警内容
        """
        charge_voltage = math.ceil(voltage / 100)
        print("---------------------current voltage > {} V  -----------------------".format(
            charge_voltage))
        for sock in self.socks:
            sock.check_charging_voltage(charge_voltage)
        if charge_voltage > 250:
            if not self.vol_than_250_upload_stat:
                print("---------------------voltage > 250 V and send error reporter-----------------------")
                self.vol_than_250_upload_stat = True
                self._upload_status()
            if charge_voltage > 260:
                if not self.vol_than_260_upload_stat:
                    print("--------------------voltage > 260 --------------charging updater status------------")
                    self.vol_than_260_upload_stat = True
                    res = (0xff, FailureReportCode.ERROR_OVER_VOLTAGE, 1, charge_voltage)
                    sys_bus.publish(INTERNAL_TOPIC.FAILURE_REPORT, dict(res=res))
                print(
                    "charge_voltage {} - self.last_charge_voltage {} ".format(charge_voltage, self.last_charge_voltage))
                if charge_voltage - self.last_charge_voltage >= 1:
                    """电压峰值超标"""
                    self._upload_status()
                if charge_voltage > 270:
                    print("---------------------charge voltage > 270 V ----------------- stop all socket -----")
                    # 推送告警
                    for sock in self.socks:
                        sock.stop_charging(STOP_CHARGING_MODE.ABNORMAL_LOAD)
        else:
            if self.vol_than_250_upload_stat or self.vol_than_260_upload_stat:
                print("-----------------------recover - and - voltage = {} < 250 V -----------".format(voltage))
                res = (0xff, FailureReportCode.ERROR_OVER_VOLTAGE, 0, charge_voltage)
                if self.vol_than_260_upload_stat:
                    self.vol_than_260_upload_stat = False
                    sys_bus.publish(INTERNAL_TOPIC.FAILURE_REPORT, dict(res=res))
                self.vol_than_250_upload_stat = False
        self.last_charge_voltage = charge_voltage

    def stop_charging(self, topic, msg):
        """
        msg 为原因 为STOP_CHARGING_MODE 枚举对象中的枚举
        """
        for sock in self.socks:
            sock.stop_charging(msg)
            utime.sleep(3)
            sock.led_normal()

    def _search_charging_socket_status(self, topic, msg):
        sock_id = msg["socket_id"]
        for sock in self.socks:
            if sock_id == sock.socket_num:
                sock.upload_charging_status(msg)

    def init(self, conf_s):
        for sock in self.socks:
            if conf_s.include(sock.name):
                sock.init(conf_s.get(sock.name))

    def _discover_card(self, topic, msg):
        uid = msg["uid"]
        timestamp = msg["timestamp"]
        print("msg = {}".format(msg))
        if uid in self.history_card:
            if timestamp - self.history_card[uid]["timestamp"] > 2:
                """不是抖动行为, 叠加充电状态"""
                sys_bus.publish(INTERNAL_TOPIC.CREDIT_CARD_CHARGING, msg)
            """行为更新"""
            self.history_card[uid] = msg
        else:
            """证明是第一次刷卡,汇报刷卡状态"""
            self.history_card[uid] = msg
            # self._card_charging(msg)
            sys_bus.publish(INTERNAL_TOPIC.CREDIT_CARD_CHARGING, msg)

    def _chose_socket_charging(self, topic, msg):
        print("msg ========================  {}".format(msg))
        res = msg["res"]
        """选择插头充电这里要过滤异常情况"""
        stat = msg["res"][0]
        if stat == SWIPE_CARD_RETURN_STATUS.SUCCESS:
            """成功直接给过滤"""
            pass
        elif stat == SWIPE_CARD_RETURN_STATUS.EXCEPTION:
            """卡异常"""
            media.play(data=MEDIA_PLAY_DATA.CARD_EXCEPTION)
            return
        elif stat == SWIPE_CARD_RETURN_STATUS.NOT_SUFFICIENT_FUNDS:
            """"""
            media.play(data=MEDIA_PLAY_DATA.NOT_SUFFICIENT_FUNDS.format(math.ceil(res[3] / 100)))
            return
        elif stat == SWIPE_CARD_RETURN_STATUS.CARD_NOT_INTO_SYSTEM:
            media.play(data=MEDIA_PLAY_DATA.CARD_NOT_INTO_SYSTEM)
            return
        elif stat == SWIPE_CARD_RETURN_STATUS.CARD_STATUS_ILLEGALITY:
            media.play(data=MEDIA_PLAY_DATA.CARD_EXCEPTION)
            return
        elif stat == SWIPE_CARD_RETURN_STATUS.SWIPE_CARD_OFTEN:
            media.play(data=MEDIA_PLAY_DATA.SWIPE_CARD_OFTEN)
            return
        elif stat == SWIPE_CARD_RETURN_STATUS.EXIST_NO_FINISH_ORDER:
            media.play(data=MEDIA_PLAY_DATA.EXIST_NO_FINISH_ORDER)
            return
        else:
            media.play(data=MEDIA_PLAY_DATA.EXCEED_ROUND)
            return

        charging_load = []
        no_charging_load = []
        msg["mode"] = OPEN_SOCKET_MODE.CARD
        exist_charging_device = False
        # 这里要改变res 用来适配我们原先的  扫码充电的模式, 下面需要
        msg["res"] = [NUM_SOCKET_A, res[4], res[5], res[6], res[7], res[1], res[8], res[9], res[10]]
        msg["card_balance"] = res[3]

        print("_chose_socket_charging = {}".format(msg))
        for k in res[11:]:
            """将其他参数进行追加"""
            msg["res"].append(k)
        for sock in self.socks:
            if sock.charging():
                card_order = sock.get_card_order()
                print("card_order = {}".format(card_order))
                exist_charging_device = True
                if card_order:
                    """如果刷卡订单重复情况下   重新计时"""
                    if tuple(card_order["card_id"]) == tuple(msg.get("card_id")):
                        msg["res"][0] = sock.socket_num
                        sock.open_charging(msg)
                        return
            else:
                timestamp = sock.charging_load()
                if timestamp:
                    charging_load.append(dict(sock=sock, timestamp=timestamp))
                else:
                    no_charging_load.append(dict(sock=sock))
        if len(no_charging_load) == SOCKET_COUNT or (
                exist_charging_device and len(no_charging_load) == SOCKET_COUNT - 1):
            """2个都没进入负载"""
            media.play(data=MEDIA_PLAY_DATA.ALL_SOCKET_NO_LOADER.format(math.ceil(msg["card_balance"] / 100)))
            self.charge_list.add(msg)

        if len(charging_load) == SOCKET_COUNT - 1:
            """1个进入负载"""
            sock = charging_load[-1]['sock']
            """赋值充电插座号"""
            msg["res"][0] = sock.socket_num
            sock.open_charging(msg)
        elif len(charging_load) == SOCKET_COUNT:
            """2个都进入负载"""
            sock = sorted(charging_load, key=lambda i: i['timestamp'], reverse=True)[0]['sock']
            # 主动调用
            msg["res"][0] = sock.socket_num
            sock.open_charging(msg)

    def check_socket(self, *args, **kwargs):
        """关注刷卡时间默认过期时间100s"""
        print("check socket ---------------------------------")
        self.charge_list.check(self.socks)

    @staticmethod
    def _card_charging(topic, msg):
        sys_bus.publish(INTERNAL_TOPIC.CREDIT_CARD_CHARGE_REQUEST, msg)

    def _search_socket_info(self, topic, msg):
        stats = []
        for sock in self.socks:
            """充电中返回1, 未充电返回0"""
            stats.append(1) if sock.charging() else stats.append(0)
        msg["res"] = stats
        sys_bus.publish(INTERNAL_TOPIC.SEARCH_SOCKET_STATUS, msg)

    def add(self, sock):
        self.socks.append(sock)

    def read(self):
        for sock in self.socks:
            sock.read()

    def check(self):
        for sock in self.socks:
            _thread.start_new_thread(sock.check, ())

    def upload_start(self, topic, msg):
        print("upload start topic = {} msg = {}".format(topic, msg))
        # 全局get_data
        with self.lock:
            if not self.charging:
                self.charging = True
                print("===================upload status ================")
                self._upload_status()
                self.timer.start(DefaultDeviceConfig.CHARGING_DATA_TELL_CHILL_TIME * 1000, 1, self._upload_status)

    def _upload_status(self, topic=None, msg=None):
        count = 1
        if msg:
            count = msg.get("count", 1)
        for i in range(count):
            socket_a_data = None
            socket_b_data = None
            for sock in self.socks:
                if sock.name == SOCKET_A:
                    socket_a_data = sock.charging_status()
                else:
                    socket_b_data = sock.charging_status()
            csq = net.csqQueryPoll()
            args1 = (
                csq, SOCKET_COUNT, socket_a_data[0], socket_b_data[0], socket_a_data[1],
                socket_b_data[1],
                socket_a_data[2], socket_b_data[2], socket_a_data[3], socket_b_data[3], socket_a_data[3], 9
            )
            # 同时上报充电功率和充电状态
            sys_bus.publish(INTERNAL_TOPIC.UPLOAD_CHARGING_STATUS, args1)
            args2 = (csq, socket_a_data[4], socket_b_data[4])
            sys_bus.publish(INTERNAL_TOPIC.UPLOAD_POWER_STATUS, args2)

    def upload_stop(self, topic, msg):
        """情况判断充电情况下  如果两个设备都不在充电需要, 结束定时器,  如果都在充电"""
        with self.lock:
            if self.charging:
                for sock in self.socks:
                    if not sock.charging():
                        continue
                    else:
                        print("sock name {} exist {} keep charging ~~~".format(sock.name, sock.charging()))
                        return
                self.charging = False
                self.timer.stop()


# 服务
class Server(object):

    def __init__(self, ip, port, keep_alive=120):
        self.ip = ip
        self.port = port
        self.sockaddr = None
        self.sock = None
        self.max_size = 4096
        self.__state = False
        self.__keep_alive = keep_alive
        self.timer = osTimer()
        self.lock = Lock()
        self.__id = None
        self.count = 0

    def set_keepalive(self, keep_alive):
        if isinstance(keep_alive, int):
            self.__keep_alive = keep_alive
            return

    def init(self):
        print("self.ip = {}  self.port = {}".format(self.ip, self.port))
        self.sock = usocket.socket(usocket.AF_INET, usocket.SOCK_STREAM)
        self.sockaddr = usocket.getaddrinfo(self.ip, self.port)[0][-1]

    def connect(self):
        self.sock.connect(self.sockaddr)
        self.sock.setblocking(True)

    def send(self, msg):
        with self.lock:
            try:
                self.sock.send(msg)
            except Exception as e:
                self.count += 1
                print("error count = {} e".format(self.count, e))
                if not DefaultDeviceState.DEVICE_UN_ONLINE:
                    if self.count > 3:
                        self.count = 0
                        sys_bus.publish(INTERNAL_TOPIC.DEVICE_RECONNECT, None)
            else:
                self.count = 0

    def recv(self):
        return self.sock.recv(self.max_size)

    def wait(self):
        pass

    def stop(self):
        self.sock.close()
        self.timer.stop()
        self.__state = False

    def start(self):
        self.init()
        self.connect()
        self.__state = True
        self.__id = _thread.start_new_thread(self.wait, ())
        self.timer.start(self.__keep_alive * 1000, 1, self.publish_heartbeat)
        DefaultDeviceState.set_connect_server_error(False)

    def restart(self):
        self.stop()
        self.start()

    def publish_heartbeat(self, *args):
        """ TODO 留给子类去实现, 或者自己填充"""
        pass

    def timer_start(self):
        self.timer.start(self.__keep_alive * 1000, 1, self.publish_heartbeat)

    def timer_restart(self):
        self.timer.stop()
        self.timer_start()

    def status(self):
        return self.__state


class CDZServer(Server):
    def __init__(self, ip, port):
        super(CDZServer, self).__init__(ip, port)
        self.__keep_alive = DefaultDeviceConfig.HEART_TIME
        # TODO 待写， 设置选项

    def wait(self):
        while True:
            try:
                data = self.recv()
            except Exception as e:
                print("lccdz server error = {}".format(e))
                DefaultDeviceState.set_connect_server_error(True)
                break
            else:
                if data:
                    sys_bus.publish(INTERNAL_TOPIC.PROTOCOL_ANALYSIS, {"data": data})

    def publish_heartbeat(self, *args):
        sys_bus.publish(INTERNAL_TOPIC.DEVICE_HEART, None)


class DeviceAccess(object):

    def __init__(self, uri=DEVICE_ACCESS_URI, version="v1", company="LVCC", company_id="TEST_COMPANYID",
                 dev_type=DEV_TYPE, protocol_version="0101H"):
        self.uri = uri
        self.version = version
        self.device_id = modem.getDevImei()
        self.url = self.uri + self.version
        self.company = company
        self.company_id = company_id
        self.dev_type = dev_type
        self.protocol_version = protocol_version
        self.domain_list = []
        self.error_count = 0

    def __get_server(self):
        if DefaultDeviceState.CONNECT_SERVER_ERROR:
            self.error_count += 1
            if self.error_count == 5:
                """当失败次数达到五次则选择重连服务器"""
                self.error_count = 0
                self.device_direct_connect()
                return
        suffix = "/authentic?id={}".format(self.device_id)
        try:
            resp = request.get(self.url + suffix)
            data = resp.json()
            print(self.url + suffix)
            print("data = {}".format(data))
            ticket = data["ticket"]
        except Exception as e:
            print("get server = {}".format(e))
            return
        else:
            print("ticket = {}".format(ticket))
            self.__get_server_info(ticket)

    def device_direct_connect(self):
        self.domain_list = [{"domain": "lvccnet.lvcchong.com", "port": 9004}]
        print("device direct connect ---------- {}".format(self.domain_list))

    def __get_server_info(self, ticket):
        md5_token = ubinascii.hexlify(uhashlib.md5(self.device_id + "{" + str(self.device_id) + "}" + ticket).digest())
        suffix = "/register?id={}&token={}&company={}&devType={}&protocol={}".format(
            self.device_id, md5_token, self.company_id, self.dev_type, self.protocol_version
        )
        try:
            resp = request.get(self.url + suffix)
            data = resp.json()
        except Exception as e:
            print("get server info = {}".format(e))
            return
        else:
            self.domain_list = sorted(data['domainList'], key=lambda i: i['weight'], reverse=True)
            print("RESP = {}".format(resp.json()))

    def request(self):
        self.__get_server()


"""=======================================================传输协议层================================================"""


class IDGenerator(object):
    lock = Lock()
    id = 0

    @classmethod
    def key(cls):
        with cls.lock:
            return cls._gen()

    @classmethod
    def _gen(cls):
        cls.id += 1
        if security_msg_map.exist(cls.id):
            return cls._gen()
        if cls.id < 0xffff:
            return cls.id
        else:
            return 0


class ReqProtocol(object):
    def __init__(self):
        self.header = bytes([0x7e, 0x5d, 0x7d, 0x7f])
        self.length = bytes([0x00, 0x00])
        self.protocol_version = CDZ_VERSION.PROTOCOL
        self.socket_version = CDZ_VERSION.SOCKET
        self.version = CDZ_VERSION.MASTER
        self.tag = 0x00  # tag 0x00 , 0x01   0x10, 0x11, 0x12
        self.device_id_length = len(imei)
        self.device_id = imei.encode()
        self.phone_num = phone_num
        self.ic_cid = ic_cid
        self.msg_id = 0x0001
        self.order = 0x0000
        # 2 个插座
        self.socket_count = 0x02
        # 设备类型64
        self.device_type = 0x44
        self.content = []
        self.check_sum = 0x00

    def gen_length(self, content):
        self.msg_id = IDGenerator.key()
        self.length = 9 + self.device_id_length + len(content)

    def pack(self, tag, msg_id, content):
        pack_format = "4sHHBB" + str(self.device_id_length) + "sHH" + str(len(content)) + "s"
        args = (self.header, self.length, self.protocol_version,
                tag, self.device_id_length, self.device_id,
                msg_id, self.order, content)
        return self.pack_l(pack_format, *args)

    def unpack(self, data, msg_id):
        check_sum = self.gen_check_sum(data[:-1])
        if check_sum == data[-1]:
            self.length, = self.unpack_l('H', data[4:6])
            self.resp_content_process(data[10 + self.device_id_length + 4:-1], msg_id)
            return True
        return False

    def resp_content_process(self, data, msg_id):
        """data 数据源, msg_id唯一上下行必须保持一直类似于rrpc"""
        pass

    @staticmethod
    def gen_check_sum(data, idx=6):
        checksum = 0
        for i in range(idx, len(data)):
            checksum ^= data[i]
        return checksum

    def gen(self, tag=None, msg_id=None, content=None):
        if not content:
            content = self.content
        self.gen_length(content)
        if not msg_id:
            msg_id = self.msg_id
        if not tag:
            tag = self.tag
        data = self.pack(tag, msg_id, content)
        return data + self.pack_l('B', self.gen_check_sum(data))

    def response(self, topic, msg):
        data = msg.get("data")
        msg_id = msg.get("msg_id")
        self.unpack(data, msg_id)

    @staticmethod
    def pack_l(fmt, *args):
        return ustruct.pack(">" + fmt, *args)

    @staticmethod
    def unpack_l(fmt, *args):
        return ustruct.unpack(">" + fmt, *args)

    def protocol_analysis(self, topic, msg):
        (_, _, _, _, _, _, msg_id, tag) = self.unpack_l("4sHHBB{}sHH".format(self.device_id_length), msg.get('data'))
        msg["msg_id"] = msg_id
        sys_bus.publish(tag, msg)


class CDZREQReqProtocol(ReqProtocol):
    """主动请求"""

    def __init__(self):
        super(CDZREQReqProtocol, self).__init__()
        self.app = None
        self.topic = None

    def pre_send(self, msg, data):
        pass

    def handler(self, msg):
        if not msg:
            data = self.gen()
        else:
            data = self.gen(tag=msg.get("tag", None), msg_id=msg.get("msg_id", None), content=msg.get("content", None))
        self.pre_send(msg, data)
        self.app.send(data)

    @staticmethod
    def pack_handler_data(tag=None, msg_id=None, content=None):
        return dict(tag=tag, msg_id=msg_id, content=content)


class DeviceOrderActiveRequest(CDZREQReqProtocol):
    """        设备主动请求        需要获取响应数据    """

    def __init__(self):
        super(DeviceOrderActiveRequest, self).__init__()
        self.tag = TAGORDER.DEVICE_REQ


class GatewayIssuedOrder(CDZREQReqProtocol):
    """    网关主动请求    """

    def __init__(self):
        super(GatewayIssuedOrder, self).__init__()
        self.tag = TAGORDER.GATWAY_REQ


class ProtoDeviceRegister(DeviceOrderActiveRequest):

    def __init__(self):
        super(ProtoDeviceRegister, self).__init__()
        self.topic = INTERNAL_TOPIC.DEVICE_REGISTER
        self.order = ORDER.DEVICE_REGISTER
        self.csq = net.csqQueryPoll()
        self.device_check = 0x01
        self.content = self.pack_l("BHHHBBB", self.csq, self.version, self.socket_version, self.protocol_version,
                                   self.socket_count,
                                   self.device_type, self.device_check)

    def resp_content_process(self, data, msg_id):
        DefaultDeviceState.set_register(True)
        # 接入
        print("ProtoDeviceRegister data = {}".format(data))
        data = self.unpack_l("I", data)
        print("ProtoDeviceRegister === {}".format(data))
        sys_bus.publish(INTERNAL_TOPIC.SUCCESS_ONLINE, None)


class ProtoHeartBeat(CDZREQReqProtocol):
    """心跳包"""
    ERROR_HEART_ID_LIST = []

    def __init__(self):
        super(ProtoHeartBeat, self).__init__()
        self.tag = TAGORDER.DEVICE_HEART
        self.order = ORDER.DEVICE_HEART
        # 心跳指令
        self.topic = INTERNAL_TOPIC.DEVICE_HEART

    def handler(self, msg):
        self.content = self.pack_l("I", utime.mktime(utime.localtime()))
        super(ProtoHeartBeat, self).handler(msg)
        ProtoHeartBeat.ERROR_HEART_ID_LIST.append(self.msg_id)
        print("ProtoHeartBeat handler msg = {} self = {}".format(ProtoHeartBeat.ERROR_HEART_ID_LIST, self))
        if len(ProtoHeartBeat.ERROR_HEART_ID_LIST) > 3 and DefaultDeviceState.CONNECT_SERVER_ERROR:
            """重新连接服务器"""
            print("publish device_reconnect")
            sys_bus.publish(INTERNAL_TOPIC.DEVICE_RECONNECT, None)

    def resp_content_process(self, data, msg_id):
        data = self.unpack_l("I", data)
        ProtoHeartBeat.ERROR_HEART_ID_LIST = []
        print(
            "ProtoHeartBeat === {} self === {} error_heart = {}".format(data, self, ProtoHeartBeat.ERROR_HEART_ID_LIST))


class SearchDeviceInfo(GatewayIssuedOrder):
    """查询设备基本信息"""

    def __init__(self):
        super(SearchDeviceInfo, self).__init__()
        self.topic = INTERNAL_TOPIC.SEARCH_DEVICE_INFO
        self.order = ORDER.SEARCH_DEVICE_INFO
        self.result = RESULT.OK
        self.temperature = 12
        self.content = None

    def handler(self, msg):
        print("msg = {}".format(msg))
        tag = TAGORDER.DEVICE_RESP
        msg_id = msg.get("msg_id")
        content = self.pack_l("BHHHBH", RESULT.OK, self.version, self.socket_version, self.protocol_version,
                              self.socket_count, math.ceil(msg.get("res")))
        super(SearchDeviceInfo, self).handler(self.pack_handler_data(tag, msg_id, content))

    def resp_content_process(self, data, msg_id):
        sys_bus.publish(INTERNAL_TOPIC.SEARCH_DEVICE_TEMPERATURE, dict(msg_id=msg_id))


class SearchCommunicationModuleInfo(GatewayIssuedOrder):
    """查询通讯模块信息"""

    def __init__(self):
        super(SearchCommunicationModuleInfo, self).__init__()
        self.topic = INTERNAL_TOPIC.SEARCH_COMMUNICATION_MODULE_INFO
        self.order = ORDER.SEARCH_COMMUNICATION_MODULE_INFO
        self.result = RESULT.OK
        self.content = None

    def resp_content_process(self, data, msg_id):
        tag = TAGORDER.DEVICE_RESP
        signal = net.csqQueryPoll()
        print("result = {} device_id = {} iccid = {} phone_num = {} signal = {}".format(self.result, self.device_id,
                                                                                        sim.getIccid(), self.phone_num,
                                                                                        signal))
        content = self.pack_l("B15s20s15sB", self.result, self.device_id, sim.getIccid(), sim.getPhoneNumber(), signal)
        self.handler(self.pack_handler_data(tag, msg_id, content))


class SearchSocketStatus(GatewayIssuedOrder):
    """查询插座信息"""

    def __init__(self):
        super(SearchSocketStatus, self).__init__()
        self.topic = INTERNAL_TOPIC.SEARCH_SOCKET_STATUS
        self.order = ORDER.SEARCH_SOCKET_STATUS
        self.result = RESULT.OK
        self.content = None

    def handler(self, msg):
        print("SearchSocketStatus msg = {}".format(msg))
        tag = TAGORDER.DEVICE_RESP
        msg_id = msg.get("msg_id")
        content = self.pack_l("BBB", self.result, *msg.get("res"))
        super(SearchSocketStatus, self).handler(self.pack_handler_data(tag, msg_id, content))

    def resp_content_process(self, data, msg_id):
        sys_bus.publish(INTERNAL_TOPIC.SEARCH_SOCKET_INFO, dict(msg_id=msg_id))


class SearchLBSInfo(GatewayIssuedOrder):
    """查询LBS信息"""

    def __init__(self):
        super(SearchLBSInfo, self).__init__()
        self.topic = INTERNAL_TOPIC.SEARCH_LBS_INFO
        self.order = ORDER.SEARCH_LBS_INFO
        self.result = RESULT.OK
        self.content = None

    def resp_content_process(self, data, msg_id):
        print("SearchLBSInfo data= {}".format(data))
        tag = TAGORDER.DEVICE_RESP
        signal = net.csqQueryPoll()
        cell_info = net.getCellInfo()[2][0]
        content = self.pack_l("BBHHII", self.result, signal, cell_info[2], cell_info[3], cell_info[5], cell_info[1])
        self.handler(self.pack_handler_data(tag, msg_id, content))
        print("SearchLBSInfo success {} {} {} {}".format(cell_info[2], cell_info[3], cell_info[5], cell_info[1]))


class SearchChargingState(GatewayIssuedOrder):

    def __init__(self):
        super(SearchChargingState, self).__init__()
        self.topic = INTERNAL_TOPIC.SEARCH_CHARGING_STATE
        self.order = ORDER.SEARCH_CHARGING_STATE
        self.result = RESULT.OK
        self.content = None

    def handler(self, msg):
        tag = TAGORDER.DEVICE_RESP
        print("SearchChargingState handler msg = {}".format(msg))
        msg_id = msg.get("msg_id")
        content = self.pack_l("BBHHHB20sH", self.result, *msg.get("res"))
        super(SearchChargingState, self).handler(self.pack_handler_data(tag, msg_id, content))

    def resp_content_process(self, data, msg_id):
        print("SearchChargingState data= {} data[0] = {}".format(data, data[0]))
        sys_bus.publish(INTERNAL_TOPIC.SEARCH_CHARGING_SOCKET_STATUS, dict(msg_id=msg_id, socket_id=data[0]))


class SearchMediaInfo(GatewayIssuedOrder):
    """操作媒体信息"""

    def __init__(self):
        super(SearchMediaInfo, self).__init__()
        self.topic = INTERNAL_TOPIC.SEARCH_MEDIA_INFO
        self.order = ORDER.SEARCH_MEDIA_INFO
        self.result = RESULT.OK
        self.content = None

    def handler(self, msg):
        tag = TAGORDER.DEVICE_RESP
        print("Search-Media-Info handler msg = {}".format(msg))
        msg_id = msg.get("msg_id")
        content = self.pack_l("BBBB", self.result, *msg.get("res"))
        super(SearchMediaInfo, self).handler(self.pack_handler_data(tag, msg_id, content))

    def resp_content_process(self, data, msg_id):
        print("Search-Media-Info data= {}".format(data))
        sys_bus.publish(INTERNAL_TOPIC.GET_MEDIA_CONF, dict(msg_id=msg_id))


class SetMediaInfo(GatewayIssuedOrder):
    def __init__(self):
        super(SetMediaInfo, self).__init__()
        self.topic = INTERNAL_TOPIC.SET_MEDIA_INFO
        self.order = ORDER.SET_MEDIA_INFO
        self.result = RESULT.OK
        self.content = None

    def handler(self, msg):
        tag = TAGORDER.DEVICE_RESP
        msg_id = msg.get("msg_id")
        print("Set-Media-Info handler msg = {}".format(msg))
        content = self.pack_l("B", self.result)
        super(SetMediaInfo, self).handler(self.pack_handler_data(tag, msg_id, content))

    def resp_content_process(self, data, msg_id):
        print("Set-Media-Info data = {}".format(data))
        res = self.unpack_l("BBB", data)
        print("Set-Media-Info data= {} res = {}".format(data, res))
        sys_bus.publish(INTERNAL_TOPIC.SET_MEDIA_CONF, dict(msg_id=msg_id, res=res))


class OpenCharging(GatewayIssuedOrder):
    """手机开启充电"""

    def __init__(self):
        super(OpenCharging, self).__init__()
        self.topic = INTERNAL_TOPIC.PHONE_OPEN_CHARGING
        self.order = ORDER.PHONE_OPEN_CHARGING
        self.result = RESULT.OK
        self.content = None

    def handler(self, msg):
        tag = TAGORDER.DEVICE_RESP
        content = self.pack_l("BBH", *msg.get("res"))
        msg_id = msg.get("msg_id")
        print("open charging tag = {} content = {} msg_id = {}".format(tag, content, msg_id))
        super(OpenCharging, self).handler(self.pack_handler_data(tag, msg_id, content))

    def resp_content_process(self, data, msg_id):
        print("len(data) = {}".format(len(data)))
        print("OpenCharging data = {}".format(data))
        fmt_prefix = "BHHHHH20sBBB"
        try:
            res = self.unpack_l(fmt_prefix + "HHHHHHHHHH", data)
        except Exception as e:
            print("not truth power meter {}".format(e))
            res = self.unpack_l(fmt_prefix, data)
        # 由于云端给的区分1/2插座是通过 0/1去区分的  我们这里发布的时候需要转下
        print("res = {}".format(res))
        sys_bus.publish(INTERNAL_TOPIC.B_CHARGING if res[0] else INTERNAL_TOPIC.A_CHARGING,
                        {"res": res, "msg_id": msg_id, "socket": res[0]})


class UploadCreditCardOrder(DeviceOrderActiveRequest):
    def __init__(self):
        super(UploadCreditCardOrder, self).__init__()
        self.topic = INTERNAL_TOPIC.UPLOAD_CREDIT_CARD_ORDER
        self.order = ORDER.UPLOAD_CREDIT_CARD_ORDER
        self.csq = net.csqQueryPoll()
        self.content = None

    def resp_content_process(self, data, msg_id):
        print("Upload-Credit-Card-Order resp_content_process data = {} msg_id = {}".format(data, msg_id))
        data = self.unpack_l("B", data)
        print("Upload-Credit-Card-Order Status === {}".format(data))

    def handler(self, msg):
        print("Upload-Credit-Card-Orders handler -> {}".format(msg))
        content = self.pack_l("BH20sBBBBB", *msg["res"])
        print("Upload-Credit-Card-Order content = {}".format(content))
        super(UploadCreditCardOrder, self).handler(self.pack_handler_data(content=content))


class DeviceRestart(GatewayIssuedOrder):
    """设备重启"""

    def __init__(self):
        super(DeviceRestart, self).__init__()
        self.topic = INTERNAL_TOPIC.DEVICE_RESTART
        self.order = ORDER.DEVICE_RESTART
        self.result = RESULT.OK
        self.content = None

    def resp_content_process(self, data, msg_id):
        """设备重启"""
        print("Device-Restart handler -> data = {} msg_id = {}".format(data, msg_id))
        Power.powerRestart()


class StopCharging(GatewayIssuedOrder):
    """停止充电"""

    def __init__(self):
        super(StopCharging, self).__init__()
        self.topic = INTERNAL_TOPIC.STOP_CHARGING
        self.order = ORDER.STOP_CHARGING
        self.result = RESULT.OK
        self.content = None

    def handler(self, msg):
        tag = TAGORDER.DEVICE_RESP
        print("msg = {}".format(msg))
        content = self.pack_l("BBHB20s", *msg.get("res"))
        msg_id = msg.get("msg_id")
        print("open charging tag = {} content = {} msg_id = {}".format(tag, content, msg_id))
        super(StopCharging, self).handler(self.pack_handler_data(tag, msg_id, content))

    def resp_content_process(self, data, msg_id):
        """停止某个端口充电"""
        print("StopCharging data = {} msg_id = {}".format(data, msg_id))
        # 由于云端给的区分1/2插座是通过 0/1去区分的  我们这里发布的时候需要转下
        msg = {"res": data[0], "msg_id": msg_id, "socket": data,
               "status": STOP_CHARGING_MODE.FINISH_BY_USER}
        topic = INTERNAL_TOPIC.B_STOP_CHARGING if data[0] else INTERNAL_TOPIC.A_STOP_CHARGING
        print("resp topic = {} msg = {}".format(topic, msg))
        sys_bus.publish(topic, msg)


class UploadChargingStatus(DeviceOrderActiveRequest):
    def __init__(self):
        super(DeviceOrderActiveRequest, self).__init__()
        self.topic = INTERNAL_TOPIC.UPLOAD_CHARGING_STATUS
        self.order = ORDER.UPLOAD_CHARGING_STATUS
        self.csq = net.csqQueryPoll()
        self.content = None

    def resp_content_process(self, data, msg_id):
        print("upload charging status resp_content_process data = {} msg_id = {}".format(data, msg_id))
        data = self.unpack_l("B", data)
        print("Upload Chargin gStatus === {}".format(data))

    def handler(self, msg):
        print("upload charging status handler -> {}".format(msg))
        content = self.pack_l("BBHHHHHHHHH", *msg)
        print("UploadChargingStatus content = {}".format(content))
        super(UploadChargingStatus, self).handler(self.pack_handler_data(content=content))


class UploadPowerStatus(DeviceOrderActiveRequest):
    """充电状态定时上报"""

    def __init__(self):
        super(UploadPowerStatus, self).__init__()
        self.topic = INTERNAL_TOPIC.UPLOAD_POWER_STATUS
        self.order = ORDER.UPLOAD_POWER_STATUS
        self.csq = net.csqQueryPoll()
        self.content = None

    def resp_content_process(self, data, msg_id):
        print("Upload Power Status resp_content_process data = {} msg_id = {}".format(data, msg_id))
        data = self.unpack_l("B", data)
        print("Upload Power Status === {}".format(data))

    def handler(self, msg):
        print("Upload Power Status handler -> {}".format(msg))
        content = self.pack_l("BHH", *msg)
        print("Upload Power Status content = {}".format(content))
        super(UploadPowerStatus, self).handler(self.pack_handler_data(content=content))


class ChargingEnd(DeviceOrderActiveRequest):
    """充电结束"""

    def __init__(self):
        super(ChargingEnd, self).__init__()
        self.topic = INTERNAL_TOPIC.CHARGING_END
        self.order = ORDER.CHARGING_END
        self.csq = net.csqQueryPoll()
        self.content = None

    def resp_content_process(self, data, msg_id):
        print("charging end resp_content_process data = {} msg_id = {}".format(data, msg_id))
        data = self.unpack_l("B", data)
        print("charging end === {}".format(data))
        if security_msg_map.exist(msg_id):
            security_msg_map.delete(msg_id)

    def pre_send(self, msg, data):
        print("pre_send == {}  msg_id = {}".format(data, self.msg_id))
        security_msg_map.set(self.msg_id,
                             {"card_id": msg["content"], "data": data, "timestamp": utime.mktime(utime.localtime())})

    def handler(self, msg):
        print("charging end handler -> {}".format(msg))
        content = self.pack_l("BB20sHHI", *msg)
        print("charging end content = {}".format(content))
        super(ChargingEnd, self).handler(self.pack_handler_data(content=content))


class SearchDefaultConfigInfo(GatewayIssuedOrder):
    """查询设备配置信息"""

    def __init__(self):
        super(SearchDefaultConfigInfo, self).__init__()
        self.topic = INTERNAL_TOPIC.SEARCH_DEFAULT_CONFIG_INFO
        self.order = ORDER.SEARCH_DEFAULT_CONFIG_INFO
        self.content = None

    def resp_content_process(self, data, msg_id):
        tag = TAGORDER.DEVICE_RESP
        content = self.pack_l(
            "BHHHHBHBBB", RESULT.OK,
            DefaultDeviceConfig.MIN_POWER, DefaultDeviceConfig.MAX_POWER,
            DefaultDeviceConfig.NO_LOAD_WAIT_TIME, DefaultDeviceConfig.FULL_WAIT_TIME,
            DefaultDeviceConfig.HEART_TIME, DefaultDeviceConfig.CHARGING_DATA_TELL_CHILL_TIME,
            DefaultDeviceConfig.WORKING_VOLTAGE, DefaultDeviceConfig.TEMPERATURE_ALARM,
            DefaultDeviceConfig.SMOKE_ALARM
        )
        print("Search-Default-Config-Info content = {}".format(content))
        self.handler(self.pack_handler_data(tag, msg_id, content))


class SetDefaultConfigInfo(GatewayIssuedOrder):
    """设置默认配置信息"""

    def __init__(self):
        super(SetDefaultConfigInfo, self).__init__()
        self.topic = INTERNAL_TOPIC.SET_DEFAULT_CONFIG_INFO
        self.order = ORDER.SET_DEFAULT_CONFIG_INFO
        self.content = None

    def resp_content_process(self, data, msg_id):
        tag = TAGORDER.DEVICE_RESP
        print("data = {} len(data) = {}".format(data, len(data)))
        res = self.unpack_l(
            "HHHHBHBB", data
        )
        print("set-default-config-info res = {}".format(res))
        DefaultDeviceConfig.set_config(dict(res=res))
        content = self.pack_l("B", RESULT.OK)
        self.handler(self.pack_handler_data(tag, msg_id, content))


class CreditCardChargeRequest(DeviceOrderActiveRequest):
    """刷卡充电。。。"""

    def __init__(self):
        super(CreditCardChargeRequest, self).__init__()
        self.topic = INTERNAL_TOPIC.CREDIT_CARD_CHARGE_REQUEST
        self.order = ORDER.CREDIT_CARD_CHARGE_REQUEST
        self.csq = net.csqQueryPoll()
        self.content = None
        self.card_id = None

    def resp_content_process(self, data, msg_id):
        print("Credit-Card-Charge-Request resp_content_process data = {} msg_id = {}".format(data, msg_id))
        fmt_prefix = "BHBIHHHH20sBBB"
        try:
            res = self.unpack_l(fmt_prefix + "HHHHHHHHHH", data)
        except Exception as e:
            print("not truth power meter {}".format(e))
            res = self.unpack_l(fmt_prefix, data)
        print("Credit-Card-Charge-Request === {} credit_card_info = {}".format(res, credit_card_info.map))
        sys_bus.publish(INTERNAL_TOPIC.CREDIT_CARD_CHOSE_SOCKET_CHARGING,
                        {"res": res, "msg_id": msg_id, "card_id": credit_card_info.get(msg_id)})
        if credit_card_info.exist(msg_id):
            credit_card_info.delete(msg_id)

    def pre_send(self, msg, data):
        print("pre_send == {}  msg_id = {}".format(data, self.msg_id))
        credit_card_info.set(self.msg_id, {"card_id": self.card_id, "timestamp": utime.mktime(utime.localtime())})

    def handler(self, msg):
        print("Credit-Card-Charge-Request handler msg -> {}".format(msg))
        self.card_id = msg["uid"]
        content = self.pack_l("BBBBB", *self.card_id)
        print("Credit-Card-Charge-Request content = {}".format(content))
        super(CreditCardChargeRequest, self).handler(self.pack_handler_data(content=content))


class PlatformIssuedOta(GatewayIssuedOrder):
    """平台下发数据"""

    def __init__(self):
        super(PlatformIssuedOta, self).__init__()
        self.topic = INTERNAL_TOPIC.PLATFORM_ISSUED_OTA
        self.order = ORDER.PLATFORM_ISSUED_OTA
        self.csq = net.csqQueryPoll()
        self.dev_type = DEV_TYPE
        self.content = None
        self.card_id = None

    def resp_content_process(self, data, msg_id):
        """需要委托去下载"""
        tag = TAGORDER.DEVICE_RESP
        print("Platform-Issued-Ota resp_content_process data = {} msg_id = {}".format(data, msg_id))
        content = self.pack_l("B", RESULT.OK)
        print("Platform-Issued-Ota resp_content_process res = {}".format(data))
        sys_bus.publish(INTERNAL_TOPIC.START_DEVICE_OTA, dict(initiator=OTA_INITIATOR.PLATFORM))
        self.handler(self.pack_handler_data(tag, msg_id, content))


class FailureReport(DeviceOrderActiveRequest):
    def __init__(self):
        super(FailureReport, self).__init__()
        self.topic = INTERNAL_TOPIC.FAILURE_REPORT
        self.order = ORDER.FAILURE_REPORT
        self.csq = net.csqQueryPoll()
        self.dev_type = DEV_TYPE
        self.content = None
        self.card_id = None

    def resp_content_process(self, data, msg_id):
        """需要委托去下载"""
        print("Failure-Report resp_content_process data = {} msg_id = {}".format(data, msg_id))
        fmt_prefix = "B"
        res = self.unpack_l(fmt_prefix, data)
        print("Failure-Report resp_content_process res = {}".format(res))

    def handler(self, msg):
        print("Failure-Report handler msg -> {}".format(msg))
        content = self.pack_l("BBBI", *msg.get("res"))
        super(FailureReport, self).handler(self.pack_handler_data(content=content))
        print("Failure-Report handler msg -> {}".format(msg))


class DLTInfoReport(DeviceOrderActiveRequest):
    """设备信息定时上报"""

    def __init__(self):
        super(DLTInfoReport, self).__init__()
        self.topic = INTERNAL_TOPIC.DLT_INFO_REPORT
        self.order = ORDER.DLT_INFO_REPORT
        self.csq = net.csqQueryPoll()
        self.dev_type = DEV_TYPE
        self.content = None
        self.card_id = None

    def resp_content_process(self, data, msg_id):
        """DLT INFO reporter"""
        print("DLT-Info-Report resp_content_process data = {} msg_id = {}".format(data, msg_id))
        fmt_prefix = "B"
        res = self.unpack_l(fmt_prefix, data)
        print("DLT-Info-Report  resp_content_process res = {}".format(res))

    def handler(self, msg):
        print("DLT-Info-Report  handler msg -> {}".format(msg))
        content = self.pack_l("I", *msg.get("res"))
        super(DLTInfoReport, self).handler(self.pack_handler_data(content=content))
        print("DLT-Info-Report  handler msg -> {}".format(msg))


class SearchOTAUpgradeVersion(DeviceOrderActiveRequest):
    def __init__(self):
        super(SearchOTAUpgradeVersion, self).__init__()
        self.topic = INTERNAL_TOPIC.SEARCH_OTA_UPGRADE_VERSION
        self.order = ORDER.SEARCH_OTA_UPGRADE_VERSION
        self.csq = net.csqQueryPoll()
        self.dev_type = DEV_TYPE
        self.content = None
        self.card_id = None

    def handler(self, msg):
        print("DLT-Info-Report  handler msg -> {}".format(msg))
        content = self.pack_l("BBHB", self.dev_type, 1, MCU_VERSION, msg.get("initator", 0))
        super(SearchOTAUpgradeVersion, self).handler(self.pack_handler_data(content=content))
        print("DLT-Info-Report  handler msg -> {}".format(msg))

    def resp_content_process(self, data, msg_id):
        """Search OTA Upgrade Version"""
        print("SearchOTAUpgradeVersion resp_content_process data = {} msg_id = {}".format(data, msg_id))
        fmt_prefix = "BBHI16s"
        res = self.unpack_l(fmt_prefix, data)
        upgrade_version = res[2]
        print("SearchOTAUpgradeVersion resp_content_process res = {}".format(res))

        if upgrade_version > get_truth_version():
            print("ready to upgrade ~~~~~~~~~~~~~~~~")
            sys_bus.publish(INTERNAL_TOPIC.REQUEST_OTA_DATA,
                            dict(md5_check_sum=res[4], file_total_size=res[3], upgrade_version=upgrade_version,
                                 req_start=True))
        else:
            print("version {} <= {} as {} version do not need upgrade".format(upgrade_version, get_truth_version(),
                                                                              MCU_VERSION))


class OTAFlag(object):
    LEISURE = 0
    UPGRADING = 1

    def __init__(self):
        self.lock = Lock()
        # FLAG = 0是空闲  1升级中  2升级完成  None为时间戳地址
        self.flag = [self.LEISURE, None]
        self.timer = osTimer()
        self.timer.start(20000, 1, self.check_ota_flag)

    def set_start_ota_flag(self):
        with self.lock:
            if self.flag[0] == self.LEISURE:
                self.flag = [self.UPGRADING, utime.mktime(utime.localtime())]
                return True
            else:
                return False

    def check_ota_flag(self, *args):
        with self.lock:
            print("self.ota_flag = {}".format(self.flag))
            if self.flag[1] is None:
                if not self.flag[0] != self.LEISURE:
                    self.flag[0] = self.LEISURE
                return
            if utime.mktime(utime.localtime()) - self.flag[1] > 160:
                # 升级异常160s超时
                self.flag = [self.LEISURE, None]
                print("restore flag because of upgrading error ~~~~~~")
                return


"""OTA 标志位"""
ota_flag = OTAFlag()


class RequestOTAData(DeviceOrderActiveRequest):
    """请求OTA数据包"""
    _instance = None
    UPGRADE_PROGRESS = "upgrade_progress"
    UPGREAD_FILE = LOCAL_UPDATER_FILE
    recv_size = 1024
    md5_check_sum = 0
    file_total_size = 0
    upgrade_version = 0
    start_size = 0

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        super(RequestOTAData, self).__init__()
        self.topic = INTERNAL_TOPIC.REQUEST_OTA_DATA
        self.order = ORDER.REQUEST_OTA_DATA
        self.csq = net.csqQueryPoll()
        self.dev_type = DEV_TYPE

    def delete_upgrade_file(self):
        if ql_fs.path_exists(self.UPGREAD_FILE):
            uos.remove(self.UPGREAD_FILE)

    def check_upgrade_path(self):
        dirname = ql_fs.path_dirname(self.UPGREAD_FILE)
        if not ql_fs.path_exists(dirname):
            ql_fs.mkdirs(dirname)

    def get_upgrade_file_size(self):
        if ql_fs.path_exists(self.UPGREAD_FILE):
            return ql_fs.path_getsize(self.UPGREAD_FILE)
        return None

    def get_upgrade_info_upgrade_info(self, upgrade_info):
        md5_check_sum = upgrade_info['md5_check_sum']
        if isinstance(md5_check_sum, str):
            return md5_check_sum.encode()
        return md5_check_sum

    def handler(self, msg):
        """
            这里吧ota_flag 拆出去是因为在发布对象是我们通过反序列化来创建对象
            但是在micropython中貌似这种创建方式会出现单例不安全的情况所以全局设置ota_flag
            原因是init会被初始化的时候每次都会被调用
        """
        print("self._instance = {}".format(self._instance))
        print("RequestOTAData  handler msg -> {}".format(msg))

        if msg.get('req_start', False):
            print(
                "\n\n===================================RequestOTAData=================req_start======={}==={}======================".format(
                    self, ota_flag.flag))
            print("self.UPGRADE_VERSION = {}, self.start_size = {}, self.recv_size = {}".format(self.upgrade_version,
                                                                                                self.start_size,
                                                                                                self.recv_size))
            if not ota_flag.set_start_ota_flag():
                print("vvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvv")
                print("self.flag == {}".format(ota_flag.flag))
                print("is upgarding please wait 2 min to retry~~~~~~~~~~~~~~")
                return
            upgrade_info = conf_store.get("upgrade_progress")
            if upgrade_info:
                """判断版本是否一样"""
                print("msg = {}".format(msg))
                print("upgrade_info = {}".format(upgrade_info))
                print("ql_fs.path_getsize(self.UPGREAD_FILE) = {}".format(ql_fs.path_getsize(self.UPGREAD_FILE)))
                if msg.get('md5_check_sum') == self.get_upgrade_info_upgrade_info(upgrade_info) and msg.get(
                        'upgrade_version') == \
                        upgrade_info['upgrade_version'] and upgrade_info.get(
                    'start_size') == self.get_upgrade_file_size():
                    self.start_size = upgrade_info.get('start_size')
                    print(
                        "breakpoint resume msg.get('start_size') {} == ql_fs.path_getsize(self.UPGREAD_FILE) {}".format(
                            msg.get('start_size'), ql_fs.path_getsize(self.UPGREAD_FILE)
                        ))
                else:
                    print("Download error downloading again ~~~~~~~~~~~~~~")
                    self.delete_upgrade_file()
                    self.start_size = 0
            else:
                print("start ------- download")
                self.delete_upgrade_file()
                self.start_size = 0
            self.md5_check_sum = msg.get('md5_check_sum')
            self.file_total_size = msg.get('file_total_size')
            self.upgrade_version = msg.get('upgrade_version')
        else:
            print(
                "\n\n===================================RequestOTAData=================================================")
            print("self.UPGRADE_VERSION = {}, self.start_size = {}, self.recv_size = {}".format(self.upgrade_version,
                                                                                                self.start_size,
                                                                                                self.recv_size))
        content = self.pack_l("BBHIH", self.dev_type, 1, self.upgrade_version, self.start_size, self.recv_size)
        data = self.gen(content=content)
        self.app.send(data)
        print("RequestOTAData  handler msg -> {}".format(msg))

    def resp_content_process(self, data, msg_id):
        """Request OTA Data"""
        try:
            # print("RequestOTAData resp_content_process data = {} msg_id = {}".format(data, msg_id))
            fmt_prefix = "BBIH"
            res = self.unpack_l(fmt_prefix, data[:8])
            size = res[3]
            fmt_prefix = "BBIH{}s".format(size)
            res = self.unpack_l(fmt_prefix, data)
            # print("RequestOTAData resp_content_process res = {}".format(res))
            self.check_upgrade_path()
            f = open(self.UPGREAD_FILE, "a+")
            f.write(res[4])
            self.start_size += size
            f.close()
            conf_store.update({self.UPGRADE_PROGRESS: {
                "file_total_size": self.file_total_size,
                "md5_check_sum": self.md5_check_sum,
                "upgrade_version": self.upgrade_version,
                "start_size": self.start_size
            }})
        except Exception as e:
            print("request_ota_error =========== {}".format(e))
            usys.print_exception(e)
            return
        if res[1]:
            self.handler(dict())
        else:
            conf_store.delete(self.UPGRADE_PROGRESS)
            if self.get_local_upgrade_file_md5() == self.md5_check_sum:
                print("-------------------------------check upgrading success------------------------")
                """本地验证通过"""
                print(
                    "self.UPGREAD_FILE = {} self.file_total_size = {}".format(self.UPGREAD_FILE, self.file_total_size))
                app_fota_download.update_download_stat(None, UPGRADE_FILE, self.file_total_size)
                app_fota_download.set_update_flag()
                print("wait a moment... upgrading success will restart and upgarde = {}".format(self.UPGREAD_FILE))
                Power.powerRestart()
            print("total_size = {} self.start_size = {}".format(self.file_total_size, self.start_size))

    def get_local_upgrade_file_md5(self):
        f = open(self.UPGREAD_FILE, 'r')

        md5 = uhashlib.md5()
        try:
            while True:
                data = f.read(2048)
                if data:
                    md5.update(data.encode())
                else:
                    break
        except Exception as e:
            print("get_local_upgrade_file_md5 = {}".format(e))
        md5_digest = md5.digest()
        print("md5_digest = {}  self.md5_check_sum = {}".format(md5_digest, self.md5_check_sum))
        return md5_digest


"""=======================================================传输协议层 end================================================"""


class DeviceApplication(object):
    """设备应用"""

    def __init__(self):
        # 设备接入
        self.da = None
        # 服务器
        self.lcs = None
        # 设置security_msg_map
        self.smm = None
        # 计量器
        self.vm = None
        # 插座
        self.sm = None
        # 网络
        self.nm = None
        # 读卡芯片
        self.rd = None
        # 电表
        self.dlt = None
        # 指令列表
        self.cmd_map = {}
        # 定时检查charging_end
        self.check_upload_timer = osTimer()
        # timer
        self.vm_timer = osTimer()
        self.rd_timer = osTimer()
        # 数据中枢
        self.media = None
        # 设备温度
        self.temperature = 0
        self.dlt_timer = osTimer()
        # _thread.start_new_thread(self.wait, ())

    def init(self):
        sys_bus.subscribe(INTERNAL_TOPIC.GET_EFFVO_DATA, self.get_vm_data)
        sys_bus.subscribe(INTERNAL_TOPIC.SEARCH_DEVICE_TEMPERATURE, self._search_template)
        sys_bus.subscribe(INTERNAL_TOPIC.DEVICE_RECONNECT, self.init_server)
        sys_bus.subscribe(INTERNAL_TOPIC.START_DEVICE_OTA, self.request_ota)
        sys_bus.subscribe(INTERNAL_TOPIC.SUCCESS_ONLINE, self.success_online)

    def success_online(self, topic, msg):
        if not DefaultDeviceState.FIRST_ONLINE:
            csq = net.csqQueryPoll()
            state = "弱"
            if 20 >= csq > 10:
                state = "中"
            elif csq > 20:
                state = "强"
            media.play(data="设备成功上线，设备当前信号" + state)
            DefaultDeviceState.set_first_online()

    def request_ota(self, topic, msg):
        self.req_ota(dict(initiator=1))

    def _search_template(self, topic, msg):
        msg["res"] = math.ceil(self.temperature * 100)
        sys_bus.publish(INTERNAL_TOPIC.SEARCH_DEVICE_INFO, msg)

    def tx_init_server(self):
        while True:
            if not DefaultDeviceState.DEVICE_UN_ONLINE:
                if not self.lcs:
                    self.init_server()
                else:
                    break
            utime.sleep(60)

    def init_server(self, *args, **kwargs):
        print("init_server args = {} kwargs = {}".format(args, kwargs))
        if DefaultDeviceState.DEVICE_UN_ONLINE:
            return False
        if self.lcs:
            try:
                self.lcs.stop()
            except Exception as e:
                print("self.lcs e======{}".format(e))
        self.da.request()
        for domain in self.da.domain_list:
            try:
                print("domain = {}".format(domain))
                server = CDZServer(domain['domain'], domain["port"])
                server.start()
            except Exception as e:
                print("init server error e = {}".format(e))
                continue
            else:
                print("set lcs = {}".format(server))
                self.set_lcs(server)
                sys_bus.publish(INTERNAL_TOPIC.DEVICE_REGISTER, None)
                return True
        DefaultDeviceState.set_connect_server_error(True)
        return False

    def set_dlt(self, dlt):
        self.dlt = dlt
        return self

    def set_media(self, m):
        self.media = m
        return self

    def set_lcs(self, lcs):
        """设置lc server"""
        self.lcs = lcs
        return self

    def set_da(self, da):
        """设置device access"""
        self.da = da
        return self

    def set_vm(self, vm):
        """设置 VoltaMeter"""
        self.vm = vm
        return self

    def set_sm(self, sm):
        """设置socket manager"""
        self.sm = sm
        return self

    def set_smm(self, smm):
        """设置security_msg_map"""
        self.smm = smm
        return self

    def set_nm(self, nm):
        """设置  net manager"""
        self.nm = nm
        return self

    def set_rd(self, rd):
        """设置读卡器"""
        self.rd = rd
        return self

    def get_vm_data(self, *args):
        while True:
            # 计量器来数据后通知插座, 这里选择非多线程方式, 数据中枢
            vp = self.vm.read()
            if vp:
                self.temperature = vp.device_internal_temperature
                self.sm.publish(vp)
            utime.sleep(1)

    def get_rd_data(self, *args):
        uid = self.rd.read_id()
        if uid:
            print("uid = {}".format(uid))
            sys_bus.publish(INTERNAL_TOPIC.DISCOVER_CARD,
                            {"uid": tuple(uid), "timestamp": utime.mktime(utime.localtime())})

    def check(self):
        self.sm.check()
        return self

    def add_cmd(self, cmd_handler: CDZREQReqProtocol):
        cmd_handler.app = self
        sys_bus.subscribe(cmd_handler.order, cmd_handler.response)
        self.cmd_map[cmd_handler.topic] = cmd_handler
        sys_bus.subscribe(cmd_handler.topic, self.publish)

    def send(self, msg):
        self.lcs.send(msg)

    def publish(self, topic, msg):
        cmd_handler = self.cmd_map.get(topic, None)
        if cmd_handler:
            handler = cmd_handler.__class__
            cmd = handler()
            cmd.app = self
            cmd.handler(msg)
        else:
            print("execute cmd failed")

    def dlt_upload(self, *args, **kwargs):
        data = self.dlt.read()
        print("read dlt upload data = {}".format(data))
        if data:
            sys_bus.publish(INTERNAL_TOPIC.DLT_INFO_REPORT, dict(res=[data]))

    def req_ota(self, msg=None):
        print("da = {}".format(self.da))
        """主动请求OTA"""
        if not msg:
            msg = dict()
        sys_bus.publish(INTERNAL_TOPIC.SEARCH_OTA_UPGRADE_VERSION, msg)

    def device_req(self, *args, **kwargs):
        """设备主动定时请求和上报的数据"""
        sleep_time = 60 * 10
        while True:
            utime.sleep(sleep_time)
            try:
                self.dlt_upload()
                self.req_ota()
            except Exception as e:
                print("device req error = {}".format(e))
            sleep_time = 60 * 60 * 8

    def start(self):
        _thread.start_new_thread(self.get_vm_data, ())
        self.rd_timer.start(200, 1, self.get_rd_data)
        self.check_upload_timer.start(10 * 60 * 1000, 1, self.check_upload_info)
        _thread.start_new_thread(self.device_req, ())

    def check_upload_info(self, *args):
        self.smm.check_upload()

    def stop(self):
        self.vm_timer.stop()


# 外界电表上报数据
class DLT645(object):
    def __init__(self):
        self.uart = UART(UART.UART1, 2400, 8, 1, 1, 0)

    @staticmethod
    def _read_by_addr(addr=None):
        if addr is None:
            addr = []
        ex_buff = [0x68, ]
        ex_buff.extend(addr)
        ex_buff.extend([0x68, 0x11, 0x04, 0x33, 0x33, 0x34, 0x33])
        cs = 0
        for i in ex_buff:
            cs += i
        ex_buff.append(cs & 0xff)
        ex_buff.append(0x16)
        return ustruct.pack("B" * 16, *ex_buff)

    def read(self):
        data = self._read_plan(sign_arr=[0xAA, 0xAA, 0xAA, 0xAA, 0xAA, 0xAA])
        if data is None:
            return self._read_plan(sign_arr=[0x99, 0x99, 0x99, 0x99, 0x99, 0x99])
        else:
            return data

    def _read_plan(self, sign_arr=None):
        """第一种方案读取 0xaa"""
        data = self._read_by_addr(sign_arr)
        self.uart.write(data)
        utime.sleep(1)
        data = self.uart.read()
        if data:
            return self.resolve(data)
        else:
            return None

    def resolve(self, data):
        # 解析额外电表数据
        try:
            DLT645_Addr = [0, 0, 0, 0, 0, 0]
            Offset = 0
            # 电表地址
            DLT645_Addr[0] = data[Offset + 6]
            DLT645_Addr[1] = data[Offset + 5]
            DLT645_Addr[2] = data[Offset + 4]
            DLT645_Addr[3] = data[Offset + 3]
            DLT645_Addr[4] = data[Offset + 2]
            DLT645_Addr[5] = data[Offset + 1]
            DLT645_EQ = [0, 0, 0, 0]

            DLT645_EQ[0] = data[Offset + 17] - 0x33
            DLT645_EQ[1] = data[Offset + 16] - 0x33
            DLT645_EQ[2] = data[Offset + 15] - 0x33
            DLT645_EQ[3] = data[Offset + 14] - 0x33
            # 转换成常量
            DLT645_Energy = (DLT645_EQ[0] >> 4) & 0x0F
            DLT645_Energy *= 10
            DLT645_Energy += DLT645_EQ[0] & 0x0F
            DLT645_Energy *= 10
            DLT645_Energy += (DLT645_EQ[1] >> 4) & 0x0F
            DLT645_Energy *= 10
            DLT645_Energy += DLT645_EQ[1] & 0x0F
            DLT645_Energy *= 10
            DLT645_Energy += (DLT645_EQ[2] >> 4) & 0x0F
            DLT645_Energy *= 10
            DLT645_Energy += DLT645_EQ[2] & 0x0F
            DLT645_Energy *= 10
            DLT645_Energy += (DLT645_EQ[3] >> 4) & 0x0F
            DLT645_Energy *= 10
            DLT645_Energy += DLT645_EQ[3] & 0x0F
            return DLT645_Energy
        except Exception as e:
            print("read and solve error = {}".format(e))
            return None


def init_cmd(application: DeviceApplication):
    # 指令操作
    heart = ProtoHeartBeat()
    register = ProtoDeviceRegister()
    device_search = SearchDeviceInfo()
    search_communication_module_info = SearchCommunicationModuleInfo()
    search_socket_status = SearchSocketStatus()
    search_lbs_info = SearchLBSInfo()
    search_media_info = SearchMediaInfo()
    set_media_info = SetMediaInfo()
    open_charging = OpenCharging()
    stop_charging = StopCharging()
    upload_charge_status = UploadChargingStatus()
    upload_power_status = UploadPowerStatus()
    charging_end = ChargingEnd()
    device_restart = DeviceRestart()
    set_default_config_info = SetDefaultConfigInfo()
    search_default_config_info = SearchDefaultConfigInfo()
    credit_card_charge_request = CreditCardChargeRequest()
    upload_credit_card_order = UploadCreditCardOrder()
    platform_issue_data = PlatformIssuedOta()
    search_charging_state = SearchChargingState()
    failure_report = FailureReport()
    dlt_info_report = DLTInfoReport()
    search_ota_version = SearchOTAUpgradeVersion()
    request_ota_data = RequestOTAData()
    application.add_cmd(heart)
    application.add_cmd(register)
    application.add_cmd(device_search)
    application.add_cmd(search_communication_module_info)
    application.add_cmd(search_socket_status)
    application.add_cmd(search_lbs_info)
    application.add_cmd(search_media_info)
    application.add_cmd(set_media_info)
    application.add_cmd(open_charging)
    application.add_cmd(stop_charging)
    application.add_cmd(upload_charge_status)
    application.add_cmd(upload_power_status)
    application.add_cmd(charging_end)
    application.add_cmd(set_default_config_info)
    application.add_cmd(search_default_config_info)
    application.add_cmd(credit_card_charge_request)
    application.add_cmd(upload_credit_card_order)
    application.add_cmd(platform_issue_data)
    application.add_cmd(failure_report)
    application.add_cmd(device_restart)
    application.add_cmd(dlt_info_report)
    application.add_cmd(search_charging_state)
    application.add_cmd(search_ota_version)
    application.add_cmd(request_ota_data)


if __name__ == '__main__':
    """
    1. ConfStore配置
        1.配置加载挨个去init在启动前
        2.socket的init必须是在socket启动后再去装载
    2. DeviceApplication设备应用
        1. init 去订阅设备的消息
        2. 注入网络, 设备接入, socket等管理器
        3.初始化指令
        4.检查相关状态
    3. server-init
        1. 初始化设备连接服务器
        2. 注册设备接入信息
    """
    utime.sleep(2)
    # _thread.stack_size(16 * 1024)
    # 配置存储
    conf_store = ConfStore()
    conf_store.init()
    # conf_store.init
    media = Media()
    media.init(conf_store.get("media"))
    DefaultDeviceConfig.init(conf_store.get("default_device_config"))
    security_msg_map.init(conf_store.get("security_msg_map"))

    # 设备应用
    app = DeviceApplication()
    app.init()

    # 定义全局解析指令
    req = ReqProtocol()
    sys_bus.subscribe(INTERNAL_TOPIC.PROTOCOL_ANALYSIS, req.protocol_analysis)

    # 设置设备接入
    app.set_da(DeviceAccess())
    # 优先检查网络情况
    net_manager = NetManage()

    # 设置插座
    socket_a = Socket(**config[SOCKET_A])
    socket_b = Socket(**config[SOCKET_B])
    sock_m = SocketManage()
    sock_m.add(socket_a)
    sock_m.add(socket_b)
    sock_m.read()

    # 初始化指令
    init_cmd(app)
    # 设置其他参数
    app.set_vm(VoltaMeter()) \
        .set_sm(sock_m) \
        .set_nm(net_manager) \
        .set_media(media) \
        .set_rd(MFRC522_SPI()) \
        .set_smm(
        security_msg_map).set_dlt(DLT645())
    # 开机检查
    app.check()
    net_manager.check()
    utime.sleep(2)
    # 初始化服务
    state = app.init_server()
    _thread.start_new_thread(app.tx_init_server, ())
    app.start()

    # 开启设备接入模式
    i = 5

    while True:
        if DefaultDeviceState.REGISTER:
            break
        if i < 10:
            i += 1
        else:
            break
        utime.sleep(1)
    sock_m.init(conf_store)
    app.check_upload_info()
    print("MCU VERSION = {}".format(MCU_VERSION))
