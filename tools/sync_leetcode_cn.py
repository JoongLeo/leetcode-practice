
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib import error as url_error
from urllib import parse as url_parse
from urllib import request as url_request


DEFAULT_CONFIG: Dict[str, Any] = {
    "solutions_dir": "solutions",
    "category_strategy": "first",  # first | all | joint
    "category_joiner": "+",
    "category_fallback": "uncategorized",
    "id_padding": 4,
    "file_name_style": "id",  # id | id_slug
    "readme_path": "README.md",
    "readme_markers": {
        "start": "<!-- AUTO-GENERATED:START -->",
        "end": "<!-- AUTO-GENERATED:END -->",
    },
    "category_alias": {},
    "language_extensions": {
        "python": "py",
        "python3": "py",
        "py": "py",
        "java": "java",
        "cpp": "cpp",
        "c++": "cpp",
        "c": "c",
        "csharp": "cs",
        "c#": "cs",
        "javascript": "js",
        "js": "js",
        "typescript": "ts",
        "ts": "ts",
        "golang": "go",
        "go": "go",
        "rust": "rs",
        "swift": "swift",
        "kotlin": "kt",
        "scala": "scala",
        "ruby": "rb",
        "php": "php",
        "mysql": "sql",
        "mssql": "sql",
        "oraclesql": "sql",
        "sql": "sql",
        "bash": "sh",
        "shell": "sh",
        "sh": "sh",
    },
}

EXT_LANGUAGE_LABELS = {
    "py": "Python",
    "java": "Java",
    "cpp": "C++",
    "c": "C",
    "cs": "C#",
    "js": "JavaScript",
    "ts": "TypeScript",
    "go": "Go",
    "rs": "Rust",
    "swift": "Swift",
    "kt": "Kotlin",
    "scala": "Scala",
    "rb": "Ruby",
    "php": "PHP",
    "sql": "SQL",
    "sh": "Shell",
    "txt": "Text",
}

COMMENT_PREFIXES = (
    "//",
    "#",
    "/*",
    "--",
    ";",
    "%",
    "'''",
    '"""',
    "'",
)

TAG_BRACKET_PATTERNS = [
    r"\[([^\]]+)\]",
    r"【([^】]+)】",
]

TAG_PREFIX_PATTERN = re.compile(
    r"(?:tags?|分类|标签|category|topic|topics)[:：]\s*(.+)",
    re.IGNORECASE,
)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_config(config_path: Path) -> Dict[str, Any]:
    config = DEFAULT_CONFIG.copy()
    if config_path.exists():
        raw = load_json(config_path, {})
        if isinstance(raw, dict):
            config.update(raw)
    if "readme_markers" not in config or not isinstance(config["readme_markers"], dict):
        config["readme_markers"] = DEFAULT_CONFIG["readme_markers"]
    return config


def normalize_lang(value: Optional[str]) -> str:
    if not value:
        return ""
    return str(value).strip().lower().replace(" ", "")


def detect_extension(lang: Optional[str], config: Dict[str, Any]) -> str:
    key = normalize_lang(lang)
    if not key:
        return "txt"
    return config["language_extensions"].get(key, "txt")


def is_accepted(submission: Dict[str, Any]) -> bool:
    status = submission.get("status_display") or submission.get("status") or ""
    status = str(status).strip().lower()
    return status in {"accepted", "ac", "通过"}


def strip_comment_prefix(line: str) -> Optional[str]:
    text = line.lstrip("\ufeff").strip()
    for prefix in COMMENT_PREFIXES:
        if text.startswith(prefix):
            stripped = text[len(prefix) :].strip()
            if prefix == "/*":
                stripped = stripped.rstrip("*/").strip()
            elif prefix in ('"""', "'''") and stripped.endswith(prefix):
                stripped = stripped[: -len(prefix)].strip()
            return stripped
    return None


