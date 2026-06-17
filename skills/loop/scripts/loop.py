#!/usr/bin/env python3
import argparse
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - non-Unix fallback
    fcntl = None


DEFAULT_ROOT = Path("~/.loop")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,96}$")
LOOP_ID_RE = re.compile(r"^loop-\d{8}-\d{6}-[a-z0-9][a-z0-9-]{0,48}-[a-z0-9]{4,6}$")
VALID_PROFILES = {"software_engineering", "debate", "research", "writing", "operations", "custom"}
VALID_PHASES = {"intake", "framing", "dispatch", "work", "challenge", "synthesis", "blocked", "handoff", "done"}
VALID_SOURCE_TYPES = {"conversation", "artifact", "mixed"}
VALID_GATE_STATUSES = {"pending", "passed", "failed", "blocked", "skipped"}
QUESTION_RE = re.compile(r"^q-\d{4}$")
EVENT_RE = re.compile(r"^e-\d{4}$")
IMPACT_RE = re.compile(r"^i-\d{4}$")
CORRECTABLE_EVENT_TYPES = {"note", "decision", "handoff", "question", "answer"}
INTERNAL_EVENT_TYPES = {"retract", "impact", "resolve-impact"}
WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
GLOB_CHARS = set("*?[")


class LoopError(Exception):
    pass


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def root_dir():
    configured = os.environ.get("LOOP_ROOT")
    return Path(configured or DEFAULT_ROOT).expanduser().resolve()


def validate_name(kind, value):
    if not NAME_RE.match(value):
        raise LoopError(f"invalid {kind}: {value}")
    return value


def validate_loop_id(value):
    if not LOOP_ID_RE.match(value):
        raise LoopError(f"invalid loop id: {value}")
    return value


def validate_event_id(value):
    if not EVENT_RE.match(value):
        raise LoopError(f"invalid event id: {value}")
    return value


def validate_impact_id(value):
    if not IMPACT_RE.match(value):
        raise LoopError(f"invalid impact id: {value}")
    return value


def ensure_inside(root, path):
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
        raise LoopError(f"refusing to write outside loop root: {resolved_path}")
    return resolved_path


def loop_dir(root, loop_id):
    validate_loop_id(loop_id)
    return ensure_inside(root, root / "loops" / loop_id)


def agent_file(root, loop_id, agent):
    validate_name("agent", agent)
    return ensure_inside(root, loop_dir(root, loop_id) / "agents" / f"{agent}.md")


def loop_paths(root, loop_id):
    base = loop_dir(root, loop_id)
    return {
        "base": base,
        "manifest": base / "manifest.json",
        "loop": base / "loop.json",
        "events": base / "events.jsonl",
        "questions": base / "questions.jsonl",
        "claims": base / "claims.json",
        "agents": base / "agents",
    }


@contextmanager
def locked(root, loop_id=None):
    root.mkdir(parents=True, exist_ok=True)
    if loop_id is None:
        lock_name = ".root.lock"
    else:
        validate_loop_id(loop_id)
        lock_name = f"{loop_id}.lock"
    locks_dir = ensure_inside(root, root / "locks")
    lock_path = ensure_inside(root, locks_dir / lock_name)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        if fcntl is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def read_json(path, default):
    if not path.exists():
        return default
    if path.is_symlink():
        raise LoopError(f"refusing to read symlink: {path}")
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def write_json(root, path, data):
    path = Path(path)
    if path.is_symlink():
        raise LoopError(f"refusing to write symlink: {path}")
    path = ensure_inside(root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy_tmp_candidate = path.with_name(f"{path.name}.tmp")
    if legacy_tmp_candidate.is_symlink():
        raise LoopError(f"refusing to write symlink: {legacy_tmp_candidate}")
    ensure_inside(root, legacy_tmp_candidate)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
            if tmp_path.is_symlink():
                raise LoopError(f"refusing to write symlink: {tmp_path}")
            ensure_inside(root, tmp_path)
            tmp.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp_path, path)
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()


