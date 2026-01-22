# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen

AUTO_START = "<!-- AUTO-GENERATED:START -->"
AUTO_END = "<!-- AUTO-GENERATED:END -->"

REPO_IGNORE_DIRS = {".git", ".vscode", ".idea", "__pycache__", "tools", "data"}
CONFIG_PATH = Path("tools/config.json")
PLAN_PATH = Path("data/endless_plan.json")
TODO_STATE_PATH = Path("data/todo_state.json")

SOLVED_ID_RE = re.compile(r"^(\d+)\.")
TAG_RE = re.compile(r"<[^>]+>")
TOKEN_RE = re.compile(
    r"(<h[1-6][^>]*>.*?</h[1-6]>)|(<p[^>]*>.*?</p>)|(<li[^>]*>.*?</li>)",
    re.IGNORECASE | re.DOTALL,
)
H_RE = re.compile(r"<h([1-6])[^>]*>(.*?)</h\1>", re.IGNORECASE | re.DOTALL)
P_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.IGNORECASE | re.DOTALL)
A_PROB_RE = re.compile(
    r'<a[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<text>\s*\d+\.\s*[^<]+)</a>',
    re.IGNORECASE,
)

DEFAULT_CONFIG = {
    "todo_size": 10,
    "exclude_premium": True,
    "plan_auto_sync_days": 30,
    "section_pick_rules": [
        {"match": "基础", "mode": "all"},
        {"match": "进阶", "mode": "first", "limit": 1},
        {"match": "其他", "mode": "first", "limit": 1},
        {"match": "思维扩展", "mode": "first", "limit": 1},
    ],
    "modules": [
        {"name": "滑动窗口与双指针", "url": "https://leetcode.cn/discuss/post/0viNMK/"},
    ],
}


@dataclass(frozen=True)
class Rule:
    tag: str
    rx: re.Pattern
    mode: str  # "all" or "first"
    limit: int


def strip_tags(s: str) -> str:
    s = TAG_RE.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


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


def load_config(repo_root: Path) -> dict:
    p = repo_root / CONFIG_PATH
    if not p.exists():
        write_text(p, json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2))
        return DEFAULT_CONFIG
    try:
        cfg = json.loads(read_text(p))
        merged = dict(DEFAULT_CONFIG)
        merged.update(cfg)
        # merge nested lists if provided
        if "section_pick_rules" in cfg:
            merged["section_pick_rules"] = cfg["section_pick_rules"]
        if "modules" in cfg:
            merged["modules"] = cfg["modules"]
        return merged
    except Exception:
        return DEFAULT_CONFIG


