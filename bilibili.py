import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox, ttk
import os
import sys
import subprocess
import json
import threading
import re
import tempfile # 用于临时解码M4S文件

# --- FFmpeg 路径检测逻辑 (支持PyInstaller打包) ---
def get_ffmpeg_executable_path():
    ffmpeg_filename = "ffmpeg.exe" if os.name == 'nt' else "ffmpeg"
    local_ffmpeg_path_options = []

    if getattr(sys, 'frozen', False):
        # 运行在PyInstaller打包的exe中
        application_path = sys._MEIPASS  # PyInstaller临时解压目录
        local_ffmpeg_path_options = [
            # 对应 --add-data="ffmpeg/bin:ffmpeg/bin" 的路径结构
            os.path.join(application_path, "ffmpeg", "bin", ffmpeg_filename),
            # 其他可能的路径结构
            os.path.join(application_path, "ffmpeg_bin", "bin", ffmpeg_filename),
            os.path.join(application_path, "ffmpeg_bin", ffmpeg_filename),
            os.path.join(application_path, ffmpeg_filename),
            # 如果用户使用了不同的add-data路径
            os.path.join(application_path, "bin", ffmpeg_filename),
        ]
        print(f"[DEBUG] 运行在打包环境中，临时目录: {application_path}")
        print(f"[DEBUG] 尝试查找FFmpeg路径: {local_ffmpeg_path_options}")
    else:
        # 运行在开发环境中
        script_dir = os.path.dirname(os.path.abspath(__file__))
        local_ffmpeg_path_options = [
            os.path.join(script_dir, "ffmpeg", "bin", ffmpeg_filename),
            os.path.join(script_dir, "ffmpeg-7.1.1-full_build", "bin", ffmpeg_filename),
            os.path.join(script_dir, "ffmpeg_bin", "bin", ffmpeg_filename),
            os.path.join(script_dir, "ffmpeg_bin", ffmpeg_filename),
            os.path.join(script_dir, ffmpeg_filename)
        ]
        print(f"[DEBUG] 运行在开发环境中，脚本目录: {script_dir}")
        print(f"[DEBUG] 尝试查找FFmpeg路径: {local_ffmpeg_path_options}")

    # 逐一检查路径
    for path_option in local_ffmpeg_path_options:
        print(f"[DEBUG] 检查路径: {path_option}")
        if os.path.exists(path_option) and os.access(path_option, os.X_OK):
            print(f"[DEBUG] 找到FFmpeg: {path_option}")
            return path_option
        else:
            print(f"[DEBUG] 路径不存在或不可执行: {path_option}")

    # 如果本地路径都找不到，尝试系统PATH
    try:
        print(f"[DEBUG] 尝试从系统PATH查找FFmpeg")
        subprocess.run([ffmpeg_filename, "-version"], capture_output=True, check=True, shell=(os.name == 'nt'))
        print(f"[DEBUG] 在系统PATH中找到FFmpeg")
        return ffmpeg_filename
    except (subprocess.CalledProcessError, FileNotFoundError):
        print(f"[DEBUG] 系统PATH中未找到FFmpeg")
        return None

FFMPEG_EXECUTABLE = get_ffmpeg_executable_path()

# --- M4S解码函数 (仅用于电脑端) ---
def decode_bilibili_m4s(input_path, output_path, log_callback=None):
    """
    解码/修复Bilibili M4S文件，通过移除/修改其自定义头部。
    此函数主要用于处理电脑端下载的M4S文件。
    """
    try:
        if log_callback: log_callback(f"开始解码M4S文件: {os.path.basename(input_path)} -> {os.path.basename(output_path)}", "info")
        with open(input_path, 'rb') as fin, open(output_path, 'wb') as fout:
            first_chunk_processed = False
            while True:
                if not first_chunk_processed:
                    buffer = fin.read(1024)
                    if not buffer:
                        break

                    data = bytearray(buffer)
                    # 1. 移除前9个字节
                    if len(data) >= 9:
                        del data[0:9]
                    else:
                        if log_callback: log_callback(f"警告: M4S文件 {os.path.basename(input_path)} 头部过短", "warning")
                        fout.write(data)
                        first_chunk_processed = True
                        buffer = fin.read(4096)
                        if not buffer: break
                        data = bytearray(buffer)

                    # 2. 修改新索引3处的字节
                    if len(data) > 3:
                        data[3] = 0x20
                    else:
                        if log_callback: log_callback(f"警告: M4S文件 {os.path.basename(input_path)} 头部在修改第3字节时过短", "warning")

                    # 3. 检查"iso5"品牌
                    if len(data) >= 20:
                        if not (data[16] == 0x69 and data[17] == 0x73 and data[18] == 0x6f and data[19] == 0x35):
                            if log_callback: log_callback(f"信息: M4S文件 {os.path.basename(input_path)} 在偏移量16处未找到 'iso5' 标记，移除4字节", "info")
                            del data[16:20]
                        else:
                            if log_callback: log_callback(f"信息: M4S文件 {os.path.basename(input_path)} 在偏移量16处找到 'iso5' 标记", "info")
                    elif len(data) > 16:
                        if log_callback: log_callback(f"警告: M4S文件 {os.path.basename(input_path)} 头部在检查 'iso5' 标记时过短", "warning")

                    fout.write(data)
                    first_chunk_processed = True
                else:
                    buffer = fin.read(4096)
                    if not buffer:
                        break
                    fout.write(buffer)
        if log_callback: log_callback(f"M4S文件解码完成: {os.path.basename(output_path)}", "success")
        return True
    except Exception as e:
        if log_callback: log_callback(f"错误: 解码M4S文件 {os.path.basename(input_path)} 失败: {e}", "error")
        return False

