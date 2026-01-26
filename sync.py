#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import time
import requests
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timezone, timedelta

class LeetCodeSyncer:
    def __init__(self, sync_after: Optional[str] = None, debug: bool = False):
        """
        初始化同步器
        
        Args:
            sync_after: 只同步此时间之后的提交，格式: "2026-01-26 23:47" (北京时间)
            debug: 是否启用调试模式
        """
        # 优先使用 LeetCode CN
        self.use_cn = bool(os.getenv('LEETCODE_CN_SESSION'))
        
        if self.use_cn:
            self.base_url = "https://leetcode.cn"
            self.session_cookie = os.getenv('LEETCODE_CN_SESSION')
            self.csrf_token = os.getenv('LEETCODE_CN_CSRF_TOKEN')
            print("✅ 使用 LeetCode CN")
        else:
            self.base_url = "https://leetcode.com"
            self.session_cookie = os.getenv('LEETCODE_SESSION')
            self.csrf_token = os.getenv('LEETCODE_CSRF_TOKEN')
            print("✅ 使用 LeetCode Global")
        
        if not self.session_cookie:
            raise ValueError("❌ 未找到 LeetCode Session Cookie，请检查环境变量")
        
        self.session = requests.Session()
        self.session.cookies.set('LEETCODE_SESSION', self.session_cookie)
        if self.csrf_token:
            self.session.cookies.set('csrftoken', self.csrf_token)
        
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': self.base_url,
            'Origin': self.base_url,
            'Accept': 'application/json',
        })
        
        if self.csrf_token:
            self.session.headers['X-CSRFToken'] = self.csrf_token
        
        self.synced_file = Path('.synced_submissions.json')
        self.synced_ids = self.load_synced_ids()
        self.debug = debug
        
        if debug:
            print("🐛 调试模式已启用")
        
        # 设置时间过滤
        self.sync_after_timestamp = self._parse_sync_after_time(sync_after)
        if self.sync_after_timestamp:
            dt = datetime.fromtimestamp(self.sync_after_timestamp, tz=timezone(timedelta(hours=8)))
            print(f"⏰ 只同步 {dt.strftime('%Y-%m-%d %H:%M:%S')} (北京时间) 之后的提交")
    
    def _parse_sync_after_time(self, time_str: Optional[str]) -> Optional[int]:
        """解析时间字符串为 Unix 时间戳"""
        if not time_str:
            return self._get_last_sync_time()
        
        try:
            dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M")
            dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
            return int(dt.timestamp())
        except Exception as e:
            print(f"⚠️  时间格式解析失败 '{time_str}': {e}")
            return None
    
    def _get_last_sync_time(self) -> Optional[int]:
        """从配置文件获取上次同步时间"""
        if self.synced_file.exists():
            try:
                with open(self.synced_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    last_sync = data.get('last_sync')
                    if last_sync:
                        dt = datetime.fromisoformat(last_sync)
                        print(f"📅 上次同步时间: {data.get('last_sync_beijing', 'Unknown')}")
                        return int(dt.timestamp())
            except:
                pass
        return None
    
    def load_synced_ids(self) -> set:
        """加载已同步的提交ID"""
        if self.synced_file.exists():
            try:
                with open(self.synced_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    synced_ids = set(data.get('synced_ids', []))
                    if synced_ids:
                        print(f"📦 已加载 {len(synced_ids)} 条同步记录")
                    return synced_ids
            except:
                return set()
        return set()
    
    def save_synced_ids(self):
        """保存已同步的提交ID"""
        with open(self.synced_file, 'w', encoding='utf-8') as f:
            json.dump({
                'synced_ids': list(self.synced_ids),
                'last_sync': datetime.now().isoformat(),
                'last_sync_beijing': datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
            }, f, indent=2, ensure_ascii=False)
    
    def get_ac_submissions(self) -> List[Dict]:
        """获取所有AC的提交记录"""
        print("🔍 正在获取AC提交记录...")
        
        url = f"{self.base_url}/api/submissions/"
        params = {'offset': 0, 'limit': 20, 'lastkey': ''}
        
        all_submissions = []
        seen_ids = set()
        page = 0
        
        while True:
            try:
                page += 1
                if self.debug:
                    print(f"  📄 获取第 {page} 页...")
                
                response = self.session.get(url, params=params, timeout=30)
                
                if response.status_code == 403:
                    print(f"⚠️  请求被限制 (403)，等待5秒...")
                    time.sleep(5)
                    continue
                
                response.raise_for_status()
                data = response.json()
                
                submissions = data.get('submissions_dump', [])
                if not submissions:
                    break
                
                should_stop = False
                for sub in submissions:
                    sub_id = sub.get('id')
                    timestamp = sub.get('timestamp')
                    
                    # 时间过滤
                    if self.sync_after_timestamp and timestamp:
                        if int(timestamp) < self.sync_after_timestamp:
                            should_stop = True
                            break
                    
                    if sub.get('status_display') == 'Accepted' and sub_id not in seen_ids:
                        seen_ids.add(sub_id)
                        all_submissions.append(sub)
                
                if should_stop:
                    print(f"⏹️  已到达时间截止点，停止获取")
                    break
                
                if not data.get('has_next', False):
                    break
                
                params['offset'] += params['limit']
                params['lastkey'] = submissions[-1].get('id', '')
                time.sleep(1)
                
            except Exception as e:
                print(f"❌ 获取提交记录出错: {e}")
                break
        
        print(f"✅ 共获取到 {len(all_submissions)} 条AC提交记录")
        return all_submissions
    
    def get_submission_detail(self, submission_id: int) -> Optional[Dict]:
        """获取提交详情（包含代码）"""
        url = f"{self.base_url}/api/submissions/{submission_id}/"
        
        for retry in range(3):
            try:
                response = self.session.get(url, timeout=30)
                response.raise_for_status()
                return response.json()
            except Exception as e:
                if retry < 2:
                    time.sleep(2)
                    continue
                if self.debug:
                    print(f"  ❌ 获取详情失败: {e}")
                return None
    
    def has_valid_comment(self, code: str) -> bool:
        """检查代码是否包含有效的目录结构注释"""
        if not code:
            return False
        
        lines = code.strip().split('\n')
        
        # 至少需要2行注释（1个目录 + 文件名）
        if len(lines) < 2:
            return False
        
        if self.debug:
            print(f"  📝 代码前10行:")
            for i in range(min(10, len(lines))):
                print(f"     {i+1}: {lines[i][:100]}")
        
        # 检测注释类型
        comment_prefix = None
        if lines[0].strip().startswith('//'):
            comment_prefix = '//'
        elif lines[0].strip().startswith('#'):
            comment_prefix = '#'
        else:
            if self.debug:
                print(f"  ❌ 第一行不是注释")
            return False
        
        # 收集连续的注释行
        comment_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(comment_prefix):
                content = stripped[len(comment_prefix):].strip()
                if content:  # 忽略空注释
                    comment_lines.append(content)
            else:
                break  # 遇到非注释行就停止
        
        if self.debug:
            print(f"  📋 找到 {len(comment_lines)} 行连续注释:")
            for i, line in enumerate(comment_lines, 1):
                print(f"     {i}: {line}")
        
        # 至少需要2行（1个目录 + 文件名）
        if len(comment_lines) < 2:
            if self.debug:
                print(f"  ❌ 注释行数不足（需要至少2行）")
            return False
        
        # 最后一行应该是文件名
        last_line = comment_lines[-1]
        if not self._looks_like_filename(last_line):
            if self.debug:
                print(f"  ❌ 最后一行不像文件名: {last_line}")
            return False
        
        # 前面的行应该是目录（不能是文件名）
        directories = comment_lines[:-1]
        for i, dir_name in enumerate(directories, 1):
            if self._looks_like_filename(dir_name):
                if self.debug:
                    print(f"  ❌ 第{i}行看起来像文件名而不是目录: {dir_name}")
                return False
            if len(dir_name) < 2 or len(dir_name) > 100:
                if self.debug:
                    print(f"  ❌ 第{i}行长度不合法: {dir_name}")
                return False
        
        if self.debug:
            print(f"  ✅ 验证通过: {len(directories)} 级目录")
        
        return True
    
    def parse_comment(self, code: str) -> Tuple[Optional[List[str]], Optional[str]]:
        """
        解析代码注释，提取目录结构和文件名
        
        Returns:
            (目录列表, 文件名) 或 (None, None)
        """
        if not code:
            return None, None
        
        lines = code.strip().split('\n')
        
        if len(lines) < 2:
            return None, None
        
        # 检测注释类型
        comment_prefix = None
        if lines[0].strip().startswith('//'):
            comment_prefix = '//'
        elif lines[0].strip().startswith('#'):
            comment_prefix = '#'
        else:
            return None, None
        
        # 收集连续的注释行
        comment_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(comment_prefix):
                content = stripped[len(comment_prefix):].strip()
                if content:
                    comment_lines.append(content)
            else:
                break
        
        if len(comment_lines) < 2:
            return None, None
        
        # 最后一行是文件名
        filename = comment_lines[-1]
        if not self._looks_like_filename(filename):
            return None, None
        
        # 前面的行是目录
        directories = comment_lines[:-1]
        
        # 验证所有目录
        for dir_name in directories:
            if self._looks_like_filename(dir_name):
                return None, None
            if len(dir_name) < 2 or len(dir_name) > 100:
                return None, None
        
        return directories, filename
    
    def _looks_like_filename(self, text: str) -> bool:
        """检查文本是否看起来像文件名"""
        if not text:
            return True
        
        # 如果包含文件扩展名
        if re.search(r'\.(cpp|java|py|js|go|c|cs|rb|swift|kt|rs|php|ts|txt|md)$', text, re.IGNORECASE):
            return True
        
        # 如果以数字和点开头（题号）
        if re.match(r'^\d+\.', text):
            return True
        
        # 如果包含多个连字符（通常是文件名格式）
        if text.count('-') >= 2:
            return True
        
        return False
    
    def get_file_extension(self, lang: str) -> str:
        """根据语言获取文件扩展名"""
        ext_map = {
            'cpp': 'cpp', 'c++': 'cpp', 'java': 'java',
            'python': 'py', 'python3': 'py',
            'javascript': 'js', 'typescript': 'ts',
            'golang': 'go', 'go': 'go', 'rust': 'rs',
            'c': 'c', 'csharp': 'cs', 'c#': 'cs',
            'ruby': 'rb', 'swift': 'swift', 'kotlin': 'kt',
            'scala': 'scala', 'php': 'php',
        }
        return ext_map.get(lang.lower(), 'txt')
    
    def sanitize_path_component(self, name: str) -> str:
        """清理路径组件，移除非法字符"""
        if not name:
            return 'untitled'
        
        # 移除 Windows 和 Unix 非法字符
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name)
        name = name.strip('. \t\n\r')
        
        if not name:
            return 'untitled'
        
        if len(name) > 100:
            name = name[:100]
        
        return name
    
    def extract_title_from_filename(self, filename: str) -> str:
        """
        从注释中的文件名提取题目名称
        例如: "2841. 几乎唯一子数组的最大和.cpp" -> "2841. 几乎唯一子数组的最大和"
        """
        # 去掉文件扩展名
        title = re.sub(r'\.(cpp|java|py|js|go|c|cs|rb|swift|kt|rs|php|ts)$', '', filename, flags=re.IGNORECASE)
        return title.strip()
    
    def delete_old_versions(self, dir_path: Path, title_pattern: str, current_file: Path):
        """
        删除同一题目的旧版本文件
        
        Args:
            dir_path: 目录路径
            title_pattern: 题目名称模式（用于匹配）
            current_file: 当前要保存的文件（不删除）
        """
        if not dir_path.exists():
            return
        
        # 提取题号（如果有）
        match = re.match(r'^(\d+)\.', title_pattern)
        if match:
            problem_id = match.group(1)
            # 查找所有以相同题号开头的文件
            deleted_count = 0
            for file in dir_path.glob(f"{problem_id}.*"):
                if file != current_file and file.is_file():
                    try:
                        file.unlink()
                        deleted_count += 1
                        if self.debug:
                            print(f"  🗑️  删除旧版本: {file.name}")
                    except Exception as e:
                        if self.debug:
                            print(f"  ⚠️  删除失败 {file.name}: {e}")
            
            if deleted_count > 0 and not self.debug:
                print(f"  🗑️  删除了 {deleted_count} 个旧版本")
    
    def save_submission(self, submission: Dict, detail: Dict) -> bool:
        """保存提交到本地文件"""
        code = detail.get('code', '')
        if not code:
            if self.debug:
                print(f"  ❌ 没有代码内容")
            return False
        
        directories, filename = self.parse_comment(code)
        
        if not directories or not filename:
            if self.debug:
                print(f"  ⊘ 跳过：没有有效的目录结构注释")
            return False
        
        # 清理所有目录名
        safe_dirs = [self.sanitize_path_component(d) for d in directories]
        
        # 构建目录路径
        dir_path = Path(*safe_dirs)
        
        try:
            dir_path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"  ❌ 创建目录失败 {dir_path}: {e}")
            return False
        
        # 从注释中的文件名提取完整题目名称（保留题号）
        title = self.extract_title_from_filename(filename)
        safe_title = self.sanitize_path_component(title)
        
        # 使用实际的语言扩展名
        lang = detail.get('lang', 'txt')
        ext = self.get_file_extension(lang)
        
        file_name = f"{safe_title}.{ext}"
        file_path = dir_path / file_name
        
        if self.debug:
            print(f"  📂 目录结构: {' / '.join(safe_dirs)}")
            print(f"  📄 文件名: {file_name}")
            print(f"  📍 完整路径: {file_path}")
        
        # 删除同一题目的旧版本
        self.delete_old_versions(dir_path, safe_title, file_path)
        
        # 如果文件已存在，检查内容是否相同
        if file_path.exists():
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    existing_code = f.read()
                if existing_code == code:
                    print(f"  ⊙ 已存在（内容相同）: {file_path}")
                    return True
                else:
                    print(f"  ♻️  更新文件: {file_path}")
            except:
                pass
        
        # 保存文件
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(code)
            print(f"  ✅ 已保存: {file_path}")
            return True
        except Exception as e:
            print(f"  ❌ 保存失败 {file_path}: {e}")
            return False
    
    def sync(self):
        """执行同步"""
        print("=" * 60)
        print("🚀 开始同步 LeetCode 提交记录")
        print("=" * 60)
        
        submissions = self.get_ac_submissions()
        
        if not submissions:
            print("📭 没有找到AC提交记录")
            return
        
        new_submissions = [
            sub for sub in submissions 
            if str(sub['id']) not in self.synced_ids
        ]
        
        if not new_submissions:
            print("✨ 没有新的提交需要同步")
            return
        
        print(f"\n📦 共 {len(new_submissions)} 条新提交，开始检查...")
        print("-" * 60)
        
        success_count = 0
        skipped_count = 0
        failed_count = 0
        
        for i, submission in enumerate(new_submissions, 1):
            sub_id = submission['id']
            title = submission.get('title', 'Unknown')
            timestamp = submission.get('timestamp')
            
            time_str = ""
            if timestamp:
                dt = datetime.fromtimestamp(int(timestamp), tz=timezone(timedelta(hours=8)))
                time_str = f" [{dt.strftime('%Y-%m-%d %H:%M')}]"
            
            print(f"\n[{i}/{len(new_submissions)}] {title}{time_str} (ID: {sub_id})")
            
            detail = self.get_submission_detail(sub_id)
            if not detail:
                failed_count += 1
                time.sleep(1)
                continue
            
            code = detail.get('code', '')
            if not self.has_valid_comment(code):
                print(f"  ⊘ 跳过：没有符合格式的目录结构注释")
                skipped_count += 1
                self.synced_ids.add(str(sub_id))
                
                if i % 10 == 0:
                    self.save_synced_ids()
                
                time.sleep(0.5)
                continue
            
            if self.save_submission(submission, detail):
                self.synced_ids.add(str(sub_id))
                success_count += 1
            else:
                failed_count += 1
            
            if i % 10 == 0:
                self.save_synced_ids()
            
            time.sleep(1)
        
        self.save_synced_ids()
        
        print("\n" + "=" * 60)
        print(f"🎉 同步完成！")
        print(f"  ✅ 成功保存: {success_count}")
        print(f"  ⊘ 跳过（无注释）: {skipped_count}")
        print(f"  ❌ 失败: {failed_count}")
        print(f"  📊 总计: {len(new_submissions)}")
        print("=" * 60)

