# 致谢、来源与第三方组件

感谢原作者分享格式研究和实现，使本地离线工具能够继续改进。

## NCM 格式与核心算法

### ww-rm / ncmdump-py

- 作者：[ww-rm](https://github.com/ww-rm)
- 仓库：[ncmdump-py](https://github.com/ww-rm/ncmdump-py)
- 参考提交：[`b7b33ce973eef2cdb57e3220d2a43f4f908f598c`](https://github.com/ww-rm/ncmdump-py/tree/b7b33ce973eef2cdb57e3220d2a43f4f908f598c)
- 参考文件：`ncmdump/core.py`、`ncmdump/crypto.py`
- Copyright (c) 2023 ww-rm；[原始 MIT 许可](licenses/ncmdump-py-MIT.txt)。

本项目参考其 NCM 密钥块、元数据块和音频密钥调度，并做了以下改写：使用 cryptography；有界分段读取；长度校验；取消与独占输出；去除封面网络请求；增加中文窗口、命令行和批次报告。

### Unlock Music 与 music-batch-unlock

- 感谢 MengYX 和 Unlock Music 项目贡献者。
- 参考代码：[Magiicccc/music-batch-unlock 的 ncm.ts](https://github.com/Magiicccc/music-batch-unlock/blob/main/src/decrypt/ncm.ts)。
- 参考范围：封面预留空间可能大于图片长度，音频起始偏移需要跳过整个预留区。
- 未复制该项目的前端界面代码。
- Copyright (c) 2019-2025 MengYX；[保留的 MIT 许可](licenses/unlock-music-MIT.txt)。

## 运行和打包组件

| 组件 | 本版构建版本 | 许可与作用 |
|---|---|---|
| [cryptography](https://github.com/pyca/cryptography) | 44.0.1 | Apache-2.0 / BSD-3-Clause；AES 处理 |
| [Mutagen](https://github.com/quodlibet/mutagen) | 1.47.0 | GPL-2.0-or-later；音频结构和标签 |
| [Python](https://www.python.org/) | 3.11.11 | Python 许可；运行时 |
| [Tcl/Tk](https://www.tcl.tk/) | 8.6 系列 | Tcl/Tk 许可；桌面窗口 |
| [PyInstaller](https://pyinstaller.org/) | 6.16.0 | GPL 及 bootloader 分发例外；打包工具 |
| [OpenSSL](https://www.openssl.org/) | 随构建运行时 | OpenSSL 许可；cryptography 的底层组件 |
| CFFI / pycparser | 随构建环境 | 相关许可保留于 licenses 目录 |

许可证文本见 [licenses](licenses/)，对应依赖源码获取方式见 [DEPENDENCY_SOURCES.md](DEPENDENCY_SOURCES.md)。重新构建时请核对实际使用版本，并继续保留上游声明。

应用采用 GPL-3.0-or-later；上游 MIT 代码的原有版权和许可不因此消失。
