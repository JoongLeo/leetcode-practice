# -*- coding: utf-8 -*-
"""
One-command workflow:
  - Auto update Root README navigation + per-topic README indexes
  - Generate weekly TODO list based on endlesscheng official lists (in order)
  - Freeze weekly TODO within the same week

Usage (run at repo root):
  python tools/update_readme.py --sync-plan   # first time (or when lists update)
  python tools/update_readme.py               # daily

Options:
  --weekly N        Weekly TODO size (default 10)
  --include-premium Include premium problems in TODO (default: True if detected; you can still skip manually)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen


AUTO_START = "<!-- AUTO-GENERATED:START -->"
AUTO_END = "<!-- AUTO-GENERATED:END -->"

REPO_IGNORE_DIRS = {".git", ".vscode", ".idea", "__pycache__", "tools", "data"}
PLAN_FILE = Path("data/endless_plan.json")
WEEK_FILE = Path(".todo_week.json")

# Module order is exactly the “完整题单” order in the main post. :contentReference[oaicite:1]{index=1}
ENDLESS_MODULES = [
    ("滑动窗口与双指针", "https://leetcode.cn/discuss/post/0viNMK/"),
    ("二分算法", "https://leetcode.cn/discuss/post/SqopEo/"),
    ("单调栈", "https://leetcode.cn/discuss/post/9oZFK9/"),
    ("网格图", "https://leetcode.cn/discuss/post/YiXPXW/"),
    ("位运算", "https://leetcode.cn/discuss/post/dHn9Vk/"),
    ("图论算法", "https://leetcode.cn/discuss/post/01LUak/"),
    ("动态规划", "https://leetcode.cn/discuss/post/tXLS3i/"),
    ("常用数据结构", "https://leetcode.cn/discuss/post/mOr1u6/"),
    ("数学算法", "https://leetcode.cn/discuss/post/IYT3ss/"),
    ("贪心与思维", "https://leetcode.cn/discuss/post/g6KTKL/"),
    ("链表、树与回溯", "https://leetcode.cn/discuss/post/K0n2gO/"),
    ("字符串", "https://leetcode.cn/discuss/post/SJFwQI/"),
]


def find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for p in [cur, *cur.parents]:
        if (p / ".git").exists():
            return p
    return start.resolve()


def natural_key(s: str):
    parts = re.split(r"(\d+)", s)
    key = []
    for part in parts:
        key.append(int(part) if part.isdigit() else part.lower())
    return key


def is_dir_ignorable(name: str) -> bool:
    return name in REPO_IGNORE_DIRS or name.startswith(".")


def md_link(text: str, rel_posix_path: str) -> str:
    url = quote(rel_posix_path, safe="/-_.~")
    return f"[{text}]({url})"


def replace_auto_section(original: str, new_section: str) -> str:
    if AUTO_START in original and AUTO_END in original:
        pre = original.split(AUTO_START)[0].rstrip()
        post = original.split(AUTO_END)[1].lstrip()
        return f"{pre}\n{AUTO_START}\n{new_section.rstrip()}\n{AUTO_END}\n{post}".rstrip() + "\n"
    base = original.rstrip()
    if base:
        base += "\n\n"
    return f"{base}{AUTO_START}\n{new_section.rstrip()}\n{AUTO_END}\n"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def list_topics(repo_root: Path) -> list[Path]:
    topics = [p for p in repo_root.iterdir() if p.is_dir() and not is_dir_ignorable(p.name)]
    topics.sort(key=lambda x: natural_key(x.name))
    return topics


def list_subcats(topic_dir: Path) -> list[Path]:
    subs = [p for p in topic_dir.iterdir() if p.is_dir() and not is_dir_ignorable(p.name)]
    subs.sort(key=lambda x: natural_key(x.name))
    return subs


def list_cpp_files(folder: Path) -> list[Path]:
    files = []
    for p in folder.iterdir():
        if p.is_file() and p.suffix.lower() == ".cpp" and p.name.lower() != "readme.md":
            files.append(p)
    files.sort(key=lambda x: natural_key(x.name))
    return files


def ensure_root_readme_has_header(existing: str) -> str:
    if existing.strip():
        return existing
    return "# leetcode-practice\n\n"


def ensure_topic_readme_has_header(existing: str, topic_name: str) -> str:
    if existing.strip():
        return existing
    return f"# {topic_name}\n\n"


# ------------------ Endless plan sync & parsing ------------------

def http_get(url: str, timeout: int = 30) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; leetcode-practice-bot/1.0)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        method="GET",
    )
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


PROBLEM_ANCHOR_RE = re.compile(
    r'<a[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<text>\s*\d+\.\s*[^<]+)</a>',
    re.IGNORECASE
)
LI_BLOCK_RE = re.compile(r"<li[^>]*>(?P<body>.*?)</li>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")

def strip_tags(s: str) -> str:
    return TAG_RE.sub("", s)

def parse_problem_text(text: str) -> tuple[int, str]:
    # "1456. 定长子串中元音的最大数目" -> (1456, "定长子串中元音的最大数目")
    text = re.sub(r"\s+", " ", text).strip()
    m = re.match(r"^(\d+)\.\s*(.+)$", text)
    if not m:
        raise ValueError(f"Cannot parse problem text: {text}")
    return int(m.group(1)), m.group(2).strip()

def normalize_problem_url(base_post_url: str, href: str) -> str:
    # href could be "/problems/xxx/" or full URL
    full = urljoin(base_post_url, href)
    # Prefer canonical problem link when possible
    if "/problems/" in full:
        # ensure host is leetcode.cn
        if full.startswith("https://leetcode.cn"):
            return full
        # sometimes urljoin with relative already ok
        return full
    return full

def parse_module_problems(post_url: str) -> list[dict]:
    """
    Extract ordered problems from a module post.
    We try to parse within <li> blocks to detect "(会员题)" in the same bullet.
    Fallback: scan all anchors in order.
    """
    html = http_get(post_url)

    problems: list[dict] = []
    seen_ids: set[int] = set()

    li_blocks = LI_BLOCK_RE.findall(html)
    if li_blocks:
        for li_html in li_blocks:
            anchor = PROBLEM_ANCHOR_RE.search(li_html)
            if not anchor:
                continue
            raw_text = strip_tags(anchor.group("text"))
            try:
                pid, title = parse_problem_text(raw_text)
            except Exception:
                continue
            if pid in seen_ids:
                continue
            href = anchor.group("href")
            url = normalize_problem_url(post_url, href)
            premium = "会员题" in strip_tags(li_html)
            problems.append({"id": pid, "title": title, "url": url, "premium": premium})
            seen_ids.add(pid)
        return problems

    # Fallback: anchor scan
    for m in PROBLEM_ANCHOR_RE.finditer(html):
        raw_text = strip_tags(m.group("text"))
        try:
            pid, title = parse_problem_text(raw_text)
        except Exception:
            continue
        if pid in seen_ids:
            continue
        href = m.group("href")
        url = normalize_problem_url(post_url, href)
        problems.append({"id": pid, "title": title, "url": url, "premium": False})
        seen_ids.add(pid)

    return problems

def sync_endless_plan(repo_root: Path) -> dict:
    modules = []
    for name, url in ENDLESS_MODULES:
        try:
            probs = parse_module_problems(url)
        except Exception as e:
            print(f"⚠️ Failed to fetch/parse {name}: {e}", file=sys.stderr)
            probs = []
        modules.append({"name": name, "source": url, "problems": probs})

    plan = {
        "version": 1,
        "synced_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "modules": modules,
    }
    (repo_root / PLAN_FILE).parent.mkdir(parents=True, exist_ok=True)
    write_text(repo_root / PLAN_FILE, json.dumps(plan, ensure_ascii=False, indent=2))
    return plan

def load_plan(repo_root: Path) -> dict | None:
    p = repo_root / PLAN_FILE
    if not p.exists():
        return None
    try:
        return json.loads(read_text(p))
    except Exception:
        return None

def flatten_plan(plan: dict, include_premium: bool = True) -> list[dict]:
    flat = []
    for mod in plan.get("modules", []):
        for prob in mod.get("problems", []):
            if (not include_premium) and prob.get("premium"):
                continue
            flat.append({
                "module": mod.get("name", ""),
                "id": int(prob["id"]),
                "title": prob.get("title", ""),
                "url": prob.get("url", ""),
                "premium": bool(prob.get("premium", False)),
            })
    return flat


# ------------------ Solved detection ------------------

SOLVED_ID_RE = re.compile(r"^(\d+)\.")

def collect_solved_ids(repo_root: Path) -> set[int]:
    solved = set()
    for path in repo_root.rglob("*.cpp"):
        # ignore tools/data/.git etc
        rel_parts = path.relative_to(repo_root).parts
        if rel_parts and rel_parts[0] in REPO_IGNORE_DIRS:
            continue
        m = SOLVED_ID_RE.match(path.name)
        if m:
            solved.add(int(m.group(1)))
    return solved


# ------------------ Weekly TODO freeze ------------------

def week_start_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())

def load_week_state(repo_root: Path) -> dict | None:
    p = repo_root / WEEK_FILE
    if not p.exists():
        return None
    try:
        return json.loads(read_text(p))
    except Exception:
        return None

def save_week_state(repo_root: Path, state: dict) -> None:
    write_text(repo_root / WEEK_FILE, json.dumps(state, ensure_ascii=False, indent=2))

def make_weekly_todo_ids(flat: list[dict], solved: set[int], n: int) -> list[int]:
    ids = []
    for item in flat:
        pid = item["id"]
        if pid in solved:
            continue
        ids.append(pid)
        if len(ids) >= n:
            break
    return ids

def weekly_todo_block(repo_root: Path, flat: list[dict], solved: set[int], n: int) -> str:
    today = date.today()
    ws = week_start_monday(today)
    we = ws + timedelta(days=6)

    # load or create weekly list
    state = load_week_state(repo_root)
    if state and state.get("week_start") == ws.isoformat():
        todo_ids = state.get("todo_ids", [])
    else:
        todo_ids = make_weekly_todo_ids(flat, solved, n)
        save_week_state(repo_root, {"week_start": ws.isoformat(), "todo_ids": todo_ids})

    # map id -> info
    info = {x["id"]: x for x in flat}
    lines = []
    lines.append(f"## 本周 TODO（{ws.isoformat()} ~ {we.isoformat()}）")
    if not todo_ids:
        lines.append("_（已刷完题单 or 未能生成 TODO）_")
        return "\n".join(lines)

    for pid in todo_ids:
        item = info.get(pid)
        if not item:
            # plan changed; keep id visible
            checked = "x" if pid in solved else " "
            lines.append(f"- [{checked}] {pid}")
            continue
        checked = "x" if pid in solved else " "
        title = f'{pid}. {item["title"]}'
        prefix = f''
        premium_tag = "（会员）" if item.get("premium") else ""
        lines.append(f'- [{checked}] {prefix} [{title}]({item["url"]}){premium_tag}')

    return "\n".join(lines)


# ------------------ README generators ------------------

def generate_root_auto(repo_root: Path, topics: list[Path], plan: dict | None, weekly_n: int, include_premium: bool) -> str:
    solved = collect_solved_ids(repo_root)

    lines = []
    # Weekly TODO
    if plan:
        flat = flatten_plan(plan, include_premium=include_premium)
        lines.append(weekly_todo_block(repo_root, flat, solved, weekly_n))
        # progress summary
        plan_ids = {x["id"] for x in flat}
        done = len(plan_ids & solved)
        total = len(plan_ids)
        lines.append("")
        lines.append(f"**题单进度**：{done}/{total}（以 endlesscheng 题单为准）")
    else:
        lines.append("## 本周 TODO")
        lines.append("_（未同步题单：运行 `python tools/update_readme.py --sync-plan`）_")

    lines.append("\n---\n")

    # Directory navigation (only existing)
    lines.append("## 当前目录")
    if not topics:
        lines.append("_（暂无内容）_")
    else:
        for topic in topics:
            rel_topic = topic.relative_to(repo_root).as_posix() + "/"
            cpp_count = 0
            for sub in list_subcats(topic):
                cpp_count += len(list_cpp_files(sub))
            topic_text = f"{topic.name}（{cpp_count} 题）" if cpp_count else topic.name
            lines.append(f"- {md_link(topic_text, rel_topic)}")
            for sub in list_subcats(topic):
                rel_sub = sub.relative_to(repo_root).as_posix() + "/"
                c = len(list_cpp_files(sub))
                sub_text = f"{sub.name}（{c}）" if c else sub.name
                lines.append(f"  - {md_link(sub_text, rel_sub)}")

    lines.append("")
    lines.append("## 日常使用")
    lines.append("新增 `.cpp` 文件后运行：")
    lines.append("")
    lines.append("```bash")
    lines.append("python tools/update_readme.py")
    lines.append("```")
    return "\n".join(lines)

def generate_topic_auto(repo_root: Path, topic_dir: Path) -> str:
    subs = list_subcats(topic_dir)
    lines = []
    if not subs:
        lines.append("_（暂无小类目录）_")
        return "\n".join(lines)

    lines.append("## 小类导航")
    for sub in subs:
        rel_sub = sub.relative_to(topic_dir).as_posix() + "/"
        c = len(list_cpp_files(sub))
        sub_text = f"{sub.name}（{c}）" if c else sub.name
        lines.append(f"- {md_link(sub_text, rel_sub)}")

    lines.append("")
    lines.append("## 题目索引")
    for sub in subs:
        cpp_files = list_cpp_files(sub)
        if not cpp_files:
            continue
        lines.append("")
        lines.append(f"### {sub.name}")
        for f in cpp_files:
            disp = Path(f.name).stem
            rel = f.relative_to(topic_dir).as_posix()
            lines.append(f"- {md_link(disp, rel)}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sync-plan", action="store_true", help="Sync endlesscheng plan from leetcode.cn discuss posts")
    parser.add_argument("--weekly", type=int, default=10, help="Weekly TODO size")
    parser.add_argument("--include-premium", action="store_true", help="Include premium problems in weekly TODO")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    repo_root = find_repo_root(script_dir)
    os.chdir(repo_root)

    # Plan
    plan = load_plan(repo_root)
    if args.sync_plan or plan is None:
        print("🔄 Syncing endlesscheng plan...")
        plan = sync_endless_plan(repo_root)

    topics = list_topics(repo_root)

    # Root README
    root_readme = repo_root / "README.md"
    root_existing = ensure_root_readme_has_header(read_text(root_readme))
    root_auto = generate_root_auto(
        repo_root=repo_root,
        topics=topics,
        plan=plan,
        weekly_n=args.weekly,
        include_premium=args.include_premium,
    )
    root_new = replace_auto_section(root_existing, root_auto)
    write_text(root_readme, root_new)

    # Topic READMEs
    for topic in topics:
        topic_readme = topic / "README.md"
        existing = ensure_topic_readme_has_header(read_text(topic_readme), topic.name)
        topic_auto = generate_topic_auto(repo_root, topic)
        new_content = replace_auto_section(existing, topic_auto)
        write_text(topic_readme, new_content)

    print("✅ Updated:")
    print(" - README.md")
    for topic in topics:
        print(f" - {topic.name}/README.md")


if __name__ == "__main__":
    main()