def extract_categories(code: str) -> List[str]:
    first_line: Optional[str] = None
    for raw in code.splitlines():
        if raw.strip():
            first_line = raw
            break
    if not first_line:
        return []
    comment = strip_comment_prefix(first_line)
    if comment is None:
        return []
    tags: List[str] = []
    for pattern in TAG_BRACKET_PATTERNS:
        for match in re.findall(pattern, comment):
            tag = match.strip()
            if tag:
                tags.append(tag)
    if tags:
        return tags
    match = TAG_PREFIX_PATTERN.search(comment)
    if match:
        raw = match.group(1)
        parts = re.split(r"[，,;/|、]+", raw)
        tags = [part.strip() for part in parts if part.strip()]
    return tags


def normalize_category(name: str, config: Dict[str, Any]) -> str:
    alias = config.get("category_alias", {})
    name = alias.get(name, name).strip()
    name = re.sub(r'[<>:"/\\\\|?*]', "_", name)
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    if not name:
        return config.get("category_fallback", "uncategorized")
    return name


def select_categories(tags: List[str], config: Dict[str, Any]) -> List[str]:
    strategy = config.get("category_strategy", "first")
    normalized: List[str] = []
    seen = set()
    for tag in tags:
        normalized_tag = normalize_category(tag, config)
        if normalized_tag and normalized_tag not in seen:
            seen.add(normalized_tag)
            normalized.append(normalized_tag)
    if not normalized:
        fallback = config.get("category_fallback", "uncategorized")
        return [fallback]
    if strategy == "all":
        return normalized
    if strategy == "joint":
        joiner = config.get("category_joiner", "+")
        return [joiner.join(normalized)]
    return [normalized[0]]


def problem_id_to_name(problem_id: int, title_slug: Optional[str], config: Dict[str, Any]) -> str:
    padded = str(problem_id).zfill(int(config.get("id_padding", 4)))
    style = config.get("file_name_style", "id")
    if style == "id_slug" and title_slug:
        safe_slug = re.sub(r"[^a-z0-9-]+", "-", title_slug.lower()).strip("-")
        if safe_slug:
            return f"{padded}-{safe_slug}"
    return padded


def http_request(
    method: str,
    url: str,
    headers: Dict[str, str],
    data: Optional[bytes] = None,
) -> str:
    request = url_request.Request(url, method=method, headers=headers, data=data)
    try:
        with url_request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8")
    except url_error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {url}: {body[:200]}") from exc


def api_get(base_url: str, path: str, headers: Dict[str, str], params: Optional[Dict[str, Any]] = None) -> Any:
    url = f"{base_url}{path}"
    if params:
        url = f"{url}?{url_parse.urlencode(params)}"
    body = http_request("GET", url, headers=headers)
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON response from {url}") from exc


def api_post(base_url: str, path: str, headers: Dict[str, str], payload: Dict[str, Any]) -> Any:
    url = f"{base_url}{path}"
    data = json.dumps(payload).encode("utf-8")
    post_headers = headers.copy()
    post_headers["Content-Type"] = "application/json"
    body = http_request("POST", url, headers=post_headers, data=data)
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON response from {url}") from exc


def build_headers(session: str, csrf: Optional[str]) -> Dict[str, str]:
    headers = {
        "User-Agent": "leetcode-cn-sync/1.0",
        "Accept": "application/json",
        "Referer": "https://leetcode.cn/",
    }
    cookie = f"LEETCODE_SESSION={session}"
    if csrf:
        cookie = f"{cookie}; csrftoken={csrf}"
        headers["x-csrftoken"] = csrf
    headers["Cookie"] = cookie
    return headers


