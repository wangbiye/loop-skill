---
name: loop-handoff
description: 当用户输入 $loop-handoff，或要求把当前 loop root 交接给新会话、新 looper 或未来 agent 接手时使用。
---

# Loop Handoff

Follow the main protocol in `loop/SKILL.md`.

Before handoff, inspect current conversation and loop state. Update `loop.json` first if phase, gates, next action, role briefs, or communication policy changed.

Then execute:

```bash
python3 <loop-skill-dir>/scripts/loop.py handoff --loop <loop-id> --agent <agent> "<summary>"
```

The summary must include current goal, source context, profile, phase, gates, open blockers, residual risks, must-read artifacts, and next action.
