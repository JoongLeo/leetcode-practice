#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

GRAPHQL_URL = "https://leetcode.cn/graphql"

LANG_EXT = {
    "cpp": ".cpp",
    "c++": ".cpp",
    "c": ".c",
    "java": ".java",
    "python": ".py",
    "python3": ".py",
    "javascript": ".js",
    "typescript": ".ts",
    "golang": ".go",
    "go": ".go",
    "rust": ".rs",
    "kotlin": ".kt",
    "swift": ".swift",
    "csharp": ".cs",
    "c#": ".cs",
    "php": ".php",
    "ruby": ".rb",
    "scala": ".scala",
    "mysql": ".sql",
    "mssql": ".sql",
    "bash": ".sh",
    "shell": ".sh",
}

COMMENT_PREFIXES = ("//", "#", "--", ";")


def die(msg: str) -> None:
    raise SystemExit(msg)


def safe_path_name(value: str) -> str:
    value = value.strip()
    value = value.replace("/", "_").replace("\\", "_")
    value = re.sub(r"[\x00-\x1f<>:\"|?*]", "_", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value or "Uncategorized"


def safe_filename(value: str) -> str:
    value = safe_path_name(value)
    value = value.rstrip(". ")
    return value or "untitled"


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return default
        return json.loads(text)
    except Exception:
        try:
            backup = path.with_suffix(path.suffix + ".bak")
            backup.write_text(path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
        except Exception:
            pass
        return default


def save_json(path: Path, obj) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


class LeetCodeCN:
    def __init__(self, session_cookie: str, csrf_token: Optional[str] = None, timeout: int = 30):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0",
                "Content-Type": "application/json",
                "Referer": "https://leetcode.cn/",
                "Origin": "https://leetcode.cn",
            }
        )
        cookies = {"LEETCODE_SESSION": session_cookie}
        if csrf_token:
            cookies["csrftoken"] = csrf_token
            self.session.headers["x-csrftoken"] = csrf_token
        self.session.cookies.update(cookies)
        self.timeout = timeout

    def graphql(self, query: str, variables: Dict[str, Any], operation_name: str) -> Dict[str, Any]:
        payload = {"query": query, "variables": variables, "operationName": operation_name}
        response = self.session.post(GRAPHQL_URL, data=json.dumps(payload), timeout=self.timeout)
        if response.status_code == 429:
            time.sleep(2)
            response = self.session.post(GRAPHQL_URL, data=json.dumps(payload), timeout=self.timeout)
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError(f"Invalid JSON response: {response.text[:200]}") from exc
        if "errors" in data:
            raise RuntimeError(f"GraphQL errors: {data['errors']}")
        return data["data"]

    def get_all_submissions_page(
        self,
        limit: int,
        offset: int,
        last_key: Optional[str] = None,
        status: Optional[str] = "AC",
    ) -> Dict[str, Any]:
        query = """
        query submissionList($offset: Int!, $limit: Int!, $lastKey: String, $status: String) {
          submissionList(offset: $offset, limit: $limit, lastKey: $lastKey, status: $status) {
            lastKey
            hasNext
            submissions {
              id
              statusDisplay
              lang
              timestamp
              title
              titleSlug
            }
          }
        }
        """
        variables = {"offset": offset, "limit": limit, "lastKey": last_key, "status": status}
        return self.graphql(query, variables, "submissionList")["submissionList"]

    def get_submission_detail(self, submission_id: int) -> Dict[str, Any]:
        query = """
        query submissionDetail($id: ID!) {
          submissionDetail(submissionId: $id) {
            id
            code
            runtime
            memory
          }
        }
        """
        return self.graphql(query, {"id": str(submission_id)}, "submissionDetail")["submissionDetail"]

    def get_question_meta(self, title_slug: str) -> Dict[str, Any]:
        query = """
        query questionData($titleSlug: String!) {
          question(titleSlug: $titleSlug) {
            questionFrontendId
            translatedTitle
            title
          }
        }
        """
        return self.graphql(query, {"titleSlug": title_slug}, "questionData")["question"]


