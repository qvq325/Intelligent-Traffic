# 模型权重文件

模型权重由项目组直接分发，不存放在 Git 仓库中。克隆仓库后，请先向项目组获取以下三个文件，并严格按照相对路径放置，再启动应用。

## 文件清单

| 用途 | 相对路径 | 文件大小 | SHA-256 |
| --- | --- | ---: | --- |
| 现有车辆检测模型 | `yolo11m.pt` | 40,684,120 字节（38.80 MiB） | `d5ffc1a674953a08e11a8d21e022781b1b23a19b730afc309290bd9fb5305b95` |
| 训练后车辆检测模型 | `训练后模型/yolo26x.pt` | 118,667,365 字节（113.17 MiB） | `9fdd44a31c504547ffb81d2c6d9e6dac3493c8eaa8b0398d3f43bae6c7003e92` |
| 训练后车牌检测模型 | `训练后模型/license_plate_best.pt` | 175,045,023 字节（166.94 MiB） | `66f6a3c115977fe12b936035821fa8e69117b4c6f14f33f7f3b4b75caa15be02` |

## 目录结构

```text
VideoTest/
|-- yolo11m.pt
`-- 训练后模型/
    |-- yolo26x.pt
    `-- license_plate_best.pt
```

如目录尚不存在，可在仓库根目录执行：

```powershell
New-Item -ItemType Directory -Force '.\训练后模型' | Out-Null
```

## 校验文件

在仓库根目录运行以下 PowerShell 命令，检查文件是否存在并计算哈希：

```powershell
$weights = @(
    '.\yolo11m.pt'
    '.\训练后模型\yolo26x.pt'
    '.\训练后模型\license_plate_best.pt'
)

$weights | ForEach-Object {
    if (-not (Test-Path -LiteralPath $_ -PathType Leaf)) {
        throw "缺少模型权重：$_"
    }
}

Get-FileHash -Algorithm SHA256 -LiteralPath $weights |
    Format-Table Path, Hash -AutoSize
```

计算结果必须与上表一致。哈希不匹配时不要使用该文件，请重新向项目组获取。

## Git 规则

`.gitignore` 已忽略所有 `*.pt` 文件。不要使用 `git add -f` 强制提交权重；模型更新后应通过项目组约定的文件传输方式重新分发，并同步更新本文档中的文件大小和 SHA-256。
