#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import time
import requests
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

class LeetCodeSyncer:
    def __init__(self):
        # 优先使用 LeetCode CN
        self.use_cn = bool(os.getenv('LEETCODE_CN_SESSION'))
        
        if self.use_cn:
            self.base_url = "https://leetcode.cn"
            self.session_cookie = os.getenv('LEETCODE_CN_SESSION')
            self.csrf_token = os.getenv('LEETCODE_CN_CSRF_TOKEN')
            print("使用 LeetCode CN")
        else:
            self.base_url = "https://leetcode.com"
            self.session_cookie = os.getenv('LEETCODE_SESSION')
            self.csrf_token = os.getenv('LEETCODE_CSRF_TOKEN')
            print("使用 LeetCode Global")
        
        if not self.session_cookie:
            raise ValueError("未找到 LeetCode Session Cookie")
        
        self.session = requests.Session()
        self.session.cookies.set('LEETCODE_SESSION', self.session_cookie)
        if self.csrf_token:
            self.session.cookies.set('csrftoken', self.csrf_token)
        
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': self.base_url,
            'X-CSRFToken': self.csrf_token or '',
        })
        
        self.synced_file = Path('.synced_submissions.json')
        self.synced_ids = self.load_synced_ids()
    
    def load_synced_ids(self) -> set:
        """加载已同步的提交ID"""
        if self.synced_file.exists():
            try:
                with open(self.synced_file, 'r') as f:
                    data = json.load(f)
                    return set(data.get('synced_ids', []))
            except:
                return set()
        return set()
    
    def save_synced_ids(self):
        """保存已同步的提交ID"""
        with open(self.synced_file, 'w') as f:
            json.dump({
                'synced_ids': list(self.synced_ids),
                'last_sync': datetime.now().isoformat()
            }, f, indent=2)
    
    def get_ac_submissions(self) -> List[Dict]:
        """获取所有AC的提交记录"""
        print("正在获取AC提交记录...")
        
        url = f"{self.base_url}/api/submissions/"
        params = {
            'offset': 0,
            'limit': 20,
            'lastkey': ''
        }
        
        all_submissions = []
        seen_ids = set()
        
        while True:
            try:
                response = self.session.get(url, params=params, timeout=30)
                response.raise_for_status()
                data = response.json()
                
                submissions = data.get('submissions_dump', [])
                if not submissions:
                    break
                
                # 过滤AC的提交
                for sub in submissions:
                    sub_id = sub.get('id')
                    if sub.get('status_display') == 'Accepted' and sub_id not in seen_ids:
                        seen_ids.add(sub_id)
                        all_submissions.append(sub)
                
                # 检查是否有更多数据
                has_next = data.get('has_next', False)
                if not has_next:
                    break
                
                # 更新分页参数
                params['offset'] += params['limit']
                params['lastkey'] = submissions[-1].get('id', '')
                
                time.sleep(0.5)  # 避免请求过快
                
            except Exception as e:
                print(f"获取提交记录出错: {e}")
                break
        
        print(f"共获取到 {len(all_submissions)} 条AC提交记录")
        return all_submissions
    
    def get_submission_detail(self, submission_id: int) -> Optional[Dict]:
        """获取提交详情（包含代码）"""
        url = f"{self.base_url}/api/submissions/{submission_id}/"
        
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"获取提交详情失败 (ID: {submission_id}): {e}")
            return None
    
    def parse_comment(self, code: str) -> tuple:
        """解析代码第一行注释，提取分类信息"""
        lines = code.strip().split('\n')
        if not lines:
            return None, None
        
        first_line = lines[0].strip()
        
        # 匹配注释格式: // 一级分类 / 二级分类
        patterns = [
            r'^//\s*(.+?)\s*/\s*(.+?)$',  # // 分类1 / 分类2
            r'^//\s*(.+?)\s*[/／]\s*(.+?)$',  # 支持中文斜杠
            r'^//\s*(.+?)\s*-\s*(.+?)$',  # // 分类1 - 分类2
        ]
        
        for pattern in patterns:
            match = re.match(pattern, first_line)
            if match:
                category1 = match.group(1).strip()
                category2 = match.group(2).strip()
                return category1, category2
        
        # 如果只有一级分类
        match = re.match(r'^//\s*(.+?)$', first_line)
        if match:
            category = match.group(1).strip()
            # 检查是否包含分隔符
            if '/' in category or '／' in category:
                parts = re.split(r'[/／]', category, 1)
                return parts[0].strip(), parts[1].strip()
            return category, None
        
        return None, None
    
    def get_file_extension(self, lang: str) -> str:
        """根据语言获取文件扩展名"""
        ext_map = {
            'cpp': 'cpp',
            'c++': 'cpp',
            'java': 'java',
            'python': 'py',
            'python3': 'py',
            'javascript': 'js',
            'typescript': 'ts',
            'golang': 'go',
            'go': 'go',
            'rust': 'rs',
            'c': 'c',
            'csharp': 'cs',
            'c#': 'cs',
            'ruby': 'rb',
            'swift': 'swift',
            'kotlin': 'kt',
            'scala': 'scala',
            'php': 'php',
        }
        return ext_map.get(lang.lower(), 'txt')
    
    def sanitize_filename(self, name: str) -> str:
        """清理文件名，移除非法字符"""
        # 移除或替换非法字符
        name = re.sub(r'[<>:"/\\|?*]', '', name)
        name = name.strip('. ')
        return name or 'untitled'
    
    def save_submission(self, submission: Dict, detail: Dict):
        """保存提交到本地文件"""
        code = detail.get('code', '')
        if not code:
            print(f"提交 {submission['id']} 没有代码内容")
            return
        
        # 解析分类
        category1, category2 = self.parse_comment(code)
        
        # 获取题目信息
        title = submission.get('title', 'Unknown')
        title_slug = submission.get('title_slug', 'unknown')
        lang = detail.get('lang', 'txt')
        
        # 构建文件路径
        if category1 and category2:
            # 有两级分类
            dir_path = Path(category1) / category2
        elif category1:
            # 只有一级分类
            dir_path = Path(category1)
        else:
            # 没有分类，使用默认分类
            dir_path = Path('未分类')
        
        # 创建目录
        dir_path.mkdir(parents=True, exist_ok=True)
        
        # 构建文件名
        safe_title = self.sanitize_filename(title)
        ext = self.get_file_extension(lang)
        filename = f"{safe_title}.{ext}"
        file_path = dir_path / filename
        
        # 保存文件
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(code)
            print(f"✓ 已保存: {file_path}")
            return True
        except Exception as e:
            print(f"✗ 保存失败 {file_path}: {e}")
            return False
    
    def sync(self):
        """执行同步"""
        print("=" * 60)
        print("开始同步 LeetCode 提交记录")
        print("=" * 60)
        
        # 获取所有AC提交
        submissions = self.get_ac_submissions()
        
        if not submissions:
            print("没有找到AC提交记录")
            return
        
        # 过滤出新的提交
        new_submissions = [
            sub for sub in submissions 
            if str(sub['id']) not in self.synced_ids
        ]
        
        if not new_submissions:
            print("没有新的提交需要同步")
            return
        
        print(f"\n发现 {len(new_submissions)} 条新提交")
        print("-" * 60)
        
        success_count = 0
        
        for i, submission in enumerate(new_submissions, 1):
            sub_id = submission['id']
            title = submission.get('title', 'Unknown')
            
            print(f"\n[{i}/{len(new_submissions)}] 处理: {title} (ID: {sub_id})")
            
            # 获取详细信息
            detail = self.get_submission_detail(sub_id)
            if not detail:
                continue
            
            # 保存提交
            if self.save_submission(submission, detail):
                self.synced_ids.add(str(sub_id))
                success_count += 1
            
            time.sleep(0.5)  # 避免请求过快
        
        # 保存同步记录
        self.save_synced_ids()
        
        print("\n" + "=" * 60)
        print(f"同步完成！成功: {success_count}/{len(new_submissions)}")
        print("=" * 60)

def main():
    try:
        syncer = LeetCodeSyncer()
        syncer.sync()
    except Exception as e:
        print(f"同步失败: {e}")
        import traceback
        traceback.print_exc()
        exit(1)

if __name__ == '__main__':
    main()
