# 沙盘交通智控台

项目已重构为浏览器前端与 FastAPI 后端。后端继续复用原有 YOLO 车辆检测、中文车牌识别、白名单匹配和交通拓扑模型；浏览器通过 MJPEG 查看实时标注画面，并通过 REST API 管理视频、检测参数、道路和摄像头。

## 项目结构

```text
backend/
  app.py             FastAPI 应用与接口
  config.py          视频源和路径配置
  schemas.py         API 请求模型
  state.py           持久化与共享业务状态
  video_stream.py    视频采集、推理与 MJPEG 发布
frontend/
  index.html         Web 控制台
  styles.css         响应式界面样式
  js/
    api.js            API 客户端
    app.js            页面状态与交互
    map-canvas.js     道路、摄像头和轨迹 Canvas
traffic_map.py       道路拓扑与轨迹领域模型
detection_processor.py
vehicle_detector.py
lpr_recognizer.py
main.py              Web 服务入口
```

## 运行

项目要求 Python 3.11，并使用 `uv` 管理依赖。

```powershell
uv sync
uv run python main.py
```

打开 <http://127.0.0.1:8000>。OpenAPI 文档位于 <http://127.0.0.1:8000/docs>。

监听地址和端口可通过环境变量调整：

```powershell
$env:VIDEOTEST_HOST = "0.0.0.0"
$env:VIDEOTEST_PORT = "8080"
uv run python main.py
```

## Web 功能

- 切换 12 路 RTSP 视频源，上传本地视频，暂停、继续、停止和下载截图
- 懒加载车辆/车牌模型，在线调整推理设备、检测阈值和帧间隔
- 实时展示车辆、车牌、白名单匹配结果与统计
- 查看道路热度、车辆轨迹和摄像头覆盖范围
- 定位摄像头，新建、绘制、编辑和删除道路，上传沙盘底图
- 添加、更新、启停、移除和清空车牌白名单

上传的视频和地图保存在忽略版本控制的 `runtime/` 目录。道路与摄像头配置仍写入 `traffic_map.json`，白名单写入 `whitelist.json`。

## 测试

```powershell
uv run pytest
```

测试覆盖识别基础逻辑、交通拓扑、视频服务状态、Web 页面入口和主要 API 契约。测试不会连接实际 RTSP 视频，也不会加载推理模型。
