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

from machine import SPI, Pin
import utime, ustruct


class MFRC522(object):
    MAX_LEN = 16

    PCD_IDLE = 0x00
    PCD_AUTHENT = 0x0E
    PCD_RECEIVE = 0x08
    PCD_TRANSMIT = 0x04
    PCD_TRANSCEIVE = 0x0C
    PCD_RESETPHASE = 0x0F
    PCD_CALCCRC = 0x03

    PICC_REQIDL = 0x26
    PICC_REQALL = 0x52
    PICC_ANTICOLL = 0x93
    PICC_SElECTTAG = 0x93
    PICC_AUTHENT1A = 0x60
    PICC_AUTHENT1B = 0x61
    PICC_READ = 0x30
    PICC_WRITE = 0xA0
    PICC_DECREMENT = 0xC0
    PICC_INCREMENT = 0xC1
    PICC_RESTORE = 0xC2
    PICC_TRANSFER = 0xB0
    PICC_HALT = 0x50

    MI_OK = 0
    MI_NOTAGERR = 1
    MI_ERR = 2

    Reserved00 = 0x00
    CommandReg = 0x01
    CommIEnReg = 0x02
    DivlEnReg = 0x03
    CommIrqReg = 0x04
    DivIrqReg = 0x05
    ErrorReg = 0x06
    Status1Reg = 0x07
    Status2Reg = 0x08
    FIFODataReg = 0x09
    FIFOLevelReg = 0x0A
    WaterLevelReg = 0x0B
    ControlReg = 0x0C
    BitFramingReg = 0x0D
    CollReg = 0x0E
    Reserved01 = 0x0F

    Reserved10 = 0x10
    ModeReg = 0x11
    TxModeReg = 0x12
    RxModeReg = 0x13
    TxControlReg = 0x14
    TxAutoReg = 0x15
    TxSelReg = 0x16
    RxSelReg = 0x17
    RxThresholdReg = 0x18
    DemodReg = 0x19
    Reserved11 = 0x1A
    Reserved12 = 0x1B
    MifareReg = 0x1C
    Reserved13 = 0x1D
    Reserved14 = 0x1E
    SerialSpeedReg = 0x1F

    Reserved20 = 0x20
    CRCResultRegM = 0x21
    CRCResultRegL = 0x22
    Reserved21 = 0x23
    ModWidthReg = 0x24
    Reserved22 = 0x25
    RFCfgReg = 0x26
    GsNReg = 0x27
    CWGsPReg = 0x28
    ModGsPReg = 0x29
    TModeReg = 0x2A
    TPrescalerReg = 0x2B
    TReloadRegH = 0x2C
    TReloadRegL = 0x2D
    TCounterValueRegH = 0x2E
    TCounterValueRegL = 0x2F

    Reserved30 = 0x30
    TestSel1Reg = 0x31
    TestSel2Reg = 0x32
    TestPinEnReg = 0x33
    TestPinValueReg = 0x34
    TestBusReg = 0x35
    AutoTestReg = 0x36
    VersionReg = 0x37
    AnalogTestReg = 0x38
    TestDAC1Reg = 0x39
    TestDAC2Reg = 0x3A
    TestADCReg = 0x3B
    Reserved31 = 0x3C
    Reserved32 = 0x3D
    Reserved33 = 0x3E
    Reserved34 = 0x3F

    serNum = []
    KEY = [0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]
    BLOCK_ADDRS = [8, 9, 10]

    def __init__(self):

        self.MFRC522_Init()

    def _MFRC522_Reset(self):
        self._Write_MFRC522(self.CommandReg, self.PCD_RESETPHASE)

    def _Write_MFRC522(self, addr, val):
        print("this bug write")
        raise NotImplementedError

    def _Read_MFRC522(self, addr):
        print("this bug read")
        raise NotImplementedError

    def _Close_MFRC522(self):
        raise NotImplementedError

    def _SetBitMask(self, reg, mask):
        tmp = self._Read_MFRC522(reg)
        self._Write_MFRC522(reg, tmp | mask)

    def _ClearBitMask(self, reg, mask):
        tmp = self._Read_MFRC522(reg)
        self._Write_MFRC522(reg, tmp & (~mask))

    def AntennaOn(self):
        temp = self._Read_MFRC522(self.TxControlReg)
        if (~(temp & 0x03)):
            self._SetBitMask(self.TxControlReg, 0x03)

    def AntennaOff(self):
        self._ClearBitMask(self.TxControlReg, 0x03)

    def _MFRC522_ToCard(self, command, sendData):
        backData = []
        backLen = 0
        status = self.MI_ERR
        irqEn = 0x00
        waitIRq = 0x00
        lastBits = None
        n = 0

        if command == self.PCD_AUTHENT:
            irqEn = 0x12
            waitIRq = 0x10
        if command == self.PCD_TRANSCEIVE:
            irqEn = 0x77
            waitIRq = 0x30

        self._Write_MFRC522(self.CommIEnReg, irqEn | 0x80)
        self._ClearBitMask(self.CommIrqReg, 0x80)
        self._SetBitMask(self.FIFOLevelReg, 0x80)

        self._Write_MFRC522(self.CommandReg, self.PCD_IDLE)

        for i in range(len(sendData)):
            self._Write_MFRC522(self.FIFODataReg, sendData[i])

        self._Write_MFRC522(self.CommandReg, command)

        if command == self.PCD_TRANSCEIVE:
            self._SetBitMask(self.BitFramingReg, 0x80)

        i = 5
        while True:
            n = self._Read_MFRC522(self.CommIrqReg)
            i -= 1
            if ~((i != 0) and ~(n & 0x01) and ~(n & waitIRq)):
                break

        self._ClearBitMask(self.BitFramingReg, 0x80)

        if i != 0:
            if (self._Read_MFRC522(self.ErrorReg) & 0x1B) == 0x00:
                status = self.MI_OK

                if n & irqEn & 0x01:
                    status = self.MI_NOTAGERR

                if command == self.PCD_TRANSCEIVE:
                    n = self._Read_MFRC522(self.FIFOLevelReg)
                    lastBits = self._Read_MFRC522(self.ControlReg) & 0x07
                    if lastBits != 0:
                        backLen = (n - 1) * 8 + lastBits
                    else:
                        backLen = n * 8

                    if n == 0:
                        n = 1
                    if n > self.MAX_LEN:
                        n = self.MAX_LEN

                    for i in range(n):
                        backData.append(self._Read_MFRC522(self.FIFODataReg))
            else:
                status = self.MI_ERR

        return (status, backData, backLen)

    def _MFRC522_Request(self, reqMode):
        status = None
        backBits = None
        TagType = []

        self._Write_MFRC522(self.BitFramingReg, 0x07)

        TagType.append(reqMode)
        (status, backData, backBits) = self._MFRC522_ToCard(
            self.PCD_TRANSCEIVE, TagType)

        if ((status != self.MI_OK) | (backBits != 0x10)):
            status = self.MI_ERR

        return (status, backBits)

    def _MFRC522_Anticoll(self):
        backData = []
        serNumCheck = 0

        serNum = []

        self._Write_MFRC522(self.BitFramingReg, 0x00)

        serNum.append(self.PICC_ANTICOLL)
        serNum.append(0x20)

        (status, backData, backBits) = self._MFRC522_ToCard(
            self.PCD_TRANSCEIVE, serNum)

        if (status == self.MI_OK):
            i = 0
            if len(backData) == 5:
                for i in range(4):
                    serNumCheck = serNumCheck ^ backData[i]
                if serNumCheck != backData[4]:
                    status = self.MI_ERR
            else:
                status = self.MI_ERR

        return (status, backData)

    def _CalulateCRC(self, pIndata):
        self._ClearBitMask(self.DivIrqReg, 0x04)
        self._SetBitMask(self.FIFOLevelReg, 0x80)

        for i in range(len(pIndata)):
            self._Write_MFRC522(self.FIFODataReg, pIndata[i])

        self._Write_MFRC522(self.CommandReg, self.PCD_CALCCRC)
        i = 0xFF
        while True:
            n = self._Read_MFRC522(self.DivIrqReg)
            i -= 1
            if not ((i != 0) and not (n & 0x04)):
                break
        pOutData = []
        pOutData.append(self._Read_MFRC522(self.CRCResultRegL))
        pOutData.append(self._Read_MFRC522(self.CRCResultRegM))
        return pOutData

    def _MFRC522_SelectTag(self, serNum):
        backData = []
        buf = []
        buf.append(self.PICC_SElECTTAG)
        buf.append(0x70)

        for i in range(5):
            buf.append(serNum[i])

        pOut = self._CalulateCRC(buf)
        buf.append(pOut[0])
        buf.append(pOut[1])
        (status, backData, backLen) = self._MFRC522_ToCard(
            self.PCD_TRANSCEIVE, buf)

        if (status == self.MI_OK) and (backLen == 0x18):
            print("Size: " + str(backData[0]))
            return backData[0]
        else:
            return 0

    def MFRC522_Auth(self, authMode, BlockAddr, Sectorkey, serNum):
        buff = []

        # First byte should be the authMode (A or B)
        buff.append(authMode)

        # Second byte is the trailerBlock (usually 7)
        buff.append(BlockAddr)

        # Now we need to append the authKey which usually is 6 bytes of 0xFF
        for i in range(len(Sectorkey)):
            buff.append(Sectorkey[i])

        # Next we append the first 4 bytes of the UID
        for i in range(4):
            buff.append(serNum[i])

        # Now we start the authentication itself
        (status, backData, backLen) = self._MFRC522_ToCard(self.PCD_AUTHENT, buff)

        # Check if an error occurred
        if not (status == self.MI_OK):
            print("AUTH ERROR!!")
        if not (self._Read_MFRC522(self.Status2Reg) & 0x08) != 0:
            print("AUTH ERROR(status2reg & 0x08) != 0")

        # Return the status
        return status

    def MFRC522_StopCrypto1(self):
        self._ClearBitMask(self.Status2Reg, 0x08)

    def MFRC522_Read(self, blockAddr):
        recvData = []
        recvData.append(self.PICC_READ)
        recvData.append(blockAddr)
        pOut = self._CalulateCRC(recvData)
        recvData.append(pOut[0])
        recvData.append(pOut[1])
        (status, backData, backLen) = self._MFRC522_ToCard(
            self.PCD_TRANSCEIVE, recvData)
        if not (status == self.MI_OK):
            print("Error while reading!")

        if len(backData) == 16:
            print("Sector " + str(blockAddr) + " " + str(backData))
            return backData
        else:
            return None

    def MFRC522_Write(self, blockAddr, writeData):
        buff = []
        buff.append(self.PICC_WRITE)
        buff.append(blockAddr)
        crc = self._CalulateCRC(buff)
        buff.append(crc[0])
        buff.append(crc[1])
        (status, backData, backLen) = self._MFRC522_ToCard(
            self.PCD_TRANSCEIVE, buff)
        if not (status == self.MI_OK) or not (backLen == 4) or not ((backData[0] & 0x0F) == 0x0A):
            status = self.MI_ERR

        print("%s backdata &0x0F == 0x0A %s" % (backLen, backData[0] & 0x0F))
        if status == self.MI_OK:
            buf = []
            for i in range(16):
                buf.append(writeData[i])

            crc = self._CalulateCRC(buf)
            buf.append(crc[0])
            buf.append(crc[1])
            (status, backData, backLen) = self._MFRC522_ToCard(
                self.PCD_TRANSCEIVE, buf)
            if not (status == self.MI_OK) or not (backLen == 4) or not ((backData[0] & 0x0F) == 0x0A):
                print("Error while writing")
            if status == self.MI_OK:
                print("Data written")

    def MFRC522_DumpClassic1K(self, key, uid):
        for i in range(64):
            status = self.MFRC522_Auth(self.PICC_AUTHENT1A, i, key, uid)
            # Check if authenticated
            if status == self.MI_OK:
                self.MFRC522_Read(i)
            else:
                print("Authentication error")

    def MFRC522_Init(self):
        self._MFRC522_Reset()

        self._Write_MFRC522(self.TModeReg, 0x8D)
        self._Write_MFRC522(self.TPrescalerReg, 0x3E)
        self._Write_MFRC522(self.TReloadRegL, 30)
        self._Write_MFRC522(self.TReloadRegH, 0)

        self._Write_MFRC522(self.TxAutoReg, 0x40)
        self._Write_MFRC522(self.ModeReg, 0x3D)
        self.AntennaOff()
        self.AntennaOn()
        self.M500PcdConfigISOType('A')

    def M500PcdConfigISOType(self, type):

        if type == 'A':
            self._ClearBitMask(self.Status2Reg, 0x08)
            self._Write_MFRC522(self.ModeReg, 0x3D)
            self._Write_MFRC522(self.RxSelReg, 0x86)
            self._Write_MFRC522(self.RFCfgReg, 0x7F)
            self._Write_MFRC522(self.TReloadRegL, 30)
            self._Write_MFRC522(self.TReloadRegH, 0)
            self._Write_MFRC522(self.TModeReg, 0x8D)
            self._Write_MFRC522(self.TPrescalerReg, 0x3E)
            utime.sleep_us(1000)
            self.AntennaOn()
        else:
            return 1

    def read(self):
        id, text = self.read_no_block()
        while not id:
            id, text = self.read_no_block()
        return id, text

    def read_id(self):
        return self.read_id_no_block()

    def read_id_no_block(self):
        (status, TagType) = self._MFRC522_Request(self.PICC_REQIDL)
        if status != self.MI_OK:
            return None
        (status, uid) = self._MFRC522_Anticoll()
        if status != self.MI_OK:
            return None
        return uid

    def read_no_block(self):
        (status, TagType) = self._MFRC522_Request(self.PICC_REQIDL)
        if status != self.MI_OK:
            return None, None
        (status, uid) = self._MFRC522_Anticoll()
        if status != self.MI_OK:
            return None, None
        id = self._uid_to_num(uid)
        self._MFRC522_SelectTag(uid)
        status = self.MFRC522_Auth(
            self.PICC_AUTHENT1A, 11, self.KEY, uid)
        data = []
        text_read = ''
        if status == self.MI_OK:
            for block_num in self.BLOCK_ADDRS:
                block = self.MFRC522_Read(block_num)
                if block:
                    data += block
            if data:
                text_read = ''.join(chr(i) for i in data)
        self.MFRC522_StopCrypto1()
        return id, text_read

    def write(self, text):
        id, text_in = self.write_no_block(text)
        while not id:
            id, text_in = self.write_no_block(text)
        return id, text_in

    def write_no_block(self, text):
        (status, TagType) = self._MFRC522_Request(self.PICC_REQIDL)
        if status != self.MI_OK:
            return None, None
        (status, uid) = self._MFRC522_Anticoll()
        if status != self.MI_OK:
            return None, None
        id = self._uid_to_num(uid)
        self._MFRC522_SelectTag(uid)
        status = self.MFRC522_Auth(
            self.PICC_AUTHENT1A, 11, self.KEY, uid)
        self.MFRC522_Read(11)
        if status == self.MI_OK:
            data = bytearray()
            data.extend(bytearray(text.ljust(
                len(self.BLOCK_ADDRS) * 16).encode('ascii')))
            i = 0
            for block_num in self.BLOCK_ADDRS:
                self.MFRC522_Write(block_num, data[(i * 16):(i + 1) * 16])
                i += 1
        self.MFRC522_StopCrypto1()
        return id, text[0:(len(self.BLOCK_ADDRS) * 16)]

    def _uid_to_num(self, uid):
        n = 0
        for i in range(0, 5):
            n = n * 256 + uid[i]
        return n


class MFRC522_SPI(MFRC522):
    """Driver is LIS2DH12 using I2C."""

    def __init__(self, spi=None, spi_no=0, spi_mode=0, spi_clk=0, pin_rst=Pin.GPIO17):
        if spi is None:
            self._spi = SPI(spi_no, spi_mode, spi_clk)
        else:
            self._spi = spi
        self._rst = Pin(pin_rst, Pin.OUT, Pin.PULL_DISABLE, 0)
        utime.sleep(1)
        self._rst.write(1)
        super().__init__()

    def _Write_MFRC522(self, addr, val):
        global write_buf
        write_buf = bytearray([(addr << 1) & 0x7E, val])
        # print("write_buf:",write_buf)
        self._spi.write(write_buf, len(write_buf))

    def _Read_MFRC522(self, addr):
        global write_buf, read_buf
        write_buf = bytearray([((addr << 1) & 0x7E) | 0x80, 0])
        read_buf = bytearray(len(write_buf))
        self._spi.write_read(read_buf, write_buf, len(write_buf))
        # print("read_buf:",read_buf)
        return read_buf[1]

    def _Close_MFRC522(self):
        pass
