# -*- coding: utf-8 -*-
"""
Daily workflow:
  1) Create new .cpp file
  2) Run: python tools/update_readme.py
It will:
  - Update root README navigation (only existing dirs)
  - Update each topic README index (only existing dirs/files)
  - Generate a weekly TODO list in root README (frozen within the week)
    based on endlesscheng lists, with your section picking rules.

First time (or when lists update):
  python tools/update_readme.py --sync-plan
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen

AUTO_START = "<!-- AUTO-GENERATED:START -->"
AUTO_END = "<!-- AUTO-GENERATED:END -->"

REPO_IGNORE_DIRS = {".git", ".vscode", ".idea", "__pycache__", "tools", "data"}

CONFIG_PATH = Path("tools/config.json")
PLAN_PATH = Path("data/endless_plan.json")
WEEK_STATE_PATH = Path(".todo_week.json")

# ------------ small utils ------------

def find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for p in [cur, *cur.parents]:
        if (p / ".git").exists():
            return p
    return start.resolve()

def natural_key(s: str):
    parts = re.split(r"(\d+)", s)
    return [int(p) if p.isdigit() else p.lower() for p in parts]

def is_dir_ignorable(name: str) -> bool:
    return name in REPO_IGNORE_DIRS or name.startswith(".")

def md_link(text: str, rel_posix_path: str) -> str:
    return f"[{text}]({quote(rel_posix_path, safe='/\-_.~')})"

def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else ""

def write_text(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8", newline="\n")

def replace_auto_section(original: str, new_section: str) -> str:
    if AUTO_START in original and AUTO_END in original:
        pre = original.split(AUTO_START)[0].rstrip()
        post = original.split(AUTO_END)[1].lstrip()
        return f"{pre}\n{AUTO_START}\n{new_section.rstrip()}\n{AUTO_END}\n{post}".rstrip() + "\n"
    base = original.rstrip()
    if base:
        base += "\n\n"
    return f"{base}{AUTO_START}\n{new_section.rstrip()}\n{AUTO_END}\n"

# ------------ config ------------

DEFAULT_CONFIG = {
    "weekly_todo_size": 10,
    "exclude_premium": True,
    "section_pick_rules": [
        {"match": "基础", "mode": "all"},
        {"match": "进阶", "mode": "first", "limit": 1},
        {"match": "其他", "mode": "first", "limit": 1},
        {"match": "思维扩展", "mode": "first", "limit": 1},
    ],
    "modules": [
        {"name": "滑动窗口与双指针", "url": "https://leetcode.cn/discuss/post/0viNMK/"},
        {"name": "二分算法", "url": "https://leetcode.cn/discuss/post/SqopEo/"},
        {"name": "单调栈", "url": "https://leetcode.cn/discuss/post/9oZFK9/"},
        {"name": "网格图", "url": "https://leetcode.cn/discuss/post/YiXPXW/"},
        {"name": "位运算", "url": "https://leetcode.cn/discuss/post/dHn9Vk/"},
        {"name": "图论算法", "url": "https://leetcode.cn/discuss/post/01LUak/"},
        {"name": "动态规划", "url": "https://leetcode.cn/discuss/post/tXLS3i/"},
        {"name": "常用数据结构", "url": "https://leetcode.cn/discuss/post/mOr1u6/"},
        {"name": "数学算法", "url": "https://leetcode.cn/discuss/post/IYT3ss/"},
        {"name": "贪心与思维", "url": "https://leetcode.cn/discuss/post/g6KTKL/"},
        {"name": "链表、树与回溯", "url": "https://leetcode.cn/discuss/post/K0n2gO/"},
        {"name": "字符串", "url": "https://leetcode.cn/discuss/post/SJFwQI/"},
    ],
}

def load_config(repo_root: Path) -> dict:
    p = repo_root / CONFIG_PATH
    if not p.exists():
        write_text(p, json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2))
        return DEFAULT_CONFIG
    try:
        return json.loads(read_text(p))
    except Exception:
        return DEFAULT_CONFIG

# ------------ repo scan (solved ids) ------------

SOLVED_ID_RE = re.compile(r"^(\d+)\.")

def collect_solved_ids(repo_root: Path) -> set[int]:
    solved = set()
    for f in repo_root.rglob("*.cpp"):
        rel = f.relative_to(repo_root).parts
        if rel and rel[0] in REPO_IGNORE_DIRS:
            continue
        m = SOLVED_ID_RE.match(f.name)
        if m:
            solved.add(int(m.group(1)))
    return solved

# ------------ readme navigation (only existing) ------------

def list_topics(repo_root: Path) -> list[Path]:
    topics = [p for p in repo_root.iterdir() if p.is_dir() and not is_dir_ignorable(p.name)]
    topics.sort(key=lambda x: natural_key(x.name))
    return topics

def list_subcats(topic_dir: Path) -> list[Path]:
    subs = [p for p in topic_dir.iterdir() if p.is_dir() and not is_dir_ignorable(p.name)]
    subs.sort(key=lambda x: natural_key(x.name))
    return subs

def list_cpp_files(folder: Path) -> list[Path]:
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".cpp"]
    files.sort(key=lambda x: natural_key(x.name))
    return files

# ------------ endless plan sync & parse (section-aware) ------------

def http_get(url: str, timeout: int = 30) -> str:
    req = Request(url, headers={"User-Agent": "leetcode-practice-bot/1.0"}, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")

TAG_RE = re.compile(r"<[^>]+>")
def strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", TAG_RE.sub("", s)).strip()

# tokens: headers + paragraphs + list items (to catch "思维扩展（选做）" etc)
TOKEN_RE = re.compile(
    r"(<h[1-6][^>]*>.*?</h[1-6]>)|(<p[^>]*>.*?</p>)|(<li[^>]*>.*?</li>)",
    re.IGNORECASE | re.DOTALL
)
H_RE = re.compile(r"<h[1-6][^>]*>(.*?)</h[1-6]>", re.IGNORECASE | re.DOTALL)
P_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.IGNORECASE | re.DOTALL)

A_PROB_RE = re.compile(
    r'<a[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<text>\s*\d+\.\s*[^<]+)</a>',
    re.IGNORECASE
)

def parse_problem_text(text: str) -> tuple[int, str]:
    text = re.sub(r"\s+", " ", text).strip()
    m = re.match(r"^(\d+)\.\s*(.+)$", text)
    if not m:
        raise ValueError(text)
    return int(m.group(1)), m.group(2).strip()

def is_section_title(txt: str) -> bool:
    # short-ish and contains key words, avoid normal paragraphs
    if not txt:
        return False
    if len(txt) > 40:
        return False
    keys = ("基础", "进阶", "选做", "其他", "思维扩展")
    return any(k in txt for k in keys)

def parse_module(post_url: str, module_name: str) -> list[dict]:
    html = http_get(post_url)
    cur_section = ""
    out: list[dict] = []
    seen: set[int] = set()

    for m in TOKEN_RE.finditer(html):
        token = m.group(0)

        hm = H_RE.search(token)
        if hm:
            t = strip_tags(hm.group(1))
            if is_section_title(t):
                cur_section = t
            continue

        pm = P_RE.search(token)
        if pm:
            t = strip_tags(pm.group(1))
            if is_section_title(t):
                cur_section = t
            continue

        # li
        am = A_PROB_RE.search(token)
        if not am:
            continue
        raw_text = strip_tags(am.group("text"))
        try:
            pid, title = parse_problem_text(raw_text)
        except Exception:
            continue
        if pid in seen:
            continue
        href = am.group("href")
        prob_url = urljoin(post_url, href)
        premium = "会员题" in strip_tags(token)
        out.append({
            "module": module_name,
            "section": cur_section or "",
            "id": pid,
            "title": title,
            "url": prob_url,
            "premium": premium,
        })
        seen.add(pid)

    return out

def sync_plan(repo_root: Path, config: dict) -> dict:
    modules = []
    for mod in config.get("modules", []):
        name, url = mod["name"], mod["url"]
        try:
            probs = parse_module(url, name)
        except Exception:
            probs = []
        modules.append({"name": name, "source": url, "problems": probs})
    plan = {"version": 2, "synced_at": datetime.utcnow().isoformat(timespec="seconds") + "Z", "modules": modules}
    write_text(repo_root / PLAN_PATH, json.dumps(plan, ensure_ascii=False, indent=2))
    return plan

def load_plan(repo_root: Path) -> dict | None:
    p = repo_root / PLAN_PATH
    if not p.exists():
        return None
    try:
        return json.loads(read_text(p))
    except Exception:
        return None

def apply_section_rules(plan: dict, config: dict) -> list[dict]:
    exclude_premium = bool(config.get("exclude_premium", True))
    rules_cfg = config.get("section_pick_rules", [])
    rules = []
    for r in rules_cfg:
        rules.append({
            "re": re.compile(r.get("match", ""), re.IGNORECASE),
            "mode": r.get("mode", "all"),
            "limit": int(r.get("limit", 1)),
        })

    def pick_rule(section: str):
        for rr in rules:
            if rr["re"].search(section or ""):
                return rr
        return {"mode": "all", "limit": 10**9}

    flat: list[dict] = []
    # (module, section) -> count kept for "first" mode
    kept_count: dict[tuple[str, str], int] = {}

    for mod in plan.get("modules", []):
        for p in mod.get("problems", []):
            if exclude_premium and p.get("premium"):
                continue
            module = p.get("module") or mod.get("name", "")
            section = p.get("section", "") or ""
            rr = pick_rule(section)
            if rr["mode"] == "all":
                flat.append(p)
            elif rr["mode"] == "first":
                key = (module, section)
                c = kept_count.get(key, 0)
                if c < rr["limit"]:
                    flat.append(p)
                    kept_count[key] = c + 1
            # else: skip
    return flat

# ------------ weekly todo (frozen within week) ------------

def week_start_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())

def load_week_state(repo_root: Path) -> dict | None:
    p = repo_root / WEEK_STATE_PATH
    if not p.exists():
        return None
    try:
        return json.loads(read_text(p))
    except Exception:
        return None

def save_week_state(repo_root: Path, state: dict) -> None:
    write_text(repo_root / WEEK_STATE_PATH, json.dumps(state, ensure_ascii=False, indent=2))

def make_weekly_ids(flat: list[dict], solved: set[int], n: int) -> list[int]:
    ids = []
    for x in flat:
        pid = int(x["id"])
        if pid in solved:
            continue
        ids.append(pid)
        if len(ids) >= n:
            break
    return ids

def weekly_block(repo_root: Path, flat: list[dict], solved: set[int], n: int) -> str:
    today = date.today()
    ws = week_start_monday(today)
    we = ws + timedelta(days=6)

    state = load_week_state(repo_root)
    if state and state.get("week_start") == ws.isoformat():
        todo_ids = state.get("todo_ids", [])
    else:
        todo_ids = make_weekly_ids(flat, solved, n)
        save_week_state(repo_root, {"week_start": ws.isoformat(), "todo_ids": todo_ids})

    info = {int(x["id"]): x for x in flat}

    lines = [f"## 本周 TODO（{ws.isoformat()} ~ {we.isoformat()}）"]
    if not todo_ids:
        lines.append("_（已无待刷题目 / 未能生成 TODO）_")
        return "\n".join(lines)

    for pid in todo_ids:
        x = info.get(pid)
        checked = "x" if pid in solved else " "
        if not x:
            lines.append(f"- [{checked}] {pid}")
            continue
        title = f"{pid}. {x.get('title','')}"
        module = x.get("module", "")
        url = x.get("url", "")
        lines.append(f"- [{checked}]  [{title}]({url})")
    return "\n".join(lines)

# ------------ README render ------------

def ensure_root_header(existing: str) -> str:
    return existing if existing.strip() else "# leetcode-practice\n\n"

def ensure_topic_header(existing: str, topic_name: str) -> str:
    return existing if existing.strip() else f"# {topic_name}\n\n"

def root_auto(repo_root: Path, topics: list[Path], flat: list[dict], cfg: dict) -> str:
    solved = collect_solved_ids(repo_root)
    weekly_n = int(cfg.get("weekly_todo_size", 10))

    lines = []
    lines.append(weekly_block(repo_root, flat, solved, weekly_n))

    total = len({int(x["id"]) for x in flat})
    done = len(set(solved) & {int(x["id"]) for x in flat})
    lines.append("")
    lines.append(f"**题单进度**：{done}/{total}（只统计非会员题 + 选做规则过滤后的题单）")
    lines.append("\n---\n")

    lines.append("## 当前目录")
    if not topics:
        lines.append("_（暂无内容）_")
        return "\n".join(lines)

    for topic in topics:
        rel_topic = topic.relative_to(repo_root).as_posix() + "/"
        cpp_count = sum(len(list_cpp_files(sub)) for sub in list_subcats(topic))
        topic_text = f"{topic.name}（{cpp_count} 题）" if cpp_count else topic.name
        lines.append(f"- {md_link(topic_text, rel_topic)}")
        for sub in list_subcats(topic):
            rel_sub = sub.relative_to(repo_root).as_posix() + "/"
            c = len(list_cpp_files(sub))
            sub_text = f"{sub.name}（{c}）" if c else sub.name
            lines.append(f"  - {md_link(sub_text, rel_sub)}")

    lines.append("")
    lines.append("## 日常使用")
    lines.append("新增 `.cpp` 后运行：")
    lines.append("```bash")
    lines.append("python tools/update_readme.py")
    lines.append("```")
    return "\n".join(lines)

def topic_auto(repo_root: Path, topic_dir: Path) -> str:
    subs = list_subcats(topic_dir)
    if not subs:
        return "_（暂无小类目录）_"

    lines = ["## 小类导航"]
    for sub in subs:
        rel = sub.relative_to(topic_dir).as_posix() + "/"
        c = len(list_cpp_files(sub))
        txt = f"{sub.name}（{c}）" if c else sub.name
        lines.append(f"- {md_link(txt, rel)}")

    lines.append("")
    lines.append("## 题目索引")
    for sub in subs:
        files = list_cpp_files(sub)
        if not files:
            continue
        lines.append("")
        lines.append(f"### {sub.name}")
        for f in files:
            rel = f.relative_to(topic_dir).as_posix()
            lines.append(f"- {md_link(f.stem, rel)}")

    return "\n".join(lines)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sync-plan", action="store_true", help="Sync endless plan from leetcode.cn discuss posts")
    args = ap.parse_args()

    script_dir = Path(__file__).resolve().parent
    repo_root = find_repo_root(script_dir)
    os.chdir(repo_root)

    cfg = load_config(repo_root)

    plan = load_plan(repo_root)
    if args.sync_plan or plan is None:
        plan = sync_plan(repo_root, cfg)

    flat = apply_section_rules(plan, cfg)

    # root README
    root_path = repo_root / "README.md"
    root_existing = ensure_root_header(read_text(root_path))
    root_new = replace_auto_section(root_existing, root_auto(repo_root, list_topics(repo_root), flat, cfg))
    write_text(root_path, root_new)

    # topic READMEs
    for topic in list_topics(repo_root):
        tp = topic / "README.md"
        existing = ensure_topic_header(read_text(tp), topic.name)
        new = replace_auto_section(existing, topic_auto(repo_root, topic))
        write_text(tp, new)

    print("✅ updated README(s)")

if __name__ == "__main__":
    main()