def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description='🤖 同步 LeetCode AC 提交到 GitHub',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python sync.py                                    # 同步所有新提交
  python sync.py --after "2026-01-26 23:47"        # 只同步指定时间之后的提交
  python sync.py --debug                            # 调试模式
  python sync.py --force                            # 强制重新同步所有提交

注释格式要求:
  // 一级目录
  // 二级目录
  // 2841. 几乎唯一子数组的最大和.cpp
  
  ✅ 最后一行必须是文件名（包含题号和扩展名）
  ✅ 前面的行是目录层级（支持任意多级）
  ✅ 同一题目的新提交会自动覆盖旧版本
        """
    )
    parser.add_argument('--after', type=str, help='只同步此时间之后的提交，格式: "2026-01-26 23:47" (北京时间)')
    parser.add_argument('--debug', action='store_true', help='调试模式：显示详细的匹配信息')
    parser.add_argument('--force', action='store_true', help='强制重新同步所有提交（忽略已同步记录）')
    
    args = parser.parse_args()
    
    try:
        syncer = LeetCodeSyncer(sync_after=args.after, debug=args.debug)
        
        if args.force:
            print("⚠️  强制模式：将重新同步所有提交")
            syncer.synced_ids.clear()
        
        syncer.sync()
    except KeyboardInterrupt:
        print("\n\n⏸️  用户中断，正在保存进度...")
        exit(0)
    except Exception as e:
        print(f"\n❌ 同步失败: {e}")
        import traceback
        traceback.print_exc()
        exit(1)

if __name__ == '__main__':
    main()