def http_get(url: str, timeout: int = 30) -> str:
    req = Request(url, headers={"User-Agent": "leetcode-practice-bot/1.0"}, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def parse_problem_text(text: str) -> tuple[int, str]:
    text = re.sub(r"\s+", " ", text).strip()
    m = re.match(r"^(\d+)\.\s*(.+)$", text)
    if not m:
        raise ValueError(text)
    return int(m.group(1)), m.group(2).strip()


def compile_rules(cfg: dict) -> list[Rule]:
    rules_cfg = cfg.get("section_pick_rules", [])
    rules: list[Rule] = []
    for r in rules_cfg:
        tag = str(r.get("match", "")).strip() or "默认"
        rules.append(
            Rule(
                tag=tag,
                rx=re.compile(tag, re.IGNORECASE),
                mode=str(r.get("mode", "all")).strip(),
                limit=int(r.get("limit", 1)),
            )
        )
    return rules


def is_section_title(txt: str, rules: list[Rule]) -> bool:
    if not txt or len(txt) > 60:
        return False
    return any(rule.rx.search(txt) for rule in rules)


def parse_module(url: str, module_name: str, rules: list[Rule]) -> list[dict]:
    """
    Extract problems while tracking:
      - point: the current "小点" header (e.g., 定长/不定长/单序列...)
      - section: the current section header (e.g., §1.1 基础 / §1.2 进阶（选做）)
    """
    html = http_get(url)

    cur_point = ""
    cur_section = ""

    out: list[dict] = []
    seen: set[int] = set()

    for m in TOKEN_RE.finditer(html):
        token = m.group(0)

        hm = H_RE.search(token)
        if hm:
            text = strip_tags(hm.group(2))
            if is_section_title(text, rules):
                cur_section = text
            else:
                cur_point = text
            continue

        pm = P_RE.search(token)
        if pm:
            text = strip_tags(pm.group(1))
            if is_section_title(text, rules):
                cur_section = text
            continue

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
        prob_url = urljoin(url, href)
        premium = "会员题" in strip_tags(token)

        out.append(
            {
                "module": module_name,
                "point": cur_point or "",
                "section": cur_section or "",
                "id": pid,
                "title": title,
                "url": prob_url,
                "premium": premium,
            }
        )
        seen.add(pid)

    return out


def should_sync_plan(repo_root: Path, cfg: dict) -> bool:
    p = repo_root / PLAN_PATH
    if not p.exists():
        return True
    try:
        plan = json.loads(read_text(p))
        ts = plan.get("synced_at", "")
        # synced_at like "2026-01-22T12:34:56Z"
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        days = int(cfg.get("plan_auto_sync_days", 30))
        return datetime.now(timezone.utc) - dt > timedelta(days=days)
    except Exception:
        return True


def sync_plan(repo_root: Path, cfg: dict) -> dict:
    rules = compile_rules(cfg)
    modules = []
    for mod in cfg.get("modules", []):
        name, url = mod["name"], mod["url"]
        probs = parse_module(url, name, rules)
        modules.append({"name": name, "source": url, "problems": probs})

    plan = {
        "version": 4,
        "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "modules": modules,
    }
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


def pick_tag(section: str, rules: list[Rule]) -> Rule | None:
    for r in rules:
        if r.rx.search(section or ""):
            return r
    return None


def apply_pick_rules(plan: dict, cfg: dict) -> list[dict]:
    """
    Apply rules per: (module, point, rule_tag)
    - 基础: keep all
    - 进阶/其他/思维扩展: keep first K
    Also exclude premium if configured.
    Keep original order.
    """
    exclude_premium = bool(cfg.get("exclude_premium", True))
    rules = compile_rules(cfg)

    kept_count: dict[tuple[str, str, str], int] = {}
    out: list[dict] = []

    for mod in plan.get("modules", []):
        for p in mod.get("problems", []):
            if exclude_premium and p.get("premium"):
                continue

            module = p.get("module", mod.get("name", ""))
            point = p.get("point", "") or ""
            section = p.get("section", "") or ""

            rule = pick_tag(section, rules)
            # default: treat as "all" (don’t accidentally drop)
            if rule is None:
                out.append(p)
                continue

            key = (module, point, rule.tag)
            if rule.mode == "all":
                out.append(p)
            else:  # "first"
                c = kept_count.get(key, 0)
                if c < rule.limit:
                    out.append(p)
                    kept_count[key] = c + 1

    return out


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


def load_todo_state(repo_root: Path) -> dict:
    p = repo_root / TODO_STATE_PATH
    if not p.exists():
        return {}
    try:
        return json.loads(read_text(p))
    except Exception:
        return {}


def save_todo_state(repo_root: Path, state: dict) -> None:
    write_text(repo_root / TODO_STATE_PATH, json.dumps(state, ensure_ascii=False, indent=2))


def choose_current_module(cfg: dict, filtered: list[dict], solved: set[int], state: dict) -> str | None:
    modules = [m["name"] for m in cfg.get("modules", [])]
    by_mod: dict[str, list[dict]] = {name: [] for name in modules}
    for x in filtered:
        by_mod.setdefault(x["module"], []).append(x)

    # keep state module if it still has remaining tasks (or we already have a todo list there)
    cur = state.get("module")
    if cur in by_mod:
        if any(int(p["id"]) not in solved for p in by_mod[cur]):
            return cur

    # otherwise pick first module with remaining tasks
    for name in modules:
        if any(int(p["id"]) not in solved for p in by_mod.get(name, [])):
            return name
    return None


def build_todo_ids_for_module(module_name: str, filtered: list[dict], solved: set[int], n: int) -> list[int]:
    mod_list = [x for x in filtered if x["module"] == module_name]
    ids = []
    for x in mod_list:
        pid = int(x["id"])
        if pid in solved:
            continue
        ids.append(pid)
        if len(ids) >= n:
            break
    # 不跨模块凑数：不足 n 就返回不足 n
    return ids


def ensure_todo_list(cfg: dict, filtered: list[dict], solved: set[int]) -> tuple[str | None, list[int]]:
    """
    Maintain a stable todo list (fixed size) per current module:
      - Keep existing list until全部完成
      - Then regenerate next N unsolved in the same module
      - If module剩余不足N，不硬凑
      - If module完成，自动切换到下一个模块
    """
    n = int(cfg.get("todo_size", 10))
    state = load_todo_state(Path.cwd())
    cur_mod = choose_current_module(cfg, filtered, solved, state)
    if not cur_mod:
        save_todo_state(Path.cwd(), {"module": None, "todo_ids": []})
        return None, []

    # map for validation
    valid_ids = {int(x["id"]) for x in filtered if x["module"] == cur_mod}

    old_mod = state.get("module")
    old_ids = [int(x) for x in state.get("todo_ids", []) if isinstance(x, int) or (isinstance(x, str) and x.isdigit())]

    # if module switched or old list invalid => rebuild
    if old_mod != cur_mod or any(pid not in valid_ids for pid in old_ids):
        new_ids = build_todo_ids_for_module(cur_mod, filtered, solved, n)
        save_todo_state(Path.cwd(), {"module": cur_mod, "todo_ids": new_ids})
        return cur_mod, new_ids

    # keep list until all solved
    if old_ids and all(pid in solved for pid in old_ids):
        new_ids = build_todo_ids_for_module(cur_mod, filtered, solved, n)
        save_todo_state(Path.cwd(), {"module": cur_mod, "todo_ids": new_ids})
        return cur_mod, new_ids

    # if empty list (e.g., first time) => build
    if not old_ids:
        new_ids = build_todo_ids_for_module(cur_mod, filtered, solved, n)
        save_todo_state(Path.cwd(), {"module": cur_mod, "todo_ids": new_ids})
        return cur_mod, new_ids

    # otherwise keep current
    return cur_mod, old_ids


def render_root_auto(repo_root: Path, cfg: dict, filtered: list[dict], solved: set[int]) -> str:
    # TODO
    mod, todo_ids = ensure_todo_list(cfg, filtered, solved)
    info = {int(x["id"]): x for x in filtered}

    lines = []
    lines.append("## TODO")
    if not mod:
        lines.append("_（题单已刷完 / 无待办）_")
    else:
        lines.append(f"**当前模块**：{mod}")
        if not todo_ids:
            lines.append("_（本模块已无待刷题目）_")
        else:
            for pid in todo_ids:
                x = info.get(pid)
                checked = "x" if pid in solved else " "
                if not x:
                    lines.append(f"- [{checked}] {pid}")
                    continue
                title = f"{pid}. {x.get('title','')}"
                point = (x.get("point") or "").strip()
                tag = f"{mod}" + (f" / {point}" if point else "")
                lines.append(f"- [{checked}]  [{title}]({x.get('url','')})")

    # progress
    ids_in_plan = {int(x["id"]) for x in filtered}
    done = len(ids_in_plan & solved)
    total = len(ids_in_plan)
    lines.append("")
    lines.append(f"**题单进度**：{done}/{total}（非会员 + 选做规则过滤后）")
    lines.append("\n---\n")

    # navigation (only existing)
    lines.append("## 当前目录")
    topics = list_topics(repo_root)
    if not topics:
        lines.append("_（暂无内容）_")
    else:
        for topic in topics:
            rel_topic = topic.relative_to(repo_root).as_posix() + "/"
            cpp_count = sum(len(list_cpp_files(sub)) for sub in list_subcats(topic))
            lines.append(f"- {md_link(f'{topic.name}（{cpp_count} 题）', rel_topic)}")
            for sub in list_subcats(topic):
                rel_sub = sub.relative_to(repo_root).as_posix() + "/"
                lines.append(f"  - {md_link(f'{sub.name}（{len(list_cpp_files(sub))}）', rel_sub)}")

    lines.append("")
    lines.append("```bash")
    lines.append("python tools/update_readme.py")
    lines.append("```")
    return "\n".join(lines)


def render_topic_auto(topic_dir: Path) -> str:
    subs = list_subcats(topic_dir)
    if not subs:
        return "_（暂无小类目录）_"

    lines = ["## 小类导航"]
    for sub in subs:
        rel = sub.relative_to(topic_dir).as_posix() + "/"
        lines.append(f"- {md_link(f'{sub.name}（{len(list_cpp_files(sub))}）', rel)}")

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


def ensure_header(existing: str, header: str) -> str:
    return existing if existing.strip() else header


def main():
    script_dir = Path(__file__).resolve().parent
    repo_root = find_repo_root(script_dir)
    os.chdir(repo_root)

    cfg = load_config(repo_root)

    plan = load_plan(repo_root)
    if plan is None or should_sync_plan(repo_root, cfg):
        try:
            plan = sync_plan(repo_root, cfg)
        except Exception:
            # fallback to cached if sync fails mid-run
            plan = load_plan(repo_root)

    filtered: list[dict] = []
    if plan:
        filtered = apply_pick_rules(plan, cfg)

    solved = collect_solved_ids(repo_root)

    # root README
    root_path = repo_root / "README.md"
    root_existing = ensure_header(read_text(root_path), "# leetcode-practice\n\n")
    root_new = replace_auto_section(root_existing, render_root_auto(repo_root, cfg, filtered, solved))
    write_text(root_path, root_new)

    # topic READMEs
    for topic in list_topics(repo_root):
        tp = topic / "README.md"
        existing = ensure_header(read_text(tp), f"# {topic.name}\n\n")
        new = replace_auto_section(existing, render_topic_auto(topic))
        write_text(tp, new)

    print("✅ updated README(s)")


if __name__ == "__main__":
    main()
