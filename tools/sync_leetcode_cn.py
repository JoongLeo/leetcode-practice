# tools/sync_leetcode_cn.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from pathlib import Path
import time
import re

import requests

API = "https://leetcode.cn/graphql/"
UA = "leetcode-practice-bot/1.0"

OUT_DIR = Path("leetcode_sync")
STATE_PATH = Path("data/leetcode_cn_sync_state.json")

# 一些语言到扩展名的映射（不全，但够用；后续你可补）
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
}

def slugify_filename(s: str) -> str:
    s = re.sub(r"[\\/:*?\"<>|]", "_", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

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
            # 这些是很多站点/WAF更“认可”的组合
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

    # 关键：把 400 的响应正文打印出来（不包含你的 cookie）
    if r.status_code != 200:
        print("HTTP", r.status_code)
        print(r.text[:1000])  # 只打印前 1000 字，够定位了
        r.raise_for_status()

    data = r.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data["data"]


# 1) 拉最近的提交列表（这里用 submissionList；如果你后面发现 schema 不一致，
#    用第 6 步的“抓包法”替换 query 即可）
Q_SUBMISSION_LIST = r"""
query submissionList($offset: Int!, $limit: Int!) {
  submissionList(offset: $offset, limit: $limit) {
    submissions {
      id
      title
      titleSlug
      statusDisplay
      lang
      timestamp
    }
  }
}
"""

# 2) 拉某个 submission 的代码详情（常见字段：code / runtime / memory 等）
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
    # 认证：带 cookie + x-csrftoken（很多站点都需要）
    s.headers.update({
        "User-Agent": UA,
        "Referer": "https://leetcode.cn/",
        "Cookie": f"csrftoken={csrf}; LEETCODE_SESSION={sess}",
        "x-csrftoken": csrf,
    })

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    new_last_ts = last_ts
    wrote = 0

    # 拉 0..N 页（先写保守点：最多扫 5 页 * 20 = 100 条）
    for page in range(5):
        data = gql(s, Q_SUBMISSION_LIST, {"offset": page * 20, "limit": 20}, operation_name="submissionList")
        sublist = (data.get("submissionList") or {}).get("submissions") or []
        if not sublist:
            break

        for sub in sublist:
            try:
                ts = int(sub.get("timestamp", 0))
            except Exception:
                continue

            # 只处理比上次更新更新的
            if ts <= last_ts:
                continue

            if sub.get("statusDisplay") != "Accepted":
                continue

            sid = int(sub["id"])
            title = sub.get("title") or ""
            slug = sub.get("titleSlug") or ""
            lang = (sub.get("lang") or "").lower()

            # 拉代码
            detail = gql(s, Q_SUBMISSION_DETAIL, {"submissionId": sid}, operation_name="submissionDetail")
            info = detail.get("submissionDetail") or {}
            code = info.get("code") or ""
            lang2 = (info.get("lang") or lang).lower()

            ext = LANG2EXT.get(lang2, "txt")
            fname = slugify_filename(f"{title}".strip())
            if not fname:
                fname = slug or f"submission_{sid}"

            # 文件名：时间戳_标题.扩展（避免同题多语言/多次提交覆盖）
            out = OUT_DIR / f"{ts}_{fname}.{ext}"
            if out.exists():
                continue
            out.write_text(code, encoding="utf-8", newline="\n")
            wrote += 1
            new_last_ts = max(new_last_ts, ts)

        time.sleep(0.2)

    if wrote > 0:
        state["last_timestamp"] = new_last_ts
        save_state(state)

    print(f"✅ wrote {wrote} file(s). last_timestamp={new_last_ts}")

if __name__ == "__main__":
    main()
