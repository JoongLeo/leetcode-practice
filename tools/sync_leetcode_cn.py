# tools/sync_leetcode_cn.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

STATE_PATH = DATA_DIR / "leetcode_cn_sync_state.json"
LAST_REPORT_PATH = DATA_DIR / "last_sync_report.json"

# Max number of submission details per run (avoid throttling).
MAX_DETAIL_PER_RUN = int(os.getenv("MAX_DETAIL_PER_RUN", "8"))

# Allowed acceptance statuses (case-insensitive, comma-separated).
ACCEPTED_STATUSES = {
    s.strip().casefold()
    for s in os.getenv("ACCEPTED_STATUSES", "accepted,通过").split(",")
    if s.strip()
}

# Header format:
# // 一级-二级-2841. 题名.cpp
HEADER_RE = re.compile(r"^\s*(?://|#)\s*(.+?)\s*$")
FILENAME_TAIL_RE = re.compile(
    r"^\s*(\d+)\.\s*(.+?)\.(cpp|py|java|js|ts|go|rs|c|cs|kt|swift|rb|php|txt)\s*$",
    re.IGNORECASE,
)


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


def safe_name(s: str) -> str:
    # Windows/cross-platform safe path component.
    s = s.strip()
    s = s.replace("/", "_").replace("\\", "_")
    s = re.sub(r"[<>:\"|?*]", "_", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def get_session() -> requests.Session:
    csrf = os.getenv("LEETCODE_CN_CSRF_TOKEN", "").strip()
    sess = os.getenv("LEETCODE_CN_SESSION", "").strip()
    if not csrf or not sess:
        raise RuntimeError("Missing env: LEETCODE_CN_CSRF_TOKEN / LEETCODE_CN_SESSION")

    s = requests.Session()
    # leetcode.cn cookie names
    s.cookies.set("csrftoken", csrf, domain="leetcode.cn")
    s.cookies.set("LEETCODE_SESSION", sess, domain="leetcode.cn")
    s.headers.update(
        {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://leetcode.cn/",
            "Origin": "https://leetcode.cn",
            "X-CSRFToken": csrf,
        }
    )
    return s


def fetch_recent_submissions(sess: requests.Session, offset: int = 0, limit: int = 20) -> Dict[str, Any]:
    # This endpoint requires a logged-in leetcode.cn session.
    url = f"https://leetcode.cn/api/submissions/?offset={offset}&limit={limit}"
    r = sess.get(url, timeout=30)
    r.raise_for_status()
    return r.json()


def try_fetch_submission_code_graphql(sess: requests.Session, submission_id: int) -> Optional[str]:
    # GraphQL (preferred). Returns None on failure/limit.
    url = "https://leetcode.cn/graphql/"
    payload = {
        "operationName": "submissionDetail",
        "variables": {"submissionId": submission_id},
        "query": """
query submissionDetail($submissionId: Int!) {
  submissionDetail(submissionId: $submissionId) {
    code
  }
}
""".strip(),
    }
    try:
        r = sess.post(url, json=payload, timeout=30)
        if r.status_code != 200:
            return None
        js = r.json()
        code = (((js.get("data") or {}).get("submissionDetail") or {}).get("code"))
        return code if isinstance(code, str) and code.strip() else None
    except Exception:
        return None


def try_fetch_submission_code_html(sess: requests.Session, submission_id: int) -> Optional[str]:
    # Fallback: scrape detail page HTML for code (fragile).
    url = f"https://leetcode.cn/submissions/detail/{submission_id}/"
    try:
        r = sess.get(url, timeout=30)
        if r.status_code != 200:
            return None
        text = r.text

        # Most pages embed a JSON "code" field.
        m = re.search(r'"code"\s*:\s*"((?:\\.|[^"\\])*)"', text)
        if not m:
            return None
        raw = m.group(1)
        code = bytes(raw, "utf-8").decode("unicode_escape")
        code = code.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"').replace("\\\\", "\\")
        return code.strip() if code.strip() else None
    except Exception:
        return None


def fetch_submission_code(sess: requests.Session, submission_id: int) -> Optional[str]:
    code = try_fetch_submission_code_graphql(sess, submission_id)
    if code:
        return code
    return try_fetch_submission_code_html(sess, submission_id)


def parse_header_path(code: str) -> Optional[Tuple[List[str], int, str, str]]:
    """
    Parse first-line comment into:
    - folders: [level1, level2, ...] (>= 1 level)
    - pid: problem id
    - title: problem title from the tail
    - ext: cpp/py/...
    """
    lines = code.splitlines()
    if not lines:
        return None
    # Tolerate BOM or leading empty lines from platform formatting.
    first_line = ""
    for line in lines:
        if line.strip():
            first_line = line.lstrip("\ufeff")
            break
    if not first_line:
        return None
    m = HEADER_RE.match(first_line)
    if not m:
        return None
    header = m.group(1).strip()
    parts = [safe_name(p) for p in header.split("-") if p.strip()]
    if len(parts) < 2:
        return None

    tail = parts[-1]
    mt = FILENAME_TAIL_RE.match(tail)
    if not mt:
        return None

    pid = int(mt.group(1))
    title = safe_name(mt.group(2))
    ext = mt.group(3).lower()

    folders = [safe_name(x) for x in parts[:-1] if x.strip()]
    if not folders:
        return None

    return folders, pid, title, ext


def write_solution_file(folders: List[str], pid: int, title: str, ext: str, code: str) -> Path:
    out_dir = REPO_ROOT
    for f in folders:
        out_dir = out_dir / f
    out_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{pid}. {title}.{ext}"
    out_path = out_dir / filename

    # Skip writing if identical.
    if out_path.exists():
        old = out_path.read_text(encoding="utf-8", errors="ignore")
        if old == code:
            return out_path

    out_path.write_text(code.rstrip() + "\n", encoding="utf-8", newline="\n")
    return out_path


def main():
    sess = get_session()

    if os.getenv("RESET_SYNC_STATE", "").strip().lower() in {"1", "true", "yes"}:
        STATE_PATH.unlink(missing_ok=True)
    state = read_json(STATE_PATH, default={"last_timestamp": 0, "seen_ids": []})
    last_timestamp = int(state.get("last_timestamp", 0) or 0)
    seen_ids = set(int(x) for x in (state.get("seen_ids") or []) if str(x).isdigit())

    wrote = 0
    skip_no_comment = 0
    skip_no_code = 0
    skip_not_accepted = 0
    scanned = 0
    max_seen_ts = last_timestamp

    added_items: List[Dict[str, Any]] = []

    # Fetch recent submissions until we hit the saved watermark.
    offset = 0
    stop = False
    stop_reason = ""

    while not stop and wrote < MAX_DETAIL_PER_RUN:
        js = fetch_recent_submissions(sess, offset=offset, limit=20)
        subs = js.get("submissions_dump") or []
        if not subs:
            break

        for it in subs:
            scanned += 1

            sid = int(it.get("id") or 0)
            ts = int(it.get("timestamp") or 0)
            status = (it.get("status_display") or "").strip()

            if ts > max_seen_ts:
                max_seen_ts = ts

            if ts <= last_timestamp:
                stop = True
                stop_reason = "watermark"
                break

            # Only keep accepted submissions.
            if status.casefold() not in ACCEPTED_STATUSES:
                # Still mark id as seen to avoid repeated scans.
                if sid:
                    seen_ids.add(sid)
                skip_not_accepted += 1
                continue

            if sid in seen_ids:
                continue

            code = fetch_submission_code(sess, sid)
            seen_ids.add(sid)

            if not code:
                skip_no_code += 1
                continue

            parsed = parse_header_path(code)
            if not parsed:
                skip_no_comment += 1
                continue

            folders, pid, prob_title, ext = parsed
            out_path = write_solution_file(folders, pid, prob_title, ext, code)

            wrote += 1
            added_items.append(
                {
                    "pid": pid,
                    "title": prob_title,
                    "path": out_path.relative_to(REPO_ROOT).as_posix(),
                    "timestamp": ts,
                    "lang": it.get("lang", ""),
                    "submission_id": sid,
                }
            )

            if wrote >= MAX_DETAIL_PER_RUN:
                stop_reason = "max_detail"
                break

        offset += 20
        if wrote >= MAX_DETAIL_PER_RUN:
            break

    # Only advance watermark if we fully scanned past the previous watermark.
    if not stop_reason and max_seen_ts > last_timestamp:
        last_timestamp = max_seen_ts

    state_out = {
        "last_timestamp": int(last_timestamp),
        "seen_ids": sorted(list(seen_ids))[-2000:],
    }
    write_json(STATE_PATH, state_out)

    report = {
        "generated_at": int(time.time()),
        "last_timestamp": int(last_timestamp),
        "wrote": int(wrote),
        "skip_no_comment": int(skip_no_comment),
        "skip_no_code": int(skip_no_code),
        "skip_not_accepted": int(skip_not_accepted),
        "scanned": int(scanned),
        "stop_reason": stop_reason,
        "added": added_items,
    }
    write_json(LAST_REPORT_PATH, report)

    msg = f"Reach MAX_DETAIL_PER_RUN={MAX_DETAIL_PER_RUN}, stop early." if wrote >= MAX_DETAIL_PER_RUN else "Sync done."
    print(msg)
    print(
        "wrote {w} file(s). skip_no_comment={snc}, skip_no_code={sn}, "
        "skip_not_accepted={sna}, last_timestamp={ts}, scanned={sc}".format(
            w=wrote,
            snc=skip_no_comment,
            sn=skip_no_code,
            sna=skip_not_accepted,
            ts=last_timestamp,
            sc=scanned,
        )
    )


if __name__ == "__main__":
    main()
