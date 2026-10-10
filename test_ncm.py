"""用自行合成的音频验证转换，不分发真实音乐或用户数据。

执行：python -m unittest -v。测试音频由 ffmpeg 生成，需要 ffmpeg/ffprobe 在 PATH。
夹具使用独立的直接公式封装 NCM，检查分块、封面填充、损坏输入及安全输出。
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from threading import Event

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from mutagen.flac import FLAC
from mutagen.mp3 import MP3

from batch import collect_files, run_batch
from ncm_core import Cancelled, NCMError, convert_file, transform


def encrypt_aes(data: bytes, key: bytes) -> bytes:
    """测试夹具专用 AES 包装；与生产解密函数互不调用。"""
    padder = padding.PKCS7(128).padder()
    padded = padder.update(data) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def make_fixture(audio: bytes, *, cover_padding: int = 0, metadata: bool = True,
                 broken_metadata: bool = False, cover: bytes = b"") -> bytes:
    """以固定测试密钥封装已知音频，元数据故意写错 format 以检查实际签名识别。"""
    key = b"local-test-key-2026"
    encrypted_key = encrypt_aes(b"neteasecloudmusic" + key, bytes.fromhex("687a4852416d736f356b496e62617857"))
    key_block = bytes(value ^ 0x64 for value in encrypted_key)
    meta = {"musicName": "测试歌曲", "artist": [["测试歌手", 1]], "album": "离线测试", "format": "wrong"}
    meta_cipher = encrypt_aes(b"music:" + json.dumps(meta, ensure_ascii=False).encode("utf-8"),
                             bytes.fromhex("2331346c6a6b5f215c5d2630553c2728"))
    meta_block = bytes(value ^ 0x63 for value in b"163 key(Don't modify):" + base64.b64encode(meta_cipher)) if metadata else b""
    if broken_metadata:
        meta_block = b"bad-meta"
    box = list(range(256))
    last = 0
    for i in range(256):
        last = (box[i] + last + key[i % len(key)]) % 256
        box[i], box[last] = box[last], box[i]
    encrypted_audio = bytearray()
    for position, value in enumerate(audio):
        j = (position + 1) % 256
        mask = box[(box[j] + box[(box[j] + j) % 256]) % 256]
        encrypted_audio.append(value ^ mask)
    return (b"CTENFDAM\x00\x00" + struct.pack("<I", len(key_block)) + key_block
            + struct.pack("<I", len(meta_block)) + meta_block + b"\x00" * 5
            + struct.pack("<II", len(cover) + cover_padding, len(cover))
            + cover + b"\x00" * cover_padding + encrypted_audio)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "需要 ffmpeg 与 ffprobe")
class ConversionTests(unittest.TestCase):
    """合成音频端到端回归；所有文件仅写入系统临时测试目录。"""

    @classmethod
    def setUpClass(cls) -> None:
        """生成 MP3 与超过一个流式块大小的 FLAC，以检验跨块密钥偏移。"""
        cls.temp = tempfile.TemporaryDirectory(prefix="NCM 测试 ")
        cls.root = Path(cls.temp.name)
        cls.flac = cls.root / "原始.flac"
        cls.mp3 = cls.root / "原始.mp3"
        for path, source in ((cls.flac, "anoisesrc=duration=10:sample_rate=44100:seed=42"),
                             (cls.mp3, "sine=frequency=440:duration=1")):
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", source, str(path)], check=True)
        cls.flac_ncm = make_fixture(cls.flac.read_bytes(), cover_padding=123)
        cls.mp3_ncm = make_fixture(cls.mp3.read_bytes())

    @classmethod
    def tearDownClass(cls) -> None:
        """清理本测试类自己创建的临时目录，不接触任何用户音乐。"""
        cls.temp.cleanup()

    def setUp(self) -> None:
        """每个案例单独目录，便于核查残留临时文件与同名文件行为。"""
        self.directory = self.root / self._testMethodName
        self.directory.mkdir()
        self.source = self.directory / "中文 歌曲.ncm"
        self.source.write_bytes(self.flac_ncm)
        self.output = self.directory / "转换 结果"

    def test_flac_byte_identity_and_source_preservation(self) -> None:
        """关闭标签补写后应逐字节恢复源 FLAC，并且 NCM 的哈希不变。"""
        before = hashlib.sha256(self.source.read_bytes()).digest()
        result = convert_file(self.source, self.output, write_tags=False)
        self.assertGreater(self.flac.stat().st_size, 1024 * 1024)
        self.assertEqual(result.output.read_bytes(), self.flac.read_bytes())
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).digest(), before)
        self.assertFalse(list(self.output.glob("*.part")))

    def test_mp3_byte_identity(self) -> None:
        """MP3 自动按实际内容输出，不受错误 format 元数据影响。"""
        self.source.write_bytes(self.mp3_ncm)
        result = convert_file(self.source, self.output, write_tags=False)
        self.assertEqual(result.output.suffix, ".mp3")
        self.assertEqual(result.output.read_bytes(), self.mp3.read_bytes())

    def test_tags_and_decoded_pcm_identity(self) -> None:
        """写入中文标签后，FFmpeg 解码的 PCM 仍应与原音频完全一致。"""
        result = convert_file(self.source, self.output)
        self.assertEqual(FLAC(result.output)["title"], ["测试歌曲"])
        def pcm(path: Path) -> bytes:
            """解码为统一 s16le PCM，以排除标签和封装差异。"""
            return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-"],
                                  check=True, capture_output=True).stdout
        self.assertEqual(pcm(result.output), pcm(self.flac))

    def test_mp3_tags(self) -> None:
        """MP3 的 ID3 标签能正确保存中文标题。"""
        self.source.write_bytes(self.mp3_ncm)
        result = convert_file(self.source, self.output)
        self.assertEqual(MP3(result.output).tags["TIT2"].text, ["测试歌曲"])

    def test_embedded_cover_with_padding(self) -> None:
        """内嵌 PNG 后存在填充时，MP3/FLAC 都应保留图片且音频仍可解析。"""
        cover = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1S8AAAAASUVORK5CYII=")
        for path in (self.mp3, self.flac):
            self.source.write_bytes(make_fixture(path.read_bytes(), cover=cover, cover_padding=257))
            result = convert_file(self.source, self.output)
            if result.output.suffix == ".flac":
                actual = FLAC(result.output).pictures[0].data
            else:
                actual = MP3(result.output).tags.getall("APIC")[0].data
            self.assertEqual(actual, cover)

    def test_oversized_block_is_rejected(self) -> None:
        """伪造的巨大长度在分配前即应被拒绝。"""
        self.source.write_bytes(b"CTENFDAM\x00\x00" + struct.pack("<I", 0xFFFFFFFF))
        with self.assertRaises(NCMError):
            convert_file(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_duplicate_does_not_overwrite(self) -> None:
        """重复转换自动编号，原有同名输出保留原始字节。"""
        self.output.mkdir()
        existing = self.output / "中文 歌曲.flac"
        existing.write_bytes(b"keep-me")
        result = convert_file(self.source, self.output)
        self.assertEqual(result.output.name, "中文 歌曲 (1).flac")
        self.assertEqual(existing.read_bytes(), b"keep-me")

    def test_cancel_cleans_partial_output(self) -> None:
        """中途取消必须清理部分音频，且不能发布成功文件。"""
        cancel = Event()
        with self.assertRaises(Cancelled):
            convert_file(self.source, self.output, cancel=cancel, progress=lambda *_: cancel.set())
        self.assertFalse(list(self.output.iterdir()))

    def test_invalid_and_truncated_inputs(self) -> None:
        """错误魔数和截断密钥块均应失败，不生成假 MP3。"""
        for data in (b"NOT-NCM!", self.flac_ncm[:20]):
            self.source.write_bytes(data)
            with self.assertRaises(NCMError):
                convert_file(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_missing_and_broken_metadata(self) -> None:
        """可选元数据损坏不影响已能正确解密的音频，并记录提示。"""
        for broken in (False, True):
            self.source.write_bytes(make_fixture(self.mp3.read_bytes(), metadata=False, broken_metadata=broken))
            result = convert_file(self.source, self.output, write_tags=False)
            self.assertEqual(result.output.read_bytes(), self.mp3.read_bytes())
            self.assertEqual(bool(result.warnings), broken)

    def test_batch_continues_and_report_has_bom(self) -> None:
        """单项失败继续队列，报告为 Excel 可直接打开的 UTF-8 BOM 格式。"""
        bad = self.directory / "损坏.NCM"
        bad.write_bytes(b"broken")
        self.assertEqual(len(collect_files([self.directory, self.source])), 2)
        result = run_batch([bad, self.source], self.output)
        self.assertEqual((result.success, result.failed), (1, 1))
        self.assertTrue(result.report.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_arbitrary_chunk_offset(self) -> None:
        """任意非 256 倍数分块也应使用正确密钥相位。"""
        data, mask = bytes(range(256)) * 8, bytes(reversed(range(256)))
        expected = bytes(value ^ mask[i % 256] for i, value in enumerate(data))
        actual = transform(data[:357], mask, 0) + transform(data[357:], mask, 357)
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
