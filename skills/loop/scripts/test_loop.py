#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("loop.py")


class LoopCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "loop-root"

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args, ok=True, extra_env=None):
        env = os.environ.copy()
        env["LOOP_ROOT"] = str(self.root)
        if extra_env:
            env.update(extra_env)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            text=True,
            capture_output=True,
            env=env,
        )
        if ok and result.returncode != 0:
            self.fail(
                f"loop.py {' '.join(args)} failed\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        if not ok and result.returncode == 0:
            self.fail(f"loop.py {' '.join(args)} unexpectedly passed\n{result.stdout}")
        return result

    def read_json(self, relative):
        return json.loads((self.root / relative).read_text())

    def read_jsonl(self, relative):
        records = []
        for line in (self.root / relative).read_text().splitlines():
            if line.strip():
                records.append(json.loads(line))
        return records

    def test_only_loop_root_env_var_controls_default_root(self):
        default_home = Path(self.tmp.name) / "home"
        old_root = Path(self.tmp.name) / "old-root"
        env = os.environ.copy()
        env.pop("LOOP_ROOT", None)
        env["HOME"] = str(default_home)
        env["COORD" + "_ROOT"] = str(old_root)

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "start", "--title", "Work", "--goal", "Do the work"],
            text=True,
            capture_output=True,
            env=env,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((default_home / ".loop/loops").exists())
        self.assertFalse((old_root / "groups").exists())
        self.assertFalse((default_home / ".coord").exists())

    def test_start_creates_loop_state_with_generated_id(self):
        start = self.run_cli(
            "start",
            "--title", "异常法庭辩论",
            "--goal", "模拟一场中国法律框架下的异常法庭辩论",
            "--profile", "debate",
            "--source-type", "conversation",
            "--brief", "围绕既定事实进行控辩攻防，最终由审判长视角总结。",
        )

        loop_id = start.stdout.strip().splitlines()[-1].split("loop_id=", 1)[1]
        self.assertRegex(loop_id, r"^loop-\d{8}-\d{6}-[a-z0-9-]+-[a-z0-9]{4,6}$")
        base = self.root / "loops" / loop_id
        self.assertTrue((base / "manifest.json").exists())
        self.assertTrue((base / "loop.json").exists())
        self.assertTrue((base / "events.jsonl").exists())
        self.assertTrue((base / "questions.jsonl").exists())
        self.assertTrue((base / "claims.json").exists())
        self.assertTrue((base / "agents/root.md").exists())

        state = json.loads((base / "loop.json").read_text())
        self.assertEqual(loop_id, state["loop_id"])
        self.assertEqual("root", state["root_agent"])
        self.assertEqual("debate", state["domain_profile"])
        self.assertEqual("conversation", state["source_context"]["type"])
        self.assertEqual("intake", state["current_phase"])
        self.assertEqual("pending", state["gates"][0]["status"])
        self.assertIn("updated_at", state)
        self.assertIn("last_event_id", state)

    def start_loop(self):
        result = self.run_cli(
            "start",
            "--title", "Login Flow",
            "--goal", "Implement login flow",
            "--profile", "software_engineering",
            "--source-type", "conversation",
            "--brief", "Build and verify login flow.",
        )
        return result.stdout.strip().splitlines()[-1].split("loop_id=", 1)[1]

    def test_update_phase_records_event_and_updates_state_snapshot(self):
        loop_id = self.start_loop()

        update = self.run_cli("update", "--loop", loop_id, "--agent", "root", "--phase", "dispatch")

        self.assertIn("updated loop state", update.stdout)
        state = self.read_json(f"loops/{loop_id}/loop.json")
        self.assertEqual("dispatch", state["current_phase"])
        self.assertRegex(state["last_event_id"], r"^e-\d{4}$")
        events = self.read_jsonl(f"loops/{loop_id}/events.jsonl")
        self.assertEqual("state_update", events[-1]["type"])
        self.assertEqual(state["last_event_id"], events[-1]["id"])

    def test_gate_update_is_structured_and_requires_evidence_for_passed(self):
        loop_id = self.start_loop()

        missing = self.run_cli(
            "gate", "--loop", loop_id, "--agent", "root",
            "core-readiness", "passed",
            ok=False,
        )
        self.assertIn("evidence is required", missing.stderr)

        gate = self.run_cli(
            "gate", "--loop", loop_id, "--agent", "root",
            "core-readiness", "passed",
            "--evidence", "Goal, context, and next action are clear.",
        )

        self.assertIn("updated gate core-readiness=passed", gate.stdout)
        state = self.read_json(f"loops/{loop_id}/loop.json")
        gate_state = next(item for item in state["gates"] if item["name"] == "core-readiness")
        self.assertEqual("passed", gate_state["status"])
        self.assertTrue(gate_state["evidence_events"])
        events = self.read_jsonl(f"loops/{loop_id}/events.jsonl")
        self.assertEqual("gate", events[-1]["type"])

    def test_skipped_gate_requires_user_override_or_risk_acceptance(self):
        loop_id = self.start_loop()

        skipped = self.run_cli(
            "gate", "--loop", loop_id, "--agent", "root",
            "core-readiness", "skipped",
            "--evidence", "Skipping",
            ok=False,
        )
        self.assertIn("skipped gate requires", skipped.stderr)

        allowed = self.run_cli(
            "gate", "--loop", loop_id, "--agent", "root",
            "core-readiness", "skipped",
            "--evidence", "User asked to skip.",
            "--user-override", "User explicitly skipped this gate.",
        )
        self.assertIn("updated gate core-readiness=skipped", allowed.stdout)

    def test_status_reports_state_inconsistency(self):
        loop_id = self.start_loop()
        state_path = self.root / f"loops/{loop_id}/loop.json"
        state = json.loads(state_path.read_text())
        state["last_event_id"] = "e-9999"
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")

        status = self.run_cli("status", "--loop", loop_id)

        self.assertIn("state_inconsistency=true", status.stdout)
        self.assertIn("last_event_id_missing=e-9999", status.stdout)

    def test_join_does_not_apply_builtin_developer_roles(self):
        loop_id = self.start_loop()

        join = self.run_cli("join", loop_id, "reviewer")

        self.assertIn(f"joined {loop_id} as reviewer", join.stdout)
        self.assertNotIn("matched built-in role", join.stdout)
        manifest = self.read_json(f"loops/{loop_id}/manifest.json")
        self.assertIn("reviewer", manifest["agents"])
        self.assertNotIn("role_source", manifest["agents"]["reviewer"])

    def test_role_brief_is_structured_and_sync_outputs_it(self):
        loop_id = self.start_loop()
        self.run_cli("join", loop_id, "api-contract-reviewer")

        role = self.run_cli(
            "role",
            "--loop", loop_id,
            "--agent", "api-contract-reviewer",
            "--purpose", "Review API contract consistency.",
            "--deliverable", "English review verdict with risks and evidence.",
            "--must-read", "loop.json,docs/spec.md",
            "--stop-condition", "Stop if source artifacts are missing.",
        )

        self.assertIn("recorded role brief", role.stdout)
        state = self.read_json(f"loops/{loop_id}/loop.json")
        role_state = next(item for item in state["roles"] if item["agent"] == "api-contract-reviewer")
        self.assertEqual("Review API contract consistency.", role_state["purpose"])
        self.assertEqual(["loop.json", "docs/spec.md"], role_state["must_read"])
        sync = self.run_cli("sync", "--loop", loop_id, "--agent", "api-contract-reviewer")
        self.assertIn("Review API contract consistency.", sync.stdout)
        self.assertIn("English review verdict", sync.stdout)

    def test_ask_answer_and_sync_are_scoped_to_loop_and_agent(self):
        loop_id = self.start_loop()
        other_loop = self.start_loop()
        self.run_cli("join", loop_id, "frontend")
        self.run_cli("join", loop_id, "backend")
        self.run_cli("join", other_loop, "backend")

        ask = self.run_cli("ask", "--loop", loop_id, "--agent", "frontend", "@backend", "接口怎么处理")
        self.assertIn("q-0001", ask.stdout)

        backend_sync = self.run_cli("sync", "--loop", loop_id, "--agent", "backend")
        self.assertIn("q-0001", backend_sync.stdout)
        self.assertIn("接口怎么处理", backend_sync.stdout)

        other_sync = self.run_cli("sync", "--loop", other_loop, "--agent", "backend")
        self.assertNotIn("q-0001", other_sync.stdout)

        answer = self.run_cli("answer", "--loop", loop_id, "--agent", "backend", "q-0001", "Use ApiErrorCode.")
        self.assertIn("answered q-0001", answer.stdout)

        frontend_sync = self.run_cli("sync", "--loop", loop_id, "--agent", "frontend")
        self.assertIn("answer by backend", frontend_sync.stdout)
        self.assertIn("Use ApiErrorCode.", frontend_sync.stdout)

    def test_claim_rejects_overlapping_active_file_claim(self):
        loop_id = self.start_loop()
        self.run_cli("join", loop_id, "frontend")
        self.run_cli("join", loop_id, "backend")

        claim = self.run_cli(
            "claim", "--loop", loop_id, "--agent", "frontend",
            "--files", "src/login/**",
            "Login UI",
        )
        self.assertIn("c-0001", claim.stdout)

        conflict = self.run_cli(
            "claim", "--loop", loop_id, "--agent", "backend",
            "--files", "src/login/form.ts",
            "Login API",
            ok=False,
        )
        self.assertIn("conflicts with active claim c-0001", conflict.stderr)

    def test_release_rejects_non_owner_agent(self):
        loop_id = self.start_loop()
        self.run_cli("join", loop_id, "frontend")
        self.run_cli("join", loop_id, "backend")
        self.run_cli(
            "claim", "--loop", loop_id, "--agent", "frontend",
            "--files", "src/login/**",
            "Login UI",
        )

        release = self.run_cli(
            "release", "--loop", loop_id, "--agent", "backend", "c-0001",
            ok=False,
        )

        self.assertIn("claim c-0001 belongs to frontend", release.stderr)
        conflict = self.run_cli(
            "claim", "--loop", loop_id, "--agent", "backend",
            "--files", "src/login/form.ts",
            "Login API",
            ok=False,
        )
        self.assertIn("conflicts with active claim c-0001", conflict.stderr)

    def test_handoff_updates_agent_summary_and_recent_events(self):
        loop_id = self.start_loop()
        self.run_cli("join", loop_id, "implementer")

        self.run_cli(
            "handoff", "--loop", loop_id, "--agent", "implementer",
            "Implemented login flow; verification passed.",
        )

        summary = (self.root / f"loops/{loop_id}/agents/implementer.md").read_text()
        self.assertIn("Implemented login flow", summary)

        sync = self.run_cli("sync", "--loop", loop_id, "--agent", "root")
        self.assertIn("Implemented login flow", sync.stdout)

    def test_correct_replaces_note_in_sync(self):
        loop_id = self.start_loop()
        self.run_cli("join", loop_id, "reviewer")
        self.run_cli("note", "--loop", loop_id, "--agent", "reviewer", "old conclusion")
        note_event = next(
            event for event in self.read_jsonl(f"loops/{loop_id}/events.jsonl")
            if event.get("type") == "note"
        )

        self.run_cli("correct", "--loop", loop_id, "--agent", "reviewer", note_event["id"], "final conclusion")

        sync = self.run_cli("sync", "--loop", loop_id, "--agent", "root")
        self.assertNotIn("old conclusion", sync.stdout)
        self.assertIn("final conclusion", sync.stdout)

    def test_stateful_events_cannot_be_retracted_or_corrected_generically(self):
        loop_id = self.start_loop()
        self.run_cli(
            "gate", "--loop", loop_id, "--agent", "root",
            "core-readiness", "passed",
            "--evidence", "Goal, context, and next action are clear.",
        )
        gate_event = next(
            event for event in self.read_jsonl(f"loops/{loop_id}/events.jsonl")
            if event.get("type") == "gate"
        )

        retract = self.run_cli(
            "retract", "--loop", loop_id, "--agent", "root",
            gate_event["id"], "Gate evidence was wrong.",
            ok=False,
        )
        self.assertIn("event cannot be retracted", retract.stderr)

        correct = self.run_cli(
            "correct", "--loop", loop_id, "--agent", "root",
            gate_event["id"], "Gate is pending again.",
            ok=False,
        )
        self.assertIn("event cannot be corrected", correct.stderr)

    def test_impact_targets_agent_until_resolved(self):
        loop_id = self.start_loop()
        self.run_cli("join", loop_id, "reviewer")
        self.run_cli("join", loop_id, "implementer")
        self.run_cli("decision", "--loop", loop_id, "--agent", "reviewer", "old decision")
        decision_event = next(
            event for event in self.read_jsonl(f"loops/{loop_id}/events.jsonl")
            if event.get("type") == "decision"
        )

        impact = self.run_cli(
            "impact", "--loop", loop_id, "--agent", "reviewer",
            decision_event["id"], "@implementer",
            "Recheck work based on the old decision.",
        )
        self.assertIn("created impact i-0001", impact.stdout)

        implementer_sync = self.run_cli("sync", "--loop", loop_id, "--agent", "implementer")
        self.assertIn("i-0001", implementer_sync.stdout)
        self.assertIn("Recheck work", implementer_sync.stdout)

        self.run_cli("resolve-impact", "--loop", loop_id, "--agent", "implementer", "i-0001", "Rechecked.")
        implementer_sync_after = self.run_cli("sync", "--loop", loop_id, "--agent", "implementer")
        self.assertNotIn("i-0001", implementer_sync_after.stdout)

    def test_archive_moves_loop_out_of_active_loops(self):
        loop_id = self.start_loop()
        self.run_cli("join", loop_id, "worker")
        self.run_cli("note", "--loop", loop_id, "--agent", "worker", "Ready to archive.")

        archive = self.run_cli("archive", loop_id)

        self.assertIn(f"archived {loop_id} to", archive.stdout)
        self.assertFalse((self.root / f"loops/{loop_id}").exists())
        archived = list((self.root / "archive").glob(f"{loop_id}-*"))
        self.assertEqual(len(archived), 1)
        loops = self.run_cli("list", "loops")
        self.assertNotIn(loop_id, loops.stdout)

    def test_state_write_commands_require_joined_agent(self):
        loop_id = self.start_loop()

        cases = [
            ("note", "--loop", loop_id, "--agent", "typo-agent", "note"),
            (
                "gate", "--loop", loop_id, "--agent", "typo-agent",
                "core-readiness", "passed",
                "--evidence", "Goal, context, and next action are clear.",
            ),
            (
                "claim", "--loop", loop_id, "--agent", "typo-agent",
                "--files", "src/login/**",
                "Login work",
            ),
            ("handoff", "--loop", loop_id, "--agent", "typo-agent", "summary"),
        ]
        for command in cases:
            with self.subTest(command=command[0]):
                result = self.run_cli(*command, ok=False)
                self.assertIn("agent not joined: typo-agent", result.stderr)

    def test_missing_loop_write_commands_do_not_leave_residual_loop_dirs(self):
        commands = [
            ("note", "--loop", "loop-20990101-000000-missing-abcd", "--agent", "root", "note"),
            ("ask", "--loop", "loop-20990101-000000-missing-abcd", "--agent", "root", "@all", "question"),
            ("answer", "--loop", "loop-20990101-000000-missing-abcd", "--agent", "root", "q-0001", "answer"),
            ("claim", "--loop", "loop-20990101-000000-missing-abcd", "--agent", "root", "--files", "src/**", "claim"),
            ("handoff", "--loop", "loop-20990101-000000-missing-abcd", "--agent", "root", "handoff"),
        ]
        for command in commands:
            with self.subTest(command=command[0]):
                result = self.run_cli(*command, ok=False)
                self.assertIn("loop does not exist", result.stderr)
                self.assertFalse((self.root / "loops/loop-20990101-000000-missing-abcd").exists())

    def test_claim_normalizes_relative_paths_and_rejects_unsafe_paths(self):
        loop_id = self.start_loop()
        self.run_cli("join", loop_id, "worker")

        self.run_cli(
            "claim", "--loop", loop_id, "--agent", "worker",
            "--files", "./src/login/**",
            "Login work",
        )
        claims = self.read_json(f"loops/{loop_id}/claims.json")
        self.assertEqual(claims["claims"][0]["files"], ["src/login/**"])

        parent = self.run_cli(
            "claim", "--loop", loop_id, "--agent", "worker",
            "--files", "../secret",
            "Unsafe",
            ok=False,
        )
        self.assertIn("invalid claim path", parent.stderr)

        absolute = self.run_cli(
            "claim", "--loop", loop_id, "--agent", "worker",
            "--files", "/tmp/secret",
            "Unsafe",
            ok=False,
        )
        self.assertIn("invalid claim path", absolute.stderr)

    def test_write_json_rejects_loop_state_symlink(self):
        loop_id = self.start_loop()
        state_path = self.root / f"loops/{loop_id}/loop.json"
        state_path.unlink()
        outside = Path(self.tmp.name) / "outside-loop.json"
        outside.write_text("unchanged\n")
        os.symlink(outside, state_path)

        result = self.run_cli(
            "update", "--loop", loop_id, "--agent", "root", "--phase", "dispatch",
            ok=False,
        )

        self.assertIn("refusing to read symlink", result.stderr)
        self.assertEqual("unchanged\n", outside.read_text())

    def test_read_jsonl_rejects_event_symlink(self):
        loop_id = self.start_loop()
        events_path = self.root / f"loops/{loop_id}/events.jsonl"
        outside = Path(self.tmp.name) / "outside-events.jsonl"
        outside.write_text(
            json.dumps(
                {
                    "id": "e-9999",
                    "type": "note",
                    "agent": "root",
                    "created_at": "2026-06-17T00:00:00+00:00",
                    "text": "outside-root event",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        events_path.unlink()
        os.symlink(outside, events_path)

        result = self.run_cli("sync", "--loop", loop_id, "--agent", "root", ok=False)

        self.assertIn("refusing to read symlink", result.stderr)

    def test_invalid_agent_name_cannot_escape_loop_root(self):
        loop_id = self.start_loop()

        result = self.run_cli("join", loop_id, "../evil", ok=False)

        self.assertIn("invalid agent", result.stderr)
        self.assertFalse((self.root.parent / "evil.md").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
