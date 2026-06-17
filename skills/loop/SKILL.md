---
name: loop
description: 当用户要求进入 loop 模式、用 loop 推进当前任务、恢复 loop 状态、让 looper 调度 subagent、记录 loop 记忆或交接 root 时使用。
---

# Loop

Local Loop Engineering memory and control layer for agent loops. Loop stores durable state, role briefs, gates, handoffs, questions, claims, and effective events under `${LOOP_ROOT:-~/.loop}`.

## Safety Boundary

Allowed write scope for this skill:

```text
${LOOP_ROOT:-~/.loop}/**
```

Do not read, migrate, delete, or modify `${COORD_ROOT:-~/.coord}` or `~/.coord`. Installing loop skills may remove old installed skill entries from the target skills directory, but it must not touch old state data.

Loop state must not contain secrets, tokens, credentials, or private credentials.

## Public Entry

Users usually trigger loop with natural language:

- "进入 loop 模式"
- "用 loop 推进这个"
- "开始 loop"
- "同步 loop 状态"
- "交接 loop root"

Public fallback commands:

- `$loop`
- `$loop-sync`
- `$loop-status`
- `$loop-handoff`

## Internal Helper

Use the helper in this skill directory:

```bash
python3 <loop-skill-dir>/scripts/loop.py <subcommand>
```

Internal helper subcommands are root/subagent work APIs, not a user-facing command surface. They include `start`, `join`, `sync`, `status`, `list`, `update`, `role`, `note`, `decision`, `ask`, `answer`, `claim`, `release`, `gate`, `handoff`, `correct`, `retract`, `impact`, `resolve-impact`, `archive`, and `archive-all`.

All internal helper writes must stay inside `${LOOP_ROOT:-~/.loop}`.

## Current Identity

After joining or starting a loop, track the current identity in conversation:

```text
loop=<loop-id>
agent=<agent>
```

If identity is missing, only run `start`, `list loops`, or `status --loop <loop-id>` when the loop id is known. Otherwise ask one concise question for the missing loop id or agent.

## Loop Start

When loop is triggered, first classify the source context:

- `conversation`: current conversation is the source of truth.
- `artifact`: an existing spec, plan, document, issue, or file is the source of truth.
- `mixed`: an artifact exists, but current conversation adds overrides or deltas.

Extract a Loop Brief from current context:

- user goal
- source context and artifacts
- confirmed facts
- open questions
- constraints
- likely profile
- initial gates
- next action

If a blocking fact is missing, ask one concise question before starting. If enough context exists, start the loop and record state.

Example helper start:

```bash
python3 <loop-skill-dir>/scripts/loop.py start \
  --title "Login Flow" \
  --goal "Implement login flow" \
  --profile software_engineering \
  --source-type conversation \
  --brief "Build and verify login flow."
```

## Profiles

Loop Core is domain-neutral. Built-in profiles are:

- `software_engineering`
- `debate`
- `research`
- `writing`
- `operations`
- `custom`

Profiles generate default role ideas, phase ideas, gate ideas, and deliverable conventions. They do not create fixed built-in agent roles.

## software_engineering Language Policy

Use Chinese for user-facing questions, status reports, requests for confirmation, and final conclusions.

Use English for agent-facing work:

- subagent role briefs
- subagent prompts
- agent-to-agent ask/answer
- handoffs
- review verdicts
- implementation notes
- verification notes
- future-agent loop summaries

Repo-specific rules override this policy. If the target repo requires Chinese commit messages or other language-specific artifacts, follow the repo rule and record the exception in loop state.

For existing specs/plans:

- Default to a review gate before execution.
- If the user says review is already done or should be skipped, trust the user and record the user override or risk acceptance. Do not pretend review evidence exists.

## debate Profile

For court debate, default to Chinese legal framing:

- no jury
- use `审判长` and `合议庭` framing
- do not call the presiding role `法官` in Chinese user-facing framing

Common roles may include `presiding-judge`, `prosecutor`, `defense`, `evidence-reviewer`, and `collegial-panel-view`. These are debate profile examples, not Loop Core defaults.

## Dynamic Roles

Do not use fixed built-in planner/reviewer/executor/stabilizer/frontend/backend role cards.

The looper creates role briefs based on the task. Each role brief must include:

- purpose
- responsibility boundary
- must-read materials
- deliverable format
- stop conditions

The subagent must join under its own agent name, sync, read `loop.json`, read its role brief, and record its own result. The root must not write another agent's verdict or handoff.

## Gates

Gate verdicts must be structured through the helper `gate` command. Do not record gate verdicts only as free-text notes.

Gate statuses:

- `pending`
- `passed`
- `failed`
- `blocked`
- `skipped`

Non-pending statuses require evidence. `skipped` requires user override or risk acceptance.

## Effective State

Normal sync uses current effective state:

- corrected or retracted events should not pollute the normal view
- open impacts must appear in `Needs Attention`
- state inconsistency must be surfaced

Use `correct`, `retract`, `impact`, and `resolve-impact` for corrections. `correct` and `retract` are for free-text events such as notes, decisions, questions, answers, and handoffs; use structured commands to change gates, role briefs, or loop state. If an event may already have affected another agent, prefer `impact` over silent retraction.

## Handoff

Before root handoff:

- sync current state
- update `loop.json` if phase, next action, gates, role briefs, or communication policy changed
- record a handoff with current goal, source context, phase, gates, blockers, risks, must-read artifacts, and next action

New root must start by reading `loop.json`, effective events, open impacts, gates, and handoff notes.

## Record Final State

If this session has `loop=<loop-id>` and `agent=<agent>`, record durable loop-relevant results before final response for planning, review, implementation, verification, investigation, or handoff work.

Record stable facts, evidence, decisions, blockers, risks, gate verdicts, and handoffs. Do not record drafts, negotiation process, superseded reasoning, or uncertain claims as stable state.