def fetch_submissions(
    base_url: str,
    headers: Dict[str, str],
    max_pages: int,
) -> Iterable[Dict[str, Any]]:
    offset = 0
    limit = 50
    last_key: Optional[str] = None
    pages = 0
    while True:
        params: Dict[str, Any] = {"offset": offset, "limit": limit}
        if last_key:
            params["last_key"] = last_key
        data = api_get(base_url, "/api/submissions/", headers, params=params)
        submissions = data.get("submissions_dump") or data.get("submissions") or []
        if not submissions:
            return
        for submission in submissions:
            yield submission
        pages += 1
        if max_pages and pages >= max_pages:
            return
        if data.get("has_next"):
            if "last_key" in data:
                last_key = data.get("last_key")
                if not last_key:
                    return
            else:
                offset += limit
            continue
        return


def fetch_submission_detail(
    base_url: str,
    headers: Dict[str, str],
    submission_id: str,
) -> Dict[str, Any]:
    return api_get(base_url, f"/api/submissions/{submission_id}/", headers)


def fetch_question_id_by_slug(
    base_url: str,
    headers: Dict[str, str],
    title_slug: str,
) -> Optional[int]:
    query = """
    query questionTitle($titleSlug: String!) {
      question(titleSlug: $titleSlug) {
        questionId
      }
    }
    """
    payload = {"query": query, "variables": {"titleSlug": title_slug}}
    data = api_post(base_url, "/graphql/", headers, payload)
    question = (data.get("data") or {}).get("question") or {}
    question_id = question.get("questionId")
    if question_id is None:
        return None
    try:
        return int(question_id)
    except (TypeError, ValueError):
        return None


def write_if_changed(path: Path, content: str) -> bool:
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing == content:
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    return True


def update_meta_entry(meta: Dict[str, Any], question_id: int, title: str, title_slug: str) -> bool:
    key = str(question_id)
    new_entry = {"title": title, "title_slug": title_slug}
    if meta.get(key) == new_entry:
        return False
    meta[key] = new_entry
    return True


def collect_solution_entries(
    repo_root: Path,
    solutions_dir: Path,
    meta: Dict[str, Any],
) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, int], int, int]:
    categories: Dict[str, List[Dict[str, Any]]] = {}
    language_counts: Dict[str, int] = {}
    unique_problem_ids = set()
    total_solutions = 0
    if not solutions_dir.exists():
        return categories, language_counts, 0, 0
    for category_dir in sorted([p for p in solutions_dir.iterdir() if p.is_dir()]):
        entries: List[Dict[str, Any]] = []
        for file_path in sorted(category_dir.glob("*.*")):
            match = re.match(r"(\d+)", file_path.stem)
            if not match:
                continue
            problem_id = int(match.group(1))
            unique_problem_ids.add(problem_id)
            ext = file_path.suffix.lstrip(".").lower() or "txt"
            language_counts[ext] = language_counts.get(ext, 0) + 1
            meta_info = meta.get(str(problem_id), {})
            entries.append(
                {
                    "id": problem_id,
                    "title": meta_info.get("title") or "",
                    "slug": meta_info.get("title_slug") or "",
                    "ext": ext,
                    "path": file_path.relative_to(repo_root).as_posix(),
                }
            )
            total_solutions += 1
        if entries:
            categories[category_dir.name] = sorted(entries, key=lambda e: e["id"])
    return categories, language_counts, len(unique_problem_ids), total_solutions


