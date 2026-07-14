# 当前拓扑道路方案备份与恢复说明

本目录保存 2026-07-13 22:36:15（Asia/Shanghai）捕获的完整拓扑道路方案，可恢复到当前 `VideoTest` 项目版本。

## 备份内容

| 项目 | 值 |
| --- | --- |
| 源提交 | `f6249ecda3cf69c5a402033e81ca2d89cc347fd2` |
| 拓扑格式版本 | `3` |
| 道路数量 | `13` |
| 摄像头数量 | `12` |
| 拓扑配置 | `backup/traffic_map.json` |
| 地图底图 | `backup/sandpan/沙盘平面图1.png` |
| 完整性清单 | `MANIFEST.json` |

车辆实时轨迹、道路热力值、检测事件、视频和模型文件不属于拓扑方案，因此未包含在此备份中。

## 恢复前注意事项

1. 本备份面向拓扑格式版本 `3`。若项目以后提高 `TOPOLOGY_VERSION`，应先完成数据迁移，不要直接覆盖。
2. 必须先停止 VideoTest。运行中的服务可能在编辑道路、摄像头或关闭时再次写入 `traffic_map.json`，覆盖刚恢复的数据。
3. 不要修改 `backup` 或 `MANIFEST.json`。恢复脚本会对大小、SHA-256、JSON 版本、道路数量、摄像头数量和底图引用进行校验。
4. 恢复脚本不会自动启动或停止服务，也不会修改项目代码。

## 推荐恢复方式

在当前项目根目录 `E:\GitRepo\VideoTest` 打开 PowerShell。

### 1. 停止服务

```powershell
.\stop.ps1
```

如果启动时显式指定过端口，请对停止脚本传入相同参数，例如：

```powershell
.\stop.ps1 -Port 8080
```

### 2. 只做校验和演练

`-WhatIf` 会完成全部备份校验，但不会创建回滚目录或覆盖项目文件。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
    -File .\data-store-copy\restore.ps1 -WhatIf
```

预期看到：

```text
Backup verification passed: topology v3, 13 segments, 12 cameras.
No project files were changed.
```

### 3. 执行恢复

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
    -File .\data-store-copy\restore.ps1
```

脚本会先把被替换的当前文件保存到：

```text
data-store-copy/restore-rollback/<yyyyMMdd-HHmmss>/
```

随后恢复并复检以下目标：

```text
traffic_map.json
sandpan/沙盘平面图1.png
```

脚本输出的 `Rollback snapshot` 是本次恢复对应的准确回滚目录，请保留到运行验证结束。

### 4. 启动并验证

```powershell
.\start.ps1
```

默认服务地址为 `http://127.0.0.1:8000`。如果 `.env` 或启动参数配置了其他地址/端口，请替换下面的 `$baseUrl`。

```powershell
$baseUrl = "http://127.0.0.1:8000"
$health = Invoke-RestMethod "$baseUrl/api/health"
$map = Invoke-RestMethod "$baseUrl/api/map"
$image = Invoke-WebRequest "$baseUrl/api/map/image" -UseBasicParsing

$health.status
@($map.segments).Count
@($map.cameras).Count
$image.StatusCode
```

预期结果依次为：

```text
ok
13
12
200
```

在浏览器打开服务首页，检查道路形状、道路名称、摄像头位置/绑定和底图是否符合备份方案。

## 手工恢复方式

仅在无法运行 `restore.ps1` 时使用。手工方式同样必须先运行 `stop.ps1`。

```powershell
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$rollback = ".\data-store-copy\restore-rollback\$stamp"

New-Item -ItemType Directory -Path "$rollback\sandpan" -Force | Out-Null
Copy-Item -LiteralPath ".\traffic_map.json" -Destination "$rollback\traffic_map.json"
Copy-Item -LiteralPath ".\sandpan\沙盘平面图1.png" `
    -Destination "$rollback\sandpan\沙盘平面图1.png"

Copy-Item -LiteralPath ".\data-store-copy\backup\sandpan\沙盘平面图1.png" `
    -Destination ".\sandpan\沙盘平面图1.png" -Force
Copy-Item -LiteralPath ".\data-store-copy\backup\traffic_map.json" `
    -Destination ".\traffic_map.json" -Force
```

恢复后检查结构：

```powershell
$data = Get-Content -LiteralPath ".\traffic_map.json" -Raw -Encoding UTF8 |
    ConvertFrom-Json

$data.version
@($data.segments).Count
@($data.cameras).Count
Test-Path -LiteralPath (Join-Path (Get-Location) $data.map_image)
```

预期结果为 `3`、`13`、`12`、`True`。随后执行“启动并验证”。

## 校验备份完整性

完整清单位于 `MANIFEST.json`。当前快照的 SHA-256 为：

```text
traffic_map.json             4e3f2eafd9a11f4dd5143555a5b7306bee60110b559683401a5d99b785094a5f
sandpan/沙盘平面图1.png      b5b4dca6018f7db30748e0cd8c82ec2d1793c5747860ab122198e5d37bc5a0c4
```

可以随时运行只读校验：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
    -File .\data-store-copy\restore.ps1 -WhatIf
```

## 从回滚目录撤销恢复

若运行验证失败，先停止服务，然后使用恢复脚本输出的回滚目录替换 `<时间戳>`：

```powershell
$rollback = ".\data-store-copy\restore-rollback\<时间戳>"
Copy-Item -LiteralPath "$rollback\sandpan\沙盘平面图1.png" `
    -Destination ".\sandpan\沙盘平面图1.png" -Force
Copy-Item -LiteralPath "$rollback\traffic_map.json" `
    -Destination ".\traffic_map.json" -Force
.\start.ps1
```

若某个目标文件在恢复前不存在，回滚目录中不会有对应文件；撤销时应删除恢复新增的目标文件，而不是执行该项复制。
