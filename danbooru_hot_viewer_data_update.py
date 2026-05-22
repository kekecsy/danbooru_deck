# update_viewer_data.py
import os
import json
import requests
from time import sleep
from datetime import datetime
import danbooru_api

# --- 配置区 ---
BASE_DIR = './hot_pic'
RATE_LIMIT_DELAY = 1  # 请求间隔（秒），避免触发API限制
# --- 配置结束 ---

def get_date_folders():
    """获取所有日期文件夹"""
    if not os.path.exists(BASE_DIR):
        print(f"错误: 找不到 {BASE_DIR}")
        return []
    
    folders = []
    for item in os.listdir(BASE_DIR):
        item_path = os.path.join(BASE_DIR, item)
        # 检查是否是文件夹且符合日期格式 (YYYY-MM-DD)
        if os.path.isdir(item_path) and len(item) == 10 and item.count('-') == 2:
            try:
                datetime.strptime(item, '%Y-%m-%d')
                folders.append(item)
            except ValueError:
                continue
    
    return sorted(folders, reverse=True)  # 最新的在前

def get_actual_files(folder_path):
    """获取文件夹中实际存在的图片文件"""
    actual_files = set()
    for f in os.listdir(folder_path):
        if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webm', '.mp4')):
            actual_files.add(f)
    return actual_files

def fetch_post_tags(post_id, retries=3):
    """获取指定ID的标签信息"""
    url = f'{danbooru_api.post_url(post_id)}.json'
    
    for attempt in range(retries):
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            # 提取所有标签信息（不使用 tag_string，只用子集）
            tags = {
                'tag_string_general': data.get('tag_string_general', ''),
                'tag_string_character': data.get('tag_string_character', ''),
                'tag_string_copyright': data.get('tag_string_copyright', ''),
                'tag_string_artist': data.get('tag_string_artist', ''),
                'tag_string_meta': data.get('tag_string_meta', '')
            }
            return tags
        except Exception as e:
            print(f"  获取ID {post_id} 失败 (尝试 {attempt+1}/{retries}): {e}")
            if attempt < retries - 1:
                sleep(2)
    return None

def extract_post_id_from_url(post_url):
    """从 post URL 中提取 ID"""
    # URL 格式: https://danbooru.donmai.us/posts/10857729
    return post_url.split('/')[-1]

