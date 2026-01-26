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
MAX_DETAIL_PER_RUN = 8
SLEEP_BETWEEN_DETAIL = 1.2
MAX_PAGES = 6  # submissionList 扫多少页（每页 20）

# ====== 输出与状态 ======
STATE_PATH = Path("data/leetcode_cn_sync_state.json")

# ====== 语言到扩展名 ======
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


class RateLimitError(RuntimeError):
    pass


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
    # last_timestamp: 上次同步到的提交时间戳（秒）
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


# ====== 1) 提交列表 ======
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

# ====== 2) 提交详情（代码） ======
Q_SUBMISSION_DETAIL = r"""
query submissionDetail($submissionId: ID!) {
  submissionDetail(submissionId: $submissionId) {
    code
    lang
  }
}
"""

# ====== 你的首行注释规范 ======
# 支持：
#   // 一级-二级-2841. xxx.cpp
#   #  一级-二级-2841. xxx.py
# 允许二级再细分（比如 三级），只要用 "-" 分隔即可：一级-二级-三级-题号.题名.ext
HEADER_RE = re.compile(
    r"^\s*(?P<comment>//|#)\s*(?P<body>.+?)\s*$"
)
# body 里最后一段必须含题号： 2841. xxx
PROB_RE = re.compile(r"(?P<pid>\d+)\.\s*(?P<title>.+)$")


def normalize_seg(s: str) -> str:
    # 清理路径非法字符 + 去空白
    s = s.strip()
    s = re.sub(r"[\\/:*?\"<>|]", "_", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def code_ext_from_lang(lang: str) -> str:
    lang = (lang or "").lower().strip()
    return LANG2EXT.get(lang, "txt")


def parse_header_classification(code: str) -> Optional[Tuple[list[str], int, str, str]]:
    """
    从代码首行注释解析分类与题目信息。
    返回: (levels, pid, title, ext_hint)
      - levels: 目录层级列表（>=2 推荐，但你想要 2 层即可）
      - pid/title: 题号/题名（用于文件名）
      - ext_hint: 注释里若写了 .cpp/.py 等，用它优先；否则用提交语言映射
    """
    if not code:
        return None
    first_line = code.splitlines()[0] if code.splitlines() else ""
    m = HEADER_RE.match(first_line)
    if not m:
        return None

    body = m.group("body").strip()

    # 你用 "-" 分割层级
    parts = [p.strip() for p in body.split("-") if p.strip()]
    if len(parts) < 3:
        # 至少：一级-二级-题号.题名...
        return None

    tail = parts[-1]
    ext_hint = ""
    # tail 可能是 "2841. xxx.cpp" 或 "2841. xxx"
    if "." in tail:
        # 如果最后有扩展名，切出来
        # 只在末尾像 ".cpp" 这种情况生效
        ext_m = re.search(r"\.(cpp|py|java|js|ts|go|rs|c|cs|kt|swift|rb|php|txt)\s*$", tail, re.IGNORECASE)
        if ext_m:
            ext_hint = ext_m.group(1).lower()
            tail_wo_ext = re.sub(r"\.(cpp|py|java|js|ts|go|rs|c|cs|kt|swift|rb|php|txt)\s*$", "", tail, flags=re.IGNORECASE)
        else:
            tail_wo_ext = tail
    else:
        tail_wo_ext = tail

    pm = PROB_RE.search(tail_wo_ext)
    if not pm:
        return None

    pid = int(pm.group("pid"))
    title = pm.group("title").strip()

    levels = [normalize_seg(x) for x in parts[:-1]]  # 目录层级
    levels = [x for x in levels if x]
    if len(levels) < 1:
        return None

    return levels, pid, title, ext_hint


def build_target_path(levels: list[str], pid: int, title: str, ext: str) -> Path:
    # 你目前想要：一级/二级/ 题号. 题名.ext
    # 如果 levels 超过 2，就按顺序更深层目录
    folder = Path(*levels)
    fname = normalize_seg(f"{pid}. {title}") if title else f"{pid}. unknown"
    return folder / f"{fname}.{ext}"


def main():
    csrf = os.environ.get("LEETCODE_CN_CSRF_TOKEN", "").strip()
    sess = os.environ.get("LEETCODE_CN_SESSION", "").strip()
    if not csrf or not sess:
        raise SystemExit("Missing env: LEETCODE_CN_CSRF_TOKEN / LEETCODE_CN_SESSION")

    state = load_state()
    last_ts = int(state.get("last_timestamp", 0))

    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Referer": "https://leetcode.cn/", "x-csrftoken": csrf})
    s.cookies.set("csrftoken", csrf, domain="leetcode.cn")
    s.cookies.set("LEETCODE_SESSION", sess, domain="leetcode.cn")

    wrote = 0
    pulled = 0
    skip_no_comment = 0
    new_last_ts = last_ts

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

            # 增量：只处理比上次新的
            if ts <= last_ts:
                continue
            new_last_ts = max(new_last_ts, ts)

            # 只要 AC
            if sub.get("statusDisplay") != "Accepted":
                continue

            if pulled >= MAX_DETAIL_PER_RUN:
                # 到上限就保存 watermark 并结束
                state["last_timestamp"] = max(new_last_ts, last_ts)
                save_state(state)
                print(f"ℹ️ Reach MAX_DETAIL_PER_RUN={MAX_DETAIL_PER_RUN}, stop early. wrote={wrote}, skip_no_comment={skip_no_comment}, last_timestamp={state['last_timestamp']}")
                return

            sid = int(sub["id"])
            lang_list = (sub.get("lang") or "").lower().strip()

            time.sleep(SLEEP_BETWEEN_DETAIL + random.random() * 0.6)

            try:
                detail = gql(s, Q_SUBMISSION_DETAIL, {"submissionId": str(sid)}, operation_name="submissionDetail")
            except RateLimitError:
                state["last_timestamp"] = max(new_last_ts, last_ts)
                save_state(state)
                print("⚠️ Hit rate limit. Saved state and exit gracefully.")
                print(f"✅ wrote={wrote}, skip_no_comment={skip_no_comment}, last_timestamp={state['last_timestamp']}")
                return

            pulled += 1
            info = detail.get("submissionDetail") or {}
            code = info.get("code") or ""
            lang_detail = (info.get("lang") or lang_list).lower().strip()

            parsed = parse_header_classification(code)
            if parsed is None:
                skip_no_comment += 1
                continue

            levels, pid, title, ext_hint = parsed

            # ext：优先用注释里写的 .cpp，否则用 lang 映射；C++ 强制 cpp
            ext = ext_hint or code_ext_from_lang(lang_detail)
            if lang_detail in CPP_ALIASES:
                ext = "cpp"

            out_path = build_target_path(levels, pid, title, ext)
            out_path.parent.mkdir(parents=True, exist_ok=True)

            # 不覆盖
            if out_path.exists():
                continue

            out_path.write_text(code, encoding="utf-8", newline="\n")
            wrote += 1

        time.sleep(0.25 + random.random() * 0.3)

    # 保存 watermark
    if new_last_ts != last_ts:
        state["last_timestamp"] = new_last_ts
        save_state(state)

    print(f"✅ done. wrote={wrote}, skip_no_comment={skip_no_comment}, last_timestamp={state.get('last_timestamp', last_ts)}")


if __name__ == "__main__":
    main()
