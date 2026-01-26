# tools/sync_leetcode_cn.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import requests

API = "https://leetcode.cn/graphql/"
UA = "leetcode-practice-bot/1.0"

OUT_DIR = Path("leetcode_sync")
STATE_PATH = Path("data/leetcode_cn_sync_state.json")

# 语言到扩展名（可按需补充）
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

def slugify_filename(s: str) -> str:
    s = re.sub(r"[\\/:*?\"<>|]", "_", s)
    s = re.sub(r"\s+", " ", s).strip()
    # 避免太长
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
    payload = {"query": query, "variables": variables}
    if operation_name:
        payload["operationName"] = operation_name

    r = session.post(
        API,
        headers={
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://leetcode.cn",
            "Referer": "https://leetcode.cn/",
            "X-Requested-With": "XMLHttpRequest",
        },
        json=payload,
        timeout=30,
    )

    # leetcode.cn 有时会用 HTTP 400 携带 GraphQL errors
    try:
        data = r.json()
    except Exception:
        print("HTTP", r.status_code)
        print(r.text[:1000])
        r.raise_for_status()
        raise RuntimeError("Unreachable")

    if "errors" in data:
        # 打印一点点帮助定位，但不泄露敏感信息
        raise RuntimeError(f"GraphQL errors: {data['errors']}")

    if r.status_code != 200:
        print("HTTP", r.status_code)
        print(r.text[:1000])
        r.raise_for_status()

    if "data" not in data:
        raise RuntimeError(f"Bad response: {data}")

    return data["data"]

# ✅ 已适配：submissionList 里没有 titleSlug
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
query submissionDetail($submissionId: Int!) {
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
    s.headers.update({
        "User-Agent": UA,
        "Referer": "https://leetcode.cn/",
        "x-csrftoken": csrf,
    })
    # 用 cookie 机制更标准
    s.cookies.set("csrftoken", csrf, domain="leetcode.cn")
    s.cookies.set("LEETCODE_SESSION", sess, domain="leetcode.cn")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    new_last_ts = last_ts
    wrote = 0

    # 最多扫 5 页 * 20 条 = 100 条（够用了；想更多可加大）
    for page in range(5):
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

            # 增量：只处理比上次更新更新的
            if ts <= last_ts:
                continue

            if sub.get("statusDisplay") != "Accepted":
                continue

            sid = int(sub["id"])
            title = (sub.get("title") or "").strip()
            lang = (sub.get("lang") or "").lower()

            # 拉代码详情
            detail = gql(
                s,
                Q_SUBMISSION_DETAIL,
                {"submissionId": sid},
                operation_name="submissionDetail",
            )
            info = detail.get("submissionDetail") or {}
            code = info.get("code") or ""
            lang2 = (info.get("lang") or lang).lower()

            ext = LANG2EXT.get(lang2, LANG2EXT.get(lang, "txt"))
            fname = slugify_filename(title) if title else f"submission_{sid}"

            out = OUT_DIR / f"{ts}_{fname}.{ext}"
            if out.exists():
                # 已经写过就跳过
                new_last_ts = max(new_last_ts, ts)
                continue

            out.write_text(code, encoding="utf-8", newline="\n")
            wrote += 1
            new_last_ts = max(new_last_ts, ts)

        # 轻微 sleep，避免太快
        time.sleep(0.2)

    if wrote > 0:
        state["last_timestamp"] = new_last_ts
        save_state(state)

    print(f"✅ wrote {wrote} file(s). last_timestamp={new_last_ts}")

if __name__ == "__main__":
    main()
