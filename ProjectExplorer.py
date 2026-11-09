import os
import json
import pathlib
import subprocess 
import ctypes
import string
import tkinter as tk
from tkinter import filedialog, ttk, messagebox
from datetime import datetime

class UltimateProjectDashboard:
    def __init__(self, root):
        self.root = root
        self.root.title("專案檔案速查儀表板 - 終極專業版")
        self.root.geometry("1200x750")

        # --- 🎨 介面色彩主題設定 (深色模式) ---
        self.colors = {
            "bg_main": "#1e1e1e",
            "bg_panel": "#252526",
            "bg_element": "#333333",
            "bg_hover": "#2a2d2e",
            "fg_text": "#cccccc",
            "fg_highlight": "#ffffff",
            "accent": "#0e639c",
            "accent_hover": "#1177bb",
            "danger": "#c74e4e",
            "year_bg": "#2c313a",
            "year_fg": "#e5c07b"
        }
        
        self.root.configure(bg=self.colors["bg_main"])
        self.setup_styles()

        # --- 📁 副檔名群組與圖示設定 ---
        self.file_groups = {
            "PDF 報告": {"exts": [".pdf"], "icon": "📄", "default_on": True},
            "CAD 圖面": {"exts": [".dwg", ".dxf"], "icon": "📐", "default_on": True},
            "簡報": {"exts": [".ppt", ".pptx"], "icon": "📊", "default_on": True},
            "試算表": {"exts": [".xlsx", ".xls", ".csv"], "icon": "📈", "default_on": True},
            "文件": {"exts": [".docx", ".doc"], "icon": "📝", "default_on": False},
            "圖片": {"exts": [".jpg", ".jpeg", ".png"], "icon": "🖼️", "default_on": False}
        }
        
        self.target_group_vars = {}
        self.nodes = {}
        self.current_folder = ""
        self.active_column_frames = [] 

        # --- ⭐ 我的最愛系統 ---
        self.config_file = "explorer_config.json"
        self.favorite_paths = self.load_favorites()
        self.fav_node = None 

        # --- 🌟 右鍵選單系統 ---
        self.current_right_click_path = None
        self.context_menu = tk.Menu(self.root, tearoff=0, bg=self.colors["bg_panel"], fg=self.colors["fg_highlight"], 
                                    activebackground=self.colors["accent"], activeforeground=self.colors["fg_highlight"],
                                    borderwidth=0, font=('Microsoft JhengHei', 9))
        self.context_menu.add_command(label="📂 開啟檔案位置", command=self.open_file_location)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="ℹ️ 檔案詳細資訊", command=self.show_custom_properties)

        self.build_ui()

    # ==========================================
    # 核心 UI 構建與樣式
    # ==========================================
    def setup_styles(self):
        style = ttk.Style()
        style.theme_use('clam') 
        style.configure("Treeview", background=self.colors["bg_main"], foreground=self.colors["fg_text"], fieldbackground=self.colors["bg_main"], rowheight=25, borderwidth=0)
        style.map("Treeview", background=[('selected', self.colors["accent"])])
        style.configure("Treeview.Heading", background=self.colors["bg_panel"], foreground=self.colors["fg_highlight"], relief="flat", font=('Microsoft JhengHei', 9, 'bold'))
        style.map("Treeview.Heading", background=[('active', self.colors["bg_element"])])
        style.configure("Vertical.TScrollbar", background=self.colors["bg_element"], troughcolor=self.colors["bg_main"], bordercolor=self.colors["bg_main"], arrowcolor=self.colors["fg_text"])
        style.configure("TLabelframe", background=self.colors["bg_panel"], bordercolor=self.colors["bg_element"])
        style.configure("TLabelframe.Label", background=self.colors["bg_panel"], foreground=self.colors["fg_highlight"])
        style.configure("TSeparator", background=self.colors["bg_element"])

    def create_flat_button(self, parent, text, command, bg_color=None, fg_color=None):
        bg = bg_color if bg_color else self.colors["bg_element"]
        fg = fg_color if fg_color else self.colors["fg_text"]
        btn = tk.Button(parent, text=text, command=command, bg=bg, fg=fg, activebackground=self.colors["bg_hover"], activeforeground=self.colors["fg_highlight"], relief=tk.FLAT, borderwidth=0, padx=8, pady=4, cursor="hand2")
        return btn

    def build_ui(self):
        # --- 頂部控制列 ---
        self.top_panel = tk.Frame(self.root, bg=self.colors["bg_panel"], padx=10, pady=10)
        self.top_panel.pack(side=tk.TOP, fill=tk.X)

        self.dir_frame = tk.Frame(self.top_panel, bg=self.colors["bg_panel"])
        self.dir_frame.pack(side=tk.LEFT)

        self.btn_select = self.create_flat_button(self.dir_frame, "📁 瀏覽目錄", self.select_directory, bg_color=self.colors["accent"], fg_color=self.colors["fg_highlight"])
        self.btn_select.pack(side=tk.LEFT, padx=2)
        self.btn_add_fav = self.create_flat_button(self.dir_frame, "⭐ 加入最愛", self.add_current_to_favorites)
        self.btn_add_fav.pack(side=tk.LEFT, padx=2)
        self.btn_remove_fav = self.create_flat_button(self.dir_frame, "❌ 移除最愛", self.remove_selected_favorite)
        self.btn_remove_fav.pack(side=tk.LEFT, padx=2)
        
        ttk.Separator(self.top_panel, orient='vertical').pack(side=tk.LEFT, fill=tk.Y, padx=15)

        # 欄位過濾器
        self.filter_frame = tk.LabelFrame(self.top_panel, text=" 開啟實體欄位 (依軟體分類) ", bg=self.colors["bg_panel"], fg=self.colors["fg_highlight"])
        self.filter_frame.pack(side=tk.LEFT, padx=10, fill=tk.X)
        self.refresh_filters()

        self.entry_new_ext = tk.Entry(self.top_panel, width=8, bg=self.colors["bg_main"], fg=self.colors["fg_highlight"], insertbackground=self.colors["fg_highlight"], relief=tk.FLAT)
        self.entry_new_ext.pack(side=tk.LEFT, padx=5, ipady=3)
        self.btn_add_ext = self.create_flat_button(self.top_panel, "+ 加入其他", self.add_custom_extension)
        self.btn_add_ext.pack(side=tk.LEFT)

        # 狀態指示列
        self.lbl_status = tk.Label(self.top_panel, text="掃描深度: 待命", bg=self.colors["bg_panel"], fg="#56b6c2", font=('Microsoft JhengHei', 9, 'bold'))
        self.lbl_status.pack(side=tk.RIGHT, padx=10)

        # --- 核心雙窗格配置 ---
        self.main_container = tk.Frame(self.root, bg=self.colors["bg_main"])
        self.main_container.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.paned_window = ttk.PanedWindow(self.main_container, orient=tk.HORIZONTAL)
        self.paned_window.pack(fill=tk.BOTH, expand=True)

        # 左側目錄樹 (附捲軸)
        self.left_frame = tk.Frame(self.paned_window, bg=self.colors["bg_main"])
        self.paned_window.add(self.left_frame, weight=1)
        self.tree_scroll = ttk.Scrollbar(self.left_frame, orient="vertical")
        self.tree_folders = ttk.Treeview(self.left_frame, yscrollcommand=self.tree_scroll.set)
        self.tree_scroll.config(command=self.tree_folders.yview)
        self.tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree_folders.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.tree_folders.heading("#0", text="📂 專案與磁碟目錄", anchor='w')
        self.tree_folders.bind('<<TreeviewOpen>>', self.on_tree_open)
        self.tree_folders.bind('<<TreeviewSelect>>', self.on_tree_select)

        self.init_default_directories()

        # 右側動態網格區
        self.right_container = tk.Frame(self.paned_window, bg=self.colors["bg_main"])
        self.paned_window.add(self.right_container, weight=4)
        self.file_paths = {}

    # ==========================================
    # 右鍵選單與功能
    # ==========================================
    def on_right_click(self, event):
        tree = event.widget
        item_id = tree.identify_row(event.y)
        if item_id and item_id in self.file_paths:
            tree.selection_set(item_id)
            self.current_right_click_path = self.file_paths[item_id]
            self.context_menu.tk_popup(event.x_root, event.y_root)

    def open_file_location(self):
        if self.current_right_click_path and os.path.exists(self.current_right_click_path):
            subprocess.Popen(f'explorer /select,"{self.current_right_click_path}"')

    def show_custom_properties(self):
        if self.current_right_click_path and os.path.exists(self.current_right_click_path):
            try:
                stats = os.stat(self.current_right_click_path)
                size_kb = stats.st_size / 1024
                size_str = f"{size_kb / 1024:.2f} MB" if size_kb > 1024 else f"{size_kb:.2f} KB"
                c_time = datetime.fromtimestamp(stats.st_ctime).strftime("%Y-%m-%d %H:%M:%S")
                m_time = datetime.fromtimestamp(stats.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                filename = os.path.basename(self.current_right_click_path)

                info_text = (
                    f"📄 檔案名稱：{filename}\n\n"
                    f"📁 完整位置：\n{self.current_right_click_path}\n\n"
                    f"💾 檔案大小：{size_str}\n\n"
                    f"📅 建立時間：{c_time}\n"
                    f"✏️ 修改時間：{m_time}"
                )
                messagebox.showinfo("檔案詳細資訊", info_text)
            except Exception as e:
                messagebox.showerror("錯誤", f"無法讀取檔案資訊：\n{e}")

    # ==========================================
    # 目錄樹與最愛邏輯
    # ==========================================
    def load_favorites(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f: return json.load(f).get("favorites", [])
            except: pass
        return []

    def save_favorites(self):
        with open(self.config_file, 'w', encoding='utf-8') as f: json.dump({"favorites": self.favorite_paths}, f, ensure_ascii=False, indent=4)

    def init_default_directories(self):
        self.fav_node = self.tree_folders.insert('', 'end', text="⭐ 我的最愛", open=True)
        self.refresh_favorites_tree()
        
        user_home = os.path.expanduser('~')
        quick_node = self.tree_folders.insert('', 'end', text="📌 快速存取", open=False)
        self.add_tree_node(quick_node, "桌面", os.path.join(user_home, 'Desktop'))
        self.add_tree_node(quick_node, "下載", os.path.join(user_home, 'Downloads'))
        
        local_node = self.tree_folders.insert('', 'end', text="💻 本機與外接裝置", open=False)
        net_node = self.tree_folders.insert('', 'end', text="🌐 網路磁碟", open=False)

        # 🌟 自動偵測 Windows 磁碟
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for letter in string.ascii_uppercase:
            if bitmask & 1: 
                drive_path = f"{letter}:\\"
                drive_type = ctypes.windll.kernel32.GetDriveTypeW(drive_path)
                if drive_type in [2, 3]: # 2=USB, 3=硬碟
                    self.add_tree_node(local_node, f"磁碟 {letter}:", drive_path)
                elif drive_type == 4:    # 4=網路磁碟
                    self.add_tree_node(net_node, f"網路槽 {letter}:", drive_path)
            bitmask >>= 1 

    def refresh_favorites_tree(self):
        for child in self.tree_folders.get_children(self.fav_node): self.tree_folders.delete(child)
        for path in self.favorite_paths:
            if os.path.exists(path):
                name = os.path.basename(path.rstrip('\\/')) or path
                node = self.tree_folders.insert(self.fav_node, 'end', text=f"📂 {name}")
                self.nodes[node] = path
                self.tree_folders.insert(node, 'end', text='dummy')

    def add_current_to_favorites(self):
        if not self.current_folder or not os.path.exists(self.current_folder): return
        if self.current_folder not in self.favorite_paths:
            self.favorite_paths.append(self.current_folder)
            self.save_favorites()
            self.refresh_favorites_tree()

    def remove_selected_favorite(self):
        selected = self.tree_folders.focus()
        if not selected: return
        parent = self.tree_folders.parent(selected)
        if parent == self.fav_node:
            path = self.nodes.get(selected)
            if path and path in self.favorite_paths:
                self.favorite_paths.remove(path)
                self.save_favorites()
                self.refresh_favorites_tree()
                self.current_folder = ""
                for widget in self.right_container.winfo_children(): widget.destroy()

    def add_tree_node(self, parent, display_name, path):
        if os.path.exists(path):
            node = self.tree_folders.insert(parent, 'end', text=display_name)
            self.nodes[node] = path
            self.tree_folders.insert(node, 'end', text='dummy')

    def select_directory(self):
        folder_path = filedialog.askdirectory()
        if folder_path:
            self.current_folder = folder_path
            custom_node = self.tree_folders.insert('', 'end', text=f"📁 {os.path.basename(folder_path)}", open=True)
            self.nodes[custom_node] = folder_path
            self.load_subfolders(custom_node, folder_path)
            self.tree_folders.selection_set(custom_node)
            self.update_display()

    def load_subfolders(self, parent_node, path):
        try:
            items = sorted(os.listdir(path), key=lambda v: v.upper())
            for item in items:
                full_path = os.path.join(path, item)
                if os.path.isdir(full_path):
                    node = self.tree_folders.insert(parent_node, 'end', text=item)
                    self.nodes[node] = full_path
                    self.tree_folders.insert(node, 'end', text='dummy')
        except: pass

    def on_tree_open(self, event):
        node = self.tree_folders.focus()
        children = self.tree_folders.get_children(node)
        if len(children) == 1 and self.tree_folders.item(children[0], 'text') == 'dummy':
            self.tree_folders.delete(children[0])
            if node in self.nodes: self.load_subfolders(node, self.nodes[node])

    def on_tree_select(self, event):
        node = self.tree_folders.focus()
        if node in self.nodes:
            self.current_folder = self.nodes[node]
            self.update_display()

    # ==========================================
    # 檔案過濾與掃描引擎
    # ==========================================
    def refresh_filters(self):
        for widget in self.filter_frame.winfo_children(): widget.destroy()
        for group_name, info in self.file_groups.items():
            if group_name not in self.target_group_vars: self.target_group_vars[group_name] = tk.BooleanVar(value=info["default_on"])
            display_text = f"{info['icon']} {group_name}"
            cb = tk.Checkbutton(self.filter_frame, text=display_text, variable=self.target_group_vars[group_name], command=self.update_display,
                                bg=self.colors["bg_panel"], fg=self.colors["fg_text"], selectcolor=self.colors["bg_element"], 
                                activebackground=self.colors["bg_panel"], activeforeground=self.colors["fg_highlight"])
            cb.pack(side=tk.LEFT, padx=5, pady=2)

    def add_custom_extension(self):
        ext = self.entry_new_ext.get().strip().lower()
        if ext:
            if not ext.startswith("."): ext = "." + ext
            exists = any(ext in info["exts"] for info in self.file_groups.values())
            if not exists:
                group_name = f"{ext.upper()} 檔"
                self.file_groups[group_name] = {"exts": [ext], "icon": "📁", "default_on": True}
                self.target_group_vars[group_name] = tk.BooleanVar(value=True)
                self.refresh_filters()
                self.update_display()
        self.entry_new_ext.delete(0, tk.END)

    def get_files_with_depth(self, start_path, max_depth):
        scanned_files = []
        def scan_dir(current_path, current_depth):
            if current_depth > max_depth: return
            try:
                with os.scandir(current_path) as it:
                    for entry in it:
                        if entry.is_file():
                            scanned_files.append({'name': entry.name, 'path': entry.path, 'mtime': entry.stat().st_mtime, 'ext': os.path.splitext(entry.name)[1].lower()})
                        elif entry.is_dir():
                            if not entry.name.startswith(('$', '.', 'Windows', 'Program Files')): scan_dir(entry.path, current_depth + 1)
            except PermissionError: pass
        scan_dir(start_path, 0)
        return scanned_files

    def update_display(self):
        if not self.current_folder: return

        for widget in self.right_container.winfo_children(): widget.destroy()
        self.grid_frame = tk.Frame(self.right_container, bg=self.colors["bg_main"])
        self.grid_frame.pack(fill=tk.BOTH, expand=True)
        
        self.file_paths.clear()
        self.active_column_frames.clear()
        self.global_file_counter = 0

        # 動態掃描深度判斷
        path_parts = pathlib.Path(self.current_folder).parts
        parts_len = len(path_parts)
        
        if parts_len <= 1: current_scan_depth, status_text = 0, "掃描深度: 0 (根目錄保護)"
        elif parts_len == 2: current_scan_depth, status_text = 2, "掃描深度: 2 (中層掃描)"
        else: current_scan_depth, status_text = 3, f"掃描深度: 3 (深層掃描，目前位於第 {parts_len} 層)"

        self.lbl_status.config(text=status_text)

        all_files = self.get_files_with_depth(self.current_folder, current_scan_depth)
        all_files.sort(key=lambda x: x['mtime'], reverse=True)

        active_groups = [g_name for g_name, var in self.target_group_vars.items() if var.get()]
        matched_files_by_group = {g_name: [] for g_name in active_groups}
        others = []
        known_exts = set()
        for info in self.file_groups.values(): known_exts.update(info["exts"])

        for f_info in all_files:
            matched = False
            for group_name in active_groups:
                if f_info['ext'] in self.file_groups[group_name]["exts"]:
                    matched_files_by_group[group_name].append(f_info)
                    matched = True
                    break
            if not matched and f_info['ext'] not in known_exts: others.append(f_info)

        def create_column(title, file_list, is_other=False):
            if not file_list and is_other: return
            
            col_frame = tk.LabelFrame(self.grid_frame, text=f" {title} ", font=('Microsoft JhengHei', 10, 'bold'), bg=self.colors["bg_panel"], fg=self.colors["fg_highlight"])
            self.active_column_frames.append(col_frame)

            inner_frame = tk.Frame(col_frame, bg=self.colors["bg_panel"])
            inner_frame.pack(fill=tk.BOTH, expand=True)

            tree = ttk.Treeview(inner_frame, columns=("Name"), show="headings")
            tree.heading("Name", text="時間 | 檔名")
            
            scrollbar = ttk.Scrollbar(inner_frame, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=scrollbar.set)
            tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            
            tree.bind("<Double-1>", self.on_file_double_click)
            tree.bind("<Button-3>", self.on_right_click)

            limit = 25 if is_other else 100 # 調整顯示上限
            current_year = None
            for f in file_list[:limit]:
                dt_obj = datetime.fromtimestamp(f['mtime'])
                file_year = dt_obj.year
                if file_year != current_year:
                    tree.insert("", tk.END, values=(f" ━━━ 📅 {file_year} 年 ━━━ ",), tags=('year_header',))
                    current_year = file_year

                dt_str = dt_obj.strftime("%m/%d %H:%M")
                icon = "📁"
                if not is_other:
                    for g_name, info in self.file_groups.items():
                        if f['ext'] in info['exts']: icon = info['icon']
                
                display_text = f" {icon} [{dt_str}] {f['name']}"
                
                # 確保 ID 絕對唯一
                self.global_file_counter += 1
                unique_item_id = f"file_ptr_{self.global_file_counter}"
                
                tree.insert("", tk.END, iid=unique_item_id, values=(display_text,), tags=('file',))
                self.file_paths[unique_item_id] = f['path']
            
            tree.tag_configure('year_header', foreground=self.colors["year_fg"], background=self.colors["year_bg"])

        for group_name in active_groups:
            create_column(f"{self.file_groups[group_name]['icon']} {group_name}", matched_files_by_group[group_name])
        if others: create_column("📌 其他", others, is_other=True)

        self._layout_perfect_grid()

    def _layout_perfect_grid(self):
        num_frames = len(self.active_column_frames)
        if num_frames == 0: return
        if num_frames <= 3: cols, rows = num_frames, 1
        elif num_frames == 4: cols, rows = 2, 2
        elif num_frames <= 6: cols, rows = 3, 2
        elif num_frames <= 8: cols, rows = 4, 2
        else: cols, rows = 3, (num_frames + 2) // 3

        for i in range(cols): self.grid_frame.grid_columnconfigure(i, weight=1, uniform="equal_col")
        for i in range(rows): self.grid_frame.grid_rowconfigure(i, weight=1, uniform="equal_row")
        for idx, frame in enumerate(self.active_column_frames): frame.grid(row=idx // cols, column=idx % cols, sticky="nsew", padx=4, pady=4)

    def on_file_double_click(self, event):
        tree = event.widget
        item_id = tree.focus()
        if item_id and item_id in self.file_paths:
            os.startfile(self.file_paths[item_id])

if __name__ == "__main__":
    root = tk.Tk()
    app = UltimateProjectDashboard(root)
    root.mainloop()