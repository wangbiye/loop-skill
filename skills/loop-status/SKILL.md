---
name: loop-status
description: 当用户输入 $loop-status，或想查看当前 loop 的紧凑 phase、gate 和状态一致性摘要时使用。
---

# Loop Status

Follow the main protocol in `loop/SKILL.md`.

Execute:

```bash
python3 <loop-skill-dir>/scripts/loop.py status --loop <loop-id>
```

Use this for compact status checks. For agent-specific open questions, claims, recent events, or blockers, use `$loop-sync` instead. If state inconsistency is reported, stop normal execution and repair or clarify before continuing.