def update_viewer_data_for_date(date_str):
    """更新指定日期的 viewer_data.json"""
    print(f"\n{'='*60}")
    print(f"处理日期: {date_str}")
    print(f"{'='*60}")
    
    date_dir = os.path.join(BASE_DIR, date_str)
    viewer_data_path = os.path.join(date_dir, 'viewer_data.json')
    
    # 检查文件是否存在
    if not os.path.exists(viewer_data_path):
        print(f"警告: 找不到 {viewer_data_path}，跳过")
        return False
    
    # 加载 viewer_data.json
    print(f"加载 {viewer_data_path}...")
    try:
        with open(viewer_data_path, 'r', encoding='utf-8') as f:
            viewer_data = json.load(f)
    except Exception as e:
        print(f"错误: 加载文件失败: {e}")
        return False
    
    print(f"原始记录数: {len(viewer_data)}")
    
    # 获取实际存在的图片文件
    actual_files = get_actual_files(date_dir)
    print(f"实际存在的图片文件: {len(actual_files)} 个")
    
    # 筛选并更新数据
    updated_data = []
    skipped_count = 0
    updated_count = 0
    failed_count = 0
    
    for i, item in enumerate(viewer_data):
        filename = item.get('filename')
        post_url = item.get('post_url')
        
        if not filename or not post_url:
            print(f"[{i+1}/{len(viewer_data)}] 警告: 数据不完整，跳过")
            skipped_count += 1
            continue
        
        # 检查文件是否实际存在
        if filename not in actual_files:
            print(f"[{i+1}/{len(viewer_data)}] 文件不存在，删除记录: {filename}")
            skipped_count += 1
            continue
        
        # 检查是否已有 tags 信息
        if 'tags' in item and all(key in item['tags'] for key in [
            'tag_string_general', 'tag_string_character', 
            'tag_string_copyright', 'tag_string_artist', 'tag_string_meta'
        ]):
            # 已经有完整的 tags 信息
            print(f"[{i+1}/{len(viewer_data)}] 已包含完整标签，跳过: {filename}")
            updated_data.append(item)
            continue
        
        # 需要更新 tags 信息
        print(f"[{i+1}/{len(viewer_data)}] 正在更新: {filename}")
        post_id = extract_post_id_from_url(post_url)
        tags = fetch_post_tags(post_id)
        
        if tags:
            item['tags'] = tags
            updated_data.append(item)
            updated_count += 1
            print(f"  成功更新标签信息")
        else:
            print(f"  获取标签失败，保留原数据")
            updated_data.append(item)
            failed_count += 1
        
        # 限速
        sleep(RATE_LIMIT_DELAY)
    
    # 保存更新后的文件
    if len(updated_data) != len(viewer_data):
        # 创建备份
        backup_path = viewer_data_path + '.backup_' + datetime.now().strftime('%Y%m%d_%H%M%S')
        try:
            os.rename(viewer_data_path, backup_path)
            print(f"原文件已备份到: {backup_path}")
        except Exception as e:
            print(f"警告: 备份失败: {e}")
        
        # 保存更新后的数据
        try:
            with open(viewer_data_path, 'w', encoding='utf-8') as f:
                json.dump(updated_data, f, ensure_ascii=False, indent=4)
            print(f"更新后的文件已保存到: {viewer_data_path}")
        except Exception as e:
            print(f"错误: 保存文件失败: {e}")
            return False
    else:
        print("数据未变化，无需更新")
    
    print(f"\n更新完成:")
    print(f"  保留的记录数: {len(updated_data)}")
    print(f"  删除的记录数: {skipped_count}")
    print(f"  更新的记录数: {updated_count}")
    print(f"  失败的记录数: {failed_count}")
    
    return True

def main():
    """主函数"""
    print("Danbooru 旧版本 viewer_data.json 更新工具")
    print("="*60)
    
    # 获取所有日期文件夹
    date_folders = get_date_folders()
    
    if not date_folders:
        print("未找到任何日期文件夹")
        return
    
    print(f"发现 {len(date_folders)} 个日期文件夹:")
    for i, folder in enumerate(date_folders[:10]):  # 只显示前10个
        print(f"  {i+1}. {folder}")
    
    if len(date_folders) > 10:
        print(f"  ... 还有 {len(date_folders) - 10} 个")
    
    # 询问用户选择
    print("\n请选择操作:")
    print("1. 更新所有日期文件夹")
    print("2. 更新指定的日期文件夹")
    print("3. 更新最近N天的文件夹")
    
    choice = input("\n请输入选项 (1-3): ").strip()
    
    if choice == '1':
        folders_to_update = date_folders
    elif choice == '2':
        date_input = input("请输入日期 (格式: YYYY-MM-DD): ").strip()
        if date_input in date_folders:
            folders_to_update = [date_input]
        else:
            print(f"错误: 未找到日期 {date_input}")
            return
    elif choice == '3':
        try:
            days = int(input("请输入要更新的最近天数: ").strip())
            folders_to_update = date_folders[:days]
        except ValueError:
            print("错误: 请输入有效的数字")
            return
    else:
        print("错误: 无效选项")
        return
    
    # 确认
    print(f"\n准备更新 {len(folders_to_update)} 个文件夹:")
    for folder in folders_to_update:
        print(f"  - {folder}")
    
    confirm = input("\n是否继续? (y/n): ").strip().lower()
    if confirm != 'y':
        print("操作已取消")
        return
    
    # 开始更新
    print("\n开始更新...")
    success_count = 0
    
    for date_str in folders_to_update:
        if update_viewer_data_for_date(date_str):
            success_count += 1
    
    print(f"\n{'='*60}")
    print(f"更新完成! 成功更新 {success_count}/{len(folders_to_update)} 个文件夹")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
