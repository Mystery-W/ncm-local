"""批量调度、日志和 Excel 可读报告，与具体界面解耦。"""

from __future__ import annotations

import csv
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Callable

from ncm_core import Cancelled, convert_file


@dataclass
class BatchSummary:
    """批次统计；取消包括当前未完成项及尚未开始的项。"""

    success: int
    failed: int
    cancelled: int
    report: Path
    log: Path


def collect_files(paths: list[Path], recursive: bool = True) -> list[Path]:
    """展开输入目录，只收集 NCM 并按解析后路径去重；目录读取错误向上传递。"""
    found: dict[Path, None] = {}
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"输入不存在：{path}")
        entries = path.rglob("*") if recursive else path.glob("*")
        candidates = entries if path.is_dir() else [path]
        for item in candidates:
            if item.is_file() and item.suffix.lower() == ".ncm":
                found[item.resolve()] = None
    return sorted(found, key=lambda p: str(p).casefold())


def run_batch(files: list[Path], directory: Path, *, write_tags: bool = True,
              cancel: Event | None = None,
              notify: Callable[[dict], None] | None = None) -> BatchSummary:
    """顺序处理文件并逐项落盘报告；单个文件失败不会中断后续转换。

    notify 接收纯数据事件，GUI 应通过队列转交主线程，避免跨线程访问 Tk。
    报告和日志使用独立批次名，报告采用 utf-8-sig 方便 Windows Excel 打开。
    """
    directory.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
    report, log = directory / f"转换报告-{run_id}.csv", directory / f"转换日志-{run_id}.log"
    logger = logging.getLogger(run_id)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.FileHandler(log, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    cancel = cancel or Event()
    notify = notify or (lambda event: None)
    success = failed = cancelled = 0
    try:
        with report.open("x", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["源文件", "状态", "输出文件", "说明"])
            for index, source in enumerate(files):
                target, detail = "", ""
                if cancel.is_set():
                    status = "已取消"
                    cancelled += 1
                else:
                    notify({"kind": "start", "index": index, "source": str(source)})
                    try:
                        result = convert_file(source, directory, write_tags=write_tags, cancel=cancel,
                            progress=lambda done, total: notify({"kind": "progress", "index": index,
                                                                 "done": done, "total": total}))
                        target, detail = str(result.output), "；".join(result.warnings)
                        status = "成功（有提示）" if detail else "成功"
                        success += 1
                    except Cancelled:
                        status = "已取消"
                        cancelled += 1
                    except Exception as exc:
                        status, detail = "失败", str(exc)
                        failed += 1
                        logger.exception("转换失败：%s", source)
                # Excel 公式前缀转为文本，避免文件名/异常信息被当成公式执行。
                row = [str(source), status, target, detail]
                writer.writerow(["'" + v if v.startswith(("=", "+", "-", "@")) else v for v in row])
                stream.flush()
                logger.info("%s | %s | %s | %s", status, source, target, detail)
                notify({"kind": "result", "index": index, "status": status,
                        "output": target, "detail": detail})
        logger.info("批次完成：成功 %d，失败 %d，取消 %d", success, failed, cancelled)
        return BatchSummary(success, failed, cancelled, report, log)
    finally:
        handler.close()
        logger.removeHandler(handler)
