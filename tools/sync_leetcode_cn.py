# tools/sync_leetcode_cn.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import random
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests

API = "https://leetcode.cn/graphql/"
UA = "leetcode-practice-bot/1.0"

# ====== 运行控制：避免触发风控 ======
MAX_DETAIL_PER_RUN = 8               # 每次 workflow 最多拉多少份 submissionDetail
SLEEP_BETWEEN_DETAIL = 1.2           # 每次 detail 请求间隔（+随机抖动）
MAX_PAGES = 5                        # submissionList 扫多少页（每页20）

# ====== 输出与缓存 ======
INBOX_DIR = Path("leetcode_sync")    # 找不到归档位置就丢进这里
STATE_PATH = Path("data/leetcode_cn_sync_state.json")
Q_CACHE_PATH = Path("data/leetcode_cn_question_cache.json")   # title -> {id, slug, title}
PLAN_PATH = Path("data/endless_plan.json")                    # 你的题单（用于归档）

# 语言到扩展名（可按需补）
LANG2EXT = {
    "cpp": "cpp",
    "c++": "cpp",
    "python": "py",
    "python3": "py",
    "java": "java",
    "javascript": "js",
    "typescript": "ts",
    "go": "go",
    "rust": "rs",
    "c": "c",
    "csharp": "cs",
    "kotlin": "kt",
    "swift": "swift",
    "ruby": "rb",
    "php": "php",
}

# 你希望：题号. 题名.cpp —— 所以只要是 C++ 就强制 cpp
CPP_ALIASES = {"cpp", "c++"}


class RateLimitError(RuntimeError):
    pass


def slugify_filename(s: str) -> str:
    # Windows/Unix 都安全
    s = re.sub(r"[\\/:*?\"<>|]", "_", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:140] if len(s) > 140 else s


def read_json(p: Path, default: Any) -> Any:
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(p: Path, obj: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def load_state() -> dict:
    return read_json(STATE_PATH, {"last_timestamp": 0})


def save_state(state: dict) -> None:
    write_json(STATE_PATH, state)


def gql(session: requests.Session, query: str, variables: dict, operation_name: str | None = None) -> dict:
    """
    leetcode.cn GraphQL:
    - 有时 GraphQL errors 会用 HTTP 400 返回
    - 限流时 message 常包含：🐸☕超出访问限制，请稍后再试
    这里做：重试 + 指数退避 + 抖动
    """
    payload = {"query": query, "variables": variables}
    if operation_name:
        payload["operationName"] = operation_name

    headers = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://leetcode.cn",
        "Referer": "https://leetcode.cn/",
        "X-Requested-With": "XMLHttpRequest",
    }

    for attempt in range(5):
        r = session.post(API, headers=headers, json=payload, timeout=30)

        try:
            data = r.json()
        except Exception:
            print("HTTP", r.status_code)
            print(r.text[:1000])
            r.raise_for_status()
            raise RuntimeError("Bad non-json response")

        if "errors" in data:
            msg = str(data["errors"])
            if "超出访问限制" in msg:
                sleep_s = (2 ** attempt) + random.random()
                print(f"⚠️ Rate limited. backoff {sleep_s:.2f}s (attempt {attempt+1}/5)")
                time.sleep(sleep_s)
                continue
            raise RuntimeError(f"GraphQL errors: {data['errors']}")

        if r.status_code != 200:
            print("HTTP", r.status_code)
            print(r.text[:1000])
            r.raise_for_status()

        if "data" not in data:
            raise RuntimeError(f"Bad response: {data}")

        return data["data"]

    raise RateLimitError("Rate limit persists after retries")


# ====== 1) 拉提交列表（CN：没有 titleSlug） ======
Q_SUBMISSION_LIST = r"""
query submissionList($offset: Int!, $limit: Int!) {
  submissionList(offset: $offset, limit: $limit) {
    submissions {
      id
      title
      statusDisplay
      lang
      timestamp
    }
  }
}
"""

# ====== 2) 拉某个提交的代码（CN：submissionId 是 ID!） ======
Q_SUBMISSION_DETAIL = r"""
query submissionDetail($submissionId: ID!) {
  submissionDetail(submissionId: $submissionId) {
    code
    lang
  }
}
"""

