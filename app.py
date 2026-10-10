"""NCM 本地转换器的中文桌面入口与命令行入口。

无参数启动窗口；--input 与 --output 启动可自动化的批量任务。
后台线程只负责转换，通过 Queue 通知界面，保证操作窗口时不会阻塞。
"""

from __future__ import annotations

import argparse
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from batch import collect_files, run_batch


class ConverterApp:
    """单窗口转换界面；任务运行时冻结输入，关闭窗口前先取消并清理任务。"""

    def __init__(self, root: tk.Tk) -> None:
        """构建文件队列、输出设置和状态栏，不在初始化时触碰用户音乐文件。"""
        self.root = root
        self.files: list[Path] = []
        self.events: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.running = False
        self.closing = False
        self.last_output: Path | None = None
        self.output = tk.StringVar(value=str(Path.home() / "Music" / "NCM转换结果"))
        self.status = tk.StringVar(value="添加 .ncm 文件或文件夹，然后选择输出位置。")
        self.tags = tk.BooleanVar(value=True)
        root.title("NCM 本地转换器")
        root.geometry("1020x650")
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Treeview", rowheight=29, font=("Microsoft YaHei UI", 10))
        style.configure("TButton", padding=(12, 7))
        panel = ttk.Frame(root, padding=22)
        panel.pack(fill="both", expand=True)
        # 只有文件列表行参与高度伸缩。pack 按创建顺序分配空间时，列表的
        # 默认请求高度会挤掉最后创建的操作栏；grid 为其他各行先保留高度。
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(3, weight=1)
        ttk.Label(panel, text="NCM 本地转换器", font=("Microsoft YaHei UI", 22, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(panel, text="离线还原 MP3 / FLAC · 保留原文件 · 同名自动编号", padding=(0, 8, 0, 15)).grid(row=1, column=0, sticky="w")
        toolbar = ttk.Frame(panel)
        toolbar.grid(row=2, column=0, sticky="ew")
        self.controls: list[ttk.Widget] = []
        for text, callback in (("添加文件", self.add_files), ("添加文件夹", self.add_folder),
                               ("移除选中", self.remove_selected), ("清空列表", self.clear)):
            button = ttk.Button(toolbar, text=text, command=callback)
            button.pack(side="left", padx=(0, 8))
            self.controls.append(button)
        table_frame = ttk.Frame(panel)
        table_frame.grid(row=3, column=0, sticky="nsew", pady=15)
        self.table = ttk.Treeview(table_frame, columns=("file", "status", "detail"), show="headings")
        for column, label, width in (("file", "输入文件", 440), ("status", "状态", 110), ("detail", "输出 / 提示", 340)):
            self.table.heading(column, text=label)
            self.table.column(column, width=width, minwidth=80)
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.table.bind("<Double-1>", self.show_detail)
        settings = ttk.Frame(panel)
        settings.grid(row=4, column=0, sticky="ew")
        ttk.Label(settings, text="输出文件夹").pack(side="left", padx=(0, 10))
        entry = ttk.Entry(settings, textvariable=self.output)
        entry.pack(side="left", fill="x", expand=True)
        browse = ttk.Button(settings, text="选择…", command=self.choose_output)
        browse.pack(side="left", padx=(8, 0))
        self.controls.extend([entry, browse])
        tags = ttk.Checkbutton(panel, text="补写歌曲名、歌手、专辑及内嵌封面（不联网补图）", variable=self.tags)
        tags.grid(row=5, column=0, sticky="w", pady=10)
        self.controls.append(tags)
        self.progress = ttk.Progressbar(panel, maximum=100)
        self.progress.grid(row=6, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(panel, textvariable=self.status, wraplength=700).grid(row=7, column=0, sticky="w")
        actions = ttk.Frame(panel)
        actions.grid(row=8, column=0, sticky="ew", pady=(12, 0))
        self.start_button = ttk.Button(actions, text="开始转换", command=self.start)
        self.start_button.pack(side="left")
        self.controls.append(self.start_button)
        self.cancel_button = ttk.Button(actions, text="取消", command=self.request_cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=8)
        ttk.Button(actions, text="打开输出文件夹", command=self.open_output).pack(side="right")
        ttk.Label(actions, text="双击列表项查看完整路径和提示").pack(side="right", padx=12)
        # Windows 字体缩放会增加固定区域高度。根据实际请求尺寸确定下限，
        # 为列表额外保留 120 像素，避免高缩放时按钮可见但列表完全消失。
        root.update_idletasks()
        minimum_height = max(500, root.winfo_reqheight() - table_frame.winfo_reqheight() + 120)
        root.minsize(780, minimum_height)
        root.geometry(f"1020x{max(650, minimum_height)}")
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self.poll)

    def add_paths(self, paths: list[Path]) -> None:
        """收集并去重输入，文件夹默认递归；读取失败以对话框说明原因。"""
        try:
            incoming = collect_files(paths)
            for path in incoming:
                if path not in self.files:
                    self.files.append(path)
            self.refresh()
            self.status.set(f"共 {len(self.files)} 个 NCM 文件；文件夹包含子文件夹。")
        except OSError as exc:
            messagebox.showerror("无法读取输入", str(exc), parent=self.root)

    def add_files(self) -> None:
        """打开多文件选择框；取消选择不改变队列。"""
        selected = filedialog.askopenfilenames(parent=self.root, title="选择 NCM 文件",
                                              filetypes=[("NCM 音乐", "*.ncm"), ("所有文件", "*.*")])
        if selected:
            self.add_paths([Path(p) for p in selected])

    def add_folder(self) -> None:
        """选择目录并递归导入 NCM，不修改其中任何文件。"""
        selected = filedialog.askdirectory(parent=self.root, title="选择包含 NCM 的文件夹")
        if selected:
            self.add_paths([Path(selected)])

    def refresh(self) -> None:
        """按当前文件列表重建显示行，行 ID 对应固定批次索引。"""
        self.table.delete(*self.table.get_children())
        for index, path in enumerate(self.files):
            self.table.insert("", "end", iid=str(index), values=(str(path), "等待", ""))

    def remove_selected(self) -> None:
        """只移除队列条目，不删除磁盘文件。"""
        selected = {int(item) for item in self.table.selection()}
        self.files = [path for index, path in enumerate(self.files) if index not in selected]
        self.refresh()

    def clear(self) -> None:
        """清空待处理列表，输出文件及源文件均保留。"""
        self.files.clear()
        self.refresh()
        self.status.set("列表已清空。")

    def choose_output(self) -> None:
        """让用户选择输出位置；不存在的子目录也可直接在输入框填写。"""
        selected = filedialog.askdirectory(parent=self.root, title="选择输出文件夹")
        if selected:
            self.output.set(selected)

    def start(self) -> None:
        """校验界面参数后启动后台批次；工作线程不直接访问任何 Tk 变量。"""
        if not self.files:
            messagebox.showinfo("尚未添加文件", "请先添加 NCM 文件或文件夹。", parent=self.root)
            return
        if not self.output.get().strip():
            messagebox.showerror("缺少输出位置", "请选择输出文件夹。", parent=self.root)
            return
        directory = Path(self.output.get().strip()).expanduser().resolve()
        files, tags = self.files.copy(), self.tags.get()
        self.last_output = directory
        self.cancel.clear()
        self.running = True
        self.refresh()
        self.progress["value"] = 0
        for control in self.controls:
            control.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.status.set("正在开始转换…")
        threading.Thread(target=self.worker, args=(files, directory, tags), daemon=True).start()

    def worker(self, files: list[Path], directory: Path, tags: bool) -> None:
        """后台入口：批次级错误（例如目录不可写）也必须回送界面。"""
        try:
            summary = run_batch(files, directory, write_tags=tags, cancel=self.cancel, notify=self.events.put)
            self.events.put({"kind": "complete", "summary": summary})
        except Exception as exc:
            self.events.put({"kind": "fatal", "detail": str(exc)})

    def poll(self) -> None:
        """主线程消费进度事件；完成时恢复控件，关闭请求则在清理后退出。"""
        try:
            while True:
                event = self.events.get_nowait()
                kind = event["kind"]
                if kind == "start":
                    self.table.set(str(event["index"]), "status", "转换中")
                    self.status.set(f"{event['index'] + 1}/{len(self.files)}：{Path(event['source']).name}")
                elif kind == "progress":
                    self.progress["value"] = (event["index"] + event["done"] / event["total"]) / len(self.files) * 100
                elif kind == "result":
                    self.table.set(str(event["index"]), "status", event["status"])
                    self.table.set(str(event["index"]), "detail", event["output"] + (" | " if event["output"] and event["detail"] else "") + event["detail"])
                elif kind in ("complete", "fatal"):
                    self.running = False
                    for control in self.controls:
                        control.configure(state="normal")
                    self.cancel_button.configure(state="disabled")
                    if kind == "complete":
                        summary = event["summary"]
                        self.progress["value"] = 100 if not summary.cancelled else self.progress["value"]
                        self.status.set(f"完成：成功 {summary.success}，失败 {summary.failed}，取消 {summary.cancelled}。报告：{summary.report.name}")
                    else:
                        self.status.set("任务未完成：" + event["detail"])
                    if self.closing:
                        self.root.destroy()
                        return
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def request_cancel(self) -> None:
        """请求在当前安全检查点终止，已经成功输出的文件保留。"""
        self.cancel.set()
        self.status.set("正在取消并清理临时文件…")
        self.cancel_button.configure(state="disabled")

    def on_close(self) -> None:
        """运行中关闭时等待任务清理，避免残留半写入文件。"""
        if self.running:
            self.closing = True
            self.request_cancel()
        else:
            self.root.destroy()

    def show_detail(self, event: tk.Event | None = None) -> None:
        """显示被列宽截断的完整信息，便于定位失败原因。"""
        selection = self.table.selection()
        if selection:
            values = self.table.item(selection[0], "values")
            messagebox.showinfo("任务详情", "\n\n".join(values), parent=self.root)

    def open_output(self) -> None:
        """使用系统文件管理器打开输出目录，采用参数列表避免 shell 注入。"""
        path = self.last_output or Path(self.output.get()).expanduser()
        if not path.is_dir():
            messagebox.showinfo("文件夹不存在", "转换成功后会自动创建输出文件夹。", parent=self.root)
            return
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])


