
# 充电桩管理系统

中文 | [English](README.md)

## 简介

本项目实现了一个充电桩管理系统，包含了 OTA 升级、设备应用管理、外部电表数据处理等功能。通过这些功能，系统能够处理 OTA 升级、设备管理、数据传输等任务，确保充电桩系统的正常运行。

## 文件结构

- `MFRC522.py`：读卡器模块的实现。
- `main.py`：主程序，包含设备应用的初始化和启动逻辑。

## 使用方法

### 1. 配置存储初始化

在 `main.py` 中，首先初始化配置存储：

```python
conf_store = ConfStore()
conf_store.init()
```

### 2. 初始化媒体

初始化媒体模块，用于播放提示音或语音：

```python
media = Media()
media.init(conf_store.get("media"))
```

### 3. 初始化设备应用

创建并初始化设备应用实例：

```python
app = DeviceApplication()
app.init()
```

### 4. 初始化指令

初始化设备应用中的所有指令：

```python
init_cmd(app)
```

### 5. 设置设备接入

设置设备接入模块：

```python
app.set_da(DeviceAccess())
```

### 6. 设置网络管理器

设置网络管理器：

```python
net_manager = NetManage()
app.set_nm(net_manager)
```

### 7. 设置插座

设置插座管理器，并添加插座：

```python
socket_a = Socket(**config[SOCKET_A])
socket_b = Socket(**config[SOCKET_B])
sock_m = SocketManage()
sock_m.add(socket_a)
sock_m.add(socket_b)
```

### 8. 设置其他组件

设置其他必要的组件：

```python
app.set_vm(VoltaMeter()) \
    .set_sm(sock_m) \
    .set_media(media) \
    .set_rd(MFRC522_SPI()) \
    .set_smm(security_msg_map) \
    .set_dlt(DLT645())
```

### 9. 初始化服务器

初始化服务器连接：

```python
state = app.init_server()
```

### 10. 启动设备应用

启动设备应用：

```python
app.start()
```

### 11. 等待设备注册完成

等待设备注册完成：

```python
while not DefaultDeviceState.REGISTER:
    utime.sleep(1)
```

### 12. 初始化插座

初始化插座并检查上传信息：

```python
sock_m.init(conf_store)
app.check_upload_info()
print("MCU VERSION = {}".format(MCU_VERSION))
```

## 文件说明

### MFRC522.py

`MFRC522.py` 文件实现了读卡器模块的功能，包括读取卡片 ID 等操作。

### main.py

`main.py` 文件是主程序，包含设备应用的初始化和启动逻辑。

## 贡献

我们欢迎对本项目的改进做出贡献！请按照以下步骤进行贡献：

1. Fork 此仓库。
2. 创建一个新分支（`git checkout -b feature/your-feature`）。
3. 提交您的更改（`git commit -m 'Add your feature'`）。
4. 推送到分支（`git push origin feature/your-feature`）。
5. 打开一个 Pull Request。

## 许可证

本项目使用 Apache 许可证，详情请参阅 [LICENSE](LICENSE) 文件。