# ====== 3) 通过题名查询题目元信息（题号/slug）并缓存 ======
# 这个 query 在 leetcode 系列里非常常见，CN 通常也支持
Q_PROBLEMSET_SEARCH = r"""
query problemsetQuestionList($categorySlug: String, $limit: Int, $skip: Int, $filters: QuestionListFilterInput) {
  problemsetQuestionList(categorySlug: $categorySlug, limit: $limit, skip: $skip, filters: $filters) {
    total
    questions {
      title
      titleSlug
      frontendQuestionId
      paidOnly
    }
  }
}
"""


def load_plan_index() -> Dict[int, Dict[str, str]]:
    """
    读取 data/endless_plan.json（你的 update_readme.py 会维护它）。
    建一个索引：题号 -> {module, point}
    """
    plan = read_json(PLAN_PATH, None)
    idx: Dict[int, Dict[str, str]] = {}
    if not plan:
        return idx
    for mod in plan.get("modules", []):
        mod_name = mod.get("name", "") or mod.get("module", "")
        for p in mod.get("problems", []):
            try:
                pid = int(p.get("id"))
            except Exception:
                continue
            idx[pid] = {
                "module": str(p.get("module") or mod_name or "").strip(),
                "point": str(p.get("point") or "").strip(),
            }
    return idx


def load_question_cache() -> dict:
    return read_json(Q_CACHE_PATH, {"by_title": {}})


def save_question_cache(cache: dict) -> None:
    write_json(Q_CACHE_PATH, cache)


def find_question_meta_by_title(session: requests.Session, cache: dict, title: str) -> Optional[Tuple[int, str]]:
    """
    返回 (frontendQuestionId, titleSlug)；查不到返回 None
    - 先查 cache
    - 再用 problemsetQuestionList 搜索 title
    """
    title = (title or "").strip()
    if not title:
        return None

    by_title = cache.setdefault("by_title", {})
    if title in by_title:
        it = by_title[title]
        try:
            return int(it["id"]), str(it.get("slug", "") or "")
        except Exception:
            pass

    # 搜索：用题名做 searchKeywords
    data = gql(
        session,
        Q_PROBLEMSET_SEARCH,
        {
            "categorySlug": "",
            "skip": 0,
            "limit": 50,
            "filters": {"searchKeywords": title},
        },
        operation_name="problemsetQuestionList",
    )
    qs = (((data.get("problemsetQuestionList") or {}).get("questions")) or [])
    if not qs:
        return None

    # 优先精确 title 匹配
    pick = None
    for q in qs:
        if str(q.get("title", "")).strip() == title:
            pick = q
            break
    if pick is None:
        # 兜底：取第一个
        pick = qs[0]

    fid = pick.get("frontendQuestionId")
    slug = pick.get("titleSlug") or ""
    try:
        pid = int(fid)
    except Exception:
        return None

    by_title[title] = {"id": pid, "slug": slug, "title": title}
    save_question_cache(cache)
    return pid, str(slug)


def choose_target_dir(pid: int, title: str, plan_idx: Dict[int, Dict[str, str]]) -> Path:
    """
    按你的目录规则归档：
    - 顶层：module（如“滑动窗口与双指针”）
    - 二级：point（如“定长滑动窗口”）
    如果 plan 里找不到，就丢进 leetcode_sync/
    """
    info = plan_idx.get(pid)
    if not info:
        return INBOX_DIR

    module = slugify_filename(info.get("module", "") or "")
    point = slugify_filename(info.get("point", "") or "")

    if not module:
        return INBOX_DIR

    if not point:
        # 没有小点就放到模块根目录
        return Path(module)

    return Path(module) / point


def code_ext_from_lang(lang: str) -> str:
    lang = (lang or "").lower().strip()
    return LANG2EXT.get(lang, "txt")


