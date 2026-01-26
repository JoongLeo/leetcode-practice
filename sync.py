#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import time
import requests
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Set
from datetime import datetime, timezone, timedelta
from collections import defaultdict


CODE_EXTS = {'.cpp', '.py', '.java', '.js', '.go', '.c', '.cs', '.rb', '.swift', '.kt', '.rs', '.php', '.ts'}


class LeetCodeSyncer:
    def __init__(self, sync_after: Optional[str] = None, debug: bool = False):
        """
        初始化同步器

        Args:
            sync_after: 只同步此时间之后的提交，格式: "2026-01-26 23:47" (北京时间)
            debug: 是否启用调试模式
        """
        self.debug = debug

        # 优先使用 LeetCode CN
        self.use_cn = bool(os.getenv("LEETCODE_CN_SESSION"))

        if self.use_cn:
            self.base_url = "https://leetcode.cn"
            self.session_cookie = os.getenv("LEETCODE_CN_SESSION")
            self.csrf_token = os.getenv("LEETCODE_CN_CSRF_TOKEN")
            print("✅ 使用 LeetCode CN")
        else:
            self.base_url = "https://leetcode.com"
            self.session_cookie = os.getenv("LEETCODE_SESSION")
            self.csrf_token = os.getenv("LEETCODE_CSRF_TOKEN")
            print("✅ 使用 LeetCode Global")

        if not self.session_cookie:
            raise ValueError("❌ 未找到 LeetCode Session Cookie，请检查环境变量")

        self.session = requests.Session()

        # 注意：LeetCode 的会话 cookie 名通常都是 LEETCODE_SESSION（CN/Global 都是）
        self.session.cookies.set("LEETCODE_SESSION", self.session_cookie)
        if self.csrf_token:
            self.session.cookies.set("csrftoken", self.csrf_token)

        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": self.base_url,
            "Origin": self.base_url,
            "Accept": "application/json",
        })
        if self.csrf_token:
            self.session.headers["X-CSRFToken"] = self.csrf_token

        self.synced_file = Path(".synced_submissions.json")
        self.synced_ids: Set[str] = self.load_synced_ids()

        # 记录本次新增题目（用于生成 commit msg）
        self.new_problems: List[Dict] = []

        if self.debug:
            print("🐛 调试模式已启用")

        # 设置时间过滤
        self.sync_after_timestamp = self._parse_sync_after_time(sync_after)
        if self.sync_after_timestamp:
            dt = datetime.fromtimestamp(self.sync_after_timestamp, tz=timezone(timedelta(hours=8)))
            print(f"⏰ 只同步 {dt.strftime('%Y-%m-%d %H:%M:%S')} (北京时间) 之后的提交")

    # -------------------- time / state --------------------

    def _parse_sync_after_time(self, time_str: Optional[str]) -> Optional[int]:
        """解析时间字符串为 Unix 时间戳（北京时间）"""
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
                with open(self.synced_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                last_sync = data.get("last_sync")
                if last_sync:
                    dt = datetime.fromisoformat(last_sync)
                    print(f"📅 上次同步时间: {data.get('last_sync_beijing', 'Unknown')}")
                    return int(dt.timestamp())
            except Exception:
                pass
        return None

    def load_synced_ids(self) -> Set[str]:
        """加载已同步的提交ID（字符串集合）"""
        if self.synced_file.exists():
            try:
                with open(self.synced_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                synced_ids = set(map(str, data.get("synced_ids", [])))
                if synced_ids:
                    print(f"📦 已加载 {len(synced_ids)} 条同步记录")
                return synced_ids
            except Exception:
                return set()
        return set()

    def save_synced_ids(self):
        """保存已同步的提交ID"""
        now_local = datetime.now(timezone(timedelta(hours=8)))
        payload = {
            "synced_ids": sorted(list(self.synced_ids)),
            "last_sync": datetime.now().isoformat(),
            "last_sync_beijing": now_local.strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(self.synced_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

    # -------------------- leetcode API --------------------

    def get_ac_submissions(self) -> List[Dict]:
        """获取所有 AC 提交记录（从 /api/submissions/ 拉分页）"""
        print("🔍 正在获取AC提交记录...")

        url = f"{self.base_url}/api/submissions/"
        params = {"offset": 0, "limit": 20, "lastkey": ""}

        all_submissions: List[Dict] = []
        seen_ids: Set[str] = set()
        page = 0

        while True:
            try:
                page += 1
                if self.debug:
                    print(f"  📄 获取第 {page} 页...")

                resp = self.session.get(url, params=params, timeout=30)

                if resp.status_code == 403:
                    print("⚠️  请求被限制 (403)，等待5秒...")
                    time.sleep(5)
                    continue

                resp.raise_for_status()
                data = resp.json()

                submissions = data.get("submissions_dump", [])
                if not submissions:
                    break

                should_stop = False

                for sub in submissions:
                    sub_id = str(sub.get("id", ""))
                    timestamp = sub.get("timestamp")

                    # 时间过滤（越往后越新；遇到更老的就可以停）
                    if self.sync_after_timestamp and timestamp:
                        if int(timestamp) < self.sync_after_timestamp:
                            should_stop = True
                            break

                    if sub.get("status_display") == "Accepted" and sub_id and sub_id not in seen_ids:
                        seen_ids.add(sub_id)
                        all_submissions.append(sub)

                if should_stop:
                    print("⏹️  已到达时间截止点，停止获取")
                    break

                if not data.get("has_next", False):
                    break

                params["offset"] += params["limit"]
                params["lastkey"] = str(submissions[-1].get("id", ""))
                time.sleep(1)

            except Exception as e:
                print(f"❌ 获取提交记录出错: {e}")
                break

        print(f"✅ 共获取到 {len(all_submissions)} 条AC提交记录")
        return all_submissions

    def get_submission_detail(self, submission_id: str) -> Optional[Dict]:
        """获取提交详情（包含代码）"""
        url = f"{self.base_url}/api/submissions/{submission_id}/"
        for retry in range(3):
            try:
                resp = self.session.get(url, timeout=30)
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                if retry < 2:
                    time.sleep(2)
                    continue
                if self.debug:
                    print(f"  ❌ 获取详情失败: {e}")
                return None

    # -------------------- comment parsing --------------------

    def has_valid_comment(self, code: str) -> bool:
        """检查代码是否包含有效的目录结构注释"""
        if not code:
            return False

        lines = code.strip().split("\n")
        if len(lines) < 2:
            return False

        if self.debug:
            print("  📝 代码前10行:")
            for i in range(min(10, len(lines))):
                print(f"     {i+1}: {lines[i][:100]}")

        first = lines[0].strip()
        if first.startswith("//"):
            comment_prefix = "//"
        elif first.startswith("#"):
            comment_prefix = "#"
        else:
            if self.debug:
                print("  ❌ 第一行不是注释")
            return False

        comment_lines: List[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(comment_prefix):
                content = stripped[len(comment_prefix):].strip()
                if content:
                    comment_lines.append(content)
            else:
                break

        if self.debug:
            print(f"  📋 找到 {len(comment_lines)} 行连续注释:")
            for i, line in enumerate(comment_lines, 1):
                print(f"     {i}: {line}")

        if len(comment_lines) < 2:
            if self.debug:
                print("  ❌ 注释行数不足（需要至少2行）")
            return False

        last_line = comment_lines[-1]
        if not self._looks_like_filename(last_line):
            if self.debug:
                print(f"  ❌ 最后一行不像文件名: {last_line}")
            return False

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
        """解析代码注释，提取目录结构和文件名"""
        if not code:
            return None, None

        lines = code.strip().split("\n")
        if len(lines) < 2:
            return None, None

        first = lines[0].strip()
        if first.startswith("//"):
            comment_prefix = "//"
        elif first.startswith("#"):
            comment_prefix = "#"
        else:
            return None, None

        comment_lines: List[str] = []
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

        filename = comment_lines[-1]
        if not self._looks_like_filename(filename):
            return None, None

        directories = comment_lines[:-1]
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

        # 有扩展名
        if re.search(r"\.(cpp|java|py|js|go|c|cs|rb|swift|kt|rs|php|ts|txt|md)$", text, re.IGNORECASE):
            return True

        # 以 123. 开头
        if re.match(r"^\d+\.", text):
            return True

        # 像 “xxx-yyy-zzz”
        if text.count("-") >= 2:
            return True

        return False

    # -------------------- path / naming --------------------

    def get_file_extension(self, lang: str) -> str:
        """根据语言获取文件扩展名"""
        ext_map = {
            "cpp": "cpp", "c++": "cpp",
            "java": "java",
            "python": "py", "python3": "py",
            "javascript": "js", "typescript": "ts",
            "golang": "go", "go": "go",
            "rust": "rs",
            "c": "c",
            "csharp": "cs", "c#": "cs",
            "ruby": "rb",
            "swift": "swift",
            "kotlin": "kt",
            "scala": "scala",
            "php": "php",
        }
        return ext_map.get((lang or "").lower(), "txt")

    def sanitize_path_component(self, name: str) -> str:
        """清理路径组件，移除非法字符"""
        if not name:
            return "untitled"
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
        name = name.strip(". \t\n\r")
        if not name:
            return "untitled"
        if len(name) > 100:
            name = name[:100]
        return name

    def extract_title_from_filename(self, filename: str) -> str:
        """从注释中的文件名提取题目名称"""
        title = re.sub(r"\.(cpp|java|py|js|go|c|cs|rb|swift|kt|rs|php|ts)$", "", filename, flags=re.IGNORECASE)
        return title.strip()

    def extract_problem_id(self, title: str) -> Optional[str]:
        """提取题号"""
        m = re.match(r"^(\d+)\.", title)
        return m.group(1) if m else None

    def delete_old_versions(self, dir_path: Path, title_pattern: str, current_file: Path):
        """删除同一题目的旧版本文件（按题号匹配）"""
        if not dir_path.exists():
            return
        m = re.match(r"^(\d+)\.", title_pattern)
        if not m:
            return

        problem_id = m.group(1)
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

    # -------------------- saving submissions --------------------

    def save_submission(self, submission: Dict, detail: Dict) -> bool:
        """保存提交到本地文件"""
        code = detail.get("code", "")
        if not code:
            if self.debug:
                print("  ❌ 没有代码内容")
            return False

        directories, filename = self.parse_comment(code)
        if not directories or not filename:
            if self.debug:
                print("  ⊘ 跳过：没有有效的目录结构注释")
            return False

        safe_dirs = [self.sanitize_path_component(d) for d in directories]
        dir_path = Path(*safe_dirs)

        try:
            dir_path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"  ❌ 创建目录失败 {dir_path}: {e}")
            return False

        title = self.extract_title_from_filename(filename)
        safe_title = self.sanitize_path_component(title)

        lang = detail.get("lang", "txt")
        ext = self.get_file_extension(lang)

        file_name = f"{safe_title}.{ext}"
        file_path = dir_path / file_name

        if self.debug:
            print(f"  📂 目录结构: {' / '.join(safe_dirs)}")
            print(f"  📄 文件名: {file_name}")
            print(f"  📍 完整路径: {file_path}")

        self.delete_old_versions(dir_path, safe_title, file_path)

        is_new = not file_path.exists()

        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    existing_code = f.read()
                if existing_code == code:
                    print(f"  ⊙ 已存在（内容相同）: {file_path}")
                    return True
                else:
                    print(f"  ♻️  更新文件: {file_path}")
            except Exception:
                pass

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(code)
            print(f"  ✅ 已保存: {file_path}")

            # 记录新增题目
            if is_new:
                pid = self.extract_problem_id(safe_title)
                if pid:
                    self.new_problems.append({
                        "id": pid,
                        "title": safe_title,
                        "path": str(file_path),
                        "category": " / ".join(safe_dirs),
                    })

            return True
        except Exception as e:
            print(f"  ❌ 保存失败 {file_path}: {e}")
            return False

    # -------------------- README generation --------------------
    def generate_category_readme(self, dir_path: Path):
        """生成分类目录的 README.md（列出该目录下的题目文件）"""
        if not dir_path.exists() or not dir_path.is_dir():
            return

        # 收集该目录下的所有题目文件
        problems: List[Dict[str, str]] = []
        for file in sorted(dir_path.glob("*.*")):
            if file.name == "README.md":
                continue
            if file.suffix.lower() not in [
                ".cpp", ".py", ".java", ".js", ".go", ".c", ".cs",
                ".rb", ".swift", ".kt", ".rs", ".php", ".ts"
            ]:
                continue

            title = file.stem
            problem_id = self.extract_problem_id(title)
            if not problem_id:
                continue

            # 去重：同题号只保留一份
            if any(p["id"] == problem_id for p in problems):
                continue

            problems.append({"id": problem_id, "title": title, "file": file.name})

        if not problems:
            return

        # 按题号排序
        problems.sort(key=lambda x: int(x["id"]))

        category_name = dir_path.name
        now_bj = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")

        readme_content = [
            f"# {category_name}",
            "",
            f"> 本分类共 **{len(problems)}** 道题目",
            "",
            "## 📝 题目列表",
            "",
            "| # | 题目 | 代码 |",
            "|---|------|------|",
        ]

        for p in problems:
            readme_content.append(f"| {p['id']} | {p['title']} | [查看代码](./{p['file']}) |")

        readme_content.extend([
            "",
            "---",
            "",
            f"*最后更新: {now_bj} (北京时间)*",
            "",
        ])

        readme_path = dir_path / "README.md"
        try:
            readme_path.write_text("\n".join(readme_content), encoding="utf-8")
            if self.debug:
                print(f"  📄 生成 README: {readme_path}")
        except Exception as e:
            if self.debug:
                print(f"  ⚠️  生成 README 失败: {e}")

    def update_all_category_readmes(self):
        """更新所有包含代码文件的目录的 README.md"""
        print("\n📚 更新分类 README...")

        exts = (".cpp", ".py", ".java", ".js", ".go", ".c", ".cs", ".rb", ".swift", ".kt", ".rs", ".php", ".ts")

        for root, dirs, files in os.walk("."):
            root_path = Path(root)

            # 跳过隐藏目录和 .git 等
            if any(part.startswith(".") for part in root_path.parts):
                continue

            has_code = any(f.lower().endswith(exts) for f in files)
            if has_code:
                self.generate_category_readme(root_path)

    def collect_all_problems(self) -> Dict[str, List[Dict]]:
        """收集所有题目，按分类(目录)组织"""
        problems_by_category: Dict[str, List[Dict]] = defaultdict(list)

        exts = {".cpp", ".py", ".java", ".js", ".go", ".c", ".cs", ".rb", ".swift", ".kt", ".rs", ".php", ".ts"}

        for root, dirs, files in os.walk("."):
            root_path = Path(root)

            # 跳过隐藏目录
            if any(part.startswith(".") for part in root_path.parts):
                continue

            # 根目录不作为分类
            if root_path == Path("."):
                continue

            category = str(root_path).replace("\\", " / ")

            for file in root_path.glob("*.*"):
                if file.name == "README.md":
                    continue
                if file.suffix.lower() not in exts:
                    continue

                title = file.stem
                problem_id = self.extract_problem_id(title)
                if not problem_id:
                    continue

                problems_by_category[category].append({
                    "id": problem_id,
                    "title": title,
                    "file": str(file.relative_to(".")).replace("\\", "/"),
                    "lang": file.suffix.lower()[1:],
                })

        # 每个分类内：按题号排序 + 去重（同题号只保留一份）
        for category in list(problems_by_category.keys()):
            seen_ids: Set[str] = set()
            unique_list: List[Dict] = []
            for p in sorted(problems_by_category[category], key=lambda x: int(x["id"])):
                if p["id"] in seen_ids:
                    continue
                seen_ids.add(p["id"])
                unique_list.append(p)
            problems_by_category[category] = unique_list

        return dict(problems_by_category)

    def generate_main_readme(self):
        """生成仓库根目录 README.md（全局统计 + 分类目录表）"""
        print("\n📖 生成主 README...")

        problems_by_category = self.collect_all_problems()
        if not problems_by_category:
            print("  ⚠️  没有找到任何题目")
            return

        total_problems = sum(len(v) for v in problems_by_category.values())
        total_categories = len(problems_by_category)

        # 统计语言分布
        lang_count: Dict[str, int] = defaultdict(int)
        for problems in problems_by_category.values():
            for p in problems:
                lang_count[p["lang"]] += 1

        now_bj = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")

        # shields.io 徽章模板（保留你原来的逻辑）
        lang_icons = {
            "cpp": "![C++](https://img.shields.io/badge/C++-{count}-00599C?style=flat-square&logo=c%2B%2B)",
            "py": "![Python](https://img.shields.io/badge/Python-{count}-3776AB?style=flat-square&logo=python)",
            "java": "![Java](https://img.shields.io/badge/Java-{count}-007396?style=flat-square&logo=java)",
            "js": "![JavaScript](https://img.shields.io/badge/JavaScript-{count}-F7DF1E?style=flat-square&logo=javascript)",
            "go": "![Go](https://img.shields.io/badge/Go-{count}-00ADD8?style=flat-square&logo=go)",
            "c": "![C](https://img.shields.io/badge/C-{count}-A8B9CC?style=flat-square&logo=c)",
            "cs": "![C#](https://img.shields.io/badge/C%23-{count}-239120?style=flat-square&logo=c-sharp)",
            "rb": "![Ruby](https://img.shields.io/badge/Ruby-{count}-CC342D?style=flat-square&logo=ruby)",
            "swift": "![Swift](https://img.shields.io/badge/Swift-{count}-FA7343?style=flat-square&logo=swift)",
            "kt": "![Kotlin](https://img.shields.io/badge/Kotlin-{count}-0095D5?style=flat-square&logo=kotlin)",
            "rs": "![Rust](https://img.shields.io/badge/Rust-{count}-000000?style=flat-square&logo=rust)",
            "php": "![PHP](https://img.shields.io/badge/PHP-{count}-777BB4?style=flat-square&logo=php)",
            "ts": "![TypeScript](https://img.shields.io/badge/TypeScript-{count}-3178C6?style=flat-square&logo=typescript)",
        }

        badges = []
        for lang, count in sorted(lang_count.items(), key=lambda x: x[1], reverse=True):
            if lang in lang_icons:
                badges.append(lang_icons[lang].format(count=count))

        # README 内容（彻底修复你原先的代码块/字符串错乱）
        lines: List[str] = []
        lines += [
            "# 🎯 LeetCode 题解集",
            "",
            "> 自动同步的 LeetCode 刷题记录，持续更新中...",
            "",
            "## 📊 统计信息",
            "",
            f"- 📝 **总题数**: {total_problems} 道",
            f"- 📂 **分类数**: {total_categories} 个",
            f"- 🕐 **最后更新**: {now_bj} (北京时间)",
            "",
            "### 💻 语言分布",
            "",
            (" ".join(badges) if badges else "_暂无统计_"),
            "",
            "## 📚 题目分类",
            "",
        ]

        for category in sorted(problems_by_category.keys()):
            problems = problems_by_category[category]
            lines += [
                f"### {category}",
                "",
                f"> 共 **{len(problems)}** 道题目",
                "",
                "| # | 题目 | 代码 |",
                "|---|------|------|",
            ]
            for p in problems:
                lines.append(f"| {p['id']} | {p['title']} | [查看代码](./{p['file']}) |")
            lines.append("")

        lines += [
            "## 🚀 使用说明",
            "",
            "### 自动同步",
            "",
            "本仓库使用 GitHub Actions 自动同步 LeetCode 提交记录：",
            "",
            "- 🔄 自动提取代码中的目录结构注释",
            "- 📝 自动生成分类 README",
            "- 🎯 同一题目自动覆盖旧版本",
            "",
            "### 代码注释格式",
            "",
            "在 LeetCode 提交代码时，在文件开头添加注释：",
            "",
            "```cpp",
            "// 一级分类",
            "// 二级分类",
            "// 2841. 几乎唯一子数组的最大和.cpp",
            "",
            "class Solution {",
            "    // 你的代码...",
            "};",
            "```",
            "",
            "**格式要求：**",
            "",
            "- ✅ 至少 2 行注释（1 个目录 + 文件名）",
            "- ✅ 最后一行必须是完整文件名（包含题号和扩展名）",
            "- ✅ 前面的行是目录层级（支持任意多级）",
            "- ✅ 使用 `//` 或 `#` 作为注释符号",
            "",
        ]

        try:
            Path("README.md").write_text("\n".join(lines), encoding="utf-8")
            print("  ✅ 主 README 已更新")
            print(f"     - 总题数: {total_problems}")
            print(f"     - 分类数: {total_categories}")
        except Exception as e:
            print(f"  ❌ 生成主 README 失败: {e}")


    def generate_commit_message(self) -> str:
        """生成 Git 提交信息（写到 .commit_message.txt 供 Actions 使用）"""
        if not self.new_problems:
            return "🤖 自动同步 LeetCode 提交"

        # 按分类分组
        by_category = defaultdict(list)
        for p in self.new_problems:
            by_category[p["category"]].append(p)

        # 生成提交信息
        msg_lines = ["🎉 新增题目", ""]

        for category, problems in sorted(by_category.items(), key=lambda x: x[0]):
            msg_lines.append(f"**{category}**")
            for p in sorted(problems, key=lambda x: int(x["id"])):
                msg_lines.append(f"- [{p['id']}] {p['title']}")
            msg_lines.append("")

        msg_lines.append(f"共 {len(self.new_problems)} 道新题目")
        return "\n".join(msg_lines)

    def sync(self) -> bool:
        """执行同步：拉取提交 -> 校验注释 -> 写文件 -> 生成 README"""
        print("=" * 60)
        print("🚀 开始同步 LeetCode 提交记录")
        print("=" * 60)

        submissions = self.get_ac_submissions()
        if not submissions:
            print("📭 没有找到 AC 提交记录")
            return False

        new_submissions = [
            sub for sub in submissions
            if str(sub.get("id")) not in self.synced_ids
        ]

        if not new_submissions:
            print("✨ 没有新的提交需要同步")
            return False

        print(f"\n📦 共 {len(new_submissions)} 条新提交，开始检查...")
        print("-" * 60)

        success_count = 0
        skipped_count = 0
        failed_count = 0

        for i, submission in enumerate(new_submissions, 1):
            sub_id = submission.get("id")
            title = submission.get("title", "Unknown")
            timestamp = submission.get("timestamp")

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

            code = detail.get("code", "")
            if not self.has_valid_comment(code):
                print("  ⊘ 跳过：没有符合格式的目录结构注释")
                skipped_count += 1

                # 仍然标记为已处理，避免下次重复刷屏
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

        # 保存同步状态
        self.save_synced_ids()

        # 更新 README
        self.update_all_category_readmes()
        self.generate_main_readme()

        print("\n" + "=" * 60)
        print("🎉 同步完成！")
        print(f"  ✅ 成功保存: {success_count}")
        print(f"  ⊘ 跳过（无注释）: {skipped_count}")
        print(f"  ❌ 失败: {failed_count}")
        print(f"  📊 总计: {len(new_submissions)}")

        if self.new_problems:
            print(f"\n🆕 本次新增 {len(self.new_problems)} 道题目:")
            for p in self.new_problems:
                print(f"  • [{p['id']}] {p['title']}")

        print("=" * 60)

        return success_count > 0


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="🤖 同步 LeetCode AC 提交到 GitHub",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python sync.py
  python sync.py --after "2026-01-26 23:47"
  python sync.py --debug
  python sync.py --force

注释格式要求:
// 一级目录
// 二级目录
// 2841. 几乎唯一子数组的最大和.cpp

✅ 最后一行必须是文件名（包含题号和扩展名）
✅ 前面的行是目录层级（支持任意多级）
✅ 同一题目的新提交会自动覆盖旧版本
"""
    )
    parser.add_argument("--after", type=str, help='只同步此时间之后的提交，格式: "2026-01-26 23:47" (北京时间)')
    parser.add_argument("--debug", action="store_true", help="调试模式：显示详细的匹配信息")
    parser.add_argument("--force", action="store_true", help="强制重新同步所有提交（忽略已同步记录）")
    args = parser.parse_args()

    try:
        syncer = LeetCodeSyncer(sync_after=args.after, debug=args.debug)

        if args.force:
            print("⚠️  强制模式：将重新同步所有提交")
            syncer.synced_ids.clear()

        has_updates = syncer.sync()

        # 输出提交信息（供 GitHub Actions 使用）
        if has_updates:
            commit_msg = syncer.generate_commit_message()
            Path(".commit_message.txt").write_text(commit_msg, encoding="utf-8")
            print("\n📝 提交信息已生成: .commit_message.txt")

        # 约定：有更新退出码=0；无更新退出码=1（让 Actions 可据此跳过 commit）
        raise SystemExit(0 if has_updates else 1)

    except KeyboardInterrupt:
        print("\n\n⏸️  用户中断，正在退出...")
        raise SystemExit(0)
    except Exception as e:
        print(f"\n❌ 同步失败: {e}")
        import traceback
        traceback.print_exc()
        raise SystemExit(1)


if __name__ == "__main__":
    main()