def read_jsonl(path):
    if not path.exists():
        return []
    if path.is_symlink():
        raise LoopError(f"refusing to read symlink: {path}")
    records = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def append_jsonl(root, path, record):
    path = ensure_inside(root, path)
    if path.is_symlink():
        raise LoopError(f"refusing to write symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def append_text(root, path, text):
    path = ensure_inside(root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(text)


def next_id(records, prefix):
    max_id = 0
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d{{4}})$")
    for record in records:
        raw = record.get("id")
        match = pattern.match(raw or "")
        if match:
            max_id = max(max_id, int(match.group(1)))
    return f"{prefix}-{max_id + 1:04d}"


def event_by_id(events, event_id):
    for event in events:
        if event.get("id") == event_id:
            return event
    raise LoopError(f"event not found: {event_id}")


def effective_event_state(events):
    retracted = {
        event.get("target_event_id")
        for event in events
        if event.get("type") == "retract" and event.get("target_event_id")
    }
    superseded = {
        event.get("replaces_event_id")
        for event in events
        if event.get("replaces_event_id")
    }
    resolved_impacts = {
        event.get("impact_id")
        for event in events
        if event.get("type") == "resolve-impact" and event.get("impact_id")
    }
    open_impacts = [
        event
        for event in events
        if event.get("type") == "impact"
        and event.get("impact_id")
        and event.get("impact_id") not in resolved_impacts
    ]
    hidden_event_ids = {event_id for event_id in retracted | superseded if event_id}
    effective_events = [
        event
        for event in events
        if event.get("id") not in hidden_event_ids
        and event.get("type") not in INTERNAL_EVENT_TYPES
    ]
    return {
        "hidden_event_ids": hidden_event_ids,
        "effective_events": effective_events,
        "open_impacts": open_impacts,
        "resolved_impacts": resolved_impacts,
    }


def next_impact_id(events):
    return next_id([event for event in events if event.get("impact_id")], "i")


def append_event(root, loop_id, event_type, agent, **fields):
    paths = loop_paths(root, loop_id)
    event_id = next_id(read_jsonl(paths["events"]), "e")
    record = {
        "id": event_id,
        "type": event_type,
        "agent": agent,
        "created_at": now_iso(),
        **fields,
    }
    append_jsonl(root, paths["events"], record)
    return record


def append_agent_markdown_entry(root, loop_id, agent, kind, event_id, text):
    append_text(root, agent_file(root, loop_id, agent), f"\n## {kind} {event_id} {now_iso()}\n\n{text}\n")


def load_manifest(paths):
    return read_json(paths["manifest"], {"loop_id": paths["base"].name, "agents": {}})


def require_joined_agent(paths, agent):
    validate_name("agent", agent)
    manifest = load_manifest(paths)
    if agent not in manifest.get("agents", {}):
        raise LoopError(f"agent not joined: {agent}")
    return manifest


def split_csv(raw):
    return [item.strip() for item in raw.split(",") if item.strip()]


def current_questions(paths, event_state=None):
    hidden_event_ids = set()
    if event_state:
        hidden_event_ids = set(event_state.get("hidden_event_ids", set()))
    states = {}
    order = []
    for record in read_jsonl(paths["questions"]):
        event_id = record.get("event_id")
        if event_id and event_id in hidden_event_ids:
            continue
        qid = record.get("id")
        if not qid:
            continue
        if qid not in states:
            order.append(qid)
        prior = states.get(qid, {})
        if record.get("status") == "answered" and "answer" in record:
            states[qid] = {
                **prior,
                "status": "answered",
                "answered_at": record.get("answered_at"),
                "answer": record.get("answer", ""),
                "answer_by": record.get("from"),
            }
        else:
            states[qid] = {**prior, **record}
    return [states[qid] for qid in order if states[qid].get("text") and states[qid].get("to")]


def active_claims(paths):
    data = read_json(paths["claims"], {"claims": []})
    return [claim for claim in data.get("claims", []) if claim.get("status") == "active"]


def normalize_claim_path(raw):
    path = raw.strip().replace("\\", "/")
    if not path:
        raise LoopError("invalid claim path: empty")
    if path.startswith("/") or WINDOWS_DRIVE_RE.match(path):
        raise LoopError(f"invalid claim path: {raw}")
    parts = []
    for part in path.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            raise LoopError(f"invalid claim path: {raw}")
        parts.append(part)
    if not parts:
        raise LoopError(f"invalid claim path: {raw}")
    return "/".join(parts)


def parse_files(raw):
    if not raw:
        return []
    files = []
    for item in raw.split(","):
        item = item.strip()
        if item:
            files.append(normalize_claim_path(item))
    return files


def has_glob(pattern):
    return any(char in pattern for char in GLOB_CHARS)


def static_prefix(pattern):
    parts = []
    for part in pattern.split("/"):
        if any(char in part for char in GLOB_CHARS):
            break
        parts.append(part)
    return "/".join(parts)


def prefixes_overlap(left, right):
    if not left or not right:
        return True
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def patterns_overlap(left, right):
    if left == right:
        return True
    if has_glob(left) or has_glob(right):
        return prefixes_overlap(static_prefix(left), static_prefix(right))
    return left.startswith(right + "/") or right.startswith(left + "/")


def render_question(question):
    target = question.get("to", "all")
    status = question.get("status", "open")
    line = f"- {question['id']} from {question.get('from', '?')} to @{target}: {question.get('text', '')}"
    if status == "answered":
        line += f"\n  answer by {question.get('answer_by', '?')}: {question.get('answer', '')}"
    return line


def render_claim(claim):
    files = ", ".join(claim.get("files", [])) or "(no files)"
    return f"- {claim['id']} @{claim['agent']}: {claim.get('task', '')} [{files}]"


def render_impact(impact):
    target = impact.get("target", "all")
    return (
        f"- {impact['impact_id']} from @{impact.get('agent', '?')} "
        f"about {impact.get('target_event_id', '?')} to @{target}: {impact.get('text', '')}"
    )


def render_event(event):
    text = event.get("text") or event.get("answer") or event.get("task") or event.get("summary") or event.get("evidence") or ""
    target = event.get("target")
    route = f" -> @{target}" if target else ""
    return f"- {event['id']} {event['type']} @{event.get('agent', '?')}{route}: {text}"


def slugify_title(raw):
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    return slug[:48].strip("-") or "work"


def generate_loop_id(title):
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    slug = slugify_title(title)
    suffix = secrets.token_hex(3)[:6]
    return f"loop-{timestamp}-{slug}-{suffix}"


def active_loop_ids(root):
    loops_dir = root / "loops"
    if not loops_dir.exists():
        return []
    return sorted(
        path.name
        for path in loops_dir.iterdir()
        if path.is_dir() and (path / "manifest.json").exists()
    )


def require_loop(root, loop_id):
    paths = loop_paths(root, loop_id)
    if not paths["manifest"].exists():
        raise LoopError(f"loop does not exist: {loop_id}")
    return paths


def read_loop_state(paths):
    return read_json(paths["loop"], {})


def write_loop_state(root, paths, state, event_id):
    state["updated_at"] = now_iso()
    state["last_event_id"] = event_id
    write_json(root, paths["loop"], state)


def event_exists(paths, event_id):
    return any(event.get("id") == event_id for event in read_jsonl(paths["events"]))


def state_inconsistency(paths):
    state = read_loop_state(paths)
    last_event_id = state.get("last_event_id")
    if not last_event_id:
        return "last_event_id_missing"
    if not event_exists(paths, last_event_id):
        return f"last_event_id_missing={last_event_id}"
    return None


def initial_loop_state(loop_id, args, event_id):
    timestamp = now_iso()
    profile = args.profile or "custom"
    if profile not in VALID_PROFILES:
        raise LoopError(f"invalid profile: {profile}")
    if args.source_type not in VALID_SOURCE_TYPES:
        raise LoopError(f"invalid source type: {args.source_type}")
    return {
        "loop_id": loop_id,
        "root_agent": args.root_agent,
        "title": args.title,
        "updated_at": timestamp,
        "last_event_id": event_id,
        "user_goal": args.goal,
        "domain_profile": profile,
        "source_context": {
            "type": args.source_type,
            "artifacts": [],
            "confirmed_facts": [],
            "open_questions": [],
            "user_overrides": [],
        },
        "current_phase": "intake",
        "loop_brief": args.brief,
        "constraints": [],
        "communication_policy": {
            "user_language": "profile_or_user_defined",
            "agent_work_language": "profile_or_user_defined",
            "applies_to": ["agent_prompts", "role_briefs", "handoffs", "review_notes", "work_notes"],
            "user_facing_outputs": "profile_or_user_defined",
        },
        "roles": [],
        "gates": [
            {
                "name": "core-readiness",
                "status": "pending",
                "criteria": [
                    "goal is clear",
                    "source context is sufficient",
                    "next action is known",
                ],
                "evidence_events": [event_id],
            }
        ],
        "handoff_contract": {
            "restore_role": "loop root",
            "must_read": ["loop.json", "effective events"],
            "next_action": "complete intake and decide whether to dispatch subagents",
        },
    }


def cmd_start(args):
    root = root_dir()
    validate_name("agent", args.root_agent)
    loop_id = generate_loop_id(args.title)
    with locked(root, loop_id):
        paths = loop_paths(root, loop_id)
        paths["agents"].mkdir(parents=True, exist_ok=True)
        timestamp = now_iso()
        manifest = {
            "loop_id": loop_id,
            "created_at": timestamp,
            "agents": {
                args.root_agent: {
                    "joined_at": timestamp,
                    "last_seen_at": timestamp,
                }
            },
        }
        write_json(root, paths["manifest"], manifest)
        write_json(root, paths["claims"], {"claims": []})
        paths["events"].touch()
        paths["questions"].touch()
        event = append_event(root, loop_id, "loop_start", args.root_agent, text=args.brief)
        write_json(root, paths["loop"], initial_loop_state(loop_id, args, event["id"]))
        agent_file(root, loop_id, args.root_agent).write_text(
            f"# Agent {args.root_agent}\n\nJoined loop `{loop_id}` as root.\n",
            encoding="utf-8",
        )
        print(f"started loop {loop_id}")
        print(f"loop_id={loop_id}")


def cmd_join(args):
    root = root_dir()
    validate_name("agent", args.agent)
    with locked(root, args.loop):
        paths = require_loop(root, args.loop)
        manifest = load_manifest(paths)
        agents = manifest.setdefault("agents", {})
        existing = agents.get(args.agent)
        timestamp = now_iso()
        if existing:
            print(f"warning: agent {args.agent} already exists in loop {args.loop}.")
        agents[args.agent] = {
            **(existing or {}),
            "joined_at": existing.get("joined_at") if existing else timestamp,
            "last_seen_at": timestamp,
        }
        write_json(root, paths["manifest"], manifest)
        summary = agent_file(root, args.loop, args.agent)
        if not summary.exists():
            summary.write_text(
                f"# Agent {args.agent}\n\nJoined loop `{args.loop}` at {timestamp}.\n",
                encoding="utf-8",
            )
        append_event(root, args.loop, "join", args.agent, text=f"joined loop {args.loop}")
        print(f"joined {args.loop} as {args.agent}")
        print(f"current identity: loop={args.loop} agent={args.agent}")


def cmd_role(args):
    root = root_dir()
    with locked(root, args.loop):
        paths = require_loop(root, args.loop)
        require_joined_agent(paths, args.agent)
        state = read_loop_state(paths)
        roles = [item for item in state.get("roles", []) if item.get("agent") != args.agent]
        role = {
            "agent": args.agent,
            "purpose": args.purpose,
            "must_read": split_csv(args.must_read),
            "deliverable": args.deliverable,
            "stop_conditions": split_csv(args.stop_condition),
        }
        roles.append(role)
        state["roles"] = roles
        event = append_event(root, args.loop, "role", args.agent, role=role)
        write_loop_state(root, paths, state, event["id"])
        append_agent_markdown_entry(
            root,
            args.loop,
            args.agent,
            "Role Brief",
            event["id"],
            json.dumps(role, ensure_ascii=False, indent=2),
        )
        print(f"recorded role brief for {args.loop}/{args.agent}")


def cmd_update(args):
    root = root_dir()
    with locked(root, args.loop):
        paths = require_loop(root, args.loop)
        require_joined_agent(paths, args.agent)
        state = read_loop_state(paths)
        changes = {}
        if args.phase:
            if args.phase not in VALID_PHASES:
                raise LoopError(f"invalid phase: {args.phase}")
            state["current_phase"] = args.phase
            changes["phase"] = args.phase
        if args.next_action:
            state.setdefault("handoff_contract", {})["next_action"] = args.next_action
            changes["next_action"] = args.next_action
        if not changes:
            raise LoopError("no loop state changes requested")
        event = append_event(root, args.loop, "state_update", args.agent, changes=changes)
        write_loop_state(root, paths, state, event["id"])
        print(f"updated loop state {args.loop}")


def cmd_gate(args):
    root = root_dir()
    if args.status not in VALID_GATE_STATUSES:
        raise LoopError(f"invalid gate status: {args.status}")
    if args.status in {"passed", "failed", "blocked", "skipped"} and not args.evidence:
        raise LoopError("evidence is required for non-pending gate status")
    if args.status == "skipped" and not (args.user_override or args.risk_acceptance):
        raise LoopError("skipped gate requires --user-override or --risk-acceptance")
    with locked(root, args.loop):
        paths = require_loop(root, args.loop)
        require_joined_agent(paths, args.agent)
        state = read_loop_state(paths)
        gates = state.setdefault("gates", [])
        gate = next((item for item in gates if item.get("name") == args.gate_name), None)
        if gate is None:
            gate = {"name": args.gate_name, "criteria": [], "evidence_events": []}
            gates.append(gate)
        event = append_event(
            root,
            args.loop,
            "gate",
            args.agent,
            gate=args.gate_name,
            status=args.status,
            evidence=args.evidence,
            user_override=args.user_override,
            risk_acceptance=args.risk_acceptance,
        )
        gate["status"] = args.status
        gate.setdefault("evidence_events", []).append(event["id"])
        if args.user_override:
            state.setdefault("source_context", {}).setdefault("user_overrides", []).append(args.user_override)
        if args.risk_acceptance:
            gate["risk_acceptance"] = args.risk_acceptance
        write_loop_state(root, paths, state, event["id"])
        print(f"updated gate {args.gate_name}={args.status}")


def cmd_note(args):
    root = root_dir()
    with locked(root, args.loop):
        paths = require_loop(root, args.loop)
        require_joined_agent(paths, args.agent)
        text = " ".join(args.text).strip()
        event = append_event(root, args.loop, "note", args.agent, text=text)
        append_agent_markdown_entry(root, args.loop, args.agent, "Note", event["id"], text)
        print(f"noted for {args.loop}/{args.agent}")


def cmd_decision(args):
    root = root_dir()
    with locked(root, args.loop):
        paths = require_loop(root, args.loop)
        require_joined_agent(paths, args.agent)
        text = " ".join(args.text).strip()
        append_event(root, args.loop, "decision", args.agent, text=text)
        print(f"recorded decision for {args.loop}")


def cmd_ask(args):
    root = root_dir()
    target = args.target[1:] if args.target.startswith("@") else args.target
    if target != "all":
        validate_name("agent", target)
    with locked(root, args.loop):
        paths = require_loop(root, args.loop)
        require_joined_agent(paths, args.agent)
        qid = next_id(current_questions(paths), "q")
        text = " ".join(args.text).strip()
        question_event = append_event(root, args.loop, "question", args.agent, target=target, question_id=qid, text=text)
        question = {
            "id": qid,
            "from": args.agent,
            "to": target,
            "status": "open",
            "created_at": now_iso(),
            "text": text,
            "event_id": question_event["id"],
        }
        append_jsonl(root, paths["questions"], question)
        print(f"created {qid} to @{target}")


def cmd_answer(args):
    root = root_dir()
    if not QUESTION_RE.match(args.question_id):
        raise LoopError(f"invalid question id: {args.question_id}")
    with locked(root, args.loop):
        paths = require_loop(root, args.loop)
        require_joined_agent(paths, args.agent)
        questions = current_questions(paths)
        question = next((question for question in questions if question.get("id") == args.question_id), None)
        if question is None:
            raise LoopError(f"question not found: {args.question_id}")
        if question.get("status") == "answered":
            raise LoopError(f"question already answered: {args.question_id}")
        target = question.get("to", "all")
        if target not in {"all", args.agent}:
            raise LoopError(f"question {args.question_id} is targeted to @{target}; @{args.agent} cannot answer")
        answer = " ".join(args.text).strip()
        answer_event = append_event(root, args.loop, "answer", args.agent, question_id=args.question_id, answer=answer)
        record = {
            "id": args.question_id,
            "from": args.agent,
            "status": "answered",
            "answered_at": now_iso(),
            "answer": answer,
            "event_id": answer_event["id"],
        }
        append_jsonl(root, paths["questions"], record)
        print(f"answered {args.question_id}")


def cmd_claim(args):
    root = root_dir()
    with locked(root, args.loop):
        paths = require_loop(root, args.loop)
        require_joined_agent(paths, args.agent)
        claims_data = read_json(paths["claims"], {"claims": []})
        files = parse_files(args.files)
        for claim in claims_data.get("claims", []):
            if claim.get("status") != "active":
                continue
            if claim.get("agent") == args.agent:
                continue
            for existing in claim.get("files", []):
                for requested in files:
                    if patterns_overlap(existing, requested):
                        raise LoopError(f"claim conflicts with active claim {claim['id']}: {existing}")
        claim_id = next_id(claims_data.get("claims", []), "c")
        claim = {
            "id": claim_id,
            "agent": args.agent,
            "task": " ".join(args.task).strip(),
            "files": files,
            "status": "active",
            "created_at": now_iso(),
        }
        claims_data.setdefault("claims", []).append(claim)
        write_json(root, paths["claims"], claims_data)
        append_event(root, args.loop, "claim", args.agent, task=claim["task"], claim_id=claim_id, files=files)
        print(f"created claim {claim_id}")


def cmd_release(args):
    root = root_dir()
    with locked(root, args.loop):
        paths = require_loop(root, args.loop)
        require_joined_agent(paths, args.agent)
        claims_data = read_json(paths["claims"], {"claims": []})
        for claim in claims_data.get("claims", []):
            if claim.get("id") == args.claim_id:
                if claim.get("agent") != args.agent:
                    raise LoopError(f"claim {args.claim_id} belongs to {claim.get('agent')}")
                claim["status"] = "released"
                claim["released_at"] = now_iso()
                claim["released_by"] = args.agent
                write_json(root, paths["claims"], claims_data)
                append_event(root, args.loop, "release", args.agent, claim_id=args.claim_id, text=f"released {args.claim_id}")
                print(f"released {args.claim_id}")
                return
        raise LoopError(f"claim not found: {args.claim_id}")


def cmd_handoff(args):
    root = root_dir()
    with locked(root, args.loop):
        paths = require_loop(root, args.loop)
        require_joined_agent(paths, args.agent)
        summary = " ".join(args.summary).strip()
        event = append_event(root, args.loop, "handoff", args.agent, summary=summary)
        append_agent_markdown_entry(root, args.loop, args.agent, "Handoff", event["id"], summary)
        print(f"recorded handoff for {args.loop}/{args.agent}")


def cmd_retract(args):
    root = root_dir()
    validate_event_id(args.event_id)
    with locked(root, args.loop):
        paths = require_loop(root, args.loop)
        require_joined_agent(paths, args.agent)
        events = read_jsonl(paths["events"])
        target = event_by_id(events, args.event_id)
        if target.get("type") not in CORRECTABLE_EVENT_TYPES:
            raise LoopError(f"event cannot be retracted: {args.event_id}")
        reason = " ".join(args.reason).strip()
        append_event(root, args.loop, "retract", args.agent, target_event_id=args.event_id, reason=reason)
        print(f"retracted {args.event_id}")


def append_replacement(root, loop_id, agent, target, text):
    event_type = target.get("type")
    paths = loop_paths(root, loop_id)
    if event_type == "note":
        event = append_event(root, loop_id, "note", agent, text=text, replaces_event_id=target["id"])
        append_agent_markdown_entry(root, loop_id, agent, "Note", event["id"], text)
        return event
    if event_type == "handoff":
        event = append_event(root, loop_id, "handoff", agent, summary=text, replaces_event_id=target["id"])
        append_agent_markdown_entry(root, loop_id, agent, "Handoff", event["id"], text)
        return event
    if event_type == "decision":
        return append_event(root, loop_id, "decision", agent, text=text, replaces_event_id=target["id"])
    if event_type == "question":
        question_id = target.get("question_id")
        route = target.get("target", "all")
        event = append_event(root, loop_id, "question", agent, target=route, question_id=question_id, text=text, replaces_event_id=target["id"])
        append_jsonl(root, paths["questions"], {"id": question_id, "from": target.get("agent", agent), "to": route, "text": text, "event_id": event["id"]})
        return event
    if event_type == "answer":
        question_id = target.get("question_id")
        event = append_event(root, loop_id, "answer", agent, question_id=question_id, answer=text, replaces_event_id=target["id"])
        append_jsonl(root, paths["questions"], {"id": question_id, "from": agent, "status": "answered", "answered_at": now_iso(), "answer": text, "event_id": event["id"]})
        return event
    raise LoopError(f"event cannot be corrected: {target['id']}")


def cmd_correct(args):
    root = root_dir()
    validate_event_id(args.event_id)
    with locked(root, args.loop):
        paths = require_loop(root, args.loop)
        require_joined_agent(paths, args.agent)
        events = read_jsonl(paths["events"])
        target = event_by_id(events, args.event_id)
        if target.get("type") not in CORRECTABLE_EVENT_TYPES:
            raise LoopError(f"event cannot be corrected: {args.event_id}")
        text = " ".join(args.text).strip()
        replacement = append_replacement(root, args.loop, args.agent, target, text)
        print(f"corrected {args.event_id} with {replacement['id']}")


def cmd_impact(args):
    root = root_dir()
    validate_event_id(args.event_id)
    target = args.target[1:] if args.target.startswith("@") else args.target
    if target != "all":
        validate_name("agent", target)
    with locked(root, args.loop):
        paths = require_loop(root, args.loop)
        require_joined_agent(paths, args.agent)
        events = read_jsonl(paths["events"])
        event_by_id(events, args.event_id)
        impact_id = next_impact_id(events)
        text = " ".join(args.text).strip()
        append_event(
            root,
            args.loop,
            "impact",
            args.agent,
            impact_id=impact_id,
            target_event_id=args.event_id,
            target=target,
            status="open",
            text=text,
        )
        print(f"created impact {impact_id}")


def cmd_resolve_impact(args):
    root = root_dir()
    validate_impact_id(args.impact_id)
    with locked(root, args.loop):
        paths = require_loop(root, args.loop)
        require_joined_agent(paths, args.agent)
        events = read_jsonl(paths["events"])
        state = effective_event_state(events)
        impact = next((item for item in state["open_impacts"] if item.get("impact_id") == args.impact_id), None)
        if impact is None:
            raise LoopError(f"open impact not found: {args.impact_id}")
        text = " ".join(args.text).strip()
        append_event(root, args.loop, "resolve-impact", args.agent, impact_id=args.impact_id, status="resolved", text=text)
        print(f"resolved impact {args.impact_id}")


def archive_loop(root, loop_id):
    paths = require_loop(root, loop_id)
    archive_root = ensure_inside(root, root / "archive")
    archive_root.mkdir(parents=True, exist_ok=True)
    base_name = f"{loop_id}-{datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')}"
    target = ensure_inside(root, archive_root / base_name)
    suffix = 1
    while target.exists():
        target = ensure_inside(root, archive_root / f"{base_name}-{suffix}")
        suffix += 1
    shutil.move(str(paths["base"]), str(target))
    return target


def cmd_archive(args):
    root = root_dir()
    validate_loop_id(args.loop)
    with locked(root, args.loop):
        target = archive_loop(root, args.loop)
        print(f"archived {args.loop} to {target}")


def cmd_archive_all(args):
    root = root_dir()
    loops = active_loop_ids(root)
    if not loops:
        print("no active loops to archive")
        return
    for loop_id in loops:
        with locked(root, loop_id):
            paths = loop_paths(root, loop_id)
            if not paths["manifest"].exists():
                continue
            target = archive_loop(root, loop_id)
            print(f"archived {loop_id} to {target}")


def cmd_sync(args):
    root = root_dir()
    with locked(root, args.loop):
        paths = require_loop(root, args.loop)
        manifest = load_manifest(paths)
        state = read_loop_state(paths)
        events = read_jsonl(paths["events"])
        event_state = effective_event_state(events)
        questions = current_questions(paths, event_state)
        claims = active_claims(paths)
        inconsistency = state_inconsistency(paths)
        role = next((item for item in state.get("roles", []) if item.get("agent") == args.agent), None)

        print("# Loop Sync")
        print(f"Loop: {args.loop}")
        print(f"Agent: {args.agent}")
        print(f"Profile: {state.get('domain_profile', '(none)')}")
        print(f"Phase: {state.get('current_phase', '(none)')}")
        print("Agents: " + (", ".join(sorted(manifest.get("agents", {}).keys())) or "(none)"))
        print("\n## Current Role Brief")
        print(json.dumps(role, ensure_ascii=False, indent=2) if role else "(none recorded)")
        print("\n## Gates")
        gates = state.get("gates", [])
        if gates:
            for gate in gates:
                print(f"- {gate.get('name')}: {gate.get('status', 'pending')}")
        else:
            print("(none)")
        print("\n## Open Questions For This Agent")
        mine = [q for q in questions if q.get("status", "open") == "open" and q.get("to") in {args.agent, "all"}]
        print("\n".join(render_question(q) for q in mine) if mine else "(none)")
        print("\n## Recent Answers")
        answered = [q for q in questions if q.get("status") == "answered"]
        print("\n".join(render_question(q) for q in answered[-10:]) if answered else "(none)")
        print("\n## Active Claims")
        print("\n".join(render_claim(c) for c in claims) if claims else "(none)")
        print("\n## Needs Attention")
        my_impacts = [impact for impact in event_state["open_impacts"] if impact.get("target") in {args.agent, "all"}]
        print("\n".join(render_impact(impact) for impact in my_impacts) if my_impacts else "(none)")
        print("\n## Recent Effective Events")
        recent_events = event_state["effective_events"][-10:]
        print("\n".join(render_event(e) for e in recent_events) if recent_events else "(none)")
        print("\n## State Integrity")
        print(f"state_inconsistency={'true' if inconsistency else 'false'}")
        if inconsistency:
            print(inconsistency)


def cmd_status(args):
    root = root_dir()
    with locked(root, args.loop):
        paths = require_loop(root, args.loop)
        state = read_loop_state(paths)
        inconsistency = state_inconsistency(paths)
        gates = state.get("gates", [])
        open_gates = [gate for gate in gates if gate.get("status") in {"pending", "failed", "blocked"}]
        print(f"loop={args.loop}")
        print(f"profile={state.get('domain_profile', '(none)')}")
        print(f"phase={state.get('current_phase', '(none)')}")
        print(f"gates={len(gates)}")
        print(f"open_gates={len(open_gates)}")
        print(f"state_inconsistency={'true' if inconsistency else 'false'}")
        if inconsistency:
            print(inconsistency)


def cmd_list(args):
    root = root_dir()
    mode = args.what or "loops"
    if mode == "loops":
        loops = active_loop_ids(root)
        print("\n".join(loops) if loops else "(none)")
        return
    if mode == "agents":
        if not args.loop:
            raise LoopError("--loop is required for list agents")
        with locked(root, args.loop):
            paths = require_loop(root, args.loop)
            manifest = load_manifest(paths)
            agents = sorted(manifest.get("agents", {}).keys())
            print("\n".join(agents) if agents else "(none)")
            return
    raise LoopError(f"unsupported list target: {mode}")


def build_parser():
    parser = argparse.ArgumentParser(description="Local loop helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start")
    start.add_argument("--title", required=True)
    start.add_argument("--goal", required=True)
    start.add_argument("--profile", choices=sorted(VALID_PROFILES), default="custom")
    start.add_argument("--source-type", choices=sorted(VALID_SOURCE_TYPES), default="conversation")
    start.add_argument("--brief", default="")
    start.add_argument("--root-agent", default="root")
    start.set_defaults(func=cmd_start)

    join = subparsers.add_parser("join")
    join.add_argument("loop")
    join.add_argument("agent")
    join.set_defaults(func=cmd_join)

    sync = subparsers.add_parser("sync")
    sync.add_argument("--loop", required=True)
    sync.add_argument("--agent", required=True)
    sync.set_defaults(func=cmd_sync)

    list_cmd = subparsers.add_parser("list")
    list_cmd.add_argument("what", nargs="?", choices=["loops", "agents"])
    list_cmd.add_argument("--loop")
    list_cmd.set_defaults(func=cmd_list)

    role = subparsers.add_parser("role")
    role.add_argument("--loop", required=True)
    role.add_argument("--agent", required=True)
    role.add_argument("--purpose", required=True)
    role.add_argument("--deliverable", required=True)
    role.add_argument("--must-read", default="")
    role.add_argument("--stop-condition", default="")
    role.set_defaults(func=cmd_role)

    update = subparsers.add_parser("update")
    update.add_argument("--loop", required=True)
    update.add_argument("--agent", required=True)
    update.add_argument("--phase")
    update.add_argument("--next-action")
    update.set_defaults(func=cmd_update)

    gate = subparsers.add_parser("gate")
    gate.add_argument("--loop", required=True)
    gate.add_argument("--agent", required=True)
    gate.add_argument("gate_name")
    gate.add_argument("status")
    gate.add_argument("--evidence", default="")
    gate.add_argument("--user-override", default="")
    gate.add_argument("--risk-acceptance", default="")
    gate.set_defaults(func=cmd_gate)

    status = subparsers.add_parser("status")
    status.add_argument("--loop", required=True)
    status.set_defaults(func=cmd_status)

    note = subparsers.add_parser("note")
    note.add_argument("--loop", required=True)
    note.add_argument("--agent", required=True)
    note.add_argument("text", nargs="+")
    note.set_defaults(func=cmd_note)

    decision = subparsers.add_parser("decision")
    decision.add_argument("--loop", required=True)
    decision.add_argument("--agent", required=True)
    decision.add_argument("text", nargs="+")
    decision.set_defaults(func=cmd_decision)

    ask = subparsers.add_parser("ask")
    ask.add_argument("--loop", required=True)
    ask.add_argument("--agent", required=True)
    ask.add_argument("target")
    ask.add_argument("text", nargs="+")
    ask.set_defaults(func=cmd_ask)

    answer = subparsers.add_parser("answer")
    answer.add_argument("--loop", required=True)
    answer.add_argument("--agent", required=True)
    answer.add_argument("question_id")
    answer.add_argument("text", nargs="+")
    answer.set_defaults(func=cmd_answer)

    claim = subparsers.add_parser("claim")
    claim.add_argument("--loop", required=True)
    claim.add_argument("--agent", required=True)
    claim.add_argument("--files", default="")
    claim.add_argument("task", nargs="+")
    claim.set_defaults(func=cmd_claim)

    release = subparsers.add_parser("release")
    release.add_argument("--loop", required=True)
    release.add_argument("--agent", required=True)
    release.add_argument("claim_id")
    release.set_defaults(func=cmd_release)

    handoff = subparsers.add_parser("handoff")
    handoff.add_argument("--loop", required=True)
    handoff.add_argument("--agent", required=True)
    handoff.add_argument("summary", nargs="+")
    handoff.set_defaults(func=cmd_handoff)

    retract = subparsers.add_parser("retract")
    retract.add_argument("--loop", required=True)
    retract.add_argument("--agent", required=True)
    retract.add_argument("event_id")
    retract.add_argument("reason", nargs="+")
    retract.set_defaults(func=cmd_retract)

    correct = subparsers.add_parser("correct")
    correct.add_argument("--loop", required=True)
    correct.add_argument("--agent", required=True)
    correct.add_argument("event_id")
    correct.add_argument("text", nargs="+")
    correct.set_defaults(func=cmd_correct)

    impact = subparsers.add_parser("impact")
    impact.add_argument("--loop", required=True)
    impact.add_argument("--agent", required=True)
    impact.add_argument("event_id")
    impact.add_argument("target")
    impact.add_argument("text", nargs="+")
    impact.set_defaults(func=cmd_impact)

    resolve_impact = subparsers.add_parser("resolve-impact")
    resolve_impact.add_argument("--loop", required=True)
    resolve_impact.add_argument("--agent", required=True)
    resolve_impact.add_argument("impact_id")
    resolve_impact.add_argument("text", nargs="+")
    resolve_impact.set_defaults(func=cmd_resolve_impact)

    archive = subparsers.add_parser("archive")
    archive.add_argument("loop")
    archive.set_defaults(func=cmd_archive)

    archive_all = subparsers.add_parser("archive-all")
    archive_all.set_defaults(func=cmd_archive_all)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except LoopError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
