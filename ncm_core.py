"""离线 NCM 转换内核，Python 3.10+。

格式和密钥调度参考 MIT 项目 ww-rm/ncmdump-py；完整归属见 THIRD_PARTY.md。
采用有界分段读取，避免把整首音乐装入内存；不访问网络，不修改输入文件。
音频解密与界面分离，便于以后接入桌面窗口、命令行或本地 Web 服务。
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import struct
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Any, BinaryIO, Callable

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

CORE_KEY = bytes.fromhex("687a4852416d736f356b496e62617857")
META_KEY = bytes.fromhex("2331346c6a6b5f215c5d2630553c2728")
CHUNK_SIZE = 1024 * 1024


class NCMError(ValueError):
    """文件损坏、格式不支持或解密结果不符合预期时抛出的可读错误。"""


class Cancelled(Exception):
    """用户取消任务；调用者应将其与转换失败分别统计。"""


@dataclass
class ConversionResult:
    """一次转换的可审计结果；warnings 表示音频成功但附加信息不完整。"""

    source: Path
    output: Path
    audio_bytes: int
    warnings: list[str] = field(default_factory=list)


def read_exact(stream: BinaryIO, size: int) -> bytes:
    """读取指定字节数；短读直接报错，避免截断文件被误判为成功。"""
    data = stream.read(size)
    if len(data) != size:
        raise NCMError("文件已截断，数据长度与文件头不符")
    return data


def read_uint(stream: BinaryIO) -> int:
    """读取 NCM 使用的小端无符号 32 位长度。"""
    return struct.unpack("<I", read_exact(stream, 4))[0]


def read_block(stream: BinaryIO, limit: int) -> bytes:
    """读取长度前缀数据，并限制分配量，拒绝异常长度造成的内存耗尽。"""
    size = read_uint(stream)
    if size > limit:
        raise NCMError(f"文件头数据块过大：{size} 字节")
    return read_exact(stream, size)


def aes_unwrap(data: bytes, key: bytes) -> bytes:
    """解开 NCM AES-ECB/PKCS7 数据块；无效块或填充转为统一格式错误。"""
    try:
        decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
        padded = decryptor.update(data) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        return unpadder.update(padded) + unpadder.finalize()
    except ValueError as exc:
        raise NCMError("密钥或元数据块无效，可能损坏或属于不支持的 NCM 版本") from exc


def make_keybox(key: bytes) -> bytes:
    """从文件专属密钥生成 NCM 的 256 字节循环掩码。

    该格式的音频阶段不是标准 RC4；不能直接用常规 RC4 API 替代。
    """
    if not key:
        raise NCMError("文件中的音频密钥为空")
    box = list(range(256))
    j = 0
    for i in range(256):
        j = (j + box[i] + key[i % len(key)]) & 255
        box[i], box[j] = box[j], box[i]
    return bytes(box[(box[(box[(i + 1) & 255] + ((i + 1) & 255)) & 255]
                      + box[(i + 1) & 255]) & 255] for i in range(256))


def transform(data: bytes, keybox: bytes, offset: int) -> bytes:
    """解密一个音频块；offset 是音频流偏移，因此任意分块大小都可连续处理。"""
    # 逐个相位在 C 层 translate，避免每个音频字节执行 Python 循环。
    result = bytearray(data)
    for phase in range(min(256, len(data))):
        value = keybox[(offset + phase) & 255]
        table = bytes(x ^ value for x in range(256))
        result[phase::256] = result[phase::256].translate(table)
    return bytes(result)


def parse_metadata(data: bytes) -> dict[str, Any]:
    """解析普通歌曲或电台元数据；没有数据时返回空字典，不请求在线补全。"""
    if not data:
        return {}
    decoded = bytes(x ^ 0x63 for x in data)
    prefix = b"163 key(Don't modify):"
    if not decoded.startswith(prefix):
        raise NCMError("无法识别元数据前缀")
    raw = aes_unwrap(base64.b64decode(decoded[len(prefix):], validate=True), META_KEY)
    label, separator, body = raw.partition(b":")
    if not separator:
        raise NCMError("元数据缺少类型标记")
    meta = json.loads(body.decode("utf-8"))
    if label == b"dj" and isinstance(meta, dict):
        meta = meta.get("mainMusic", {})
    if not isinstance(meta, dict):
        raise NCMError("元数据不是对象")
    return meta


def sniff_audio(data: bytes) -> str:
    """按解密后的文件签名识别 MP3/FLAC，不盲信元数据里的扩展名。"""
    if data.startswith(b"fLaC"):
        return ".flac"
    if data.startswith(b"ID3"):
        return ".mp3"
    if len(data) >= 4 and data[0] == 255 and data[1] & 0xE0 == 0xE0:
        if data[1] & 0x18 != 0x08 and data[1] & 0x06 and data[2] >> 4 not in (0, 15) and data[2] & 0x0C != 0x0C:
            return ".mp3"
    raise NCMError("解密结果不是可识别的 MP3/FLAC，未输出伪装音频")


def artists_from(meta: dict[str, Any]) -> list[str]:
    """兼容 artist 字符串、字符串数组及 [姓名, ID] 数组，不丢失中文。"""
    values = meta.get("artist", [])
    if isinstance(values, str):
        return [values]
    if not isinstance(values, list):
        return []
    return [v if isinstance(v, str) else str(v[0])
            for v in values if isinstance(v, str) or isinstance(v, list) and v]


def validate_and_tag(path: Path, suffix: str, meta: dict[str, Any], cover: bytes,
                     write_tags: bool, warnings: list[str]) -> None:
    """用 mutagen 检查音频结构并写入内嵌标签。

    mutagen 缺失时仍可还原音频，但会明确记录未校验/未补写标签。
    结构校验失败视为失败；标签写入失败保留成功解密的音频并记录警告。
    """
    try:
        from mutagen.flac import FLAC, Picture
        from mutagen.mp3 import MP3
        from mutagen.id3 import APIC, TALB, TIT2, TPE1
    except ImportError:
        warnings.append("未安装 mutagen：仅还原原始音频，未额外校验或补写标签")
        return
    try:
        audio = FLAC(str(path)) if suffix == ".flac" else MP3(str(path))
        if audio.info.length <= 0:
            raise NCMError("音频时长无效")
    except Exception as exc:
        raise NCMError(f"音频结构校验失败：{exc}") from exc
    if not write_tags:
        return
    try:
        artists = artists_from(meta)
        title, album = meta.get("musicName"), meta.get("album")
        mime = "image/png" if cover.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg" if cover.startswith(b"\xff\xd8\xff") else ""
        if cover and not mime:
            warnings.append("内嵌封面不是 PNG/JPEG，未写入封面")
        if suffix == ".flac":
            for name, value in (("title", title), ("album", album), ("artist", artists)):
                if value:
                    audio[name] = value if isinstance(value, list) else str(value)
            if mime:
                picture = Picture()
                picture.type, picture.mime, picture.data = 3, mime, cover
                audio.clear_pictures()
                audio.add_picture(picture)
        else:
            if audio.tags is None:
                audio.add_tags()
            for cls, value in ((TIT2, title), (TALB, album), (TPE1, artists)):
                if value:
                    audio.tags.add(cls(encoding=3, text=value))
            if mime:
                audio.tags.delall("APIC")
                audio.tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=cover))
        audio.save()
    except Exception as exc:
        warnings.append(f"标签补写未完成：{exc}")


def publish_unique(temp: Path, directory: Path, stem: str, suffix: str) -> Path:
    """发布已完成文件，遇到同名自动编号；从不覆盖已有目标。

    支持硬链接的文件系统采用原子创建；其他文件系统使用独占创建及复制。
    失败只删除本次已独占创建的目标，绝不清理用户原有文件。
    """
    for number in range(100000):
        name = f"{stem}{f' ({number})' if number else ''}{suffix}"
        target = directory / name
        try:
            os.link(temp, target)
            return target
        except FileExistsError:
            continue
        except OSError:
            try:
                output = target.open("xb")
            except FileExistsError:
                continue
            try:
                with output, temp.open("rb") as source:
                    shutil.copyfileobj(source, output)
                return target
            except BaseException:
                target.unlink(missing_ok=True)
                raise
    raise NCMError("同名文件过多，请选择新的输出文件夹")


def convert_file(source: Path, directory: Path, *, write_tags: bool = True,
                 cancel: Event | None = None,
                 progress: Callable[[int, int], None] | None = None) -> ConversionResult:
    """将一个 NCM 转换到独立输出目录，返回路径和警告。

    支持中文/空格路径、分块进度及取消；临时文件在任何异常下都会清理。
    所有输入均只读打开，音频格式按实际签名决定，不进行有损转码。
    """
    source, directory = Path(source), Path(directory)
    warnings: list[str] = []
    temp: Path | None = None
    try:
        with source.open("rb") as stream:
            total_size = os.fstat(stream.fileno()).st_size
            if read_exact(stream, 8) != b"CTENFDAM":
                raise NCMError("不是有效的 NCM 文件（文件头不匹配）")
            read_exact(stream, 2)
            key_raw = aes_unwrap(bytes(x ^ 0x64 for x in read_block(stream, 65536)), CORE_KEY)
            if not key_raw.startswith(b"neteasecloudmusic"):
                raise NCMError("音频密钥标记不匹配")
            keybox = make_keybox(key_raw[17:])
            metadata_block = read_block(stream, 16 * 1024 * 1024)
            try:
                meta = parse_metadata(metadata_block)
            except (ValueError, UnicodeError) as exc:
                meta = {}
                warnings.append(f"歌曲信息无法解析：{exc}")
            read_exact(stream, 5)  # CRC32 与保留字节；不宣称已验证 CRC。
            cover_space, cover_size = read_uint(stream), read_uint(stream)
            if cover_size > cover_space or cover_space > 64 * 1024 * 1024:
                raise NCMError("封面块长度无效或超过 64 MiB")
            cover = read_exact(stream, cover_size)
            # 封面预留空间可能大于图片长度，必须跳过填充后再读音频。
            if stream.tell() + cover_space - cover_size >= total_size:
                raise NCMError("文件中没有完整的音频数据")
            stream.seek(cover_space - cover_size, 1)
            total = total_size - stream.tell()
            directory.mkdir(parents=True, exist_ok=True)
            handle, temp_name = tempfile.mkstemp(prefix=".ncm-", suffix=".part", dir=directory)
            temp = Path(temp_name)
            done, suffix = 0, ""
            with os.fdopen(handle, "wb") as output:
                while data := stream.read(CHUNK_SIZE):
                    if cancel is not None and cancel.is_set():
                        raise Cancelled("用户取消")
                    decoded = transform(data, keybox, done)
                    if not suffix:
                        suffix = sniff_audio(decoded)
                    output.write(decoded)
                    done += len(data)
                    if progress:
                        progress(done, total)
            if done != total or not suffix:
                raise NCMError("音频数据长度发生变化或为空")
        if cancel is not None and cancel.is_set():
            raise Cancelled("用户取消")
        validate_and_tag(temp, suffix, meta, cover, write_tags, warnings)
        if cancel is not None and cancel.is_set():
            raise Cancelled("用户取消")
        target = publish_unique(temp, directory, source.stem, suffix)
        return ConversionResult(source, target, done, warnings)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