def parse_two_level_categories(code: str) -> Tuple[str, str]:
    categories: List[str] = []
    for line in code.splitlines()[:30]:
        trimmed = line.strip()
        if not trimmed:
            continue
        if trimmed.startswith(COMMENT_PREFIXES):
            trimmed = re.sub(r"^(//+|#+|--+|;+)\s*", "", trimmed).strip()
            if trimmed:
                categories.append(trimmed)
                if len(categories) == 2:
                    break
        else:
            break

    level1 = safe_path_name(categories[0]) if len(categories) >= 1 else "Uncategorized"
    level2 = safe_path_name(categories[1]) if len(categories) >= 2 else "Misc"
    return level1, level2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".", help="Repo root path")
    parser.add_argument("--limit", type=int, default=20, help="Page size")
    parser.add_argument("--max-pages", type=int, default=20, help="Max pages per run")
    parser.add_argument("--sleep", type=float, default=0.2, help="Sleep between requests (seconds)")
    args = parser.parse_args()

    repo = Path(args.repo_root).resolve()
    sync_dir = repo / ".sync"
    state_path = sync_dir / "state.json"
    cache_path = sync_dir / "problem_cache.json"

    session_cookie = os.environ.get("LEETCODE_SESSION", "").strip()
    csrf_token = os.environ.get("LEETCODE_CSRF_TOKEN", "").strip() or None
    if not session_cookie:
        die("Missing LEETCODE_SESSION environment variable.")

    client = LeetCodeCN(session_cookie=session_cookie, csrf_token=csrf_token)

    state = load_json(state_path, default={"synced_submission_ids": []})
    synced_ids = set(state.get("synced_submission_ids", []))

    problem_cache = load_json(cache_path, default={})

    seen_output_keys = set()
    new_synced_ids: List[int] = []
    changed_files = 0

    offset = 0
    last_key = None
    pages = 0

    while pages < args.max_pages:
        page = client.get_all_submissions_page(limit=args.limit, offset=offset, last_key=last_key, status="AC")
        pages += 1
        last_key = page.get("lastKey")
        has_next = bool(page.get("hasNext"))

        submissions = page.get("submissions") or []
        if not submissions:
            break

        for sub in submissions:
            try:
                submission_id = int(sub["id"])
            except Exception:
                continue

            if submission_id in synced_ids:
                continue

            detail = client.get_submission_detail(submission_id)
            code = detail.get("code") or ""
            if not code.strip():
                continue

            lang_slug = (sub.get("lang") or "").lower().strip().replace(" ", "")
            ext = LANG_EXT.get(lang_slug, ".txt")

            title_slug = sub.get("titleSlug") or ""
            if not title_slug:
                continue

            meta = problem_cache.get(title_slug)
            if meta is None:
                question = client.get_question_meta(title_slug)
                frontend_id = question.get("questionFrontendId") or "?"
                title = question.get("translatedTitle") or question.get("title") or (sub.get("title") or "Untitled")
                meta = {"frontend_id": str(frontend_id), "title": title}
                problem_cache[title_slug] = meta
                time.sleep(args.sleep)

            problem_id = meta["frontend_id"]
            problem_title = meta["title"]

            output_key = f"{problem_id}:{ext}"
            if output_key in seen_output_keys:
                synced_ids.add(submission_id)
                new_synced_ids.append(submission_id)
                continue
            seen_output_keys.add(output_key)

            level1, level2 = parse_two_level_categories(code)
            out_dir = repo / level1 / level2
            ensure_dir(out_dir)

            filename = safe_filename(f"{problem_id}. {problem_title}{ext}")
            out_path = out_dir / filename

            old = out_path.read_text(encoding="utf-8", errors="ignore") if out_path.exists() else None
            if old != code:
                out_path.write_text(code, encoding="utf-8")
                changed_files += 1

            synced_ids.add(submission_id)
            new_synced_ids.append(submission_id)

            time.sleep(args.sleep)

        if not has_next:
            break
        offset += args.limit

    if new_synced_ids:
        state["synced_submission_ids"] = sorted(synced_ids)
        save_json(state_path, state)
        save_json(cache_path, problem_cache)

    print(f"[sync] new submissions: {len(new_synced_ids)}, changed_files: {changed_files}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