def detect_folder_structure(folder_path):
    """
    检测文件夹结构类型。
    返回: ('pc', folder_path, json_path) 或 ('mobile', actual_media_folder_path, json_path) 或 ('unknown', None, None)
    """
    try:
        items = os.listdir(folder_path)

        # 检查是否为电脑端结构（直接包含videoInfo.json）
        if 'videoInfo.json' in items:
            return ('pc', folder_path, os.path.join(folder_path, 'videoInfo.json'))

        # 检查是否为手机端结构（包含c_开头的文件夹）
        c_folders = [item for item in items if os.path.isdir(os.path.join(folder_path, item)) and item.startswith('c_')]

        if c_folders:
            # 找到c_文件夹，检查其内部结构
            # 通常只有一个c_开头的文件夹，取第一个
            c_folder_path = os.path.join(folder_path, c_folders[0])
            c_items = os.listdir(c_folder_path)

            # 查找entry.json
            entry_json_path = os.path.join(c_folder_path, 'entry.json')
            if not os.path.exists(entry_json_path):
                return ('unknown', None, None)

            # 查找包含M4S文件的数字文件夹
            for item in c_items:
                item_path = os.path.join(c_folder_path, item)
                if os.path.isdir(item_path):
                    # 检查这个文件夹是否包含M4S文件
                    try:
                        sub_items = os.listdir(item_path)
                        m4s_files = [f for f in sub_items if f.lower().endswith('.m4s')]
                        if len(m4s_files) >= 2:  # 至少要有2个M4S文件（音频+视频）
                            return ('mobile', item_path, entry_json_path)
                    except Exception as e:
                        print(f"[DEBUG] 扫描子文件夹 {item_path} 失败: {e}")
                        continue

        return ('unknown', None, None)
    except Exception as e:
        print(f"[DEBUG] 检测文件夹结构失败: {e}")
        return ('unknown', None, None)