def main():
    csrf = os.environ.get("LEETCODE_CN_CSRF_TOKEN", "").strip()
    sess = os.environ.get("LEETCODE_CN_SESSION", "").strip()
    if not csrf or not sess:
        raise SystemExit("Missing env: LEETCODE_CN_CSRF_TOKEN / LEETCODE_CN_SESSION")

    state = load_state()
    last_ts = int(state.get("last_timestamp", 0))

    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": UA,
            "Referer": "https://leetcode.cn/",
            "x-csrftoken": csrf,
        }
    )
    s.cookies.set("csrftoken", csrf, domain="leetcode.cn")
    s.cookies.set("LEETCODE_SESSION", sess, domain="leetcode.cn")

    INBOX_DIR.mkdir(parents=True, exist_ok=True)

    # 归档依据：你的题单
    plan_idx = load_plan_index()
    # 题名 -> 题号/slug 缓存
    qcache = load_question_cache()

    new_last_ts = last_ts
    wrote_files = 0
    pulled_details = 0

    for page in range(MAX_PAGES):
        data = gql(
            s,
            Q_SUBMISSION_LIST,
            {"offset": page * 20, "limit": 20},
            operation_name="submissionList",
        )
        sublist = (data.get("submissionList") or {}).get("submissions") or []
        if not sublist:
            break

        for sub in sublist:
            try:
                ts = int(sub.get("timestamp", 0))
            except Exception:
                continue

            # 增量：只处理比上次同步新的
            if ts <= last_ts:
                continue

            new_last_ts = max(new_last_ts, ts)

            if sub.get("statusDisplay") != "Accepted":
                continue

            sid = int(sub["id"])
            title = (sub.get("title") or "").strip()
            lang_list = (sub.get("lang") or "").lower().strip()

            if pulled_details >= MAX_DETAIL_PER_RUN:
                print(f"ℹ️ Reach MAX_DETAIL_PER_RUN={MAX_DETAIL_PER_RUN}, stop early.")
                state["last_timestamp"] = max(new_last_ts, last_ts)
                save_state(state)
                print(f"✅ wrote {wrote_files} file(s). last_timestamp={state['last_timestamp']}")
                return

            time.sleep(SLEEP_BETWEEN_DETAIL + random.random() * 0.6)

            try:
                detail = gql(
                    s,
                    Q_SUBMISSION_DETAIL,
                    {"submissionId": str(sid)},  # ID!
                    operation_name="submissionDetail",
                )
            except RateLimitError:
                # 限流：保存进度，正常退出（workflow 不红）
                state["last_timestamp"] = max(new_last_ts, last_ts)
                save_state(state)
                print("⚠️ Hit rate limit. Saved state and exit gracefully.")
                print(f"✅ wrote {wrote_files} file(s). last_timestamp={state['last_timestamp']}")
                return

            pulled_details += 1
            info = detail.get("submissionDetail") or {}
            code = info.get("code") or ""
            lang_detail = (info.get("lang") or lang_list).lower().strip()

            # 查题号（并缓存）
            meta = find_question_meta_by_title(s, qcache, title)
            if meta is None:
                # 查不到题号就丢 inbox，文件名用 timestamp
                ext = code_ext_from_lang(lang_detail)
                out_dir = INBOX_DIR
                out_dir.mkdir(parents=True, exist_ok=True)
                out_path = out_dir / f"{ts}_{slugify_filename(title) or f'submission_{sid}'}.{ext}"
                if not out_path.exists():
                    out_path.write_text(code, encoding="utf-8", newline="\n")
                    wrote_files += 1
                continue

            pid, _slug = meta

            # 决定扩展名：C++ 强制 .cpp；其它语言正常
            ext = code_ext_from_lang(lang_detail)
            if lang_detail in CPP_ALIASES:
                ext = "cpp"

            # 归档目录（按 plan）
            target_dir = choose_target_dir(pid, title, plan_idx)
            target_dir.mkdir(parents=True, exist_ok=True)

            # 目标命名：题号. 题名.cpp
            fname = slugify_filename(f"{pid}. {title}") if title else f"{pid}. unknown"
            out_path = target_dir / f"{fname}.{ext}"

            # 已存在就不覆盖（避免多次提交重复）
            if out_path.exists():
                continue

            out_path.write_text(code, encoding="utf-8", newline="\n")
            wrote_files += 1

        time.sleep(0.3 + random.random() * 0.3)

    if new_last_ts != last_ts:
        state["last_timestamp"] = new_last_ts
        save_state(state)

    print(f"✅ wrote {wrote_files} file(s). last_timestamp={state.get('last_timestamp', last_ts)}")


if __name__ == "__main__":
    main()
