# tools/update_readme.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import quote

AUTO_START = "<!-- AUTO-GENERATED:START -->"
AUTO_END = "<!-- AUTO-GENERATED:END -->"

# 关键：必须包含 .github，避免 GitHub Actions 推送被拒（workflows 安全限制）
REPO_IGNORE_DIRS = {".git", ".github", ".vscode", ".idea", "__pycache__", "tools", "data"}

CODE_SUFFIXES = {
    ".cpp", ".py", ".java", ".js", ".ts", ".go", ".rs", ".c", ".cs", ".kt", ".swift", ".rb", ".php", ".txt"
}

PID_RE = re.compile(r"^(\d+)\.\s*")


def natural_key(s: str):
    parts = re.split(r"(\d+)", s)
    return [int(p) if p.isdigit() else p.lower() for p in parts]


def is_dir_ignorable(name: str) -> bool:
    return name in REPO_IGNORE_DIRS or name.startswith(".")


def find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for p in [cur, *cur.parents]:
        if (p / ".git").exists():
            return p
    return start.resolve()


def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else ""


def write_text(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8", newline="\n")


def md_link(text: str, rel_posix_path: str) -> str:
    return f"[{text}]({quote(rel_posix_path, safe='/-_.~')})"


def replace_auto_section(original: str, new_section: str) -> str:
    if AUTO_START in original and AUTO_END in original:
        pre = original.split(AUTO_START)[0].rstrip()
        post = original.split(AUTO_END)[1].lstrip()
        return f"{pre}\n{AUTO_START}\n{new_section.rstrip()}\n{AUTO_END}\n{post}".rstrip() + "\n"
    base = original.rstrip()
    if base:
        base += "\n\n"
    return f"{base}{AUTO_START}\n{new_section.rstrip()}\n{AUTO_END}\n"


def ensure_header(existing: str, header: str) -> str:
    return existing if existing.strip() else header


def list_dirs(folder: Path) -> list[Path]:
    ds = [p for p in folder.iterdir() if p.is_dir() and not is_dir_ignorable(p.name)]
    ds.sort(key=lambda x: natural_key(x.name))
    return ds


def list_code_files(folder: Path) -> list[Path]:
    fs = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in CODE_SUFFIXES and p.name != "README.md"]
    fs.sort(key=lambda x: natural_key(x.name))
    return fs


def iter_dirs_skip_ignored(root: Path):
    """
    手动 walk + 剪枝：不会进入 .github / tools / data 等子树
    """
    stack = [root]
    while stack:
        cur = stack.pop()
        for d in cur.iterdir():
            if not d.is_dir():
                continue
            name = d.name
            if is_dir_ignorable(name):
                continue
            yield d
            stack.append(d)


def iter_files_skip_ignored(root: Path):
    """
    手动 walk files + 剪枝：不会进入 .github / tools / data 等子树
    """
    stack = [root]
    while stack:
        cur = stack.pop()
        for p in cur.iterdir():
            if p.is_dir():
                if is_dir_ignorable(p.name):
                    continue
                stack.append(p)
            elif p.is_file():
                yield p


def count_all_code_files(repo_root: Path, folder: Path) -> int:
    """
    统计 folder 子树下的代码文件数，且剪枝忽略目录。
    """
    # 若 folder 本身就是忽略目录，直接 0
    if folder.name in REPO_IGNORE_DIRS or folder.name.startswith("."):
        return 0

    cnt = 0
    # 从 folder 开始 walk（剪枝）
    stack = [folder]
    while stack:
        cur = stack.pop()
        for p in cur.iterdir():
            if p.is_dir():
                if is_dir_ignorable(p.name):
                    continue
                stack.append(p)
            elif p.is_file():
                if p.name == "README.md":
                    continue
                if p.suffix.lower() in CODE_SUFFIXES:
                    cnt += 1
    return cnt


def collect_problem_ids(repo_root: Path) -> set[int]:
    """
    收集仓库中所有题号（文件名以 '1234.' 开头），剪枝忽略目录。
    """
    ids = set()
    for p in iter_files_skip_ignored(repo_root):
        if p.name == "README.md":
            continue
        if p.suffix.lower() not in CODE_SUFFIXES:
            continue
        m = PID_RE.match(p.name)
        if m:
            try:
                ids.add(int(m.group(1)))
            except Exception:
                pass
    return ids


def render_root_auto(repo_root: Path) -> str:
    # 统计：全仓库代码文件数 & 题号数（剪枝）
    total_files = 0
    for d in list_dirs(repo_root):
        total_files += count_all_code_files(repo_root, d)

    ids = collect_problem_ids(repo_root)

    lines = []
    lines.append("## 统计")
    lines.append(f"- 已归档代码文件：**{total_files}**")
    lines.append(f"- 识别到题号（文件名以 `1234.` 开头）：**{len(ids)}**")
    lines.append("")
    lines.append("## 目录导航")
    dirs = list_dirs(repo_root)
    if not dirs:
        lines.append("_（暂无内容）_")
    else:
        for d in dirs:
            rel = d.relative_to(repo_root).as_posix() + "/"
            cnt = count_all_code_files(repo_root, d)
            lines.append(f"- {md_link(f'{d.name}（{cnt}）', rel)}")

    lines.append("")
    lines.append("```bash")
    lines.append("python tools/update_readme.py")
    lines.append("```")
    return "\n".join(lines)


def render_folder_readme(folder: Path, repo_root: Path) -> str:
    subs = list_dirs(folder)
    files = list_code_files(folder)

    lines = []
    if subs:
        lines.append("## 子目录")
        for sd in subs:
            rel = sd.relative_to(folder).as_posix() + "/"
            cnt = count_all_code_files(repo_root, sd)
            lines.append(f"- {md_link(f'{sd.name}（{cnt}）', rel)}")
        lines.append("")

    if files:
        lines.append("## 题目索引")
        for f in files:
            rel = f.relative_to(folder).as_posix()
            lines.append(f"- {md_link(f.stem, rel)}")
        lines.append("")

    if not subs and not files:
        lines.append("_（空目录）_")

    return "\n".join(lines).rstrip() + "\n"


def main():
    script_dir = Path(__file__).resolve().parent
    repo_root = find_repo_root(script_dir)
    os.chdir(repo_root)

    # root README
    root_path = repo_root / "README.md"
    root_existing = ensure_header(read_text(root_path), "# leetcode-practice\n\n")
    root_new = replace_auto_section(root_existing, render_root_auto(repo_root))
    write_text(root_path, root_new)

    # 只对“非忽略目录树”生成 README
    for folder in iter_dirs_skip_ignored(repo_root):
        readme = folder / "README.md"
        existing = ensure_header(read_text(readme), f"# {folder.name}\n\n")
        new = replace_auto_section(existing, render_folder_readme(folder, repo_root))
        write_text(readme, new)

    print("✅ updated README(s)")


if __name__ == "__main__":
    main()
