from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QLabel, 
                             QVBoxLayout, QHBoxLayout, QPushButton, QFileDialog,
                             QSlider, QSpinBox, QComboBox, QGroupBox, QMenu,
                             QMessageBox, QGraphicsView, QGraphicsScene, 
                             QGraphicsRectItem, QGraphicsPixmapItem,
                             QGraphicsItem, QDialog, QTextEdit, QListWidget,
                             QListWidgetItem, QInputDialog, QLineEdit)
from PyQt5.QtCore import Qt, QRectF, QSize, QDateTime
from PyQt5.QtGui import (QPixmap, QPainter, QPen, QBrush, QColor,
                         QTransform, QIcon, QTextCursor)
import os

class MosaicRectItem(QGraphicsRectItem):
    """可编辑的马赛克矩形区域"""
    
    def __init__(self, rect, parent=None, preset_dir=None):
        super().__init__(rect, parent)
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                     QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                     QGraphicsItem.GraphicsItemFlag.ItemIsFocusable |
                     QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.setZValue(10)
        
        # 控制点大小
        self.handle_size = 8
        self.handle_space = -4
        
        # 控制点位置
        self.handle_top_left = 1
        self.handle_top_middle = 2
        self.handle_top_right = 3
        self.handle_middle_left = 4
        self.handle_middle_right = 5
        self.handle_bottom_left = 6
        self.handle_bottom_middle = 7
        self.handle_bottom_right = 8
        
        # 当前控制点
        self.handle_selected = None
        self.mouse_press_pos = None
        self.mouse_press_rect = None
        
        # 马赛克内容类型
        self.fill_mode = "mosaic"  # mosaic, color, image, stripe
        self.custom_image = None
        self.fill_color = QColor(128, 128, 128)
        self.mosaic_opacity = 1.0
        self.preset_dir = preset_dir
        self.presets = {}
        
        # 全白条马赛克属性
        self.stripe_text = "该信息已被管理员撤回"  # 白条上的文字
        self.stripe_orientation = "horizontal"  # horizontal, vertical
        self.stripe_width = 80  # 白条宽度
        self.stripe_font_family = "Times New Roman"  # 字体
        self.stripe_font_size = 25  # 字体大小
        
        # 加载预设
        if self.preset_dir and os.path.exists(self.preset_dir):
            self.load_presets()
        
        # 更新控制点
        self.update_handles_pos()
        
    def load_presets(self):
        """加载预设图片"""
        self.presets = {}
        try:
            for filename in os.listdir(self.preset_dir):
                if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp')):
                    filepath = os.path.join(self.preset_dir, filename)
                    self.presets[filename] = QPixmap(filepath)
        except Exception:
            pass
    
    def update_handles_pos(self):
        """更新控制点位置"""
        s = self.handle_size + self.handle_space
        b = self.handle_size
        
        self.handles = {}
        rect = self.rect()
        
        self.handles[self.handle_top_left] = QRectF(rect.left() - s, rect.top() - s, b, b)
        self.handles[self.handle_top_middle] = QRectF(rect.center().x() - b/2, rect.top() - s, b, b)
        self.handles[self.handle_top_right] = QRectF(rect.right() - b + s, rect.top() - s, b, b)
        self.handles[self.handle_middle_left] = QRectF(rect.left() - s, rect.center().y() - b/2, b, b)
        self.handles[self.handle_middle_right] = QRectF(rect.right() - b + s, rect.center().y() - b/2, b, b)
        self.handles[self.handle_bottom_left] = QRectF(rect.left() - s, rect.bottom() - b + s, b, b)
        self.handles[self.handle_bottom_middle] = QRectF(rect.center().x() - b/2, rect.bottom() - b + s, b, b)
        self.handles[self.handle_bottom_right] = QRectF(rect.right() - b + s, rect.bottom() - b + s, b, b)
        
    def handle_at(self, point):
        """返回指定位置的控制点，增加吸附功能"""
        # 吸附距离（像素）
        snap_distance = 15
        
        for k, v in self.handles.items():
            # 扩大检测范围以实现吸附
            expanded_rect = v.adjusted(-snap_distance/2, -snap_distance/2, 
                                      snap_distance/2, snap_distance/2)
            if expanded_rect.contains(point):
                return k
        return None
        
    def hoverMoveEvent(self, event):
        """鼠标悬停时改变光标形状"""
        # 只有选中时才显示控制点光标
        if not self.isSelected():
            if self.rect().contains(event.pos()):
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
            super().hoverMoveEvent(event)
            return
        
        handle = self.handle_at(event.pos())
        cursor = Qt.CursorShape.ArrowCursor
        
        if handle == self.handle_top_left or handle == self.handle_bottom_right:
            cursor = Qt.CursorShape.SizeFDiagCursor
        elif handle == self.handle_bottom_left or handle == self.handle_top_right:
            cursor = Qt.CursorShape.SizeBDiagCursor
        elif handle == self.handle_middle_left or handle == self.handle_middle_right:
            cursor = Qt.CursorShape.SizeHorCursor
        elif handle == self.handle_top_middle or handle == self.handle_bottom_middle:
            cursor = Qt.CursorShape.SizeVerCursor
        elif self.handle_at(event.pos()) is None and self.rect().contains(event.pos()):
            cursor = Qt.CursorShape.OpenHandCursor
            
        self.setCursor(cursor)
        super().hoverMoveEvent(event)
        
    def hoverLeaveEvent(self, event):
        """鼠标离开时恢复光标"""
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().hoverLeaveEvent(event)
        
    def mousePressEvent(self, event):
        """鼠标按下事件"""
        if event.button() == Qt.MouseButton.RightButton:
            # 右键点击显示菜单
            if self.rect().contains(event.pos()):
                self.show_context_menu(event.screenPos())
                event.accept()
                return
        elif event.button() == Qt.MouseButton.LeftButton:
            # 清除其他所有马赛克的选中状态
            if self.scene():
                for item in self.scene().items():
                    if isinstance(item, MosaicRectItem) and item != self:
                        item.setSelected(False)
            
            # 只有选中时才能调整控制点
            if self.isSelected():
                self.handle_selected = self.handle_at(event.pos())
                if self.handle_selected:
                    self.mouse_press_pos = event.pos()
                    self.mouse_press_rect = self.rect()
                    event.accept()
                    return
            
            if self.rect().contains(event.pos()):
                # 先选中自己
                self.setSelected(True)
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                super().mousePressEvent(event)
                return
                
        super().mousePressEvent(event)
        
    def mouseMoveEvent(self, event):
        """鼠标移动事件"""
        if self.handle_selected:
            self.interactive_resize(event.pos())
            if self.scene():
                # 更新控制点区域以避免残影
                self.scene().update(self.mapRectToScene(self.rect()).adjusted(-20, -20, 20, 20))
        else:
            super().mouseMoveEvent(event)
            
    def mouseReleaseEvent(self, event):
        """鼠标释放事件"""
        self.handle_selected = None
        self.mouse_press_pos = None
        self.mouse_press_rect = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().mouseReleaseEvent(event)
        
    def show_context_menu(self, screen_pos):
        """显示右键菜单"""
        menu = QMenu()
        
        # 马赛克内容菜单
        fill_menu = menu.addMenu("马赛克内容")
        
        mosaic_action = fill_menu.addAction("默认马赛克")
        mosaic_action.triggered.connect(lambda: self.set_fill_mode("mosaic"))
        
        color_action = fill_menu.addAction("纯色填充")
        color_action.triggered.connect(lambda: self.set_fill_mode("color"))
        
        image_action = fill_menu.addAction("选择自定义图片...")
        image_action.triggered.connect(self.select_custom_image)
        
        # 预设图片菜单
        if self.presets:
            preset_menu = fill_menu.addMenu("预设图片")
            for name, pixmap in self.presets.items():
                preset_action = preset_menu.addAction(name)
                preset_action.triggered.connect(lambda checked, p=pixmap: self.set_custom_image(p))
        
        # 不透明度菜单
        opacity_menu = menu.addMenu("不透明度")
        opacity_values = [("100%", 1.0), ("90%", 0.9), ("80%", 0.8),
                          ("70%", 0.7), ("60%", 0.6), ("50%", 0.5),
                          ("40%", 0.4), ("30%", 0.3)]
        for text, value in opacity_values:
            action = opacity_menu.addAction(text)
            action.triggered.connect(lambda checked, v=value: self.set_opacity(v))
        
        # 如果是白条马赛克，添加编辑选项
        if self.fill_mode == "stripe":
            menu.addSeparator()
            edit_text_action = menu.addAction("编辑文字...")
            edit_text_action.triggered.connect(self.edit_stripe_text)
            
            edit_font_action = menu.addAction("编辑字体...")
            edit_font_action.triggered.connect(self.edit_stripe_font)
        
        # 删除选项
        menu.addSeparator()
        delete_action = menu.addAction("删除马赛克")
        delete_action.triggered.connect(self.delete_mosaic)
        
        menu.exec_(screen_pos)
    
    def set_fill_mode(self, mode):
        """设置填充模式"""
        self.fill_mode = mode
        self.update()
    
    def set_custom_image(self, pixmap):
        """设置自定义图片"""
        self.custom_image = pixmap
        self.fill_mode = "image"
        self.update()
    
    def select_custom_image(self):
        """选择自定义图片"""
        file_path, _ = QFileDialog.getOpenFileName(
            None, "选择图片", "", "图片文件 (*.png *.jpg *.jpeg *.bmp)"
        )
        if file_path:
            pixmap = QPixmap(file_path)
            if not pixmap.isNull():
                self.set_custom_image(pixmap)
    
    def set_stripe_mode(self, orientation):
        """设置全白条模式"""
        # 弹出输入框获取文字
        text, ok = QInputDialog.getText(None, "输入文字", "请输入要显示的文字：")
        if ok:
            self.fill_mode = "stripe"
            self.stripe_orientation = orientation
            self.stripe_text = text
            self.update()
    
    def edit_stripe_text(self):
        """编辑白条文字"""
        text, ok = QInputDialog.getText(None, "编辑文字", "请输入要显示的文字：", text=self.stripe_text)
        if ok:
            self.stripe_text = text
            self.update()
    
    def edit_stripe_font(self):
        """编辑白条字体"""
        # 字体选择
        fonts = ["Arial", "Times New Roman", "Courier New", "Verdana", "Microsoft YaHei", "SimHei", "SimSun"]
        font, ok1 = QInputDialog.getItem(None, "选择字体", "字体：", fonts, fonts.index(self.stripe_font_family) if self.stripe_font_family in fonts else 0, False)
        
        # 字体大小
        size, ok2 = QInputDialog.getInt(None, "字体大小", "大小：", value=self.stripe_font_size, min=8, max=72)
        
        if ok1 and ok2:
            self.stripe_font_family = font
            self.stripe_font_size = size
            self.update()
    
    def set_opacity(self, opacity):
        """设置不透明度"""
        self.mosaic_opacity = opacity
        self.update()
    
    def delete_mosaic(self):
        """删除马赛克"""
        if self.scene():
            self.setSelected(False)  # 清除选中状态
            self.scene().removeItem(self)
            self.scene().update()  # 刷新场景，清除控制点
    
    def itemChange(self, change, value):
        """项目改变事件"""
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self.scene():
            # 当位置改变时更新控制点并刷新场景，避免残影
            self.update_handles_pos()
            self.scene().update(self.mapRectToScene(self.rect()).adjusted(-20, -20, 20, 20))
        return super().itemChange(change, value)
    
    def interactive_resize(self, mouse_pos):
        """交互式调整大小"""
        rect = QRectF(self.mouse_press_rect)
        diff = mouse_pos - self.mouse_press_pos
        
        # 检查是否按下了Shift键（等比例缩放）
        is_shift_pressed = QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier
        
        if self.handle_selected == self.handle_top_left:
            new_top_left = self.mouse_press_rect.topLeft() + diff
            if is_shift_pressed:
                # 等比例缩放
                new_width = self.mouse_press_rect.right() - new_top_left.x()
                new_height = new_width * (self.mouse_press_rect.height() / self.mouse_press_rect.width())
                new_top_left.setY(self.mouse_press_rect.bottom() - new_height)
            rect.setTopLeft(new_top_left)
        elif self.handle_selected == self.handle_top_middle:
            rect.setTop(self.mouse_press_rect.top() + diff.y())
        elif self.handle_selected == self.handle_top_right:
            new_top_right = self.mouse_press_rect.topRight() + diff
            if is_shift_pressed:
                new_width = new_top_right.x() - self.mouse_press_rect.left()
                new_height = new_width * (self.mouse_press_rect.height() / self.mouse_press_rect.width())
                new_top_right.setY(self.mouse_press_rect.bottom() - new_height)
            rect.setTopRight(new_top_right)
        elif self.handle_selected == self.handle_middle_left:
            rect.setLeft(self.mouse_press_rect.left() + diff.x())
        elif self.handle_selected == self.handle_middle_right:
            rect.setRight(self.mouse_press_rect.right() + diff.x())
        elif self.handle_selected == self.handle_bottom_left:
            new_bottom_left = self.mouse_press_rect.bottomLeft() + diff
            if is_shift_pressed:
                new_width = self.mouse_press_rect.right() - new_bottom_left.x()
                new_height = new_width * (self.mouse_press_rect.height() / self.mouse_press_rect.width())
                new_bottom_left.setY(self.mouse_press_rect.top() + new_height)
            rect.setBottomLeft(new_bottom_left)
        elif self.handle_selected == self.handle_bottom_middle:
            rect.setBottom(self.mouse_press_rect.bottom() + diff.y())
        elif self.handle_selected == self.handle_bottom_right:
            new_bottom_right = self.mouse_press_rect.bottomRight() + diff
            if is_shift_pressed:
                new_width = new_bottom_right.x() - self.mouse_press_rect.left()
                new_height = new_width * (self.mouse_press_rect.height() / self.mouse_press_rect.width())
                new_bottom_right.setY(self.mouse_press_rect.top() + new_height)
            rect.setBottomRight(new_bottom_right)
            
        if rect.width() > 10 and rect.height() > 10:
            self.setRect(rect)
            self.update_handles_pos()
            # 强制更新场景以消除残影
            if self.scene():
                self.scene().update()
            
    def paint(self, painter, option, widget=None):
        """绘制马赛克矩形和控制点"""
        rect = self.rect()
        painter.save()
        painter.setOpacity(self.mosaic_opacity)
        painter.setClipRect(rect)
        
        if self.fill_mode == "mosaic":
            # 绘制马赛克图案
            block_size = 15
            for y in range(int(rect.top()), int(rect.bottom()), block_size):
                for x in range(int(rect.left()), int(rect.right()), block_size):
                    color = QColor(180, 180, 180) if ((x // block_size + y // block_size) % 2 == 0) else QColor(120, 120, 120)
                    painter.fillRect(x, y, block_size, block_size, color)
        elif self.fill_mode == "color":
            # 绘制纯色
            painter.fillRect(rect, self.fill_color)
        elif self.fill_mode == "image" and self.custom_image:
            # 绘制自定义图片（完整显示，不裁剪）
            scaled_pixmap = self.custom_image.scaled(
                rect.size().toSize(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            # 居中绘制
            x = int(rect.center().x() - scaled_pixmap.width() / 2)
            y = int(rect.center().y() - scaled_pixmap.height() / 2)
            painter.drawPixmap(x, y, scaled_pixmap)
        elif self.fill_mode == "stripe":
            # 绘制全白条马赛克
            painter.fillRect(rect, QColor(255, 255, 255))
            
            # 如果有文字，绘制文字
            if self.stripe_text:
                painter.setPen(QColor(0, 0, 0))
                font = painter.font()
                font.setFamily(self.stripe_font_family)
                font.setPointSize(self.stripe_font_size)
                font.setBold(True)
                painter.setFont(font)
                
                if self.stripe_orientation == "vertical":
                    # 垂直白条 - 竖排文字
                    # 计算每个字符的绘制位置
                    metrics = painter.fontMetrics()
                    line_height = metrics.height()
                    char_width = metrics.averageCharWidth()
                    
                    # 计算总高度（所有字符）
                    total_height = line_height * len(self.stripe_text)
                    
                    # 起始Y位置（垂直居中）
                    start_y = rect.center().y() - total_height / 2 + line_height / 2
                    
                    # 每个字符的X位置（水平居中）
                    center_x = rect.center().x()
                    
                    # 逐个字符绘制，每个字符占一行
                    for i, char in enumerate(self.stripe_text):
                        char_y = start_y + i * line_height
                        # 计算每个字符的水平居中位置
                        char_rect = QRectF(center_x - char_width, char_y - line_height/2, char_width*2, line_height)
                        painter.drawText(char_rect, Qt.AlignmentFlag.AlignCenter, char)
                else:
                    # 水平白条 - 横排文字
                    # 计算文字位置（居中）
                    text_flags = Qt.AlignmentFlag.AlignCenter
                    painter.drawText(rect, text_flags, self.stripe_text)
        
        painter.restore()
        
        # 如果是选中状态，绘制边框和控制点
        if self.isSelected():
            # 绘制边框
            pen = QPen(QColor(0, 150, 255), 2, Qt.PenStyle.SolidLine)
            painter.setPen(pen)
            painter.drawRect(rect)
            
            # 绘制控制点
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(QBrush(QColor(0, 150, 255)))
            painter.setPen(Qt.PenStyle.NoPen)
            
            # 只在鼠标悬停或调整大小时绘制控制点，减少残影
            for handle, handle_rect in self.handles.items():
                if self.handle_selected is not None or self.isUnderMouse():
                    painter.drawRect(handle_rect)
                    
        super().paint(painter, option, widget)
