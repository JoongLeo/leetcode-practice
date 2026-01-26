# tools/update_readme.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
import json
from pathlib import Path
from urllib.parse import quote
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

AUTO_START = "<!-- AUTO-GENERATED:START -->"
AUTO_END = "<!-- AUTO-GENERATED:END -->"

REPO_IGNORE_DIRS = {".git", ".github", ".vscode", ".idea", "__pycache__", "tools", "data"}
CODE_SUFFIXES = {".cpp", ".py", ".java", ".js", ".ts", ".go", ".rs", ".c", ".cs", ".kt", ".swift", ".rb", ".php", ".txt"}

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
    fs = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in CODE_SUFFIXES]
    fs = [p for p in fs if p.name != "README.md"]
    fs.sort(key=lambda x: natural_key(x.name))
    return fs


def _is_ignored_by_root_parts(rel_parts: tuple[str, ...]) -> bool:
    return bool(rel_parts) and (rel_parts[0] in REPO_IGNORE_DIRS or rel_parts[0].startswith("."))


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
        if _is_ignored_by_root_parts(rel):
            continue
        c += 1
    return c


def collect_solved_ids(repo_root: Path) -> set[int]:
    solved: set[int] = set()
    for p in repo_root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in CODE_SUFFIXES:
            continue
        if p.name == "README.md":
            continue
        rel = p.relative_to(repo_root).parts
        if _is_ignored_by_root_parts(rel):
            continue
        m = SOLVED_ID_RE.match(p.name)
        if m:
            try:
                solved.add(int(m.group(1)))
            except Exception:
                pass
    return solved


def load_last_report(repo_root: Path) -> dict:
    p = repo_root / "data" / "last_sync_report.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _fmt_time(ts: int | None, tz_name: str = "Asia/Shanghai") -> str:
    if not ts:
        return ""
    tz = ZoneInfo(tz_name)
    dt = datetime.fromtimestamp(int(ts), tz=tz)
    # 显示成北京时间/台北时间
    return dt.strftime("%Y-%m-%d %H:%M %Z")


def render_recent_updates(repo_root: Path, max_items: int = 8) -> str:
    rep = load_last_report(repo_root)
    added = rep.get("added") or []
    wrote = int(rep.get("wrote", 0) or 0)

    lines: list[str] = []
    lines.append("## 最近更新")

    if not rep:
        lines.append("_（还没有同步记录；跑一次 Actions 后就会出现）_")
        return "\n".join(lines)

    gen_at = _fmt_time(rep.get("generated_at"))
    last_ts = _fmt_time(rep.get("last_timestamp"))
    meta = []
    if gen_at:
        meta.append(f"生成：{gen_at}")
    if last_ts:
        meta.append(f"水位：{last_ts}")
    if meta:
        lines.append(f"_（{' · '.join(meta)}）_")
        lines.append("")

    if wrote == 0 or not added:
        lines.append("本次同步未发现符合「首行注释规范」的新 AC 提交。")
        return "\n".join(lines)

    added = list(added)[-max_items:][::-1]
    for it in added:
        pid = it.get("pid", "")
        title = it.get("title", "")
        path = it.get("path", "")
        if path:
            lines.append(f"- ✅ {md_link(f'{pid}. {title}', path)}")
        else:
            lines.append(f"- ✅ {pid}. {title}")

    if int(rep.get("wrote", 0) or 0) > max_items:
        lines.append("")
        lines.append(f"_（仅展示最近 {max_items} 条；更多见提交记录）_")
    return "\n".join(lines)


def render_root_auto(repo_root: Path) -> str:
    total_files = count_all_code_files(repo_root, repo_root)
    solved = collect_solved_ids(repo_root)

    lines: list[str] = []
    lines.append("## 🚀 LeetCode 题解仓库（自动同步 + 自动分类）")
    lines.append("")
    lines.append(f"- ✅ 已归档：**{total_files}** 份代码")
    lines.append(f"- 🧩 已识别题号：**{len(solved)}** 道")
    lines.append("- 🤖 自动化：LeetCode.cn 提交后，GitHub Actions 自动拉取并按首行注释分类")
    lines.append("")
    lines.append("> 规则：提交代码首行必须写成：`// 一级-二级-1234. 题名.cpp`（否则不会入库）")
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
    lines.append(render_recent_updates(repo_root))
    lines.append("")
    lines.append("```bash")
    lines.append("python tools/update_readme.py")
    lines.append("```")
    return "\n".join(lines)


def render_folder_auto(folder: Path, repo_root: Path) -> str:
    subs = list_dirs(folder)
    files = list_code_files(folder)

    lines: list[str] = []
    lines.append("> 本目录由脚本自动维护（子目录导航 + 题目索引）。")
    lines.append("")

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


def main():
    script_dir = Path(__file__).resolve().parent
    repo_root = find_repo_root(script_dir)
    os.chdir(repo_root)

    # root README
    root_path = repo_root / "README.md"
    root_existing = ensure_header(read_text(root_path), "# leetcode-practice\n\n")
    root_new = replace_auto_section(root_existing, render_root_auto(repo_root))
    write_text(root_path, root_new)

    # 遍历目录（跳过忽略树）
    def iter_dirs_skip_ignored(root: Path):
        stack = [root]
        while stack:
            cur = stack.pop()
            for d in cur.iterdir():
                if not d.is_dir():
                    continue
                name = d.name
                if name in REPO_IGNORE_DIRS or name.startswith("."):
                    continue
                yield d
                stack.append(d)

    for folder in iter_dirs_skip_ignored(repo_root):
        readme = folder / "README.md"
        existing = ensure_header(read_text(readme), f"# {folder.name}\n\n")
        new = replace_auto_section(existing, render_folder_auto(folder, repo_root))
        write_text(readme, new)

    print("✅ updated README(s)")


if __name__ == "__main__":
    main()
