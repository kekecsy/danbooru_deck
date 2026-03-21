from PyQt5.QtWidgets import (QGraphicsView, QGraphicsScene, 
                             QGraphicsRectItem, QGraphicsPixmapItem)
from PyQt5.QtCore import Qt, QRectF, QSize, QDateTime
from PyQt5.QtGui import (QPixmap, QPainter, QPen, QBrush, QColor,
                         QTransform, QIcon, QTextCursor)
from mosaic_base import MosaicRectItem
from zip_conventer import ZipToGifConverter
import os

class ImageView(QGraphicsView):
    """图片显示视图"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        
        # 设置渲染选项
        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        
        # 设置场景背景
        self.scene.setBackgroundBrush(QBrush(QColor(240, 240, 240)))
        
        # 启用拖放
        self.setAcceptDrops(True)
        
        # 图片项
        self.image_item = None
        
        # 绘制模式（始终启用）
        self.drawing_mode = True
        self.draw_start = None
        self.draw_rect = None
        
        # 马赛克画笔大小
        self.mosaic_size = 50
        
        # 撤销栈
        self.undo_stack = []
        
        # 预设目录
        self.preset_dir = "./present"
        if not os.path.exists(self.preset_dir):
            self.preset_dir = None
        
        # 拖放状态跟踪
        self.drag_active = False
        
        # 当前选中的填充内容（用于绘制）
        self.current_fill_mode = "mosaic"  # mosaic, color, image, stripe
        self.current_custom_image = None
        self.current_fill_color = QColor(128, 128, 128)
        self.current_mosaic_opacity = 1.0
        self.stripe_text = "该信息已被管理员撤回"
        self.stripe_orientation = "horizontal"
        self.stripe_font_family = "Times New Roman"
        self.stripe_font_size = 25
        
    def set_image(self, pixmap):
        """设置图片"""
        self.scene.clear()
        self.image_item = QGraphicsPixmapItem(pixmap)
        self.scene.addItem(self.image_item)
        self.image_item.setZValue(0)
        self.scene.setSceneRect(self.image_item.boundingRect())
        self.fitInView(self.image_item, Qt.AspectRatioMode.KeepAspectRatio)
        
        # 让视图获得焦点，以便接收键盘事件
        self.setFocus()
        
    def mousePressEvent(self, event):
        """鼠标按下事件"""
        if event.button() == Qt.LeftButton:
            scene_pos = self.mapToScene(event.pos())
            
            # 先检查是否点击了马赛克
            items = self.scene.items(scene_pos)
            mosaic_items = [item for item in items if isinstance(item, MosaicRectItem)]
            
            if mosaic_items:
                # 点击了马赛克，让scene处理事件（传递给item）
                super().mousePressEvent(event)
            elif self.image_item and self.image_item.contains(scene_pos):
                # 点击在图片上，但没有点击马赛克，开始绘制
                # 使用画笔大小创建固定尺寸的马赛克
                self.draw_start = scene_pos
                self.draw_rect = QGraphicsRectItem()
                self.draw_rect.setPen(QPen(QColor(0, 150, 255), 2, Qt.PenStyle.DashLine))
                self.draw_rect.setBrush(QBrush(QColor(0, 150, 255, 50)))
                self.scene.addItem(self.draw_rect)
            else:
                # 没有点击马赛克和图片，拖动视图
                self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
                super().mousePressEvent(event)
        else:
            super().mousePressEvent(event)
            
    def mouseMoveEvent(self, event):
        """鼠标移动事件"""
        if self.draw_start and self.draw_rect:
            # 拖动绘制，根据鼠标移动更新矩形大小
            scene_pos = self.mapToScene(event.pos())
            rect = QRectF(self.draw_start, scene_pos).normalized()
            self.draw_rect.setRect(rect)
        else:
            super().mouseMoveEvent(event)
            
    def mouseReleaseEvent(self, event):
        """鼠标释放事件"""
        if event.button() == Qt.LeftButton and self.draw_start:
            # 根据拖动的矩形创建马赛克
            scene_pos = self.mapToScene(event.pos())
            rect = QRectF(self.draw_start, scene_pos).normalized()
            
            # 只有矩形足够大才创建（避免误点击）
            if rect.width() > 10 and rect.height() > 10:
                mosaic_item = MosaicRectItem(rect, preset_dir=self.preset_dir)
                
                # 应用当前选中的填充内容
                mosaic_item.fill_mode = self.current_fill_mode
                mosaic_item.custom_image = self.current_custom_image
                mosaic_item.fill_color = self.current_fill_color
                mosaic_item.mosaic_opacity = self.current_mosaic_opacity
                
                # 如果是全白条马赛克，设置文字和方向，并自动调整大小以贯穿整个图片
                if self.current_fill_mode == "stripe" and self.image_item:
                    mosaic_item.stripe_text = self.stripe_text
                    mosaic_item.stripe_orientation = self.stripe_orientation
                    mosaic_item.stripe_font_family = self.stripe_font_family
                    mosaic_item.stripe_font_size = self.stripe_font_size
                    
                    img_rect = self.image_item.boundingRect()
                    item_pos = mosaic_item.scenePos()
                    
                    if self.stripe_orientation == "horizontal":
                        # 水平白条：贯穿左右，保持用户拖动的高度
                        new_rect = QRectF(
                            img_rect.left() - item_pos.x(),
                            rect.top(),
                            img_rect.width(),
                            rect.height()
                        )
                        mosaic_item.setRect(new_rect)
                    else:
                        # 垂直白条：贯穿上下的，保持用户拖动的宽度
                        new_rect = QRectF(
                            rect.left(),
                            img_rect.top() - item_pos.y(),
                            rect.width(),
                            img_rect.height()
                        )
                        mosaic_item.setRect(new_rect)
                
                self.scene.addItem(mosaic_item)
                self.undo_stack.append(mosaic_item)
                
                self.parent.update_status(f"已添加马赛克区域，共 {len(self.undo_stack)} 个")
            
            # 清理临时绘制
            if self.draw_rect:
                self.scene.removeItem(self.draw_rect)
                self.draw_rect = None
                
            self.draw_start = None
        else:
            super().mouseReleaseEvent(event)
            
        # 恢复拖动模式
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
    
    def dragEnterEvent(self, event):
        """拖入事件"""
        if event.mimeData().hasUrls():
            self.drag_active = True
            event.acceptProposedAction()
    
    def dragMoveEvent(self, event):
        """拖动事件"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
    
    def dragLeaveEvent(self, event):
        """拖离事件"""
        if self.drag_active:
            self.drag_active = False
            event.accept()
    
    def dropEvent(self, event):
        """放下事件"""
        urls = event.mimeData().urls()
        if urls:
            file_path = urls[0].toLocalFile()
            if file_path and os.path.isfile(file_path):
                # 检查文件类型
                ext = os.path.splitext(file_path)[1].lower()
                if ext in ['.png', '.jpg', '.jpeg', '.bmp', '.gif']:
                    self.parent.load_image_from_path(file_path)
                    event.acceptProposedAction()
                elif ZipToGifConverter.is_zip_file(file_path):
                    # 处理ZIP文件
                    self.parent.handle_zip_file(file_path)
                    event.acceptProposedAction()
                else:
                    self.parent.log_message("✗ 不支持的文件格式")
    
    def wheelEvent(self, event):
        """滚轮事件 - Ctrl+滚轮缩放"""
        # 检查是否按下了Ctrl键
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # 获取滚轮增量
            delta = event.angleDelta().y()
            
            # 计算缩放因子（每次滚动缩放10%）
            if delta > 0:
                scale_factor = 1.1  # 放大
            else:
                scale_factor = 0.9  # 缩小
            
            # 当前缩放
            current_scale = self.transform().m11()
            new_scale = current_scale * scale_factor
            
            # 限制缩放范围（20% 到 500%）
            if new_scale < 0.2 or new_scale > 5.0:
                return
            
            # 以鼠标位置为中心进行缩放
            mouse_pos = event.position()
            old_pos = self.mapToScene(mouse_pos.toPoint())
            
            # 应用缩放
            self.scale(scale_factor, scale_factor)
            
            # 调整视图位置，使鼠标位置保持不变
            new_pos = self.mapToScene(mouse_pos.toPoint())
            delta_pos = new_pos - old_pos
            self.translate(delta_pos.x(), delta_pos.y())
            
            event.accept()
        else:
            # 没有按Ctrl，使用默认行为
            super().wheelEvent(event)
    
    def keyPressEvent(self, event):
        """键盘按下事件 - 处理复制粘贴"""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.key() == Qt.Key.Key_C:
                # Ctrl+C 复制图片到剪贴板
                self.parent.copy_image_to_clipboard()
            elif event.key() == Qt.Key.Key_V:
                # Ctrl+V 粘贴图片（从剪贴板）
                if not self.parent.current_image_path and not self.parent.image_view.image_item:
                    # 没有图片，尝试从剪贴板粘贴图片
                    self.parent.paste_image_from_clipboard()
                # 如果已有图片，Ctrl+V 不执行任何操作（已移除粘贴马赛克功能）
            else:
                super().keyPressEvent(event)
        else:
            super().keyPressEvent(event)
    
    def clear_all_mosaics(self):
        """清空所有马赛克"""
        if self.image_item:
            items_to_remove = []
            for item in self.scene.items():
                if isinstance(item, MosaicRectItem):
                    items_to_remove.append(item)
                    
            for item in items_to_remove:
                self.scene.removeItem(item)
                
            self.undo_stack.clear()
            self.parent.update_status("已清空所有马赛克区域")
            
    def undo_last_mosaic(self):
        """撤销上一个马赛克"""
        if self.undo_stack:
            last_item = self.undo_stack.pop()
            self.scene.removeItem(last_item)
            self.parent.update_status(f"已撤销，剩余 {len(self.undo_stack)} 个马赛克区域")
            
    def export_image(self, max_size=None):
        """导出图片"""
        if not self.image_item:
            return None
            
        # 获取原始图片尺寸
        original_pixmap = self.image_item.pixmap()
        original_size = original_pixmap.size()
        
        # 创建输出图片
        if max_size:
            # 计算缩放比例
            scale = min(max_size / original_size.width(), max_size / original_size.height(), 1.0)
            output_size = original_size * scale
        else:
            output_size = original_size
            scale = 1.0
            
        output_pixmap = QPixmap(output_size)
        output_pixmap.fill(Qt.GlobalColor.transparent)
        
        painter = QPainter(output_pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        
        # 绘制原始图片
        scaled_pixmap = original_pixmap.scaled(output_size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        painter.drawPixmap(0, 0, scaled_pixmap)
        
        # 绘制马赛克
        for item in self.scene.items():
            if isinstance(item, MosaicRectItem):
                # 获取马赛克在场景中的绝对位置
                item_pos = item.scenePos()
                rect = item.rect()
                
                # 计算在输出图片中的位置和大小（考虑缩放）
                scaled_rect = QRectF(
                    (item_pos.x() + rect.left()) * scale,
                    (item_pos.y() + rect.top()) * scale,
                    rect.width() * scale,
                    rect.height() * scale
                )
                
                painter.save()
                painter.setOpacity(item.mosaic_opacity)
                painter.setClipRect(scaled_rect)
                
                if item.fill_mode == "mosaic":
                    # 绘制马赛克图案
                    block_size = int(15 * scale)
                    if block_size < 3:
                        block_size = 3
                        
                    for y in range(int(scaled_rect.top()), int(scaled_rect.bottom()), block_size):
                        for x in range(int(scaled_rect.left()), int(scaled_rect.right()), block_size):
                            color = QColor(180, 180, 180) if ((x // block_size + y // block_size) % 2 == 0) else QColor(120, 120, 120)
                            painter.fillRect(x, y, block_size, block_size, color)
                elif item.fill_mode == "color":
                    # 绘制纯色
                    painter.fillRect(scaled_rect, item.fill_color)
                elif item.fill_mode == "image" and item.custom_image:
                    # 绘制自定义图片
                    scaled_pixmap = item.custom_image.scaled(
                        scaled_rect.size().toSize(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation
                    )
                    # 居中绘制
                    x = int(scaled_rect.center().x() - scaled_pixmap.width() / 2)
                    y = int(scaled_rect.center().y() - scaled_pixmap.height() / 2)
                    painter.drawPixmap(x, y, scaled_pixmap)
                elif item.fill_mode == "stripe":
                    # 绘制全白条马赛克
                    painter.fillRect(scaled_rect, QColor(255, 255, 255))
                    
                    # 如果有文字，绘制文字
                    if item.stripe_text:
                        painter.setPen(QColor(0, 0, 0))
                        font = painter.font()
                        font.setFamily(item.stripe_font_family)
                        font.setPointSize(int(item.stripe_font_size * scale))
                        font.setBold(True)
                        painter.setFont(font)
                        
                        if item.stripe_orientation == "vertical":
                            # 垂直白条 - 竖排文字
                            # 计算每个字符的绘制位置
                            metrics = painter.fontMetrics()
                            line_height = metrics.height()
                            char_width = metrics.averageCharWidth()
                            
                            # 计算总高度（所有字符）
                            total_height = line_height * len(item.stripe_text)
                            
                            # 起始Y位置（垂直居中）
                            start_y = scaled_rect.center().y() - total_height / 2 + line_height / 2
                            
                            # 每个字符的X位置（水平居中）
                            center_x = scaled_rect.center().x()
                            
                            # 逐个字符绘制，每个字符占一行
                            for i, char in enumerate(item.stripe_text):
                                char_y = start_y + i * line_height
                                # 计算每个字符的水平居中位置
                                char_rect = QRectF(center_x - char_width, char_y - line_height/2, char_width*2, line_height)
                                painter.drawText(char_rect, Qt.AlignmentFlag.AlignCenter, char)
                        else:
                            # 水平白条 - 横排文字
                            # 计算文字位置（居中）
                            text_flags = Qt.AlignmentFlag.AlignCenter
                            painter.drawText(scaled_rect, text_flags, item.stripe_text)
                
                painter.restore()
                        
        painter.end()
        
        return output_pixmap