class FolderSelectionDialog:
    """自定义文件夹多选对话框"""
    def __init__(self, parent, title="选择文件夹"):
        self.result = []
        self.parent = parent

        # 创建对话框窗口
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(title)
        self.dialog.geometry("600x500")
        self.dialog.transient(parent)
        self.dialog.grab_set()

        # 居中显示
        self.dialog.geometry("+%d+%d" % (parent.winfo_rootx() + 50, parent.winfo_rooty() + 50))

        self.create_widgets()

    def create_widgets(self):
        # 顶部说明
        instruction_frame = ttk.Frame(self.dialog)
        instruction_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(instruction_frame, text="选择一个父文件夹，然后勾选要处理的子文件夹：").pack(anchor="w")

        # 父文件夹选择
        parent_frame = ttk.Frame(self.dialog)
        parent_frame.pack(fill="x", padx=10, pady=5)

        ttk.Button(parent_frame, text="选择父文件夹", command=self.select_parent_folder).pack(side="left")
        self.parent_folder_var = tk.StringVar(value="请选择父文件夹...")
        ttk.Label(parent_frame, textvariable=self.parent_folder_var, foreground="blue").pack(side="left", padx=(10, 0))

        # 子文件夹列表
        list_frame = ttk.LabelFrame(self.dialog, text="子文件夹 (勾选要处理的)", padding=5)
        list_frame.pack(fill="both", expand=True, padx=10, pady=5)

        # 创建带滚动条的框架
        canvas = tk.Canvas(list_frame)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        self.scrollable_frame = ttk.Frame(canvas)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.folder_vars = {}  # 存储每个文件夹的选择状态

        # 底部按钮
        button_frame = ttk.Frame(self.dialog)
        button_frame.pack(fill="x", padx=10, pady=10)

        ttk.Button(button_frame, text="全选", command=self.select_all).pack(side="left", padx=5)
        ttk.Button(button_frame, text="全不选", command=self.select_none).pack(side="left", padx=5)
        ttk.Button(button_frame, text="确定", command=self.ok_clicked).pack(side="right", padx=5)
        ttk.Button(button_frame, text="取消", command=self.cancel_clicked).pack(side="right")

    def select_parent_folder(self):
        folder = filedialog.askdirectory(title="选择包含M4S文件夹的父目录")
        if folder:
            self.parent_folder_var.set(folder)
            self.scan_subfolders(folder)

    def scan_subfolders(self, parent_folder):
        # 清空现有列表
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()
        self.folder_vars.clear()

        try:
            subfolders = []
            for item in os.listdir(parent_folder):
                item_path = os.path.join(parent_folder, item)
                if os.path.isdir(item_path):
                    # 检测文件夹结构
                    structure_type, media_folder, json_path = detect_folder_structure(item_path)

                    if structure_type in ['pc', 'mobile']:
                        # 尝试获取标题
                        title = self.get_folder_title(json_path, structure_type)
                        subfolders.append((item, item_path, title, structure_type))

            if not subfolders:
                ttk.Label(self.scrollable_frame, text="未找到包含M4S文件的子文件夹", foreground="red").pack(pady=20)
                return

            # 按文件夹名排序
            subfolders.sort(key=lambda x: x[0])

            for folder_name, folder_path, title, structure_type in subfolders:
                var = tk.BooleanVar()
                self.folder_vars[folder_path] = var

                frame = ttk.Frame(self.scrollable_frame)
                frame.pack(fill="x", padx=5, pady=2)

                cb = ttk.Checkbutton(frame, variable=var)
                cb.pack(side="left")

                # 显示结构类型标识
                type_label = "📱" if structure_type == 'mobile' else "💻"
                display_text = f"{type_label} {folder_name} - {title}" if title != "未知标题" else f"{type_label} {folder_name}"
                label = ttk.Label(frame, text=display_text)
                label.pack(side="left", padx=(5, 0))

        except Exception as e:
            ttk.Label(self.scrollable_frame, text=f"扫描文件夹时出错: {str(e)}", foreground="red").pack(pady=20)

    def get_folder_title(self, json_path, structure_type):
        """获取文件夹的视频标题"""
        if not os.path.exists(json_path):
            return "未知标题"

        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            title = ""
            if structure_type == 'pc':
                # 电脑端结构的JSON字段
                title = data.get('title', '')
                if not title and 'videoData' in data:
                    title = data['videoData'].get('title', '')
                if not title and 'page_data' in data:
                    title = data['page_data'].get('part', '')
                if not title and 'videoName' in data:
                    title = data.get('videoName', '')
            elif structure_type == 'mobile':
                # 手机端结构的JSON字段（entry.json）
                title = data.get('title', '')
                if not title and 'page_data' in data:
                    title = data['page_data'].get('part', '')

            if title:
                return title[:50]  # 限制长度
        except Exception as e:
            print(f"[DEBUG] 读取JSON失败: {e}")
            pass
        return "未知标题"

    def select_all(self):
        for var in self.folder_vars.values():
            var.set(True)

    def select_none(self):
        for var in self.folder_vars.values():
            var.set(False)

    def ok_clicked(self):
        self.result = [path for path, var in self.folder_vars.items() if var.get()]
        self.dialog.destroy()

    def cancel_clicked(self):
        self.result = []
        self.dialog.destroy()

