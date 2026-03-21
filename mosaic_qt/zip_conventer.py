import sys
import os
import warnings
import zipfile
import json
import tempfile
import subprocess
from pathlib import Path

class ZipToGifConverter:
    """ZIP转GIF转换器"""
    
    @staticmethod
    def is_zip_file(file_path):
        """检查文件是否为ZIP格式"""
        return file_path.lower().endswith('.zip')
    
    @staticmethod
    def convert_zip_to_gif(zip_path, output_path=None):
        """
        将ZIP文件转换为GIF
        
        Args:
            zip_path: ZIP文件路径
            output_path: 输出GIF路径，如果为None则使用临时文件
            
        Returns:
            成功返回GIF文件路径，失败返回None
        """
        try:
            if not os.path.isfile(zip_path):
                return None
                
            with tempfile.TemporaryDirectory() as tmpdir:
                tmpdir = Path(tmpdir)
                
                # 1. 解压ZIP文件
                with zipfile.ZipFile(zip_path) as z:
                    z.extractall(tmpdir)
                
                # 2. 检查是否存在animation.json
                anim_json = tmpdir / "animation.json"
                if not anim_json.exists():
                    # 尝试查找子目录中的animation.json
                    subdirs = [d for d in tmpdir.iterdir() if d.is_dir()]
                    if subdirs:
                        sub_anim_json = subdirs[0] / "animation.json"
                        if sub_anim_json.exists():
                            anim_json = sub_anim_json
                            # 重新设置tmpdir为子目录
                            tmpdir = subdirs[0]
                        else:
                            return None
                    else:
                        return None
                
                # 3. 读取animation.json
                with open(anim_json, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                # 4. 生成ffmpeg concat文件
                concat = []
                for frame in data["frames"]:
                    frame_file = tmpdir / frame['file']
                    if frame_file.exists():
                        concat.append(f"file '{frame_file}'")
                        concat.append(f"duration {frame['delay'] / 1000}")
                
                if not concat:
                    return None
                    
                # 添加最后一帧（重复最后一帧文件，不指定duration）
                concat.append(f"file '{tmpdir / data['frames'][-1]['file']}'")
                
                concat_path = tmpdir / "frames.txt"
                concat_path.write_text("\n".join(concat), encoding="utf-8")
                
                # 5. 确定输出路径
                if output_path is None:
                    output_path = str(Path(zip_path).with_suffix('.gif'))
                
                # 6. 使用ffmpeg转换
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-f", "concat",
                    "-safe", "0",
                    "-i", str(concat_path),
                    "-vf", "scale=640:-1:flags=lanczos",
                    "-loop", "0",
                    output_path
                ]
                
                result = subprocess.run(cmd, capture_output=True, text=True)
                
                if result.returncode == 0 and os.path.exists(output_path):
                    return output_path
                else:
                    print(f"FFmpeg error: {result.stderr}")
                    return None
                    
        except Exception as e:
            print(f"转换错误: {e}")
            return None
    
    @staticmethod
    def check_ffmpeg():
        """检查ffmpeg是否可用"""
        try:
            result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
            return result.returncode == 0
        except:
            return False