def render_readme_section(
    categories: Dict[str, List[Dict[str, Any]]],
    language_counts: Dict[str, int],
    unique_problem_count: int,
    total_solutions: int,
) -> str:
    lines: List[str] = []
    lines.append("## Stats")
    lines.append(f"- Total problems: {unique_problem_count}")
    lines.append(f"- Total solutions: {total_solutions}")
    if language_counts:
        language_parts = []
        for ext, count in sorted(language_counts.items(), key=lambda item: (-item[1], item[0])):
            label = EXT_LANGUAGE_LABELS.get(ext, ext.upper())
            language_parts.append(f"{label} ({count})")
        lines.append(f"- Languages: {', '.join(language_parts)}")
    if categories:
        category_parts = []
        for name, items in sorted(categories.items(), key=lambda item: item[0]):
            category_parts.append(f"{name} ({len(items)})")
        lines.append(f"- Categories: {', '.join(category_parts)}")
    lines.append("")
    lines.append("## Categories")
    if not categories:
        lines.append("_No synced solutions yet._")
        return "\n".join(lines)
    lines.append("| Category | Count |")
    lines.append("| --- | --- |")
    anchors = {}
    index = 1
    for name in sorted(categories.keys()):
        anchor = f"cat-{index}"
        anchors[name] = anchor
        lines.append(f"| [{name}](#{anchor}) | {len(categories[name])} |")
        index += 1
    for name in sorted(categories.keys()):
        anchor = anchors[name]
        lines.append("")
        lines.append(f'<a id="{anchor}"></a>')
        lines.append(f"### {name}")
        lines.append("| ID | Title | Language | File |")
        lines.append("| --- | --- | --- | --- |")
        for entry in categories[name]:
            title = entry["title"] or entry["slug"] or "-"
            slug = entry["slug"]
            if slug:
                title = f"[{title}](https://leetcode.cn/problems/{slug}/)"
            language_label = EXT_LANGUAGE_LABELS.get(entry["ext"], entry["ext"].upper())
            path = entry["path"]
            lines.append(f"| {entry['id']} | {title} | {language_label} | [{Path(path).name}]({path}) |")
    return "\n".join(lines)


def update_readme(repo_root: Path, config: Dict[str, Any], generated_section: str) -> bool:
    readme_path = repo_root / config.get("readme_path", "README.md")
    markers = config.get("readme_markers", {})
    start = markers.get("start", "<!-- AUTO-GENERATED:START -->")
    end = markers.get("end", "<!-- AUTO-GENERATED:END -->")
    if readme_path.exists():
        content = readme_path.read_text(encoding="utf-8")
    else:
        content = "# LeetCode.cn Solutions\n"
    if start in content and end in content:
        before, rest = content.split(start, 1)
        _, after = rest.split(end, 1)
        new_content = f"{before}{start}\n{generated_section}\n{end}{after}"
    else:
        if not content.endswith("\n"):
            content += "\n"
        new_content = f"{content}\n{start}\n{generated_section}\n{end}\n"
    return write_if_changed(readme_path, new_content)


def git_has_changes(repo_root: Path, paths: List[str]) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--", *paths],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    return bool(result.stdout.strip())


