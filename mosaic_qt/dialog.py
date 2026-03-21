from PyQt5.QtWidgets import QLabel, QSpinBox, QDialog, QDialogButtonBox, QFormLayout

class ExportDialog(QDialog):
    """导出设置对话框"""
    
    def __init__(self, original_size, parent=None):
        super().__init__(parent)
        self.original_size = original_size
        self.setWindowTitle("导出设置")
        self.setModal(True)
        
        layout = QFormLayout(self)
        
        # 显示原始尺寸
        layout.addRow("原始尺寸：", QLabel(f"{original_size.width()}×{original_size.height()}"))
        
        # 最大尺寸
        self.max_size_spin = QSpinBox()
        self.max_size_spin.setRange(0, 10000)
        self.max_size_spin.setValue(1920)
        self.max_size_spin.setSuffix(" px")
        self.max_size_spin.setSpecialValueText("原始尺寸")
        self.max_size_spin.valueChanged.connect(self.update_export_size)
        layout.addRow("最大边长：", self.max_size_spin)
        
        # 导出后尺寸
        self.export_size_label = QLabel()
        layout.addRow("导出尺寸：", self.export_size_label)
        
        # 按钮
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addRow(button_box)
        
        # 初始化导出尺寸显示
        self.update_export_size()
        
    def update_export_size(self):
        """更新导出尺寸显示"""
        max_size = self.max_size_spin.value()
        if max_size == 0:
            export_size = self.original_size
        else:
            scale = min(max_size / self.original_size.width(), 
                       max_size / self.original_size.height(), 1.0)
            export_size = self.original_size * scale
        
        self.export_size_label.setText(f"{int(export_size.width())}×{int(export_size.height())}")
        
    def get_max_size(self):
        """获取最大尺寸"""
        value = self.max_size_spin.value()
        return value if value > 0 else None
