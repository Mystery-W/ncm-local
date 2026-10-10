# NCM Local · NCM 本地转换器

离线、中文界面的 NCM 批量转换工具。将本地 `.ncm` 文件还原为其中的 MP3 或 FLAC 音频，保留原文件，并为重复文件名自动编号。

**[下载 Windows x64 便携版](https://github.com/Mystery-W/ncm-local/releases/latest)** · [检查记录](VALIDATION.md) · [致谢与第三方许可](THIRD_PARTY.md)

## 致谢

感谢 **[ww-rm](https://github.com/ww-rm)** 开源的 **[ncmdump-py](https://github.com/ww-rm/ncmdump-py)**，本项目的 NCM 格式解析、AES 数据块处理和音频密钥调度参考了其实现。

感谢 **[MengYX](https://github.com/MengYX)** 和 **Unlock Music** 的贡献者，以及 **[Magiicccc/music-batch-unlock](https://github.com/Magiicccc/music-batch-unlock)** 公开的解析代码，为封面预留区处理提供了参考。感谢 Mutagen、cryptography、Python、Tcl/Tk 和 PyInstaller 的开发者。

本项目在这些开源工作的基础上，提供中文窗口、流式处理、独占输出、取消、批次日志和 Windows 打包。上游版权及许可证保留在 [licenses](licenses/)；参考提交和修改说明见 [THIRD_PARTY.md](THIRD_PARTY.md)。

## 使用

1. 在 [Releases](https://github.com/Mystery-W/ncm-local/releases) 下载 Windows 便携包并解压。
2. 双击 `NCM-Local.exe`，添加 NCM 文件或文件夹。
3. 选择输出文件夹，点击“开始转换”。
4. 完成后打开输出文件夹，查看音频、CSV 报告和日志；双击列表项可看完整提示。

打包版无需安装 Python、Conda 或 FFmpeg。v0.1.1 修复了初次打开窗口时“开始转换”被遮挡的问题；列表随窗口伸缩，最小高度适应字体大小。

## 功能和边界

- 文件夹递归导入、去重、逐项状态、失败后继续、取消清理。
- 源文件始终只读，已有输出不覆盖；同名文件增加 `(1)` 等编号。
- 输出保持输入文件名，集中保存到指定目录，不保留原子目录层级。
- 根据音频实际内容输出 MP3 或 FLAC，不进行有损转码，也不会把 MP3 伪装成无损 FLAC。
- 默认补写歌曲名、歌手、专辑与文件内嵌 PNG/JPEG 封面；不会联网补图。
- 取消补写标签后，输出为解密得到的原始音频字节，已有音频标签仍保留。
- 全程离线，无账号登录、音频上传、遥测和自动更新。
- 检查文件签名和音频结构；不校验 NCM CRC，不保证发现所有音频损伤。
- 仅处理 NCM，尚未验证所有新旧格式变体。测试使用自行合成的音频。

## 源码运行

需要 Python 3.10+ 和 Tkinter，依赖见 [requirements.txt](requirements.txt)。优先使用已满足依赖的环境；需要补充依赖时，可使用项目独立虚拟环境，避免改动已有大型 Conda 环境。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

命令行批量转换：

```powershell
python app.py --input 'D:\Music' --output 'D:\Converted'
python app.py --input 'D:\Music\example.ncm' --output 'D:\Converted' --no-tags
```

`--no-recursive` 关闭子目录扫描。单项失败退出码为 1，参数或批次级错误为 2。缺少 Mutagen 时可还原音频，但会提示未进行额外结构检查和标签补写。

## Windows 构建

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1 -Python python
```

也可将 `-Python` 指向选定 Python 的完整路径。脚本在项目内创建 `.build-env`，只读复用所选环境中已有包，并仅向该独立环境安装所需依赖，不修改原 Conda 环境。产物为 `dist/NCM-Local.exe`。

运行版只提供 Windows x64 构建。其他平台需运行源码或在目标平台打包，尚未验证。

## 测试

```powershell
python -m unittest -v
```

测试需 FFmpeg/FFprobe 在 PATH 中；缺少时音频测试会跳过。FFmpeg 仅用于生成合成音频和测试，不是运行版的依赖。测试文件创建在临时目录，不读取个人音乐。

已验证范围和限制见 [VALIDATION.md](VALIDATION.md)。

## 项目结构

| 文件 | 职责 |
|---|---|
| `ncm_core.py` | NCM 解析、解密、标签、进度与安全输出 |
| `batch.py` | 文件收集、批量调度、日志与 CSV 报告 |
| `app.py` | 中文桌面窗口与命令行入口 |
| `test_ncm.py` | 自生成音频的回归测试 |
| `build.ps1` | 独立环境中的 Windows 打包 |

## 许可证与隐私

本项目采用 **GPL-3.0-or-later**，见 [LICENSE](LICENSE)。上游 MIT 许可与第三方许可均单独保留。二进制分发的完整应用源码在同版本标签中，依赖源码位置见 [DEPENDENCY_SOURCES.md](DEPENDENCY_SOURCES.md)。

仓库及发布包不包含个人音乐、转换报告、运行日志、本机截图或账号凭据。请勿在 Issue 中直接上传个人音乐；优先提供错误提示、版本信息和不含个人信息的复现步骤。
