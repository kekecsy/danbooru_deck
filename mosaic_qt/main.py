import sys
import os
import warnings
import zipfile

# 禁用弃用警告
warnings.filterwarnings("ignore", category=DeprecationWarning)

# 设置环境变量抑制libpng警告
os.environ['QT_LOGGING_RULES'] = '*.debug=false;qt.qpa.*=false'

from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QLabel, 
                             QVBoxLayout, QHBoxLayout, QPushButton, QFileDialog,
                             QSlider, QSpinBox, QComboBox, QGroupBox, QMenu,
                             QMessageBox, QDialog, QTextEdit, QListWidget,
                             QListWidgetItem, QLineEdit)
from PyQt5.QtCore import Qt, QSize, QDateTime
from PyQt5.QtGui import (QPixmap, QPainter, QColor,
                         QTransform, QIcon, QTextCursor)

from zip_conventer import ZipToGifConverter
from dialog import ExportDialog
from image_view import ImageView

class MosaicEditor(QMainWindow):
    """马赛克编辑器主窗口"""
    
    def __init__(self):
        super().__init__()
        self.initUI()
        
    def initUI(self):
        """初始化UI"""
        self.setWindowTitle("马赛克编辑器")
        self.setGeometry(100, 100, 1300, 850)  # 增加窗口高度
        
        # 设置图标
        icon_path = "./app_icon.ico"
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        
        # 创建中心部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # 创建主水平布局
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(8, 8, 8, 8)  # 调整边距
        
        # 左侧：图片区域
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setSpacing(5)  # 减少间距
        
        # 工具栏
        toolbar = self.create_toolbar()
        left_layout.addLayout(toolbar)
        
        # 图片视图
        self.image_view = ImageView(self)
        self.image_view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        left_layout.addWidget(self.image_view)
        
        # 右侧：边栏
        right_widget = QWidget()
        right_widget.setMaximumWidth(250)
        right_layout = QVBoxLayout(right_widget)
        
        # 预设图片区域
        preset_group = QGroupBox("预设马赛克")
        preset_layout = QVBoxLayout(preset_group)
        
        self.preset_list = QListWidget()
        self.preset_list.setIconSize(QSize(60, 60))
        self.preset_list.itemClicked.connect(self.on_preset_selected)
        preset_layout.addWidget(self.preset_list)
        
        right_layout.addWidget(preset_group)
        
        # 日志区域
        log_group = QGroupBox("日志")
        log_layout = QVBoxLayout(log_group)
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(200)
        log_layout.addWidget(self.log_text)
        
        right_layout.addWidget(log_group)
        
        # 添加到主布局
        main_layout.addWidget(left_widget, 1)  # 左侧占主要空间
        main_layout.addWidget(right_widget)
        
        # 状态栏
        self.statusBar().showMessage("准备就绪")
        
        # 当前图片路径和尺寸
        self.current_image_path = None
        self.current_image_size = None
        
        # 剪贴板
        self.clipboard = QApplication.clipboard()
        
        # 加载预设图片
        self.load_presets()
        
        # 设置默认透明度
        self.image_view.current_mosaic_opacity = 1.0
        
        # 初始化白条默认值
        self.image_view.stripe_text = "该信息已被管理员撤回"
        self.image_view.stripe_font_family = "Times New Roman"
        self.image_view.stripe_font_size = 25
        self.image_view.stripe_orientation = "horizontal"
        
    def create_toolbar(self):
        """创建工具栏 - 采用垂直布局，避免拥挤"""
        main_layout = QVBoxLayout()
        main_layout.setSpacing(8)
        
        # 第一行：文件操作和视图操作（常用功能）
        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        
        # 文件操作组
        file_group = QGroupBox("文件操作")
        file_layout = QHBoxLayout()
        file_layout.setSpacing(8)
        
        btn_import = QPushButton("📁 导入")
        btn_import.clicked.connect(self.import_image)
        btn_import.setStyleSheet("padding: 6px 12px; font-weight: bold; min-width: 60px;")
        file_layout.addWidget(btn_import)
        
        btn_export = QPushButton("💾 导出")
        btn_export.clicked.connect(self.export_image)
        btn_export.setStyleSheet("padding: 6px 12px; font-weight: bold; min-width: 60px;")
        btn_export.setEnabled(False)
        self.btn_export = btn_export
        file_layout.addWidget(btn_export)
        
        btn_copy_clipboard = QPushButton("📋 复制")
        btn_copy_clipboard.clicked.connect(self.copy_image_to_clipboard)
        btn_copy_clipboard.setStyleSheet("padding: 6px 12px; font-weight: bold; min-width: 60px;")
        btn_copy_clipboard.setToolTip("将当前图片复制到剪贴板")
        file_layout.addWidget(btn_copy_clipboard)
        
        btn_paste = QPushButton("📌 粘贴")
        btn_paste.clicked.connect(self.paste_image_from_clipboard)
        btn_paste.setStyleSheet("padding: 6px 12px; font-weight: bold; min-width: 60px;")
        btn_paste.setToolTip("从剪贴板粘贴图片 (Ctrl+V)")
        file_layout.addWidget(btn_paste)
        
        file_group.setLayout(file_layout)
        top_row.addWidget(file_group)
        
        # 视图操作组
        view_group = QGroupBox("视图")
        view_layout = QHBoxLayout()
        view_layout.setSpacing(8)
        
        btn_fit = QPushButton("适应窗口")
        btn_fit.clicked.connect(self.fit_to_window)
        btn_fit.setStyleSheet("padding: 5px 10px; min-width: 70px;")
        view_layout.addWidget(btn_fit)
        
        btn_actual = QPushButton("实际大小")
        btn_actual.clicked.connect(self.actual_size)
        btn_actual.setStyleSheet("padding: 5px 10px; min-width: 70px;")
        view_layout.addWidget(btn_actual)
        
        view_group.setLayout(view_layout)
        top_row.addWidget(view_group)
        
        top_row.addStretch()
        main_layout.addLayout(top_row)
        
        # 第二行：马赛克相关操作
        middle_row = QHBoxLayout()
        middle_row.setSpacing(10)
        
        # 马赛克操作组
        mosaic_group = QGroupBox("马赛克设置")
        mosaic_layout = QHBoxLayout()
        mosaic_layout.setSpacing(8)
        
        # 透明度控制
        opacity_label = QLabel("透明度:")
        opacity_label.setToolTip("设置马赛克的透明度")
        mosaic_layout.addWidget(opacity_label)
        
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(10, 100)
        self.opacity_slider.setValue(100)
        self.opacity_slider.setTickPosition(QSlider.TicksBelow)
        self.opacity_slider.setTickInterval(10)
        self.opacity_slider.valueChanged.connect(self.update_opacity)
        self.opacity_slider.setToolTip("调整马赛克透明度")
        self.opacity_slider.setFixedWidth(120)
        mosaic_layout.addWidget(self.opacity_slider)
        
        self.opacity_label = QLabel("100%")
        self.opacity_label.setToolTip("当前透明度")
        self.opacity_label.setFixedWidth(35)
        mosaic_layout.addWidget(self.opacity_label)
        
        mosaic_layout.addSpacing(15)
        
        # 撤销和清空
        btn_undo = QPushButton("↶ 撤销")
        btn_undo.clicked.connect(self.undo_last_mosaic)
        btn_undo.setStyleSheet("padding: 5px 10px; min-width: 60px;")
        mosaic_layout.addWidget(btn_undo)
        
        btn_clear = QPushButton("🗑️ 清空")
        btn_clear.clicked.connect(self.clear_all_mosaics)
        btn_clear.setStyleSheet("padding: 5px 10px; color: #d32f2f; min-width: 60px;")
        mosaic_layout.addWidget(btn_clear)
        
        mosaic_group.setLayout(mosaic_layout)
        middle_row.addWidget(mosaic_group)
        
        # 白条马赛克操作组
        stripe_group = QGroupBox("白条马赛克")
        stripe_layout = QHBoxLayout()
        stripe_layout.setSpacing(8)
        
        # 文字输入
        stripe_layout.addWidget(QLabel("文字:"))
        self.stripe_text_input = QLineEdit()
        self.stripe_text_input.setText("该信息已被管理员撤回")
        self.stripe_text_input.setToolTip("白条上显示的文字")
        self.stripe_text_input.setFixedWidth(140)
        stripe_layout.addWidget(self.stripe_text_input)
        
        # 字体选择
        stripe_layout.addWidget(QLabel("字体:"))
        self.stripe_font_combo = QComboBox()
        self.stripe_font_combo.addItems(["Arial", "Times New Roman", "Courier New", "Verdana", "Microsoft YaHei", "SimHei", "SimSun"])
        self.stripe_font_combo.setCurrentText("Times New Roman")
        self.stripe_font_combo.setToolTip("选择字体")
        self.stripe_font_combo.setFixedWidth(110)
        stripe_layout.addWidget(self.stripe_font_combo)
        
        # 字体大小
        stripe_layout.addWidget(QLabel("大小:"))
        self.stripe_font_size_spin = QSpinBox()
        self.stripe_font_size_spin.setRange(8, 72)
        self.stripe_font_size_spin.setValue(25)
        self.stripe_font_size_spin.setToolTip("字体大小")
        self.stripe_font_size_spin.setFixedWidth(60)
        stripe_layout.addWidget(self.stripe_font_size_spin)
        
        # 方向选择
        stripe_layout.addWidget(QLabel("方向:"))
        self.stripe_orientation_combo = QComboBox()
        self.stripe_orientation_combo.addItems(["水平", "垂直"])
        self.stripe_orientation_combo.setToolTip("白条方向")
        self.stripe_orientation_combo.setFixedWidth(70)
        stripe_layout.addWidget(self.stripe_orientation_combo)
        
        # 创建白条按钮
        btn_create_stripe = QPushButton("✏️ 创建")
        btn_create_stripe.clicked.connect(self.prepare_stripe_mode)
        btn_create_stripe.setStyleSheet("padding: 5px 10px; background-color: #e3f2fd; font-weight: bold; min-width: 60px;")
        btn_create_stripe.setToolTip("设置当前绘制模式为白条马赛克")
        stripe_layout.addWidget(btn_create_stripe)
        
        stripe_group.setLayout(stripe_layout)
        middle_row.addWidget(stripe_group)
        
        middle_row.addStretch()
        main_layout.addLayout(middle_row)
        
        # 第三行：缩小比例操作
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(10)
        
        # 缩小比例操作组
        scale_group = QGroupBox("图片缩放")
        scale_layout = QHBoxLayout()
        scale_layout.setSpacing(8)
        
        # 比例输入
        scale_layout.addWidget(QLabel("缩放比例:"))
        self.scale_percent_spin = QSpinBox()
        self.scale_percent_spin.setRange(10, 100)
        self.scale_percent_spin.setValue(100)
        self.scale_percent_spin.setSuffix("%")
        self.scale_percent_spin.setToolTip("设置图片缩小比例")
        self.scale_percent_spin.setFixedWidth(70)
        self.scale_percent_spin.valueChanged.connect(self.update_scaled_size_display)
        scale_layout.addWidget(self.scale_percent_spin)
        
        # 预计图片大小显示
        self.scaled_size_label = QLabel("原尺寸")
        self.scaled_size_label.setToolTip("缩小后的预计尺寸")
        self.scaled_size_label.setFixedWidth(110)
        scale_layout.addWidget(self.scaled_size_label)
        
        # 复制缩小图片按钮
        btn_copy_scaled = QPushButton("📋 复制缩小图")
        btn_copy_scaled.clicked.connect(self.copy_scaled_image_to_clipboard)
        btn_copy_scaled.setStyleSheet("padding: 5px 10px; background-color: #fff3e0; font-weight: bold; min-width: 100px;")
        btn_copy_scaled.setToolTip("将缩小后的图片复制到剪贴板")
        scale_layout.addWidget(btn_copy_scaled)
        
        scale_group.setLayout(scale_layout)
        bottom_row.addWidget(scale_group)
        
        bottom_row.addStretch()
        main_layout.addLayout(bottom_row)
        
        return main_layout
        
    def import_image(self):
        """导入图片"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择图片", "", "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif);;ZIP文件 (*.zip)"
        )
        
        if file_path:
            # 检查是否为ZIP文件
            if ZipToGifConverter.is_zip_file(file_path):
                self.handle_zip_file(file_path)
            else:
                self.load_image_from_path(file_path)
    
    def load_image_from_path(self, file_path):
        """从路径加载图片（支持拖放）"""
        pixmap = QPixmap(file_path)
        if pixmap.isNull():
            self.log_message("✗ 无法加载图片")
            QMessageBox.critical(self, "错误", "无法加载图片！")
            return
            
        self.image_view.set_image(pixmap)
        self.current_image_path = file_path
        self.btn_export.setEnabled(True)
        
        # 更新状态，显示图片大小
        img_size = pixmap.size()
        self.current_image_size = img_size
        self.update_status(f"已导入: {os.path.basename(file_path)} | 尺寸: {img_size.width()}×{img_size.height()} | 准备绘制马赛克")
        
        # 更新缩小尺寸显示
        self.update_scaled_size_display()
            
    def paste_image_from_clipboard(self):
        """从剪贴板粘贴图片"""
        # 获取剪贴板
        clipboard = self.clipboard
        
        # 检查剪贴板是否有图片
        if clipboard.mimeData().hasImage():
            # 弹出确认对话框
            reply = QMessageBox.question(
                self, "确认导入",
                "是否导入剪切板中的图片？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            
            if reply == QMessageBox.Yes:
                # 从剪贴板获取图片
                image = clipboard.image()
                if not image.isNull():
                    # 转换为 QPixmap
                    pixmap = QPixmap.fromImage(image)
                    
                    # 显示图片
                    self.image_view.set_image(pixmap)
                    self.current_image_path = None  # 不是从文件加载的
                    self.btn_export.setEnabled(True)
                    
                    # 更新状态
                    img_size = pixmap.size()
                    self.current_image_size = img_size
                    self.log_message("✓ 从剪贴板粘贴图片")
                    self.log_message(f"  尺寸: {img_size.width()}×{img_size.height()}")
                    self.update_status(f"已从剪贴板粘贴 | 尺寸: {img_size.width()}×{img_size.height()} | 准备绘制马赛克")
                    
                    # 更新缩小尺寸显示
                    self.update_scaled_size_display()
                    
                    return True
                else:
                    self.log_message("✗ 剪贴板中的图片无效")
                    QMessageBox.warning(self, "警告", "剪贴板中的图片无效！")
                    return False
            else:
                # 用户取消
                return False
        else:
            self.log_message("✗ 剪贴板中没有图片")
            QMessageBox.warning(self, "警告", "剪贴板中没有图片！")
            return False
            
    def copy_image_to_clipboard(self):
        """将当前图片（含马赛克）复制到剪贴板"""
        if not self.image_view.image_item:
            self.log_message("✗ 没有图片可以复制")
            QMessageBox.warning(self, "警告", "没有图片可以复制！")
            return
        
        # 弹出确认对话框
        reply = QMessageBox.question(
            self, "确认复制",
            "是否将打码图片复制到剪贴板？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        
        if reply == QMessageBox.Yes:
            # 导出当前图片（原始尺寸）
            output_pixmap = self.image_view.export_image()
            if output_pixmap:
                # 复制到剪贴板
                self.clipboard.setPixmap(output_pixmap)
                self.log_message("✓ 图片已复制到剪贴板")
                self.update_status("图片已复制到剪贴板")
            else:
                self.log_message("✗ 复制失败")
                QMessageBox.critical(self, "错误", "复制失败！")
        # 如果用户选择No，则不执行任何操作
            
    def export_image(self):
        """导出图片"""
        if not self.current_image_path:
            return
            
        # 显示导出设置对话框
        dialog = ExportDialog(self.current_image_size, self)
        if dialog.exec_() != QDialog.Accepted:
            return
            
        max_size = dialog.get_max_size()
        
        # 选择保存路径
        default_name = os.path.splitext(os.path.basename(self.current_image_path))[0] + "_mosaic.png"
        save_path, _ = QFileDialog.getSaveFileName(
            self, "保存图片", default_name, "PNG文件 (*.png);;JPG文件 (*.jpg);;BMP文件 (*.bmp)"
        )
        
        if save_path:
            # 导出图片
            output_pixmap = self.image_view.export_image(max_size)
            if output_pixmap and output_pixmap.save(save_path):
                export_size = output_pixmap.size()
                self.log_message(f"✓ 导出成功: {os.path.basename(save_path)}")
                self.log_message(f"  尺寸: {export_size.width()}×{export_size.height()}")
                self.update_status(f"图片已导出: {os.path.basename(save_path)} | 尺寸: {export_size.width()}×{export_size.height()}")
            else:
                self.log_message(f"✗ 导出失败")
                QMessageBox.critical(self, "错误", "导出失败！")
    
    def load_presets(self):
        """加载预设图片"""
        preset_dir = "./present"
        if not os.path.exists(preset_dir):
            self.log_message("✗ 预设目录不存在")
            return
        
        self.preset_list.clear()
        
        # 添加默认马赛克选项
        default_item = QListWidgetItem()
        default_item.setText("默认马赛克")
        # 创建一个简单的马赛克图标
        default_pixmap = QPixmap(60, 60)
        default_pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(default_pixmap)
        block_size = 15
        for y in range(0, 60, block_size):
            for x in range(0, 60, block_size):
                color = QColor(180, 180, 180) if ((x // block_size + y // block_size) % 2 == 0) else QColor(120, 120, 120)
                painter.fillRect(x, y, block_size, block_size, color)
        painter.end()
        default_item.setIcon(QIcon(default_pixmap))
        default_item.setData(Qt.ItemDataRole.UserRole, None)  # 无自定义图片
        default_item.setData(Qt.ItemDataRole.UserRole + 1, "mosaic")  # 填充模式
        self.preset_list.addItem(default_item)
        
        # 加载预设图片
        preset_count = 0
        for filename in os.listdir(preset_dir):
            if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp')):
                filepath = os.path.join(preset_dir, filename)
                pixmap = QPixmap(filepath)
                if not pixmap.isNull():
                    # 创建列表项
                    item = QListWidgetItem()
                    item.setText(filename)
                    item.setIcon(QIcon(pixmap.scaled(60, 60, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)))
                    item.setData(Qt.ItemDataRole.UserRole, pixmap)
                    item.setData(Qt.ItemDataRole.UserRole + 1, "image")  # 图片模式
                    self.preset_list.addItem(item)
                    preset_count += 1
        
        self.log_message(f"✓ 已加载 {preset_count} 个预设图片")
        # 默认选中第一个（默认马赛克）
        self.preset_list.setCurrentRow(0)
    
    def on_preset_selected(self, item):
        """选择预设图片"""
        fill_mode = item.data(Qt.ItemDataRole.UserRole + 1)
        
        if fill_mode == "mosaic":
            # 选择默认马赛克
            self.image_view.current_fill_mode = "mosaic"
            self.image_view.current_custom_image = None
            self.log_message("✓ 选择填充: 默认马赛克")
            self.update_status("填充内容已设置为: 默认马赛克")
        else:
            pixmap = item.data(Qt.ItemDataRole.UserRole)
            if pixmap:
                # 设置当前填充内容为该图片
                self.image_view.current_fill_mode = "image"
                self.image_view.current_custom_image = pixmap
                self.log_message(f"✓ 选择填充: {item.text()}")
                self.update_status(f"填充内容已设置为: {item.text()}")
    
    def log_message(self, message):
        """添加日志消息"""
        timestamp = QDateTime.currentDateTime().toString("hh:mm:ss")
        self.log_text.append(f"[{timestamp}] {message}")
        
        # 限制日志条目数，防止过多
        max_lines = 100
        if self.log_text.document().lineCount() > max_lines:
            cursor = self.log_text.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            cursor.movePosition(QTextCursor.MoveOperation.Down, QTextCursor.MoveMode.MoveAnchor, self.log_text.document().lineCount() - max_lines)
            cursor.movePosition(QTextCursor.MoveOperation.Start, QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
    
    def update_status(self, message):
        """更新状态栏"""
        self.statusBar().showMessage(message)
    
    def update_opacity(self, value):
        """更新马赛克透明度"""
        opacity = value / 100.0
        self.image_view.current_mosaic_opacity = opacity
        self.opacity_label.setText(f"{value}%")
        self.update_status(f"马赛克透明度: {value}%")
            
    def undo_last_mosaic(self):
        """撤销上一个马赛克"""
        self.image_view.undo_last_mosaic()
        
    def clear_all_mosaics(self):
        """清空所有马赛克"""
        reply = QMessageBox.question(
            self, "确认", "确定要清空所有马赛克区域吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            self.image_view.clear_all_mosaics()
            
    def fit_to_window(self):
        """适应窗口"""
        if self.image_view.image_item:
            self.image_view.fitInView(self.image_view.image_item, Qt.AspectRatioMode.KeepAspectRatio)
            
    def actual_size(self):
        """实际大小"""
        if self.image_view.image_item:
            self.image_view.setTransform(QTransform())
    
    def prepare_stripe_mode(self):
        """准备白条马赛克模式"""
        # 获取白条设置
        text = self.stripe_text_input.text().strip()
        if not text:
            QMessageBox.warning(self, "警告", "请输入白条文字！")
            return
        
        font_family = self.stripe_font_combo.currentText()
        font_size = self.stripe_font_size_spin.value()
        orientation = "horizontal" if self.stripe_orientation_combo.currentText() == "水平" else "vertical"
        
        # 应用到ImageView的当前设置
        self.image_view.current_fill_mode = "stripe"
        self.image_view.stripe_text = text
        self.image_view.stripe_font_family = font_family
        self.image_view.stripe_font_size = font_size
        self.image_view.stripe_orientation = orientation
        
        # 更新状态
        orient_text = "水平" if orientation == "horizontal" else "垂直"
        self.log_message(f"✓ 白条模式已准备: {orient_text} | 文字: {text} | 字体: {font_family} | 大小: {font_size}")
        self.update_status(f"准备绘制白条: {text} ({orient_text})")
        
        # 让图片视图获得焦点，准备绘制
        self.image_view.setFocus()
    
    def update_scaled_size_display(self):
        """更新缩小后的预计图片大小"""
        if not self.current_image_size:
            self.scaled_size_label.setText("无图片")
            return
        
        scale_percent = self.scale_percent_spin.value()
        if scale_percent == 100:
            self.scaled_size_label.setText(f"{self.current_image_size.width()}×{self.current_image_size.height()}")
        else:
            scaled_width = int(self.current_image_size.width() * scale_percent / 100)
            scaled_height = int(self.current_image_size.height() * scale_percent / 100)
            self.scaled_size_label.setText(f"{scaled_width}×{scaled_height}")
    
    def handle_zip_file(self, zip_path):
        """处理ZIP文件，提示是否转换为GIF"""
        # 首先检查是否是有效的ZIP文件
        if not zipfile.is_zipfile(zip_path):
            self.log_message("✗ 不是有效的ZIP文件")
            QMessageBox.warning(self, "警告", "不是有效的ZIP文件！")
            return
        
        # 检查ffmpeg是否可用
        if not ZipToGifConverter.check_ffmpeg():
            self.log_message("✗ 未找到ffmpeg，无法转换ZIP文件")
            QMessageBox.warning(self, "警告", "未找到ffmpeg，无法转换ZIP文件！\n请先安装ffmpeg并添加到系统PATH中。")
            return
        
        # 弹出确认对话框
        reply = QMessageBox.question(
            self, "转换ZIP文件",
            f"检测到ZIP文件: {os.path.basename(zip_path)}\n\n是否转换为GIF动画？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        
        if reply == QMessageBox.Yes:
            self.log_message(f"→ 开始转换ZIP文件: {os.path.basename(zip_path)}")
            
            # 显示进度对话框
            progress = QMessageBox(self)
            progress.setWindowTitle("转换中")
            progress.setText("正在转换ZIP文件为GIF...")
            progress.setStandardButtons(QMessageBox.NoButton)
            progress.show()
            QApplication.processEvents()
            
            # 转换ZIP为GIF
            gif_path = ZipToGifConverter.convert_zip_to_gif(zip_path)
            
            progress.close()
            
            if gif_path and os.path.exists(gif_path):
                self.log_message(f"✓ ZIP转换成功: {os.path.basename(gif_path)}")
                
                # 加载转换后的GIF
                self.load_image_from_path(gif_path)
                
                # 提示是否复制到剪贴板或导出
                self.show_gif_options(gif_path)
            else:
                self.log_message("✗ ZIP转换失败")
                QMessageBox.critical(self, "错误", "ZIP文件转换失败！\n请确保ZIP文件中包含animation.json和正确的图片文件。")
    
    def show_gif_options(self, gif_path):
        """显示GIF操作选项"""
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("GIF转换完成")
        msg_box.setText(f"ZIP文件已成功转换为GIF:\n{os.path.basename(gif_path)}")
        
        # 添加自定义按钮
        export_button = msg_box.addButton("导出到文件", QMessageBox.ActionRole)
        cancel_button = msg_box.addButton("取消", QMessageBox.RejectRole)
        
        msg_box.exec_()
        
        clicked_button = msg_box.clickedButton()
        if clicked_button == export_button:
            # 导出到文件
            default_name = os.path.basename(gif_path)
            save_path, _ = QFileDialog.getSaveFileName(
                self, "保存GIF", default_name, "GIF文件 (*.gif)"
            )
            
            if save_path:
                try:
                    # 复制文件
                    with open(gif_path, 'rb') as src, open(save_path, 'wb') as dst:
                        dst.write(src.read())
                    
                    self.log_message(f"✓ GIF已导出: {os.path.basename(save_path)}")
                    self.update_status(f"GIF已导出: {os.path.basename(save_path)}")
                    QMessageBox.information(self, "成功", f"GIF已导出到:\n{save_path}")
                except Exception as e:
                    self.log_message(f"✗ 导出失败: {e}")
                    QMessageBox.critical(self, "错误", f"导出失败！\n{e}")
    
    def copy_scaled_image_to_clipboard(self):
        """将缩小后的图片复制到剪贴板"""
        if not self.image_view.image_item:
            self.log_message("✗ 没有图片可以复制")
            QMessageBox.warning(self, "警告", "没有图片可以复制！")
            return
        
        scale_percent = self.scale_percent_spin.value()
        if scale_percent == 100:
            # 如果比例为100%，直接复制原图
            reply = QMessageBox.question(
                self, "确认复制",
                f"当前比例为100%，将复制原尺寸图片 ({self.current_image_size.width()}×{self.current_image_size.height()}) 到剪贴板？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
        else:
            scaled_width = int(self.current_image_size.width() * scale_percent / 100)
            scaled_height = int(self.current_image_size.height() * scale_percent / 100)
            reply = QMessageBox.question(
                self, "确认复制",
                f"是否将缩小后的图片 ({scaled_width}×{scaled_height}) 复制到剪贴板？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
        
        if reply == QMessageBox.Yes:
            # 导出缩小后的图片
            if scale_percent == 100:
                output_pixmap = self.image_view.export_image()
            else:
                # 计算缩小后的最大边长
                max_size = max(self.current_image_size.width(), self.current_image_size.height()) * scale_percent / 100
                output_pixmap = self.image_view.export_image(max_size=int(max_size))
            
            if output_pixmap:
                # 复制到剪贴板
                self.clipboard.setPixmap(output_pixmap)
                export_size = output_pixmap.size()
                self.log_message(f"✓ 缩小图片已复制到剪贴板 ({export_size.width()}×{export_size.height()})")
                self.update_status(f"缩小图片已复制: {export_size.width()}×{export_size.height()}")
            else:
                self.log_message("✗ 复制失败")
                QMessageBox.critical(self, "错误", "复制失败！")
            
    def update_status(self, message):
        """更新状态栏"""
        self.statusBar().showMessage(message)


def main():
    app = QApplication(sys.argv)
    
    # 设置应用程序样式
    app.setStyle("Fusion")
    
    # 创建并显示主窗口
    window = MosaicEditor()
    window.show()
    
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
