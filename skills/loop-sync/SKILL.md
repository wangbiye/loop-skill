---
name: loop-sync
description: 当用户输入 $loop-sync，或想同步当前 loop 状态、恢复 looper/root 当前角色和下一步时使用。
---

# Loop Sync

Follow the main protocol in `loop/SKILL.md`.

Use when the current session has `loop=<loop-id>` and `agent=<agent>`, or when the user asks to sync a specific loop.

Execute:

```bash
python3 <loop-skill-dir>/scripts/loop.py sync --loop <loop-id> --agent <agent>
```

Treat the output as current loop context: phase, role brief, gates, open questions, active claims, impacts, effective events, and state integrity.