def git_commit(repo_root: Path, paths: List[str], message: str, push: bool) -> None:
    subprocess.run(["git", "add", "--", *paths], cwd=repo_root, check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=repo_root, check=True)
    if push:
        subprocess.run(["git", "push"], cwd=repo_root, check=True)


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync LeetCode.cn accepted submissions.")
    parser.add_argument("--commit", action="store_true", help="Commit changes after sync.")
    parser.add_argument("--push", action="store_true", help="Push after committing.")
    parser.add_argument("--max-pages", type=int, default=0, help="Limit pages for debugging.")
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    config_path = repo_root / "tools" / "leetcode_cn_config.json"
    state_path = repo_root / "tools" / "leetcode_cn_state.json"
    meta_path = repo_root / "tools" / "leetcode_cn_meta.json"

    config = load_config(config_path)
    state = load_json(state_path, {})
    meta = load_json(meta_path, {})
    if not isinstance(meta, dict):
        meta = {}

    session = os.environ.get("LEETCODE_SESSION", "").strip()
    if not session:
        print("Missing LEETCODE_SESSION environment variable.", file=sys.stderr)
        return 1
    csrf = os.environ.get("LEETCODE_CSRF", "").strip() or os.environ.get("CSRF_TOKEN", "").strip()
    base_url = os.environ.get("LEETCODE_CN_BASE", "https://leetcode.cn").rstrip("/")

    headers = build_headers(session, csrf or None)

    last_timestamp = int(state.get("last_timestamp", 0) or 0)
    last_ids = set(state.get("last_ids", []))
    newest_timestamp = 0
    newest_ids: List[str] = []

    solutions_dir = repo_root / config.get("solutions_dir", "solutions")
    updated_files: List[Path] = []
    processed = 0
    accepted = 0
    saved = 0
    skipped_comment = 0
    skipped_missing = 0
    seen_paths = set()
    meta_changed = False

    for submission in fetch_submissions(base_url, headers, args.max_pages):
        processed += 1
        submission_id = str(submission.get("id") or "").strip()
        raw_timestamp = submission.get("timestamp") or submission.get("submission_time") or submission.get("submit_time")
        try:
            timestamp = int(raw_timestamp or 0)
        except (TypeError, ValueError):
            timestamp = 0

        if newest_timestamp == 0 or timestamp > newest_timestamp:
            newest_timestamp = timestamp
            newest_ids = [submission_id] if submission_id else []
        elif timestamp == newest_timestamp and submission_id:
            newest_ids.append(submission_id)

        if timestamp < last_timestamp:
            break
        if timestamp == last_timestamp and submission_id in last_ids:
            continue
        if not is_accepted(submission):
            continue
        accepted += 1

        if not submission_id:
            skipped_missing += 1
            continue
        detail = fetch_submission_detail(base_url, headers, submission_id)
        code = detail.get("code") or ""
        if not code:
            skipped_missing += 1
            continue
        code = code.replace("\r\n", "\n")
        tags = extract_categories(code)
        if not tags:
            skipped_comment += 1
            continue
        question_id = detail.get("question_id") or submission.get("question_id")
        title_slug = detail.get("title_slug") or submission.get("title_slug")
        title = detail.get("title") or submission.get("title")
        if not question_id and title_slug:
            question_id = fetch_question_id_by_slug(base_url, headers, title_slug)
        try:
            question_id = int(question_id)
        except (TypeError, ValueError):
            skipped_missing += 1
            continue
        if title:
            meta_changed = update_meta_entry(meta, question_id, title, title_slug or "") or meta_changed
        elif title_slug:
            fallback_title = title_slug.replace("-", " ").title()
            meta_changed = update_meta_entry(meta, question_id, fallback_title, title_slug) or meta_changed

        extension = detect_extension(detail.get("lang") or submission.get("lang"), config)
        file_base = problem_id_to_name(question_id, title_slug, config)
        categories = select_categories(tags, config)
        for category in categories:
            target_path = solutions_dir / category / f"{file_base}.{extension}"
            if target_path in seen_paths:
                continue
            changed = write_if_changed(target_path, code)
            if changed:
                updated_files.append(target_path)
                saved += 1
            seen_paths.add(target_path)

    if newest_timestamp:
        state["last_timestamp"] = newest_timestamp
        state["last_ids"] = newest_ids
        save_json(state_path, state)
    if meta_changed or meta_path.exists():
        save_json(meta_path, meta)

    categories, language_counts, unique_count, total_solutions = collect_solution_entries(
        repo_root, solutions_dir, meta
    )
    generated = render_readme_section(categories, language_counts, unique_count, total_solutions)
    update_readme(repo_root, config, generated)

    print(
        f"Scanned: {processed}, accepted: {accepted}, saved: {saved}, "
        f"skipped (no comment): {skipped_comment}, skipped (missing): {skipped_missing}"
    )

    auto_commit = args.commit or os.environ.get("AUTO_COMMIT") == "1" or os.environ.get("GITHUB_ACTIONS") == "true"
    auto_push = args.push or os.environ.get("AUTO_PUSH") == "1"
    if auto_commit:
        paths = [
            str(Path(config.get("solutions_dir", "solutions")).as_posix()),
            str(Path(config.get("readme_path", "README.md")).as_posix()),
            str(Path("tools/leetcode_cn_state.json").as_posix()),
            str(Path("tools/leetcode_cn_meta.json").as_posix()),
        ]
        if git_has_changes(repo_root, paths):
            git_commit(repo_root, paths, "chore: sync leetcode.cn submissions", auto_push)
        else:
            print("No changes to commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
