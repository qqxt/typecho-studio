import sys
import os
import yaml
import xmlrpc.client
import markdown
import re
import webbrowser
import json
import requests 
from datetime import datetime
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLineEdit, QTextEdit, QPushButton, QLabel, 
                             QComboBox, QTreeWidgetItem, QFileDialog, QMenu,
                             QTreeWidget, QGroupBox, QGridLayout, QCheckBox,
                             QDateTimeEdit, QScrollArea, QTabWidget, QHeaderView, QInputDialog)
from PyQt6.QtCore import Qt, QDateTime, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QAction

# 新增：异步 AI 处理线程，防止 UI 卡死
class AIWorker(QThread):
    finished = pyqtSignal(str, str) # 状态, 内容
    
    def __init__(self, api_key, model, content, prompt_type):
        super().__init__()
        self.api_key = api_key
        self.model = model
        self.content = content
        self.prompt_type = prompt_type

    def run(self):
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            prompt = "请作为资深博客编辑，对以下内容进行润色，优化表达并保持Markdown格式：" if self.prompt_type == "润色" else "请为我续写并完善以下文章内容："
            data = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "你是一个专业的博文写作专家。"},
                    {"role": "user", "content": f"{prompt}\n\n{self.content}"}
                ],
                "stream": False
            }
            response = requests.post("https://api.deepseek.com/chat/completions", headers=headers, json=data, timeout=60)
            res_json = response.json()
            if "choices" in res_json:
                result = res_json['choices'][0]['message']['content']
                self.finished.emit("success", result)
            else:
                self.finished.emit("error", f"API 错误: {res_json.get('error', {}).get('message', '未知错误')}")
        except Exception as e:
            self.finished.emit("error", str(e))

