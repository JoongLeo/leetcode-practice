# tools/update_readme.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import quote

AUTO_START = "<!-- AUTO-GENERATED:START -->"
AUTO_END = "<!-- AUTO-GENERATED:END -->"

REPO_IGNORE_DIRS = {
    ".git", ".github", ".vscode", ".idea", "__pycache__", "tools", "data"
}
CODE_SUFFIXES = {
    ".cpp", ".py", ".java", ".js", ".ts", ".go", ".rs", ".c", ".cs", ".kt", ".swift", ".rb", ".php", ".txt"
}

SOLVED_ID_RE = re.compile(r"^(\d+)\.\s*")


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
    fs = [
        p for p in folder.iterdir()
        if p.is_file()
        and p.suffix.lower() in CODE_SUFFIXES
        and p.name != "README.md"
    ]
    fs.sort(key=lambda x: natural_key(x.name))
    return fs


def count_all_code_files(folder: Path, repo_root: Path) -> int:
    c = 0
    for p in folder.rglob("*"):
        if not p.is_file():
            continue
        if p.name == "README.md":
            continue
        if p.suffix.lower() not in CODE_SUFFIXES:
            continue
        rel = p.relative_to(repo_root).parts
        if rel and rel[0] in REPO_IGNORE_DIRS:
            continue
        c += 1
    return c


def collect_solved_ids(repo_root: Path) -> set[int]:
    solved = set()
    for p in repo_root.rglob("*"):
        if not p.is_file():
            continue
        if p.name == "README.md":
            continue
        if p.suffix.lower() not in CODE_SUFFIXES:
            continue
        rel = p.relative_to(repo_root).parts
        if rel and rel[0] in REPO_IGNORE_DIRS:
            continue
        m = SOLVED_ID_RE.match(p.name)
        if m:
            try:
                solved.add(int(m.group(1)))
            except Exception:
                pass
    return solved


def render_root_auto(repo_root: Path) -> str:
    total_files = count_all_code_files(repo_root, repo_root)
    solved = collect_solved_ids(repo_root)

    lines = []
    lines.append("## 统计")
    lines.append(f"- 已归档代码文件：**{total_files}**")
    lines.append(f"- 识别到题号（文件名以 `1234.` 开头）：**{len(solved)}**")
    lines.append("")
    lines.append("## 目录导航")

    dirs = list_dirs(repo_root)
    if not dirs:
        lines.append("_（暂无内容）_")
    else:
        for d in dirs:
            rel = d.relative_to(repo_root).as_posix() + "/"
            cnt = count_all_code_files(d, repo_root)
            lines.append(f"- {md_link(f'{d.name}（{cnt}）', rel)}")

    lines.append("")
    lines.append("```bash")
    lines.append("python tools/update_readme.py")
    lines.append("```")
    return "\n".join(lines)


def render_folder_auto(folder: Path, repo_root: Path) -> str:
    subs = list_dirs(folder)
    files = list_code_files(folder)

    lines = []
    if subs:
        lines.append("## 子目录")
        for sd in subs:
            rel = sd.relative_to(folder).as_posix() + "/"
            cnt = count_all_code_files(sd, repo_root)
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


def iter_dirs_skip_ignored(repo_root: Path):
    # 手动 DFS，避免枚举 .github 子树
    stack = [repo_root]
    while stack:
        cur = stack.pop()
        for d in cur.iterdir():
            if not d.is_dir():
                continue
            if is_dir_ignorable(d.name):
                continue
            yield d
            stack.append(d)


def main():
    script_dir = Path(__file__).resolve().parent
    repo_root = find_repo_root(script_dir)
    os.chdir(repo_root)

    # root README
    root_path = repo_root / "README.md"
    root_existing = ensure_header(read_text(root_path), "# leetcode-practice\n\n")
    root_new = replace_auto_section(root_existing, render_root_auto(repo_root))
    write_text(root_path, root_new)

    # folder READMEs
    for folder in iter_dirs_skip_ignored(repo_root):
        readme = folder / "README.md"
        existing = ensure_header(read_text(readme), f"# {folder.name}\n\n")
        new = replace_auto_section(existing, render_folder_auto(folder, repo_root))
        write_text(readme, new)

    print("✅ updated README(s)")


if __name__ == "__main__":
    main()