class M4SConverterApp:
    def __init__(self, master):
        self.master = master
        master.title("M4S 批量转换工具 (支持手机端结构)")
        master.geometry("700x550")

        self.input_folders = []
        self.output_folder = tk.StringVar()

        style = ttk.Style()
        style.theme_use('clam')

        # 源文件夹选择区域
        input_frame = ttk.LabelFrame(master, text="源文件夹选择", padding=(10, 5))
        input_frame.pack(padx=10, pady=10, fill="x")

        # 按钮行
        button_row = ttk.Frame(input_frame)
        button_row.pack(fill="x", pady=(0, 5))

        self.add_input_button = ttk.Button(button_row, text="添加文件夹", command=self.add_input_folder)
        self.add_input_button.pack(side="left", padx=5)

        self.batch_add_button = ttk.Button(button_row, text="批量添加", command=self.batch_add_folders)
        self.batch_add_button.pack(side="left", padx=5)

        self.remove_input_button = ttk.Button(button_row, text="移除选中", command=self.remove_selected_folder)
        self.remove_input_button.pack(side="left", padx=5)

        self.clear_all_button = ttk.Button(button_row, text="清空列表", command=self.clear_all_folders)
        self.clear_all_button.pack(side="left", padx=5)

        # 文件夹列表
        self.input_listbox_frame = ttk.Frame(input_frame)
        self.input_listbox_frame.pack(fill="both", expand=True, padx=5, pady=5)

        self.input_listbox_scrollbar = ttk.Scrollbar(self.input_listbox_frame, orient="vertical")
        self.input_listbox = tk.Listbox(self.input_listbox_frame, selectmode=tk.EXTENDED, height=6, yscrollcommand=self.input_listbox_scrollbar.set)
        self.input_listbox_scrollbar.config(command=self.input_listbox.yview)

        self.input_listbox_scrollbar.pack(side="right", fill="y")
        self.input_listbox.pack(side="left", fill="both", expand=True)

        # 输出文件夹选择
        output_frame = ttk.LabelFrame(master, text="输出文件夹选择", padding=(10, 5))
        output_frame.pack(padx=10, pady=5, fill="x")

        self.select_output_button = ttk.Button(output_frame, text="选择输出文件夹", command=self.select_output_folder)
        self.select_output_button.pack(side="left", padx=5, pady=5)

        self.output_folder_label = ttk.Label(output_frame, textvariable=self.output_folder, wraplength=450)
        self.output_folder_label.pack(side="left", padx=5, pady=5, fill="x", expand=True)
        self.output_folder.set("尚未选择输出文件夹")

        # 控制区域
        control_frame = ttk.Frame(master, padding=(10,5))
        control_frame.pack(padx=10, pady=5, fill="x")

        self.start_button = ttk.Button(control_frame, text="开始处理", command=self.start_processing_thread)
        self.start_button.pack(side="left", padx=5, pady=5)

        self.progress_bar = ttk.Progressbar(control_frame, orient="horizontal", length=300, mode="determinate")
        self.progress_bar.pack(side="left", padx=10, pady=5, fill="x", expand=True)

        # 日志区域
        log_frame = ttk.LabelFrame(master, text="处理日志", padding=(10, 5))
        log_frame.pack(padx=10, pady=10, fill="both", expand=True)

        self.log_text = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, height=10, state=tk.DISABLED)
        self.log_text.pack(fill="both", expand=True, padx=5, pady=5)

        # 初始化时显示环境信息
        if getattr(sys, 'frozen', False):
            self.add_log(f"运行环境: PyInstaller打包版本", "info")
            self.add_log(f"临时目录: {sys._MEIPASS}", "info")
        else:
            self.add_log(f"运行环境: Python开发环境", "info")
            self.add_log(f"脚本目录: {os.path.dirname(os.path.abspath(__file__))}", "info")

        if not FFMPEG_EXECUTABLE:
            self.add_log("错误：未找到 FFmpeg 可执行文件。", "error")
            self.add_log("请确保 FFmpeg 文件夹已正确包含在程序中。", "error")
            if getattr(sys, 'frozen', False):
                self.add_log("检查 PyInstaller 的 --add-data 参数是否正确。", "error")
            self.start_button.config(state=tk.DISABLED)
        else:
            self.add_log(f"FFmpeg 已找到: {FFMPEG_EXECUTABLE}", "success")

    def add_log(self, message, level="info"):
        self.log_text.config(state=tk.NORMAL)
        tag_name = level
        self.log_text.insert(tk.END, f"{message}\n", tag_name)
        if level == "error": self.log_text.tag_config(tag_name, foreground="red")
        elif level == "success": self.log_text.tag_config(tag_name, foreground="green")
        elif level == "warning": self.log_text.tag_config(tag_name, foreground="orange")
        else: self.log_text.tag_config(tag_name, foreground="blue")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
        self.master.update_idletasks()

    def add_input_folder(self):
        """添加单个文件夹"""
        folder_selected = filedialog.askdirectory(title="选择一个包含M4S文件的源文件夹")
        if folder_selected:
            if folder_selected not in self.input_folders:
                # 检测文件夹结构
                structure_type, media_folder, json_path = detect_folder_structure(folder_selected)

                if structure_type in ['pc', 'mobile']:
                    self.input_folders.append(folder_selected)

                    # 获取文件夹标题用于显示
                    title = self.get_folder_title(json_path, structure_type)
                    type_label = "📱" if structure_type == 'mobile' else "💻"
                    display_name = f"{type_label} {os.path.basename(folder_selected)} - {title}" if title != "未知标题" else f"{type_label} {os.path.basename(folder_selected)}"

                    self.input_listbox.insert(tk.END, f"{folder_selected} ({display_name})")
                    self.add_log(f"已添加{structure_type}端文件夹: {folder_selected}", "info")
                else:
                    messagebox.showwarning("警告", f"选择的文件夹不包含有效的M4S文件结构")
            else:
                self.add_log(f"文件夹 {folder_selected} 已在列表中。", "warning")

    def batch_add_folders(self):
        """批量添加文件夹"""
        dialog = FolderSelectionDialog(self.master, "批量选择M4S文件夹")
        self.master.wait_window(dialog.dialog)

        if dialog.result:
            added_count = 0
            for folder_path in dialog.result:
                if folder_path not in self.input_folders:
                    structure_type, media_folder, json_path = detect_folder_structure(folder_path)

                    if structure_type in ['pc', 'mobile']:
                        self.input_folders.append(folder_path)

                        title = self.get_folder_title(json_path, structure_type)
                        type_label = "📱" if structure_type == 'mobile' else "💻"
                        display_name = f"{type_label} {os.path.basename(folder_path)} - {title}" if title != "未知标题" else f"{type_label} {os.path.basename(folder_path)}"

                        self.input_listbox.insert(tk.END, f"{folder_path} ({display_name})")
                        added_count += 1

            self.add_log(f"批量添加完成，新增 {added_count} 个文件夹", "success")

    def get_folder_title(self, json_path, structure_type):
        """获取文件夹的视频标题"""
        if not os.path.exists(json_path):
            return "未知标题"

        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            title = ""
            if structure_type == 'pc':
                # 电脑端结构的JSON字段
                title = data.get('title', '')
                if not title and 'videoData' in data:
                    title = data['videoData'].get('title', '')
                if not title and 'page_data' in data:
                    title = data['page_data'].get('part', '')
                if not title and 'videoName' in data:
                    title = data.get('videoName', '')
            elif structure_type == 'mobile':
                # 手机端结构的JSON字段（entry.json）
                title = data.get('title', '')
                if not title and 'page_data' in data:
                    title = data['page_data'].get('part', '')

            if title:
                return title[:30]  # 限制长度
        except Exception as e:
            print(f"[DEBUG] 读取JSON失败: {e}")
            pass
        return "未知标题"

    def remove_selected_folder(self):
        """移除选中的文件夹"""
        selected_indices = list(self.input_listbox.curselection())
        if not selected_indices:
            messagebox.showwarning("提示", "请先在列表中选择要移除的文件夹。")
            return

        # 从后往前删除，避免索引问题
        selected_indices.reverse()
        for index in selected_indices:
            folder_path = self.input_folders[index]
            self.input_listbox.delete(index)
            self.input_folders.remove(folder_path)
            self.add_log(f"已移除源文件夹: {folder_path}", "info")

    def clear_all_folders(self):
        """清空所有文件夹"""
        if self.input_folders:
            result = messagebox.askyesno("确认", "确定要清空所有文件夹吗？")
            if result:
                self.input_listbox.delete(0, tk.END)
                self.input_folders.clear()
                self.add_log("已清空所有源文件夹", "info")

    def select_output_folder(self):
        folder_selected = filedialog.askdirectory(title="选择保存转换后视频的输出文件夹")
        if folder_selected:
            self.output_folder.set(folder_selected)
            self.add_log(f"输出文件夹已选定: {folder_selected}", "info")

    def sanitize_filename(self, filename):
        filename = str(filename)
        filename = re.sub(r'[\\/*?:"<>|]', "_", filename)
        filename = re.sub(r'\s+', ' ', filename).strip()
        return filename[:150]

    def _run_ffmpeg_command(self, ffmpeg_cmd, input_description, output_basename):
        self.add_log(f"执行 FFmpeg 命令: {' '.join(ffmpeg_cmd)}", "info")
        try:
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
            process = subprocess.run(ffmpeg_cmd, check=True, capture_output=True, text=True,
                                     encoding='utf-8', errors='replace', startupinfo=startupinfo)
            self.add_log(f"成功: {input_description} -> {output_basename}", "success")
            if process.stderr: self.add_log(f"FFmpeg 输出:\n{process.stderr}", "info")
            return True
        except subprocess.CalledProcessError as e:
            self.add_log(f"错误: FFmpeg 处理 {input_description} 失败。", "error")
            error_output = ""
            if e.stdout: error_output += f"FFmpeg stdout:\n{e.stdout}\n"
            if e.stderr: error_output += f"FFmpeg stderr:\n{e.stderr}\n"
            if "Invalid data found when processing input" in e.stderr:
                error_output += ("提示: FFmpeg报告\"输入数据无效\"。可能的原因：\n"
                                 "1. M4S文件已损坏或下载不完整。\n"
                                 "2. M4S文件受DRM版权保护加密。\n"
                                 "3. M4S文件格式特殊。\n")
            self.add_log(error_output.strip(), "error")
            return False
        except FileNotFoundError:
            self.add_log(f"严重错误: FFmpeg ({FFMPEG_EXECUTABLE}) 在执行时未找到。", "error")
            return False
        except Exception as e:
            self.add_log(f"处理 {input_description} 时发生未知错误: {e}", "error")
            return False

    def _try_manifest_file_content(self, manifest_file_path_source, manifest_content, sanitized_title, output_dir_path, input_folder_path):
        # 此函数主要用于处理直接的manifest文件内容，对于手机端结构，通常不直接使用此函数
        # 但保留其兼容性
        temp_manifest_path = None
        original_input_description = os.path.basename(manifest_file_path_source)
        try:
            normalized_content = manifest_content.replace('\r\n', '\n').replace('\r', '\n').strip()
            if normalized_content.lower().startswith(("<?xml", "<mpd")):
                temp_manifest_path = os.path.join(input_folder_path, "_temp_manifest.mpd")
                self.add_log(f"内容 ({original_input_description}) 看起来像MPD XML，保存到临时文件: {temp_manifest_path}", "info")
            elif normalized_content.lower().startswith("#extm3u"):
                temp_manifest_path = os.path.join(input_folder_path, "_temp_manifest.m3u8")
                self.add_log(f"内容 ({original_input_description}) 看起来像M3U8播放列表，保存到临时文件: {temp_manifest_path}", "info")
            else:
                self.add_log(f"{original_input_description} 内容不是可识别的URL、MPD或M3U8。", "info")
                return False

            with open(temp_manifest_path, 'w', encoding='utf-8') as f_temp:
                f_temp.write(manifest_content)

            output_filename_base = sanitized_title
            output_filename_ext = ".mp4"
            normalized_output_dir_path = os.path.normpath(output_dir_path)
            output_filepath = os.path.join(normalized_output_dir_path, output_filename_base + output_filename_ext)
            count = 0
            while os.path.exists(output_filepath):
                count += 1
                output_filepath = os.path.join(normalized_output_dir_path, f"{output_filename_base}_{count}{output_filename_ext}")
            final_output_filepath = os.path.normpath(output_filepath)

            ffmpeg_cmd_manifest = [
                FFMPEG_EXECUTABLE,
                "-protocol_whitelist", "file,http,https,tcp,tls,crypto",
                "-allowed_extensions", "ALL",
                "-i", os.path.normpath(temp_manifest_path),
                "-c", "copy",
                "-y",
                final_output_filepath
            ]
            return self._run_ffmpeg_command(ffmpeg_cmd_manifest, f"临时清单 ({original_input_description})", os.path.basename(final_output_filepath))
        except Exception as e:
            self.add_log(f"尝试使用临时清单 {original_input_description} 时出错: {e}", "error")
            return False
        finally:
            if temp_manifest_path and os.path.exists(temp_manifest_path):
                try:
                    os.remove(temp_manifest_path)
                    self.add_log(f"已删除临时清单文件: {temp_manifest_path}", "info")
                except Exception as e_del:
                    self.add_log(f"警告: 删除临时清单文件 {temp_manifest_path} 失败: {e_del}", "warning")

    def process_mobile_m4s_files(self, media_folder_path, sanitized_title, output_dir_path, folder_name):
        """处理手机端M4S文件 - 尝试不解码直接合并"""
        self.add_log(f"手机端策略: 尝试直接合并M4S文件...", "info")
        try:
            m4s_files = []
            for item in os.listdir(media_folder_path):
                if item.lower().endswith(".m4s"):
                    item_path = os.path.join(media_folder_path, item)
                    try:
                        size = os.path.getsize(item_path)
                        m4s_files.append({"path": item_path, "size": size, "name": item})
                    except OSError:
                        self.add_log(f"警告: 无法获取文件 '{item}' 的大小。", "warning")

            if len(m4s_files) < 2:
                self.add_log(f"错误: 在 {media_folder_path} 中未找到至少两个 .m4s 文件用于合并。", "error")
                return False

            m4s_files.sort(key=lambda x: x["size"], reverse=True)
            video_file_info = m4s_files[0] if len(m4s_files) >= 1 else None
            audio_file_info = m4s_files[1] if len(m4s_files) >= 2 else None

            if not video_file_info or not audio_file_info:
                self.add_log(f"错误: 未能识别出视频和音频M4S文件。", "error")
                return False

            self.add_log(f"选定视频M4S: {os.path.basename(video_file_info['path'])}", "info")
            self.add_log(f"选定音频M4S: {os.path.basename(audio_file_info['path'])}", "info")

            # 设置输出路径
            output_filename_base = sanitized_title
            output_filename_ext = ".mp4"
            normalized_output_dir_path = os.path.normpath(output_dir_path)
            output_filepath = os.path.join(normalized_output_dir_path, output_filename_base + output_filename_ext)
            count = 0
            while os.path.exists(output_filepath):
                count += 1
                output_filepath = os.path.join(normalized_output_dir_path, f"{output_filename_base}_{count}{output_filename_ext}")
            final_output_filepath = os.path.normpath(output_filepath)

            # 策略1: 直接使用原始M4S文件合并（不解码）
            self.add_log("手机端策略1: 尝试直接合并原始M4S文件（不解码）", "info")
            ffmpeg_cmd_direct = [
                FFMPEG_EXECUTABLE,
                "-i", os.path.normpath(video_file_info["path"]),
                "-i", os.path.normpath(audio_file_info["path"]),
                "-c", "copy", "-y", final_output_filepath
            ]

            if self._run_ffmpeg_command(ffmpeg_cmd_direct, f"直接合并M4S ({folder_name})", os.path.basename(final_output_filepath)):
                return True
            else:
                self.add_log("手机端策略1失败，尝试策略2: 回退到解码后合并策略", "warning")
                # 策略2: 如果直接合并失败，回退到电脑端策略（解码后合并）
                return self.process_pc_m4s_files(media_folder_path, sanitized_title, output_dir_path, folder_name)

        except Exception as e:
            self.add_log(f"处理手机端M4S文件时出错: {e}", "error")
            return False

    def process_pc_m4s_files(self, media_folder_path, sanitized_title, output_dir_path, folder_name):
        """处理电脑端M4S文件 - 使用解码方法"""
        self.add_log(f"电脑端策略: 尝试解码并合并M4S文件...", "info")
        try:
            m4s_files = []
            for item in os.listdir(media_folder_path):
                if item.lower().endswith(".m4s"):
                    item_path = os.path.join(media_folder_path, item)
                    try:
                        size = os.path.getsize(item_path)
                        m4s_files.append({"path": item_path, "size": size, "name": item})
                    except OSError:
                        self.add_log(f"警告: 无法获取文件 '{item}' 的大小。", "warning")

            if len(m4s_files) < 2:
                self.add_log(f"错误: 在 {media_folder_path} 中未找到至少两个 .m4s 文件用于合并。", "error")
                return False

            m4s_files.sort(key=lambda x: x["size"], reverse=True)
            video_file_info_orig = m4s_files[0] if len(m4s_files) >= 1 else None
            audio_file_info_orig = m4s_files[1] if len(m4s_files) >= 2 else None

            if not video_file_info_orig or not audio_file_info_orig:
                self.add_log(f"错误: 未能识别出视频和音频M4S文件。", "error")
                return False

            self.add_log(f"选定原始视频M4S: {os.path.basename(video_file_info_orig['path'])}", "info")
            self.add_log(f"选定原始音频M4S: {os.path.basename(audio_file_info_orig['path'])}", "info")

            temp_video_m4s = None
            temp_audio_m4s = None
            success_decoding = False
            try:
                temp_video_handle, temp_video_m4s = tempfile.mkstemp(suffix=".m4s", prefix="decoded_vid_")
                os.close(temp_video_handle)
                temp_audio_handle, temp_audio_m4s = tempfile.mkstemp(suffix=".m4s", prefix="decoded_aud_")
                os.close(temp_audio_handle)

                if decode_bilibili_m4s(video_file_info_orig["path"], temp_video_m4s, self.add_log) and \
                   decode_bilibili_m4s(audio_file_info_orig["path"], temp_audio_m4s, self.add_log):
                    success_decoding = True

                if success_decoding:
                    self.add_log(f"临时解码视频M4S: {temp_video_m4s}", "info")
                    self.add_log(f"临时解码音频M4S: {temp_audio_m4s}", "info")

                    output_filename_base = sanitized_title
                    output_filename_ext = ".mp4"
                    normalized_output_dir_path = os.path.normpath(output_dir_path)
                    output_filepath = os.path.join(normalized_output_dir_path, output_filename_base + output_filename_ext)
                    count = 0
                    while os.path.exists(output_filepath):
                        count += 1
                        output_filepath = os.path.join(normalized_output_dir_path, f"{output_filename_base}_{count}{output_filename_ext}")
                    final_output_filepath = os.path.normpath(output_filepath)

                    ffmpeg_cmd_decoded_m4s = [
                        FFMPEG_EXECUTABLE,
                        "-i", os.path.normpath(temp_video_m4s),
                        "-i", os.path.normpath(temp_audio_m4s),
                        "-c", "copy", "-y", final_output_filepath
                    ]
                    return self._run_ffmpeg_command(ffmpeg_cmd_decoded_m4s, f"解码后M4S ({folder_name})", os.path.basename(final_output_filepath))
                else:
                    self.add_log("错误: 一个或多个M4S文件解码失败。", "error")
                    return False

            finally:
                if temp_video_m4s and os.path.exists(temp_video_m4s):
                    os.remove(temp_video_m4s)
                if temp_audio_m4s and os.path.exists(temp_audio_m4s):
                    os.remove(temp_audio_m4s)

        except Exception as e:
            self.add_log(f"处理电脑端M4S文件时出错: {e}", "error")
            return False

        return False

    def process_single_folder(self, input_folder_path, output_dir_path):
        """处理单个文件夹 - 根据结构类型使用不同策略"""
        self.add_log(f"--- 开始处理文件夹: {os.path.basename(input_folder_path)} ---", "info")

        # 检测文件夹结构
        structure_type, media_folder_path, json_path = detect_folder_structure(input_folder_path)

        if structure_type == 'unknown':
            self.add_log(f"错误: 无法识别文件夹 {input_folder_path} 的结构", "error")
            return False

        self.add_log(f"检测到文件夹结构: {'手机端' if structure_type == 'mobile' else '电脑端'}", "info")
        self.add_log(f"JSON文件路径: {json_path}", "info")
        self.add_log(f"媒体文件路径: {media_folder_path}", "info")

        # 读取视频信息
        video_title = "untitled_video"
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                video_info = json.load(f)

            if structure_type == 'pc':
                # 电脑端结构的字段解析
                if 'title' in video_info and video_info['title']:
                    video_title = video_info['title']
                elif 'videoData' in video_info and 'title' in video_info['videoData']:
                    video_title = video_info['videoData']['title']
                elif 'page_data' in video_info and 'part' in video_info['page_data']:
                    video_title = video_info['page_data']['part']
                elif 'videoName' in video_info and video_info['videoName']:
                    video_title = video_info['videoName']
            elif structure_type == 'mobile':
                # 手机端结构的字段解析（entry.json）
                if 'title' in video_info and video_info['title']:
                    video_title = video_info['title']
                elif 'page_data' in video_info and 'part' in video_info['page_data']:
                    video_title = video_info['page_data'].get('part', '')

            self.add_log(f"提取到视频标题: {video_title}", "info")
        except Exception as e:
            self.add_log(f"错误: 读取或解析JSON文件失败: {e}", "error")
            return False

        sanitized_title = self.sanitize_filename(video_title)
        if not sanitized_title:
            sanitized_title = self.sanitize_filename(os.path.basename(input_folder_path))
            if not sanitized_title: sanitized_title = "converted_video"
            self.add_log(f"警告: 原始标题清理后为空或无效，使用备用标题: {sanitized_title}", "warning")

        # 根据结构类型使用不同的处理策略
        self.add_log(f"开始使用{'手机端' if structure_type == 'mobile' else '电脑端'}处理策略", "info")

        if structure_type == 'mobile':
            # 手机端：先尝试不解码直接合并，如果失败则回退到解码后合并
            return self.process_mobile_m4s_files(media_folder_path, sanitized_title, output_dir_path, os.path.basename(input_folder_path))
        else:
            # 电脑端：使用解码方法
            return self.process_pc_m4s_files(media_folder_path, sanitized_title, output_dir_path, os.path.basename(input_folder_path))

    def process_folders(self):
        if not self.input_folders:
            messagebox.showerror("错误", "请先添加至少一个源文件夹。")
            self.start_button.config(state=tk.NORMAL)
            self.add_input_button.config(state=tk.NORMAL)
            self.batch_add_button.config(state=tk.NORMAL)
            self.remove_input_button.config(state=tk.NORMAL)
            self.clear_all_button.config(state=tk.NORMAL)
            self.select_output_button.config(state=tk.NORMAL)
            return

        output_dir = self.output_folder.get()
        if not output_dir or output_dir == "尚未选择输出文件夹":
            messagebox.showerror("错误", "请选择一个输出文件夹。")
            self.start_button.config(state=tk.NORMAL)
            self.add_input_button.config(state=tk.NORMAL)
            self.batch_add_button.config(state=tk.NORMAL)
            self.remove_input_button.config(state=tk.NORMAL)
            self.clear_all_button.config(state=tk.NORMAL)
            self.select_output_button.config(state=tk.NORMAL)
            return

        if not os.path.isdir(output_dir):
            try:
                os.makedirs(output_dir, exist_ok=True)
                self.add_log(f"输出文件夹不存在，已创建: {output_dir}", "info")
            except Exception as e:
                messagebox.showerror("错误", f"输出文件夹路径无效且无法创建: {output_dir}\n{e}")
                self.start_button.config(state=tk.NORMAL)
                self.add_input_button.config(state=tk.NORMAL)
                self.batch_add_button.config(state=tk.NORMAL)
                self.remove_input_button.config(state=tk.NORMAL)
                self.clear_all_button.config(state=tk.NORMAL)
                self.select_output_button.config(state=tk.NORMAL)
                return

        # 禁用控件
        self.start_button.config(state=tk.DISABLED)
        self.add_input_button.config(state=tk.DISABLED)
        self.batch_add_button.config(state=tk.DISABLED)
        self.remove_input_button.config(state=tk.DISABLED)
        self.clear_all_button.config(state=tk.DISABLED)
        self.select_output_button.config(state=tk.DISABLED)

        self.progress_bar["value"] = 0
        self.progress_bar["maximum"] = len(self.input_folders)
        total_folders = len(self.input_folders)
        success_count = 0
        folders_to_process = list(self.input_folders)

        for i, folder_path in enumerate(folders_to_process):
            if self.process_single_folder(folder_path, output_dir):
                success_count += 1
            self.progress_bar["value"] = i + 1
            self.master.update_idletasks()

        self.add_log(f"--- 处理完成 ---", "info")
        self.add_log(f"总计文件夹: {total_folders}, 成功转换: {success_count}, 失败: {total_folders - success_count}", "info")
        messagebox.showinfo("完成", f"所有文件夹处理完毕！\n成功: {success_count}\n失败: {total_folders - success_count}")

        # 恢复控件
        self.start_button.config(state=tk.NORMAL)
        self.add_input_button.config(state=tk.NORMAL)
        self.batch_add_button.config(state=tk.NORMAL)
        self.remove_input_button.config(state=tk.NORMAL)
        self.clear_all_button.config(state=tk.NORMAL)
        self.select_output_button.config(state=tk.NORMAL)
        self.progress_bar["value"] = 0

    def start_processing_thread(self):
        if not FFMPEG_EXECUTABLE:
            messagebox.showerror("FFmpeg 错误", "未找到 FFmpeg，无法开始处理。请检查日志区域的提示。")
            return
        processing_thread = threading.Thread(target=self.process_folders, daemon=True)
        processing_thread.start()

if __name__ == "__main__":
    root = tk.Tk()
    app = M4SConverterApp(root)
    root.mainloop()
