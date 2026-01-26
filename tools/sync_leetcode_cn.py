# tools/sync_leetcode_cn.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import random
import re
import time
from pathlib import Path

import requests

API = "https://leetcode.cn/graphql/"
UA = "leetcode-practice-bot/1.0"

OUT_DIR = Path("leetcode_sync")
STATE_PATH = Path("data/leetcode_cn_sync_state.json")

# 每次 workflow 最多拉多少份 submissionDetail（避免限流）
MAX_DETAIL_PER_RUN = 8
# 每次拉 detail 之间的间隔（秒），再叠加一点随机抖动
SLEEP_BETWEEN_DETAIL = 1.2
# 拉 submissionList 的页数上限（每页20）
MAX_PAGES = 5

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


class RateLimitError(RuntimeError):
    pass


def slugify_filename(s: str) -> str:
    s = re.sub(r"[\\/:*?\"<>|]", "_", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:120] if len(s) > 120 else s


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_timestamp": 0}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def gql(session: requests.Session, query: str, variables: dict, operation_name: str | None = None) -> dict:
    """
    leetcode.cn GraphQL:
    - 有时 GraphQL errors 会用 HTTP 400 返回
    - 限流时 message 会包含：🐸☕超出访问限制，请稍后再试
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
            # 不是 JSON 就输出部分文本，方便定位
            print("HTTP", r.status_code)
            print(r.text[:1000])
            r.raise_for_status()
            raise RuntimeError("Bad non-json response")

        # GraphQL errors（leetcode.cn 有时会 400 + errors）
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


# ✅ leetcode.cn 的 submissionList.submissions 里没有 titleSlug
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

# ✅ submissionId 在 leetcode.cn 这里是 ID!（不是 Int!）
Q_SUBMISSION_DETAIL = r"""
query submissionDetail($submissionId: ID!) {
  submissionDetail(submissionId: $submissionId) {
    code
    lang
  }
}
"""


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
    # 用 cookies 机制更标准
    s.cookies.set("csrftoken", csrf, domain="leetcode.cn")
    s.cookies.set("LEETCODE_SESSION", sess, domain="leetcode.cn")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    new_last_ts = last_ts
    wrote_files = 0
    pulled_details = 0

    # 拉最近若干页 submissions
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
            # timestamp 可能是字符串或数字
            try:
                ts = int(sub.get("timestamp", 0))
            except Exception:
                continue

            # 增量：只处理比上次同步更新的
            if ts <= last_ts:
                continue

            # 只同步 AC
            if sub.get("statusDisplay") != "Accepted":
                new_last_ts = max(new_last_ts, ts)
                continue

            sid = int(sub["id"])
            title = (sub.get("title") or "").strip()
            lang = (sub.get("lang") or "").lower()

            # 限制每次 run 拉 detail 的数量，避免触发风控
            if pulled_details >= MAX_DETAIL_PER_RUN:
                print(f"ℹ️ Reach MAX_DETAIL_PER_RUN={MAX_DETAIL_PER_RUN}, stop early.")
                state["last_timestamp"] = max(new_last_ts, last_ts)
                save_state(state)
                print(f"✅ wrote {wrote_files} file(s). last_timestamp={state['last_timestamp']}")
                return

            # 请求 detail 前限速
            time.sleep(SLEEP_BETWEEN_DETAIL + random.random() * 0.6)

            try:
                detail = gql(
                    s,
                    Q_SUBMISSION_DETAIL,
                    {"submissionId": str(sid)},  # 关键：ID! 用字符串
                    operation_name="submissionDetail",
                )
            except RateLimitError:
                # 触发限流：保存进度，正常退出（让 workflow 不红）
                state["last_timestamp"] = max(new_last_ts, last_ts)
                save_state(state)
                print("⚠️ Hit rate limit. Saved state and exit gracefully.")
                print(f"✅ wrote {wrote_files} file(s). last_timestamp={state['last_timestamp']}")
                return

            pulled_details += 1
            info = detail.get("submissionDetail") or {}
            code = info.get("code") or ""
            lang2 = (info.get("lang") or lang).lower()

            ext = LANG2EXT.get(lang2, LANG2EXT.get(lang, "txt"))
            fname = slugify_filename(title) if title else f"submission_{sid}"

            out = OUT_DIR / f"{ts}_{fname}.{ext}"
            if out.exists():
                new_last_ts = max(new_last_ts, ts)
                continue

            out.write_text(code, encoding="utf-8", newline="\n")
            wrote_files += 1
            new_last_ts = max(new_last_ts, ts)

        # 页与页之间稍微歇一下
        time.sleep(0.3 + random.random() * 0.3)

    # 本次跑完：如果有新文件或进度推进，就写状态
    if new_last_ts != last_ts:
        state["last_timestamp"] = new_last_ts
        save_state(state)

    print(f"✅ wrote {wrote_files} file(s). last_timestamp={state.get('last_timestamp', last_ts)}")


if __name__ == "__main__":
    main()
