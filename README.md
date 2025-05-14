# Charging Pile Management System

[中文](README.zh.md) | English

## Introduction

This project implements a charging pile management system, including OTA upgrades, device application management, external meter data processing, and other functions. Through these features, the system can handle OTA upgrades, device management, data transmission, and other tasks to ensure the normal operation of the charging pile system.

## File Structure

- `MFRC522.py`: Implementation of the card reader module.
- `main.py`: Main program, including the initialization and startup logic of the device application.

## Usage

### 1. Initialize Configuration Storage

In `main.py`, first initialize the configuration storage:

```python
conf_store = ConfStore()
conf_store.init()
```

### 2. Initialize Media

Initialize the media module for playing prompts or voice:

```python
media = Media()
media.init(conf_store.get("media"))
```

### 3. Initialize Device Application

Create and initialize the device application instance:

```python
app = DeviceApplication()
app.init()
```

### 4. Initialize Commands

Initialize all commands in the device application:

```python
init_cmd(app)
```

### 5. Set Device Access

Set the device access module:

```python
app.set_da(DeviceAccess())
```

### 6. Set Network Manager

Set the network manager:

```python
net_manager = NetManage()
app.set_nm(net_manager)
```

### 7. Set Sockets

Set the socket manager and add sockets:

```python
socket_a = Socket(**config[SOCKET_A])
socket_b = Socket(**config[SOCKET_B])
sock_m = SocketManage()
sock_m.add(socket_a)
sock_m.add(socket_b)
```

### 8. Set Other Components

Set other necessary components:

```python
app.set_vm(VoltaMeter()) \
    .set_sm(sock_m) \
    .set_media(media) \
    .set_rd(MFRC522_SPI()) \
    .set_smm(security_msg_map) \
    .set_dlt(DLT645())
```

### 9. Initialize Server

Initialize the server connection:

```python
state = app.init_server()
```

### 10. Start Device Application

Start the device application:

```python
app.start()
```

### 11. Wait for Device Registration to Complete

Wait for the device registration to complete:

```python
while not DefaultDeviceState.REGISTER:
    utime.sleep(1)
```

### 12. Initialize Sockets

Initialize sockets and check upload information:

```python
sock_m.init(conf_store)
app.check_upload_info()
print("MCU VERSION = {}".format(MCU_VERSION))
```

## File Description

### MFRC522.py

The `MFRC522.py` file implements the card reader module's functions, including reading card IDs and other operations.

### main.py

The `main.py` file is the main program, including the initialization and startup logic of the device application.

## Contribution

We welcome contributions to improve this project! Please follow these steps to contribute:

1. Fork the repository.
2. Create a new branch (`git checkout -b feature/your-feature`).
3. Commit your changes (`git commit -m 'Add your feature'`).
4. Push to the branch (`git push origin feature/your-feature`).
5. Open a Pull Request.

## License

This project is licensed under the Apache License. For more details, please refer to the [LICENSE](LICENSE) file.
