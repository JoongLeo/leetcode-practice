# -*- coding: utf-8 -*-
"""
Auto-generate README navigation for this LeetCode repo.

Usage (run at repo root):
  python tools/update_readme.py

What it updates:
  - Root README.md  (auto section)
  - Each topic README.md under <topic>/README.md (auto section)

It ONLY lists existing folders/files. No TODO, no future topics.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import quote


AUTO_START = "<!-- AUTO-GENERATED:START -->"
AUTO_END = "<!-- AUTO-GENERATED:END -->"


def find_repo_root(start: Path) -> Path:
    """Find repo root by walking up until .git exists; fallback to start."""
    cur = start.resolve()
    for p in [cur, *cur.parents]:
        if (p / ".git").exists():
            return p
    return start.resolve()


def natural_key(s: str):
    """Natural sort key: '10-x' after '2-x'."""
    parts = re.split(r"(\d+)", s)
    key = []
    for part in parts:
        if part.isdigit():
            key.append(int(part))
        else:
            key.append(part.lower())
    return key


def is_dir_ignorable(name: str) -> bool:
    return name in {".git", ".vscode", ".idea", "__pycache__", "tools"} or name.startswith(".")


def is_file_ignorable(path: Path) -> bool:
    name = path.name
    if name.startswith("."):
        return True
    if name.lower() in {"readme.md", "readme.txt"}:
        return True
    return False


def md_link(text: str, rel_posix_path: str) -> str:
    # Encode URL for spaces/Chinese; keep "/" safe
    url = quote(rel_posix_path, safe="/-_.~")
    return f"[{text}]({url})"


def parse_problem_display_name(filename: str) -> str:
    """
    Turn '1456. 定长子串中元音的最大数目.cpp' -> '1456. 定长子串中元音的最大数目'
    Fallback: stem.
    """
    stem = Path(filename).stem
    # normalize multiple spaces
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem


def replace_auto_section(original: str, new_section: str) -> str:
    if AUTO_START in original and AUTO_END in original:
        pre = original.split(AUTO_START)[0].rstrip()
        post = original.split(AUTO_END)[1].lstrip()
        return f"{pre}\n{AUTO_START}\n{new_section.rstrip()}\n{AUTO_END}\n{post}".rstrip() + "\n"
    # If no markers, append auto section at end
    base = original.rstrip()
    if base:
        base += "\n\n"
    return f"{base}{AUTO_START}\n{new_section.rstrip()}\n{AUTO_END}\n"


def read_text(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")


def list_topics(repo_root: Path) -> list[Path]:
    topics = []
    for p in repo_root.iterdir():
        if p.is_dir() and not is_dir_ignorable(p.name):
            topics.append(p)
    topics.sort(key=lambda x: natural_key(x.name))
    return topics


def list_subcats(topic_dir: Path) -> list[Path]:
    subs = []
    for p in topic_dir.iterdir():
        if p.is_dir() and not is_dir_ignorable(p.name):
            subs.append(p)
    subs.sort(key=lambda x: natural_key(x.name))
    return subs


def list_cpp_files(folder: Path) -> list[Path]:
    files = []
    for p in folder.iterdir():
        if p.is_file() and not is_file_ignorable(p) and p.suffix.lower() == ".cpp":
            files.append(p)
    files.sort(key=lambda x: natural_key(x.name))
    return files


def generate_root_auto(repo_root: Path, topics: list[Path]) -> str:
    lines = []
    lines.append("## 当前目录")
    if not topics:
        lines.append("_（暂无内容）_")
        return "\n".join(lines)

    for topic in topics:
        rel_topic = topic.relative_to(repo_root).as_posix() + "/"
        # Count cpp files under topic
        cpp_count = 0
        for sub in list_subcats(topic):
            cpp_count += len(list_cpp_files(sub))
        topic_text = f"{topic.name}（{cpp_count} 题）" if cpp_count else topic.name
        lines.append(f"- {md_link(topic_text, rel_topic)}")

        subs = list_subcats(topic)
        for sub in subs:
            rel_sub = sub.relative_to(repo_root).as_posix() + "/"
            c = len(list_cpp_files(sub))
            sub_text = f"{sub.name}（{c}）" if c else sub.name
            lines.append(f"  - {md_link(sub_text, rel_sub)}")

    lines.append("")
    lines.append("## 使用方式")
    lines.append("每天只需要：新增 `.cpp` 文件 → 运行脚本更新目录：")
    lines.append("")
    lines.append("```bash")
    lines.append("python tools/update_readme.py")
    lines.append("```")
    return "\n".join(lines)


def generate_topic_auto(repo_root: Path, topic_dir: Path) -> str:
    lines = []
    subs = list_subcats(topic_dir)
    if not subs:
        lines.append("_（暂无小类目录）_")
        return "\n".join(lines)

    # Small navigation
    lines.append("## 小类导航")
    for sub in subs:
        rel_sub = sub.relative_to(topic_dir).as_posix() + "/"
        c = len(list_cpp_files(sub))
        sub_text = f"{sub.name}（{c}）" if c else sub.name
        lines.append(f"- {md_link(sub_text, rel_sub)}")

    # Problem index
    lines.append("")
    lines.append("## 题目索引")
    for sub in subs:
        cpp_files = list_cpp_files(sub)
        if not cpp_files:
            continue
        lines.append("")
        lines.append(f"### {sub.name}")
        for f in cpp_files:
            disp = parse_problem_display_name(f.name)
            rel = f.relative_to(topic_dir).as_posix()
            lines.append(f"- {md_link(disp, rel)}")

    return "\n".join(lines)


def ensure_root_readme_has_header(existing: str) -> str:
    """
    Keep user's manual header if exists; otherwise create a minimal header.
    We'll only manage the AUTO section.
    """
    if existing.strip():
        return existing
    return (
        "# leetcode-practice\n\n"
        "按「题型 / 专题」整理的 LeetCode 题解仓库。\n\n"
    )


def ensure_topic_readme_has_header(existing: str, topic_name: str) -> str:
    if existing.strip():
        return existing
    return f"# {topic_name}\n\n"


def main():
    script_dir = Path(__file__).resolve().parent
    repo_root = find_repo_root(script_dir)

    topics = list_topics(repo_root)

    # ---- Root README ----
    root_readme = repo_root / "README.md"
    root_existing = read_text(root_readme)
    root_existing = ensure_root_readme_has_header(root_existing)

    root_auto = generate_root_auto(repo_root, topics)
    root_new = replace_auto_section(root_existing, root_auto)
    write_text(root_readme, root_new)

    # ---- Topic READMEs ----
    for topic in topics:
        topic_readme = topic / "README.md"
        existing = read_text(topic_readme)
        existing = ensure_topic_readme_has_header(existing, topic.name)

        topic_auto = generate_topic_auto(repo_root, topic)
        new_content = replace_auto_section(existing, topic_auto)
        write_text(topic_readme, new_content)

    print("✅ README updated:")
    print(f" - {root_readme.relative_to(repo_root)}")
    for topic in topics:
        print(f" - {topic.name}/README.md")


if __name__ == "__main__":
    main()
