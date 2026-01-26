#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import time
import argparse
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import requests

GRAPHQL_URL = "https://leetcode.cn/graphql"


LANG_EXT = {
    "cpp": ".cpp",
    "c": ".c",
    "java": ".java",
    "python": ".py",
    "python3": ".py",
    "javascript": ".js",
    "typescript": ".ts",
    "golang": ".go",
    "rust": ".rs",
    "kotlin": ".kt",
    "swift": ".swift",
    "csharp": ".cs",
    "php": ".php",
    "ruby": ".rb",
    "scala": ".scala",
    "mysql": ".sql",
    "bash": ".sh",
}

# --------- utils ---------

def die(msg: str) -> None:
    raise SystemExit(msg)

def safe_path_name(s: str) -> str:
    s = s.strip()
    s = s.replace("/", "／").replace("\\", "＼")
    s = re.sub(r"[\x00-\x1f<>:\"|?*]", "_", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or "未分类"

def safe_filename(s: str) -> str:
    s = safe_path_name(s)
    s = s.rstrip(". ")
    return s or "untitled"

def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def load_json(path: Path, default):
    """
    Robust JSON loader:
    - if file missing -> default
    - if empty/whitespace -> default
    - if invalid JSON -> backup and default
    """
    if not path.exists():
        return default
    try:
        txt = path.read_text(encoding="utf-8").strip()
        if not txt:
            return default
        return json.loads(txt)
    except Exception:
        # backup the broken file for debugging, then recover
        try:
            bak = path.with_suffix(path.suffix + ".bak")
            bak.write_text(path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
        except Exception:
            pass
        return default


def save_json(path: Path, obj) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

# --------- LeetCode client ---------

class LeetCodeCN:
    def __init__(self, session_cookie: str, csrf_token: Optional[str] = None, timeout: int = 30):
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/json",
            "Referer": "https://leetcode.cn/",
            "Origin": "https://leetcode.cn",
        })
        cookies = {"LEETCODE_SESSION": session_cookie}
        if csrf_token:
            cookies["csrftoken"] = csrf_token
            self.s.headers["x-csrftoken"] = csrf_token
        self.s.cookies.update(cookies)
        self.timeout = timeout

    def graphql(self, query: str, variables: Dict[str, Any], operation_name: str) -> Dict[str, Any]:
        payload = {"query": query, "variables": variables, "operationName": operation_name}
        r = self.s.post(GRAPHQL_URL, data=json.dumps(payload), timeout=self.timeout)
        if r.status_code == 429:
            time.sleep(3)
            r = self.s.post(GRAPHQL_URL, data=json.dumps(payload), timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
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
        # 这个 query 形式与社区脚本/工具对 leetcode.cn 的 submissionList 用法一致
        #（status / lastKey / offset / limit 等参数在工具文档中广泛使用） :contentReference[oaicite:2]{index=2}
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
        vars_ = {"offset": offset, "limit": limit, "lastKey": last_key, "status": status}
        return self.graphql(query, vars_, "submissionList")["submissionList"]

    def get_submission_detail(self, submission_id: int) -> Dict[str, Any]:
        # submissionDetail(submissionId) 返回 code/runtime/memory 等字段是常见用法 :contentReference[oaicite:3]{index=3}
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
        # problem fields（含 translatedTitle / questionFrontendId）在常用类型定义中存在 :contentReference[oaicite:4]{index=4}
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

# --------- category parsing ---------

COMMENT_PREFIXES = ("//", "#", "--", ";")  # 覆盖常见语言

def parse_two_level_categories(code: str) -> Tuple[str, str]:
    """
    从代码顶部提取两行“单行注释”文本作为 (lv1, lv2)。
    规则：
      - 从前 30 行内扫描
      - 忽略空行
      - 只接受单行注释开头（// 或 # 等）
      - 取到 2 条为止
    """
    lv = []
    for line in code.splitlines()[:30]:
        t = line.strip()
        if not t:
            continue
        if t.startswith(COMMENT_PREFIXES):
            # 去掉前缀与多余空格
            t = re.sub(r"^(//+|#+|--+|;+)\s*", "", t).strip()
            if t:
                lv.append(t)
                if len(lv) == 2:
                    break
        else:
            # 遇到非注释且非空行，认为用户没写头部注释，停止
            break

    lv1 = safe_path_name(lv[0]) if len(lv) >= 1 else "未分类"
    lv2 = safe_path_name(lv[1]) if len(lv) >= 2 else "未细分"
    return lv1, lv2

# --------- main sync ---------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=".", help="repo root")
    ap.add_argument("--limit", type=int, default=20, help="page size")
    ap.add_argument("--max-pages", type=int, default=20, help="max pages to scan per run")
    ap.add_argument("--sleep", type=float, default=0.2, help="sleep between requests")
    args = ap.parse_args()

    repo = Path(args.repo_root).resolve()
    sync_dir = repo / ".sync"
    state_path = sync_dir / "state.json"
    cache_path = sync_dir / "problem_cache.json"

    session_cookie = os.environ.get("LEETCODE_SESSION", "").strip()
    csrf = os.environ.get("LEETCODE_CSRF_TOKEN", "").strip() or None
    if not session_cookie:
        die("Missing env LEETCODE_SESSION (set it in GitHub Secrets).")

    lc = LeetCodeCN(session_cookie=session_cookie, csrf_token=csrf)

    state = load_json(state_path, default={"synced_submission_ids": []})
    synced = set(state.get("synced_submission_ids", []))

    problem_cache = load_json(cache_path, default={})  # titleSlug -> {id,title}
    changed_files = 0
    new_synced_ids: List[int] = []

    offset = 0
    last_key = None
    pages = 0

    while pages < args.max_pages:
        page = lc.get_all_submissions_page(limit=args.limit, offset=offset, last_key=last_key, status="AC")
        pages += 1
        last_key = page.get("lastKey")
        has_next = bool(page.get("hasNext"))

        subs = page.get("submissions") or []
        if not subs:
            break

        for sub in subs:
            sid = int(sub["id"])
            if sid in synced:
                continue

            # 1) code
            detail = lc.get_submission_detail(sid)
            code = detail.get("code") or ""
            if not code.strip():
                # 没拿到代码就跳过（可能 cookie 失效/接口变更）
                continue

            # 2) language slug -> ext
            lang_slug = (sub.get("lang") or "").lower()
            ext = LANG_EXT.get(lang_slug, ".txt")

            # 3) problem meta (cache)
            title_slug = sub.get("titleSlug") or ""
            meta = problem_cache.get(title_slug)
            if meta is None:
                q = lc.get_question_meta(title_slug)
                frontend_id = q.get("questionFrontendId") or "?"
                zh_title = q.get("translatedTitle") or q.get("title") or (sub.get("title") or "Untitled")
                meta = {"frontend_id": str(frontend_id), "title": zh_title}
                problem_cache[title_slug] = meta
                time.sleep(args.sleep)

            pid = meta["frontend_id"]
            title = meta["title"]

            # 4) categories from code header comments
            lv1, lv2 = parse_two_level_categories(code)

            out_dir = repo / safe_path_name(lv1) / safe_path_name(lv2)
            ensure_dir(out_dir)

            filename = f"{pid}. {title}{ext}"
            filename = safe_filename(filename)
            out_path = out_dir / filename

            # 5) write file (avoid no-op overwrite)
            old = out_path.read_text(encoding="utf-8", errors="ignore") if out_path.exists() else None
            if old != code:
                out_path.write_text(code, encoding="utf-8")
                changed_files += 1

            synced.add(sid)
            new_synced_ids.append(sid)

            time.sleep(args.sleep)

        if not has_next:
            break

        # 注意：CN 的 submissionList 常见分页同时支持 offset/lastKey（不同实现可能偏向 lastKey）
        offset += args.limit

    if new_synced_ids:
        state["synced_submission_ids"] = sorted(synced)
        save_json(state_path, state)
        save_json(cache_path, problem_cache)

    print(f"[sync] new submissions: {len(new_synced_ids)}, changed_files: {changed_files}")

    # 用于 CI 判断是否需要 commit：changed_files > 0 或 state 有更新
    # 这里简单：只要新同步到 submission，就返回 0，交给 git diff 判断
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
