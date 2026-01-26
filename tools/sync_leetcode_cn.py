# tools/sync_leetcode_cn.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import os
import random
import re
import time
from pathlib import Path
from typing import Any, Optional, Tuple

import requests

API = "https://leetcode.cn/graphql/"
UA = "leetcode-practice-bot/2.0"

# ====== 运行控制（防风控）======
MAX_DETAIL_PER_RUN = 8
SLEEP_BETWEEN_DETAIL = 1.2
MAX_PAGES = 5  # submissionList 扫多少页（每页20）

# ====== 状态文件（要提交到仓库）======
STATE_PATH = Path("data/leetcode_cn_sync_state.json")

# 语言到扩展名
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
CPP_ALIASES = {"cpp", "c++"}

# 首个非空行注释： // ...  或  # ...
FIRST_LINE_COMMENT_RE = re.compile(r"^\s*(//|#)\s*(?P<text>.+?)\s*$")


class RateLimitError(RuntimeError):
    pass


def slugify_filename(s: str) -> str:
    # Windows / Linux 安全
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
                sleep_s = (2**attempt) + random.random()
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

Q_SUBMISSION_DETAIL = r"""
query submissionDetail($submissionId: ID!) {
  submissionDetail(submissionId: $submissionId) {
    code
    lang
  }
}
"""


def code_ext_from_lang(lang: str) -> str:
    lang = (lang or "").lower().strip()
    return LANG2EXT.get(lang, "txt")


def extract_path_from_code(code: str) -> Optional[str]:
    """
    取“首个非空行”，要求是 //... 或 #...
    返回注释正文（不含 ///#），否则 None
    """
    if not code:
        return None
    for line in code.splitlines():
        if line.strip() == "":
            continue
        m = FIRST_LINE_COMMENT_RE.match(line)
        if not m:
            return None
        return (m.group("text") or "").strip()
    return None


def split_path(comment_text: str) -> list[str]:
    # 用 '-' 分隔层级；过滤空片段
    parts = [p.strip() for p in (comment_text or "").split("-")]
    return [p for p in parts if p]


def ensure_extension(filename: str, lang: str) -> str:
    # 如果用户已经写了扩展名，就尊重；否则按语言补
    if "." in Path(filename).name:
        return filename
    ext = code_ext_from_lang(lang)
    if (lang or "").lower().strip() in CPP_ALIASES:
        ext = "cpp"
    return f"{filename}.{ext}"


def bootstrap_watermark(session: requests.Session) -> int:
    """
    用“当前最新一页提交的最大 timestamp”作为水位线，避免首次把历史无注释提交全扫进来。
    """
    data = gql(session, Q_SUBMISSION_LIST, {"offset": 0, "limit": 20}, operation_name="submissionList")
    sublist = (data.get("submissionList") or {}).get("submissions") or []
    mx = 0
    for sub in sublist:
        try:
            mx = max(mx, int(sub.get("timestamp", 0)))
        except Exception:
            pass
    return mx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true", help="Initialize watermark to latest submissions and exit.")
    ap.add_argument("--verbose", action="store_true", help="Print more logs.")
    args = ap.parse_args()

    csrf = os.environ.get("LEETCODE_CN_CSRF_TOKEN", "").strip()
    sess = os.environ.get("LEETCODE_CN_SESSION", "").strip()
    if not csrf or not sess:
        raise SystemExit("Missing env: LEETCODE_CN_CSRF_TOKEN / LEETCODE_CN_SESSION")

    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Referer": "https://leetcode.cn/", "x-csrftoken": csrf})
    s.cookies.set("csrftoken", csrf, domain="leetcode.cn")
    s.cookies.set("LEETCODE_SESSION", sess, domain="leetcode.cn")

    state = load_state()

    # 首次没有 state：默认 bootstrap（避免历史无注释污染）
    if not STATE_PATH.exists():
        wm = bootstrap_watermark(s)
        save_state({"last_timestamp": wm})
        print(f"🧱 init state (auto bootstrap). last_timestamp={wm}")
        return

    if args.init:
        wm = bootstrap_watermark(s)
        save_state({"last_timestamp": wm})
        print(f"🧱 init state. last_timestamp={wm}")
        return

    last_ts = int(state.get("last_timestamp", 0))
    new_last_ts = last_ts

    wrote = 0
    skipped_no_comment = 0
    skipped_bad_path = 0
    pulled_details = 0

    for page in range(MAX_PAGES):
        data = gql(s, Q_SUBMISSION_LIST, {"offset": page * 20, "limit": 20}, operation_name="submissionList")
        sublist = (data.get("submissionList") or {}).get("submissions") or []
        if not sublist:
            break

        for sub in sublist:
            try:
                ts = int(sub.get("timestamp", 0))
            except Exception:
                continue

            if ts <= last_ts:
                continue  # 老的

            new_last_ts = max(new_last_ts, ts)

            if sub.get("statusDisplay") != "Accepted":
                continue

            if pulled_details >= MAX_DETAIL_PER_RUN:
                state["last_timestamp"] = new_last_ts
                save_state(state)
                print(f"ℹ️ Reach MAX_DETAIL_PER_RUN={MAX_DETAIL_PER_RUN}, stop early. wrote={wrote}, skip_no_comment={skipped_no_comment}, last_timestamp={new_last_ts}")
                return

            sid = str(sub.get("id"))
            lang_list = (sub.get("lang") or "").lower().strip()

            time.sleep(SLEEP_BETWEEN_DETAIL + random.random() * 0.6)

            try:
                detail = gql(s, Q_SUBMISSION_DETAIL, {"submissionId": sid}, operation_name="submissionDetail")
            except RateLimitError:
                state["last_timestamp"] = new_last_ts
                save_state(state)
                print("⚠️ Hit rate limit. Saved state and exit gracefully.")
                print(f"✅ wrote={wrote}, skip_no_comment={skipped_no_comment}, last_timestamp={new_last_ts}")
                return

            pulled_details += 1
            info = detail.get("submissionDetail") or {}
            code = info.get("code") or ""
            lang_detail = (info.get("lang") or lang_list).lower().strip()

            comment_text = extract_path_from_code(code)
            if not comment_text:
                skipped_no_comment += 1
                if args.verbose:
                    title = (sub.get("title") or "").strip()
                    print(f"⏭️ skip(no path comment): {title} sid={sid} ts={ts}")
                continue

            parts = split_path(comment_text)
            if len(parts) < 2:
                skipped_bad_path += 1
                if args.verbose:
                    print(f"⏭️ skip(bad path): '{comment_text}' sid={sid}")
                continue

            dir_parts = [slugify_filename(p) for p in parts[:-1]]
            file_part = slugify_filename(parts[-1])
            file_part = ensure_extension(file_part, lang_detail)

            out_dir = Path(*dir_parts)
            out_dir.mkdir(parents=True, exist_ok=True)

            out_path = out_dir / file_part
            if out_path.exists():
                continue

            out_path.write_text(code, encoding="utf-8", newline="\n")
            wrote += 1
            if args.verbose:
                print(f"✅ wrote: {out_path.as_posix()}")

        time.sleep(0.2 + random.random() * 0.2)

    if new_last_ts != last_ts:
        state["last_timestamp"] = new_last_ts
        save_state(state)

    print(f"✅ wrote={wrote}, skip_no_comment={skipped_no_comment}, skip_bad_path={skipped_bad_path}, last_timestamp={state.get('last_timestamp', last_ts)}")


if __name__ == "__main__":
    main()