class TypechoContentStudio(QWidget):
    def __init__(self):
        super().__init__()
        # --- 核心修改：确保路径始终指向 EXE 所在的真实文件夹 ---
        if getattr(sys, 'frozen', False):
            # 如果是打包后的 EXE 运行
            self.base_dir = os.path.dirname(sys.executable)
        else:
            # 如果是普通的 .py 脚本运行
            self.base_dir = os.path.dirname(os.path.abspath(__file__))
        
        # 剩下的路径保持不变
        self.config_path = os.path.join(self.base_dir, 'config.yaml')
        self.log_file_path = os.path.join(self.base_dir, 'studio_log.txt')
        self.dir_drafts = os.path.join(self.base_dir, 'content', 'drafts')
        self.dir_sent = os.path.join(self.base_dir, 'content', 'sent')
        self.dir_backups = os.path.join(self.base_dir, 'backups')
        
        self.init_directories()
        self.rpc_client = None
        self.current_post_id = None
        self.ai_thread = None # AI 线程引用
        
        self.setup_ui_structure()
        self.bind_events()
        self.load_configuration()
        
        self.save_timer = QTimer()
        self.save_timer.timeout.connect(self.auto_save_draft)
        self.save_timer.start(60000)

    def init_directories(self):
        for d in [self.dir_drafts, self.dir_sent, self.dir_backups]:
            os.makedirs(d, exist_ok=True)

    def setup_ui_structure(self):
        self.setWindowTitle('Typecho Studio')
        self.resize(1000, 850)
        self.main_layout = QVBoxLayout(self)
        
        self.tabs = QTabWidget()
        self.tab_editor = QWidget(); self.setup_editor_tab(); self.tabs.addTab(self.tab_editor, "创作中心")
        self.tab_local = QWidget(); self.setup_local_tab(); self.tabs.addTab(self.tab_local, "本地仓库")
        self.tab_remote = QWidget(); self.setup_remote_tab(); self.tabs.addTab(self.tab_remote, "远程管理")
        self.tab_comment = QWidget(); self.setup_comment_tab(); self.tabs.addTab(self.tab_comment, "评论管理")
        self.tab_about = QWidget(); self.setup_about_tab(); self.tabs.addTab(self.tab_about, "关于软件")
        self.main_layout.addWidget(self.tabs)
        
        log_header = QHBoxLayout()
        log_header.addWidget(QLabel("系统运行日志 (自动同步至本地文本)"))
        self.btn_clear_log = QPushButton("清空面板日志")
        self.btn_clear_log.setFixedWidth(100)
        log_header.addStretch()
        log_header.addWidget(self.btn_clear_log)
        self.main_layout.addLayout(log_header)

        self.console_output = QTreeWidget()
        self.console_output.setColumnCount(2)
        self.console_output.setHeaderLabels(["时间", "系统消息"])
        self.console_output.setColumnWidth(0, 80)
        self.console_output.setFixedHeight(140)
        self.console_output.setStyleSheet("background-color: white; border: 1px solid #aaa; font-size: 11px;")
        self.main_layout.addWidget(self.console_output)

    def bind_events(self):
        self.tabs.currentChanged.connect(lambda i: self.write_log(f"切换至标签页: {self.tabs.tabText(i)}"))
        self.btn_clear_log.clicked.connect(self.clear_ui_logs)

    def setup_editor_tab(self):
        layout = QHBoxLayout(self.tab_editor)
        editor_area = QVBoxLayout()
        self.edit_title = QLineEdit(); self.edit_title.setPlaceholderText("标题"); self.edit_title.setFixedHeight(35)
        self.edit_tags = QLineEdit(); self.edit_tags.setPlaceholderText("标签 (英文逗号隔开)")
        self.edit_body = QTextEdit(); self.edit_body.setFont(QFont("Consolas", 11))
        self.edit_body.textChanged.connect(self.update_word_count)
        self.label_word_count = QLabel("字数: 0")
        self.label_word_count.setStyleSheet("color: gray; font-size: 10px;")
        editor_area.addWidget(self.edit_title); editor_area.addWidget(self.edit_tags); editor_area.addWidget(self.edit_body); editor_area.addWidget(self.label_word_count)
        
        bl = QHBoxLayout()
        self.btn_upload = QPushButton("上传图片附件"); self.btn_upload.clicked.connect(self.process_media)
        self.btn_save_now = QPushButton("保存本地草稿"); self.btn_save_now.clicked.connect(self.auto_save_draft)
        self.btn_preview = QPushButton("浏览器预览"); self.btn_preview.clicked.connect(self.preview_markdown)
        bl.addWidget(self.btn_upload); bl.addWidget(self.btn_save_now); bl.addWidget(self.btn_preview); bl.addStretch()
        editor_area.addLayout(bl)
        
        param_scroll = QScrollArea(); param_scroll.setFixedWidth(260); param_scroll.setWidgetResizable(True)
        pp = QWidget(); pl = QVBoxLayout(pp)
        gc = QGroupBox("连接配置"); gcl = QGridLayout()
        self.in_host = QLineEdit(); self.in_user = QLineEdit(); self.in_pass = QLineEdit(); self.in_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.in_ai_key = QLineEdit(); self.in_ai_key.setPlaceholderText("DeepSeek API Key"); self.in_ai_key.setEchoMode(QLineEdit.EchoMode.Password)
        gcl.addWidget(QLabel("域名:"), 0, 0); gcl.addWidget(self.in_host, 0, 1)
        gcl.addWidget(QLabel("账号:"), 1, 0); gcl.addWidget(self.in_user, 1, 1)
        gcl.addWidget(QLabel("密码:"), 2, 0); gcl.addWidget(self.in_pass, 2, 1)
        gcl.addWidget(QLabel("AI秘钥:"), 3, 0); gcl.addWidget(self.in_ai_key, 3, 1)
        btn_s = QPushButton("同步配置并连接"); btn_s.clicked.connect(self.sync_server_data)
        gcl.addWidget(btn_s, 4, 0, 1, 2); gc.setLayout(gcl)
        
        gp = QGroupBox("发布参数"); gpl = QVBoxLayout()
        self.cb_cat = QComboBox(); self.cb_status = QComboBox()
        self.status_map = {"公开": "publish", "待审核": "pending", "私密": "private", "隐藏": "hidden", "密码保护": "password"}
        self.cb_status.addItems(list(self.status_map.keys()))
        self.in_post_pass = QLineEdit(); self.in_post_pass.setPlaceholderText("访问密码")
        self.dt_picker = QDateTimeEdit(QDateTime.currentDateTime()); self.dt_picker.setCalendarPopup(True)
        gpl.addWidget(QLabel("分类:")); gpl.addWidget(self.cb_cat); gpl.addWidget(QLabel("状态:")); gpl.addWidget(self.cb_status)
        gpl.addWidget(self.in_post_pass); gpl.addWidget(QLabel("发布日期:")); gpl.addWidget(self.dt_picker); gp.setLayout(gpl)
        
        # --- 全站与 AI 控制区 ---
        go = QGroupBox("智能工具箱")
        gol = QVBoxLayout()
        self.cb_ai_model = QComboBox()
        self.cb_ai_model.addItems(["deepseek-chat", "deepseek-reasoner"])
        self.btn_ai_fix = QPushButton("✨ AI 智能润色正文")
        self.btn_ai_fix.setStyleSheet("background-color: #9b59b6; color: white; border-radius: 3px;")
        self.btn_ai_fix.clicked.connect(self.execute_ai_beautify)
        
        self.btn_backup = QPushButton("一键全站本地备份")
        self.btn_backup.setStyleSheet("background-color: #3498db; color: white; border-radius: 3px;")
        self.btn_backup.clicked.connect(self.execute_full_backup)
        
        gol.addWidget(QLabel("AI 模型:")); gol.addWidget(self.cb_ai_model)
        gol.addWidget(self.btn_ai_fix); gol.addWidget(self.btn_backup)
        go.setLayout(gol)
        
        self.btn_pub = QPushButton("确认提交文章"); self.btn_pub.setFixedHeight(50); self.btn_pub.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold; border-radius: 5px;")
        self.btn_pub.clicked.connect(self.execute_publish)
        
        pl.addWidget(gc); pl.addWidget(gp); pl.addWidget(go); pl.addStretch(); pl.addWidget(self.btn_pub)
        param_scroll.setWidget(pp); layout.addLayout(editor_area, 1); layout.addWidget(param_scroll)

    def execute_ai_beautify(self):
        key = self.in_ai_key.text().strip()
        content = self.edit_body.toPlainText().strip()
        if not key or not content:
            self.write_log("AI 润色失败：请先填写 AI 秘钥且编辑器内容不能为空", "red")
            return
        
        self.write_log(f"正在发送请求至 DeepSeek ({self.cb_ai_model.currentText()})...", "purple")
        self.btn_ai_fix.setEnabled(False)
        self.btn_ai_fix.setText("AI 正在思考中...")
        
        self.ai_thread = AIWorker(key, self.cb_ai_model.currentText(), content, "润色")
        self.ai_thread.finished.connect(self.on_ai_finished)
        self.ai_thread.start()

    def on_ai_finished(self, status, result):
        self.btn_ai_fix.setEnabled(True)
        self.btn_ai_fix.setText("✨ AI 智能润色正文")
        if status == "success":
            self.edit_body.setPlainText(result)
            self.write_log("✅ AI 润色完成，编辑器内容已更新", "green")
        else:
            self.write_log(f"❌ AI 润色失败: {result}", "red")

    def execute_full_backup(self):
        if not self.rpc_client:
            self.write_log("无法备份：请先同步服务器信息", "red")
            return
        self.write_log("开始全站备份任务...", "blue")
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            save_path = os.path.join(self.dir_backups, f"backup_{timestamp}")
            os.makedirs(save_path, exist_ok=True)
            posts = self.rpc_client.metaWeblog.getRecentPosts(1, self.in_user.text(), self.in_pass.text(), 1000)
            count = 0
            for p in posts:
                pid = p['postid']; title = p['title']
                safe_title = "".join([i for i in title if i.isalnum() or i in (' ', '_')]).rstrip()
                if not safe_title: safe_title = f"post_{pid}"
                full_post = self.rpc_client.metaWeblog.getPost(pid, self.in_user.text(), self.in_pass.text())
                content = self.clean_html(full_post['description'])
                meta = f"---\ntitle: {title}\nid: {pid}\ncategories: {full_post.get('categories', [])}\ntags: {full_post.get('mt_keywords', '')}\n---\n\n"
                with open(os.path.join(save_path, f"{safe_title}.md"), "w", encoding="utf-8") as f:
                    f.write(meta + content)
                count += 1
            self.write_log(f"✅ 备份成功！已导出 {count} 篇文章", "green")
            os.startfile(save_path)
        except Exception as e: self.write_log(f"备份失败: {e}", "red")

    def setup_local_tab(self):
        layout = QVBoxLayout(self.tab_local)
        self.local_search = QLineEdit(); self.local_search.setPlaceholderText("搜索本地文章..."); self.local_search.textChanged.connect(self.filter_local)
        layout.addWidget(self.local_search)
        self.local_tree = QTreeWidget(); self.local_tree.setColumnCount(3); self.local_tree.setHeaderLabels(["类型", "文件名", "最后修改时间"])
        self.local_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.local_tree.doubleClicked.connect(self.load_local_file)
        layout.addWidget(self.local_tree)
        side = QHBoxLayout()
        b_rf = QPushButton("刷新列表"); b_rf.clicked.connect(self.refresh_local_list)
        b_od = QPushButton("稿箱目录"); b_od.clicked.connect(lambda: (self.write_log("点击：打开草稿箱目录"), os.startfile(self.dir_drafts)))
        b_ob = QPushButton("查看备份"); b_ob.clicked.connect(lambda: (self.write_log("点击：打开备份目录"), os.startfile(self.dir_backups)))
        side.addWidget(b_rf); side.addWidget(b_od); side.addWidget(b_ob); side.addStretch()
        layout.addLayout(side)

    def setup_remote_tab(self):
        layout = QVBoxLayout(self.tab_remote)
        self.remote_search = QLineEdit(); self.remote_search.setPlaceholderText("搜索远程文章..."); self.remote_search.textChanged.connect(self.filter_remote)
        layout.addWidget(self.remote_search)
        self.remote_tree = QTreeWidget(); self.remote_tree.setColumnCount(4)
        self.remote_tree.setHeaderLabels(["ID", "文章标题", "分类", "作者"])
        self.remote_tree.setColumnWidth(0, 50); self.remote_tree.setColumnWidth(2, 100); self.remote_tree.setColumnWidth(3, 80)
        self.remote_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.remote_tree.doubleClicked.connect(self.fetch_remote_post)
        layout.addWidget(self.remote_tree)
        b_fr = QPushButton("拉取远程最新列表"); b_fr.clicked.connect(self.refresh_remote_list)
        layout.addWidget(b_fr)

    def setup_comment_tab(self):
        layout = QVBoxLayout(self.tab_comment)
        self.comment_tree = QTreeWidget(); self.comment_tree.setColumnCount(5)
        self.comment_tree.setHeaderLabels(["ID", "关联文章", "评论者", "评论内容", "状态"])
        self.comment_tree.setColumnWidth(0, 50); self.comment_tree.setColumnWidth(1, 150); self.comment_tree.setColumnWidth(2, 100); self.comment_tree.setColumnWidth(4, 80)
        self.comment_tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.comment_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.comment_tree.customContextMenuRequested.connect(self.show_comment_context_menu)
        layout.addWidget(self.comment_tree)
        b_sync = QPushButton("同步全部状态评论"); b_sync.clicked.connect(self.refresh_comments)
        layout.addWidget(b_sync)

    def write_log(self, text, color="black"):
        if not hasattr(self, 'console_output'): return
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        item = QTreeWidgetItem(self.console_output)
        item.setText(0, now.split(' ')[1])
        item.setText(1, text)
        item.setForeground(1, QColor(color))
        self.console_output.scrollToBottom()
        try:
            with open(self.log_file_path, "a", encoding="utf-8") as f:
                f.write(f"[{now}] {text}\n")
        except: pass

    def clear_ui_logs(self):
        self.console_output.clear()
        self.write_log("点击：清空面板日志 (本地文本已保留)", "gray")

    def show_comment_context_menu(self, pos):
        item = self.comment_tree.itemAt(pos)
        if not item: return
        menu = QMenu(); cid = item.text(0)
        act_del = QAction("彻底删除评论", self); act_del.triggered.connect(lambda: self.handle_comment_action(cid, "delete"))
        menu.addAction(act_del); menu.exec(self.comment_tree.viewport().mapToGlobal(pos))

    def handle_comment_action(self, cid, action):
        if not self.rpc_client: return
        self.write_log(f"操作：评论 {cid} {action}", "blue")
        try:
            user, pwd = self.in_user.text(), self.in_pass.text()
            if action == "delete": self.rpc_client.wp.deleteComment(1, user, pwd, cid)
            QTimer.singleShot(500, self.refresh_comments)
        except Exception as e: self.write_log(f"操作失败: {e}", "red")

    def refresh_comments(self):
        if not self.rpc_client: return
        self.write_log("同步评论...", "blue")
        try:
            comments = self.rpc_client.wp.getComments(1, self.in_user.text(), self.in_pass.text(), {})
            self.comment_tree.clear()
            for c in comments:
                item = QTreeWidgetItem(self.comment_tree)
                item.setText(0, str(c['comment_id'])); item.setText(1, c.get('post_title', '')); item.setText(2, c.get('author', ''))
                item.setText(3, c.get('content', '').replace('\n', ' ')); st = c.get('status', 'approved')
                item.setText(4, "[待审核]" if st == 'hold' else ("[垃圾]" if st == 'spam' else "已通过"))
                item.setForeground(4, QColor("orange" if st == 'hold' else ("red" if st == 'spam' else "green")))
        except Exception as e: self.write_log(f"失败: {e}", "red")

    def sync_server_data(self):
        host = self.in_host.text().strip()
        if not host: return
        self.write_log(f"点击：保存配置并同步 {host}", "blue")
        try:
            protocol = "https://" if not host.startswith('http') else ""
            self.rpc_client = xmlrpc.client.ServerProxy(f"{protocol}{host}/action/xmlrpc", allow_none=True)
            cats = self.rpc_client.metaWeblog.getCategories(1, self.in_user.text(), self.in_pass.text())
            self.cb_cat.clear(); self.cb_cat.addItems([c['description'] for c in cats])
            with open(self.config_path, 'w', encoding='utf-8') as f: 
                yaml.dump({'host': host, 'user': self.in_user.text(), 'pass': self.in_pass.text(), 'ai_key': self.in_ai_key.text()}, f)
            self.write_log("同步成功，AI 秘钥已记录", "green")
        except Exception as e: self.write_log(f"失败: {e}", "red")

    def refresh_remote_list(self):
        if not self.rpc_client:
            self.write_log("无法拉取：请先连接服务器", "red")
            return
        
        self.write_log("正在从服务器获取最新文章列表...", "blue")
        try:
            # 获取最近的 50 篇文章
            posts = self.rpc_client.metaWeblog.getRecentPosts(1, self.in_user.text(), self.in_pass.text(), 50)
            self.remote_tree.clear()
            
            for p in posts:
                item = QTreeWidgetItem(self.remote_tree)
                # 填充四列数据
                item.setText(0, str(p.get('postid', '')))
                item.setText(1, p.get('title', '无标题'))
                
                # 处理分类 (通常返回的是列表)
                cats = p.get('categories', [])
                item.setText(2, cats[0] if cats else "未分类")
                
                # 处理作者：XMLRPC 扩展字段中通常包含 nickname 或 wp_author_display_name
                author = p.get('nickname') or p.get('wp_author_display_name') or "未知"
                item.setText(3, author)
                
                # 绑定隐藏数据，方便双击读取
                item.setData(0, Qt.ItemDataRole.UserRole, p.get('postid'))
            
            self.write_log(f"成功拉取 {len(posts)} 篇文章", "green")
        except Exception as e:
            self.write_log(f"拉取失败: {e}", "red")

    def execute_publish(self):
        """发布文章并记录详细日志"""
        title = self.edit_title.text().strip()
        content = self.edit_body.toPlainText().strip()
        
        if not self.rpc_client:
            self.write_log("无法提交：未连接到服务器", "red")
            return
        if not title:
            self.write_log("提交失败：文章标题不能为空", "red")
            return

        self.write_log(f"正在发布文章: {title} ...", "blue")
        
        try:
            # 转换 Markdown 为 HTML
            html_content = markdown.markdown(content, extensions=['extra', 'codehilite', 'toc'])
            
            # 组织发布数据
            payload = {
                'title': title,
                'description': html_content,
                'categories': [self.cb_cat.currentText()],
                'mt_keywords': self.edit_tags.text(),
                'post_status': self.status_map.get(self.cb_status.currentText(), "publish")
            }
            
            if self.current_post_id:
                # 编辑现有文章
                self.rpc_client.metaWeblog.editPost(self.current_post_id, self.in_user.text(), self.in_pass.text(), payload, True)
                action_text = "更新"
            else:
                # 发布新文章
                new_id = self.rpc_client.metaWeblog.newPost(1, self.in_user.text(), self.in_pass.text(), payload, True)
                action_text = "发布"

            # 核心新增：发布成功日志
            self.write_log(f"🎉 成功！文章《{title}》已完成{action_text}并同步到服务器", "green")
            
            # 发布后自动将当前内容存入 sent 目录
            sent_path = os.path.join(self.dir_sent, f"{title}.md")
            with open(sent_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            # 重置编辑器或刷新列表
            self.refresh_remote_list()
            self.reset_editor() # 如果你想发布后清空编辑器，取消此行注释
            
        except Exception as e:
            self.write_log(f"❌ 提交失败: {e}", "red")

    def auto_save_draft(self):
        """每分钟自动保存草稿，并记录日志"""
        title = self.edit_title.text().strip() or "未命名"
        content = self.edit_body.toPlainText().strip()
        
        # 如果内容为空，不执行保存，也不写日志防止刷屏
        if not content:
            return
            
        try:
            filename = f"{title}.md"
            # 过滤文件名非法字符
            safe_filename = "".join([i for i in filename if i.isalnum() or i in (' ', '.', '_', '-')]).strip()
            save_path = os.path.join(self.dir_drafts, safe_filename)
            
            with open(save_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            
            self.write_log(f"💾 自动保存成功: {safe_filename}", "#1a06f1") 
        except Exception as e:
            self.write_log(f"❌ 自动保存失败: {e}", "red")

    def update_word_count(self): self.label_word_count.setText(f"字数: {len(self.edit_body.toPlainText())}")

    def reset_editor(self):
        """清空编辑器，准备撰写新文章"""
        self.edit_title.clear()
        self.edit_body.clear()
        self.edit_tags.clear()
        self.current_post_id = None # 关键：必须清空 ID，否则会覆盖旧文
        self.cb_status.setCurrentIndex(0)
        self.write_log("🧹 编辑器已清空，当前处于“新建文章”模式", "#2a08ec")

    def clean_html(self, raw_html):
        c = re.sub(r'<img.*?src="(.*?)".*?/>', r'![](\1)', raw_html)
        c = re.sub(r'</?(h\d|p|span|div|blockquote|ul|li|ol|pre|code|a).*?>', '', c)
        return html_unescape(c).strip()

    def refresh_local_list(self):
        self.local_tree.clear()
        for folder, label in [(self.dir_drafts, "草稿"), (self.dir_sent, "发布")]:
            if os.path.exists(folder):
                for f in os.listdir(folder):
                    if f.endswith('.md'):
                        item = QTreeWidgetItem(self.local_tree); item.setText(0, label); item.setText(1, f)
                        item.setData(0, Qt.ItemDataRole.UserRole, os.path.join(folder, f))

    def filter_local(self, t):
        for i in range(self.local_tree.topLevelItemCount()): self.local_tree.topLevelItem(i).setHidden(t.lower() not in self.local_tree.topLevelItem(i).text(1).lower())

    def filter_remote(self, t):
        for i in range(self.remote_tree.topLevelItemCount()): self.remote_tree.topLevelItem(i).setHidden(t.lower() not in self.remote_tree.topLevelItem(i).text(1).lower())

    def fetch_remote_post(self):
        item = self.remote_tree.currentItem(); pid = item.data(0, Qt.ItemDataRole.UserRole)
        try:
            p = self.rpc_client.metaWeblog.getPost(pid, self.in_user.text(), self.in_pass.text())
            self.edit_title.setText(p['title']); self.edit_body.setPlainText(self.clean_html(p['description']))
            self.current_post_id = pid; self.tabs.setCurrentIndex(0)
        except: pass

    def load_local_file(self):
        item = self.local_tree.currentItem(); p = item.data(0, Qt.ItemDataRole.UserRole)
        try:
            with open(p, 'r', encoding='utf-8') as f: self.edit_body.setPlainText(self.clean_html(f.read()))
            self.edit_title.setText(item.text(1).replace('.md', '')); self.tabs.setCurrentIndex(0)
        except: pass

    def process_media(self):
        if not self.rpc_client: 
            self.write_log("上传失败：请先连接服务器", "red")
            return
            
        # 1. 全格式文件过滤器
        img_exts = "*.jpg *.jpeg *.png *.gif *.webp *.svg *.bmp *.ico *.tiff"
        media_exts = "*.mp3 *.mp4 *.mov *.wmv *.wma *.rmvb *.rm *.avi *.flv *.ogg *.oga *.ogv"
        doc_exts = "*.txt *.doc *.docx *.xls *.xlsx *.ppt *.pptx *.zip *.rar *.pdf"
        
        filter_str = f"所有支持文件 ({img_exts} {media_exts} {doc_exts});;" \
                     f"图片文件 ({img_exts});;" \
                     f"多媒体文件 ({media_exts});;" \
                     f"档案文件 ({doc_exts});;" \
                     f"所有文件 (*.*)"
                     
        f, _ = QFileDialog.getOpenFileName(self, "选择上传文件", "", filter_str)
        
        if f:
            file_name = os.path.basename(f)
            self.write_log(f"点击：尝试上传 {file_name}", "blue")
            try:
                with open(f, "rb") as rb: 
                    b = xmlrpc.client.Binary(rb.read())
                
                ext = os.path.splitext(f)[1].lower().replace('.', '')
                
                # 2. 增强型 MIME 类型映射
                mime_map = {
                    # 图片
                    'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'svg': 'image/svg+xml', 'ico': 'image/x-icon',
                    # 视频
                    'mp4': 'video/mp4', 'mov': 'video/quicktime', 'flv': 'video/x-flv', 'avi': 'video/x-msvideo',
                    'wmv': 'video/x-ms-wmv', 'rmvb': 'application/vnd.rn-realmedia-vbr', 'ogv': 'video/ogg',
                    # 音频
                    'mp3': 'audio/mpeg', 'wma': 'audio/x-ms-wma', 'ogg': 'audio/ogg', 'oga': 'audio/ogg',
                    # 档案
                    'pdf': 'application/pdf', 'zip': 'application/zip', 'rar': 'application/x-rar-compressed',
                    'doc': 'application/msword', 'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    'xls': 'application/vnd.ms-excel', 'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    'ppt': 'application/vnd.ms-powerpoint', 'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
                }
                mime_type = mime_map.get(ext, 'application/octet-stream')
                
                # 3. 执行上传
                res = self.rpc_client.metaWeblog.newMediaObject(1, 
                    self.in_user.text(), self.in_pass.text(), 
                    {'name': file_name, 'bits': b, 'type': mime_type})
                
                url = res['url']
                
                # Markdown/HTML 代码
                insert_code = ""
                
                # 图片类
                if ext in ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp']:
                    insert_code = f"\n<p align=\"center\">\n  <img src=\"{url}\" alt=\"{file_name}\" style=\"max-width:100%;\">\n</p>\n"
                
                # 视频类： 
                elif ext in ['mp4', 'mov', 'avi', 'wmv', 'flv', 'rmvb', 'ogv']:
                    insert_code = f"\n<div align=\"center\">\n  <video src=\"{url}\" controls style=\"max-width:100%;\">您的浏览器不支持播放该视频</video>\n</div>\n"
                
                # 音频类：
                elif ext in ['mp3', 'wma', 'ogg', 'oga']:
                    insert_code = f"\n<div align=\"center\">\n  <audio src=\"{url}\" controls>您的浏览器不支持音频播放</audio>\n</div>\n"
                
                # 档案类：普通下载链接
                else:
                    insert_code = f"\n> 📁 [下载附件：{file_name}]({url})\n"
                
                self.edit_body.insertPlainText(insert_code)
                self.write_log(f"✅ 上传成功并已插入代码", "green")
                
            except Exception as e: 
                self.write_log(f"❌ 上传失败: {e}", "red")
                self.write_log("提示：请确保 Typecho 后台已允许该后缀文件上传。", "gray")

    def preview_markdown(self):
        html = markdown.markdown(self.edit_body.toPlainText()); temp = os.path.join(self.base_dir, "preview_temp.html")
        with open(temp, "w", encoding="utf-8") as f: f.write(html)
        webbrowser.open(os.path.abspath(temp))

    def load_configuration(self):
        if os.path.exists(self.config_path):
            with open(self.config_path, 'r', encoding='utf-8') as f:
                d = yaml.safe_load(f)
                if d: 
                    self.in_host.setText(d.get('host', '')); self.in_user.setText(d.get('user', ''))
                    self.in_pass.setText(d.get('pass', '')); self.in_ai_key.setText(d.get('ai_key', ''))

    def setup_about_tab(self):
        layout = QVBoxLayout(self.tab_about)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter) 

        # 软件图标或标题
        title_label = QLabel("Typecho 文章发布器")
        title_label.setFont(QFont("Arial", 20, QFont.Weight.Bold))
        title_label.setStyleSheet("color: #2c3e50; margin-top: 20px;")
        
        version_label = QLabel("版本: v1.0.0 (Build 20260104)")
        version_label.setStyleSheet("color: #7f8c8d; font-size: 12px;")

        # 版权声明内容
        desc_box = QTextEdit()
        desc_box.setReadOnly(True)
        desc_box.setFixedWidth(500)
        desc_box.setFixedHeight(250)
        desc_box.setStyleSheet("background-color: transparent; border: none; color: #34495e;")
        desc_box.setHtml("""
            <h3 style='text-align: center;'>版权信息</h3>
            <p><b>开发者:</b> 小野博客</p>
            <p><b>官方博客:</b> <a href='https://lb5.net'>https://lb5.net</a></p>
            <p><b>开源地址:</b> <a href='https://github.com/qqxt/typecho-studio'>GitHub Repo</a></p>
            <hr>
            <p>本软件旨在提升 Typecho 用户的创作效率。集成了 XMLRPC 远程管理、DeepSeek AI 润色、全站备份以及全格式多媒体上传功能。</p>
            <p><b>许可声明:</b> 本软件仅供个人学习与交流使用。未经许可，禁止将本工具用于商业售卖。</p>
            <p>本软件基于 MIT 协议开源。您可以自由地使用、修改和分发本软件，但请务必在软件中保留原始版权声明。</p>
            <p style='text-align: center; color: #95a5a6; margin-top: 20px;'>
                © 2026 小野博客. All rights reserved.
            </p>
        """)

        # 友情链接或按钮
        btn_layout = QHBoxLayout()
        btn_site = QPushButton("访问官网"); btn_site.setFixedWidth(120)
        btn_site.clicked.connect(lambda: webbrowser.open("https://lb5.net"))
        btn_update = QPushButton("检查更新"); btn_update.setFixedWidth(120)
        btn_update.clicked.connect(lambda: self.write_log("检查更新：当前已是最新版本"))
        
        btn_layout.addStretch(); btn_layout.addWidget(btn_site); btn_layout.addWidget(btn_update); btn_layout.addStretch()

        layout.addStretch()
        layout.addWidget(title_label, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version_label, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(20)
        layout.addWidget(desc_box, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addLayout(btn_layout)
        layout.addStretch()

def html_unescape(s): return s.replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", " ")



if __name__ == '__main__':
    app = QApplication(sys.argv); app.setStyle("Fusion")
    win = TypechoContentStudio(); win.show(); sys.exit(app.exec())