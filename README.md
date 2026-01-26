# leetcode-cn-sync

This repo syncs accepted submissions from leetcode.cn into a folder structure.

Folder rules
- The first two single-line comments at the top of your submission define the level-1 and level-2 folders.
- Supported comment prefixes: `//`, `#`, `--`, `;`.
- Example:
  - `// Sliding Window`
  - `// Fixed Length`
  - Output path: `Sliding Window/Fixed Length/2841. Some Title.cpp`

Setup
1. Add GitHub secrets:
   - `LEETCODE_SESSION` (required)
   - `LEETCODE_CSRF_TOKEN` (optional)
2. Trigger the workflow manually or wait for the schedule in `.github/workflows/leetcode-cn-sync.yml`.

Notes
- Sync state and metadata cache live under `.sync/`.