def main() -> None:
    """解析命令行；无输入时启动窗口，批次失败时返回非零退出码。"""
    parser = argparse.ArgumentParser(description="离线批量还原 NCM 为原始 MP3/FLAC")
    parser.add_argument("--input", nargs="+", type=Path, help="NCM 文件或目录，可提供多个")
    parser.add_argument("--output", type=Path, help="输出目录（命令行模式必填）")
    parser.add_argument("--no-tags", action="store_true", help="只还原原始音频，不补写标签")
    parser.add_argument("--no-recursive", action="store_true", help="不扫描子目录")
    args = parser.parse_args()
    if args.input:
        if not args.output:
            parser.error("命令行模式必须指定 --output")
        try:
            files = collect_files(args.input, not args.no_recursive)
            if not files:
                parser.error("未找到 NCM 文件")
            summary = run_batch(files, args.output, write_tags=not args.no_tags)
            print(f"成功 {summary.success}，失败 {summary.failed}，取消 {summary.cancelled}")
            print(f"输出：{args.output.resolve()}\n报告：{summary.report.resolve()}")
            raise SystemExit(1 if summary.failed else 0)
        except (OSError, ValueError) as exc:
            parser.exit(2, f"无法完成：{exc}\n")
    root = tk.Tk()
    ConverterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
