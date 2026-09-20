from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from preflight import BLOCKED, DRY, MIGRATE, PORTABLE_SPECS, build_report, resolve_root, source_path  # noqa: E402
from scaffold import ApplyError, verify_live_record  # noqa: E402


@contextmanager
def clean_agent_workspace_env(home: Path):
    original = dict(os.environ)
    try:
        for key in list(os.environ):
            if key in {"KGM_AGENT_WORKSPACE_HOME", "AI_INFRA_HOME"} or key.startswith(
                ("KGM_AGENT_SOURCE_", "AI_INFRA_SOURCE_")
            ):
                os.environ.pop(key, None)
        os.environ["HOME"] = str(home)
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)


class QuickstartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="agent-workspace-bootstrap-")
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / "home"
        self.home.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def plan(self) -> dict:
        with clean_agent_workspace_env(self.home):
            return build_report(self.home)

    def write_plan(self, plan: dict) -> Path:
        path = self.base / "plan.json"
        path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        return path

    def run_script(self, name: str, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        for key in list(env):
            if key in {"KGM_AGENT_WORKSPACE_HOME", "AI_INFRA_HOME"} or key.startswith(
                ("KGM_AGENT_SOURCE_", "AI_INFRA_SOURCE_")
            ):
                env.pop(key, None)
        env["HOME"] = str(self.home)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / name), *args],
            text=True,
            capture_output=True,
            env=env,
        )

    def export_handle(self) -> str:
        marker = "Copyright (c) 2026 "
        placeholder = "<YOUR_" + "GITHUB_HANDLE>"
        for line in (SKILL_ROOT / "LICENSE").read_text(encoding="utf-8").splitlines():
            if line.startswith(marker):
                handle = line.removeprefix(marker)
                return "Example-Owner" if handle == placeholder else handle
        self.fail("LICENSE copyright line missing")

    def apply(self, plan: dict, *extra: str) -> subprocess.CompletedProcess:
        plan_path = self.write_plan(plan)
        return self.run_script(
            "scaffold.py",
            "--plan-file",
            str(plan_path),
            "--approve-digest",
            plan["plan_digest"],
            *extra,
        )

    def make_skill(self, root: Path, name: str, description: str = "test skill") -> Path:
        path = root / name
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n\n# Test\n",
            encoding="utf-8",
        )
        return path

    def test_fresh_install_and_doctor(self) -> None:
        plan = self.plan()
        self.assertEqual(plan["verdict"], DRY)
        applied = self.apply(plan)
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        root = self.home / "kgm-agent-workspace"
        self.assertTrue((root / "control/bin/link-local.sh").is_file())
        self.assertTrue((root / "content/skills").is_dir())
        self.assertTrue((root / "content/settings").is_dir())
        self.assertTrue((root / "content/subagent").is_dir())
        self.assertTrue((root / "state").is_dir())
        self.assertFalse((root / ".git").exists())
        journal = json.loads((root / "state/transaction.json").read_text())
        inventory = root / journal["inventory_path"]
        self.assertTrue(inventory.is_file())
        self.assertEqual(inventory.stat().st_mode & 0o077, 0)
        doctor = self.run_script("doctor.py", "--home", str(self.home))
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)

    def test_root_override_priority_and_legacy_alias(self) -> None:
        canonical = self.base / "canonical-workspace"
        legacy = self.base / "legacy-workspace"
        with clean_agent_workspace_env(self.home):
            self.assertEqual(resolve_root(self.home), self.home / "kgm-agent-workspace")
            os.environ["AI_INFRA_HOME"] = str(legacy)
            self.assertEqual(resolve_root(self.home), legacy)
            os.environ["KGM_AGENT_WORKSPACE_HOME"] = str(canonical)
            self.assertEqual(resolve_root(self.home), canonical)

    def test_source_override_priority_and_legacy_alias(self) -> None:
        spec = next(item for item in PORTABLE_SPECS if item.key == "claude_md")
        canonical = self.base / "canonical-CLAUDE.md"
        legacy = self.base / "legacy-CLAUDE.md"
        with clean_agent_workspace_env(self.home):
            os.environ["AI_INFRA_SOURCE_CLAUDE_MD"] = str(legacy)
            self.assertEqual(source_path(self.home, spec), legacy)
            os.environ["KGM_AGENT_SOURCE_CLAUDE_MD"] = str(canonical)
            self.assertEqual(source_path(self.home, spec), canonical)

    def test_existing_ai_infra_directory_is_not_auto_adopted(self) -> None:
        legacy_or_other_project = self.home / "ai-infra"
        nacos = legacy_or_other_project / "nacos"
        nacos.mkdir(parents=True)
        marker = nacos / "do-not-touch.txt"
        marker.write_text("other project\n", encoding="utf-8")
        plan = self.plan()
        self.assertEqual(plan["ai_infra_root"], str(self.home / "kgm-agent-workspace"))
        applied = self.apply(plan)
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        self.assertEqual(marker.read_text(encoding="utf-8"), "other project\n")
        self.assertTrue((self.home / "kgm-agent-workspace").is_dir())

    def test_migrates_portable_assets_and_preserves_backups(self) -> None:
        self.make_skill(self.home / ".claude/skills", "sample-skill")
        claude_md = self.home / ".claude/CLAUDE.md"
        claude_md.parent.mkdir(parents=True, exist_ok=True)
        claude_md.write_text("# Existing rules\n", encoding="utf-8")
        plan = self.plan()
        self.assertEqual(plan["verdict"], MIGRATE)
        applied = self.apply(plan)
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        root = self.home / "kgm-agent-workspace"
        self.assertTrue((root / "content/skills/sample-skill/SKILL.md").is_file())
        self.assertEqual((root / "content/settings/claude/CLAUDE.md").read_text(), "# Existing rules\n")
        self.assertTrue(claude_md.is_symlink())
        self.assertTrue(list(claude_md.parent.glob("CLAUDE.md.backup.*")))

    def test_migrates_hermes_assets_and_preserves_backups(self) -> None:
        self.make_skill(self.home / ".hermes/skills", "hermes-skill")
        soul = self.home / ".hermes/SOUL.md"
        soul.write_text("# Hermes rules\n", encoding="utf-8")
        agents = self.home / ".hermes/agents"
        agents.mkdir()
        (agents / "reviewer.md").write_text("# Reviewer\n", encoding="utf-8")

        plan = self.plan()
        self.assertEqual(plan["verdict"], MIGRATE)
        actions = {item["key"]: item["action"] for item in plan["portable_assets"]}
        self.assertEqual(actions["hermes_skills"], "migrate")
        self.assertEqual(actions["hermes_soul"], "migrate")
        self.assertEqual(actions["hermes_agents"], "migrate")

        applied = self.apply(plan)
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        root = self.home / "kgm-agent-workspace"
        self.assertTrue((root / "content/skills/hermes-skill/SKILL.md").is_file())
        self.assertEqual(
            (root / "content/settings/hermes/SOUL.md").read_text(encoding="utf-8"),
            "# Hermes rules\n",
        )
        self.assertEqual(
            (root / "content/subagent/imported/hermes/reviewer.md").read_text(encoding="utf-8"),
            "# Reviewer\n",
        )
        for live, expected in (
            (self.home / ".hermes/skills", root / "content/skills"),
            (soul, root / "content/settings/hermes/SOUL.md"),
            (agents, root / "content/subagent/imported/hermes"),
        ):
            self.assertTrue(live.is_symlink())
            self.assertEqual(live.resolve(strict=True), expected.resolve(strict=True))
            self.assertTrue(list(live.parent.glob(f"{live.name}.backup.*")))
        doctor = self.run_script("doctor.py", "--home", str(self.home))
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)

    def test_identical_hermes_skill_merges_without_conflict(self) -> None:
        self.make_skill(self.home / ".claude/skills", "shared-skill")
        self.make_skill(self.home / ".hermes/skills", "shared-skill")
        plan = self.plan()
        self.assertEqual(plan["verdict"], MIGRATE)
        self.assertFalse(any(item["kind"] == "skill-entry-conflict" for item in plan["blockers"]))
        applied = self.apply(plan)
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        root = self.home / "kgm-agent-workspace"
        self.assertTrue((root / "content/skills/shared-skill/SKILL.md").is_file())

    def test_sensitive_hermes_agent_blocks_without_value_echo(self) -> None:
        secret = "sk-" + ("J" * 24)
        agent = self.home / ".hermes/agents/reviewer.md"
        agent.parent.mkdir(parents=True)
        agent.write_text(f"api_key={secret}\n", encoding="utf-8")
        plan = self.plan()
        encoded = json.dumps(plan)
        self.assertEqual(plan["verdict"], BLOCKED)
        self.assertNotIn(secret, encoded)
        self.assertIn("openai-style-token", encoded)

    def test_unreadable_hermes_agent_directory_blocks(self) -> None:
        private = self.home / ".hermes/agents/private-subdir"
        private.mkdir(parents=True)
        private.chmod(0)
        try:
            plan = self.plan()
        finally:
            private.chmod(0o700)
        self.assertEqual(plan["verdict"], BLOCKED)
        self.assertIn("unreadable", json.dumps(plan))

    def test_hermes_skill_conflict_blocks_without_writes(self) -> None:
        self.make_skill(self.home / ".claude/skills", "collision", "claude")
        self.make_skill(self.home / ".hermes/skills", "collision", "hermes")
        plan = self.plan()
        self.assertEqual(plan["verdict"], BLOCKED)
        self.assertTrue(any(item["kind"] == "skill-entry-conflict" for item in plan["blockers"]))
        self.assertFalse((self.home / "kgm-agent-workspace").exists())

    def test_sensitive_hermes_soul_blocks_without_value_echo(self) -> None:
        secret = "sk-" + ("H" * 24)
        soul = self.home / ".hermes/SOUL.md"
        soul.parent.mkdir(parents=True)
        soul.write_text(f"api_key={secret}\n", encoding="utf-8")
        plan = self.plan()
        encoded = json.dumps(plan)
        self.assertEqual(plan["verdict"], BLOCKED)
        self.assertNotIn(secret, encoded)
        self.assertIn("openai-style-token", encoded)
        self.assertFalse((self.home / "kgm-agent-workspace").exists())

    def test_different_same_name_skills_block_without_writes(self) -> None:
        self.make_skill(self.home / ".claude/skills", "collision", "one")
        self.make_skill(self.home / ".codex/skills", "collision", "two")
        plan = self.plan()
        self.assertEqual(plan["verdict"], BLOCKED)
        self.assertTrue(any(item["kind"] == "skill-entry-conflict" for item in plan["blockers"]))
        self.assertFalse((self.home / "kgm-agent-workspace").exists())

    def test_reserved_kimi_bridge_skill_name_blocks_user_content(self) -> None:
        self.make_skill(
            self.home / ".claude/skills",
            "kgm-kimi-agent-workspace-bridge",
            "untrusted replacement",
        )
        plan = self.plan()
        self.assertEqual(plan["verdict"], BLOCKED)
        self.assertIn("reserved-skill-name", json.dumps(plan))
        self.assertFalse((self.home / "kgm-agent-workspace").exists())

    def test_kimi_bridge_catalogs_only_portable_workspace_content(self) -> None:
        plan = self.plan()
        applied = self.apply(plan)
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        root = self.home / "kgm-agent-workspace"
        bridge = root / "content/skills/kgm-kimi-agent-workspace-bridge"
        script = bridge / "scripts/workspace_catalog.py"
        self.assertTrue((bridge / "SKILL.md").is_file())
        self.assertTrue(script.is_file())
        persona = root / "content/subagent/example-persona.md"
        persona.write_text("# Example persona\n", encoding="utf-8")
        private = root / "state/private-note.md"
        private.write_text("PRIVATE_MARKER\n", encoding="utf-8")

        result = subprocess.run(
            [sys.executable, "-B", str(script), "--root", str(root), "--json"],
            text=True,
            capture_output=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema_version"], 1)
        paths = {entry["path"] for entry in payload["entries"]}
        self.assertIn("content/skills/kgm-kimi-agent-workspace-bridge/SKILL.md", paths)
        self.assertIn("content/settings/agents/AGENTS.md", paths)
        self.assertIn("content/subagent/example-persona.md", paths)
        self.assertFalse(any(path.startswith("state/") for path in paths))
        self.assertNotIn("PRIVATE_MARKER", result.stdout)

        via_environment = subprocess.run(
            [sys.executable, "-B", str(script), "--json"],
            text=True,
            capture_output=True,
            env={
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "KGM_AGENT_WORKSPACE_HOME": str(root),
            },
        )
        self.assertEqual(via_environment.returncode, 0, via_environment.stdout + via_environment.stderr)
        self.assertEqual(json.loads(via_environment.stdout), payload)

    def test_kimi_bridge_uses_default_workspace_root(self) -> None:
        plan = self.plan()
        applied = self.apply(plan)
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        root = self.home / "kgm-agent-workspace"
        script = root / "content/skills/kgm-kimi-agent-workspace-bridge/scripts/workspace_catalog.py"
        env = {**os.environ, "HOME": str(self.home), "PYTHONDONTWRITEBYTECODE": "1"}
        env.pop("KGM_AGENT_WORKSPACE_HOME", None)
        result = subprocess.run(
            [sys.executable, "-B", str(script), "--json"],
            text=True,
            capture_output=True,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("content/settings/hermes/SOUL.md", result.stdout)

    def test_kimi_bridge_rejects_sensitive_path_names(self) -> None:
        plan = self.plan()
        applied = self.apply(plan)
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        root = self.home / "kgm-agent-workspace"
        script = root / "content/skills/kgm-kimi-agent-workspace-bridge/scripts/workspace_catalog.py"
        unsafe_root = root / "content/subagent/unsafe-case"
        samples = (
            "history.txt",
            ".history/session.md",
            "receipt.json",
            "SOUL.md.backup.20260920",
            "telemetry.log",
            "device-id.txt",
            "access-token.json",
            "api-secret.txt",
            "session.json",
            "backups/old.md",
            "credentials/token.json",
            "accessToken.json",
            "apiSecret.txt",
            "credentialStore.json",
            "historyLog.txt",
            "backup2026.txt",
            "telemetryData.json",
            "sessionData.json",
            "deviceIdentifier.json",
            "device_uuid.json",
            "machine-id.json",
            "apiKey.json",
            "apikey.json",
            "privateKey.json",
            "accessKey.json",
            "sshKey.json",
            "oauth.json",
            "jwt.json",
            "bearer.json",
            "cookie.json",
            "hardwareId.json",
            "hostUuid.json",
            "systemFingerprint.json",
            "clientGuid.json",
            "deviceUid.json",
            "accesstoken.json",
            "apisecret.txt",
            "credentialstore.json",
            "historylog.txt",
            "sessiondata.json",
            "telemetrydata.json",
            "deviceid.json",
            "clientkey.json",
            "signingkey.json",
            "authtoken.json",
            "bearertoken.json",
            "serviceAccountKey.json",
            "service-account-key.json",
            "encryptionKey.json",
            "userKey.json",
            "serviceAccount.json",
            "clientCertificate.pem.json",
            "dbConnectionString.json",
        )
        for relative in samples:
            with self.subTest(relative=relative):
                target = unsafe_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("PRIVATE_MARKER\n", encoding="utf-8")
                result = subprocess.run(
                    [sys.executable, "-B", str(script), "--root", str(root), "--json"],
                    text=True,
                    capture_output=True,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("refused", result.stderr.lower())
                self.assertNotIn("PRIVATE_MARKER", result.stdout + result.stderr)
                shutil.rmtree(unsafe_root)

    def test_kimi_bridge_allows_noncredential_lookalike_names(self) -> None:
        plan = self.plan()
        applied = self.apply(plan)
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        root = self.home / "kgm-agent-workspace"
        expected = {
            "content/subagent/tokenizer-guide.md",
            "content/subagent/secretary-persona.md",
            "content/subagent/sessionization.md",
        }
        for relative in expected:
            target = root / relative
            target.write_text("# Portable persona\n", encoding="utf-8")
        script = root / "content/skills/kgm-kimi-agent-workspace-bridge/scripts/workspace_catalog.py"
        result = subprocess.run(
            [sys.executable, "-B", str(script), "--root", str(root), "--json"],
            text=True,
            capture_output=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        paths = {entry["path"] for entry in json.loads(result.stdout)["entries"]}
        self.assertTrue(expected.issubset(paths))

    def test_kimi_bridge_uses_strict_settings_and_subagent_allowlists(self) -> None:
        plan = self.plan()
        applied = self.apply(plan)
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        root = self.home / "kgm-agent-workspace"
        custom_setting = root / "content/settings/custom.md"
        custom_setting.write_text("# Not registered\n", encoding="utf-8")
        script = root / "content/skills/kgm-kimi-agent-workspace-bridge/scripts/workspace_catalog.py"
        settings = subprocess.run(
            [sys.executable, "-B", str(script), "--root", str(root), "--category", "settings", "--json"],
            text=True,
            capture_output=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(settings.returncode, 0, settings.stdout + settings.stderr)
        paths = {entry["path"] for entry in json.loads(settings.stdout)["entries"]}
        self.assertEqual(
            paths,
            {
                "content/settings/agents/AGENTS.md",
                "content/settings/claude/CLAUDE.md",
                "content/settings/codex/AGENTS.md",
                "content/settings/hermes/SOUL.md",
            },
        )
        self.assertNotIn("content/settings/custom.md", paths)
        self.assertNotIn("content/settings/claude/settings.json", paths)
        self.assertNotIn("content/settings/codex/config.toml", paths)

        unsupported = root / "content/subagent/notes.json"
        unsupported.write_text("{}\n", encoding="utf-8")
        subagents = subprocess.run(
            [sys.executable, "-B", str(script), "--root", str(root), "--category", "subagent", "--json"],
            text=True,
            capture_output=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertNotEqual(subagents.returncode, 0)
        self.assertIn("unsupported subagent file type", subagents.stderr.lower())

    def test_kimi_bridge_rejects_unreadable_directory(self) -> None:
        plan = self.plan()
        applied = self.apply(plan)
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        root = self.home / "kgm-agent-workspace"
        private = root / "content/subagent/no-read"
        private.mkdir()
        private.chmod(0)
        script = root / "content/skills/kgm-kimi-agent-workspace-bridge/scripts/workspace_catalog.py"
        try:
            result = subprocess.run(
                [sys.executable, "-B", str(script), "--root", str(root), "--json"],
                text=True,
                capture_output=True,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
        finally:
            private.chmod(0o700)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unreadable", result.stderr.lower())

    def test_kimi_bridge_rejects_special_file(self) -> None:
        plan = self.plan()
        applied = self.apply(plan)
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        root = self.home / "kgm-agent-workspace"
        fifo = root / "content/subagent/private-pipe"
        os.mkfifo(fifo)
        script = root / "content/skills/kgm-kimi-agent-workspace-bridge/scripts/workspace_catalog.py"
        result = subprocess.run(
            [sys.executable, "-B", str(script), "--root", str(root), "--json"],
            text=True,
            capture_output=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("regular file", result.stderr.lower())

    def test_kimi_bridge_rejects_symlink_escape(self) -> None:
        plan = self.plan()
        applied = self.apply(plan)
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        root = self.home / "kgm-agent-workspace"
        outside = self.base / "outside-private.md"
        outside.write_text("OUTSIDE_PRIVATE_MARKER\n", encoding="utf-8")
        (root / "content/subagent/escape.md").symlink_to(outside)
        script = root / "content/skills/kgm-kimi-agent-workspace-bridge/scripts/workspace_catalog.py"

        result = subprocess.run(
            [sys.executable, "-B", str(script), "--root", str(root), "--json"],
            text=True,
            capture_output=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlink", result.stderr.lower())
        self.assertNotIn("OUTSIDE_PRIVATE_MARKER", result.stdout + result.stderr)
        self.assertNotIn(str(outside), result.stdout + result.stderr)

    def test_kimi_bridge_rejects_allowlisted_setting_with_symlinked_parent(self) -> None:
        plan = self.plan()
        applied = self.apply(plan)
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        root = self.home / "kgm-agent-workspace"
        agents = root / "content/settings/agents"
        agents.rename(root / "content/settings/agents-original")
        outside = self.base / "outside-settings"
        outside.mkdir()
        marker = "OUTSIDE_SETTINGS_MARKER"
        (outside / "AGENTS.md").write_text(marker + "\n", encoding="utf-8")
        agents.symlink_to(outside, target_is_directory=True)
        script = root / "content/skills/kgm-kimi-agent-workspace-bridge/scripts/workspace_catalog.py"
        result = subprocess.run(
            [sys.executable, "-B", str(script), "--root", str(root), "--category", "settings", "--json"],
            text=True,
            capture_output=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlink", result.stderr.lower())
        self.assertNotIn(marker, result.stdout + result.stderr)
        self.assertNotIn(str(outside), result.stdout + result.stderr)

    def test_kimi_bridge_rejects_unknown_category(self) -> None:
        plan = self.plan()
        applied = self.apply(plan)
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        root = self.home / "kgm-agent-workspace"
        script = root / "content/skills/kgm-kimi-agent-workspace-bridge/scripts/workspace_catalog.py"
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(script),
                "--root",
                str(root),
                "--category",
                "state",
                "--json",
            ],
            text=True,
            capture_output=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid choice", result.stderr)
        self.assertNotIn("state/private", result.stdout + result.stderr)

    def test_kimi_private_data_is_left_untouched_and_uncataloged(self) -> None:
        credentials = self.home / ".kimi/credentials/account.json"
        credentials.parent.mkdir(parents=True)
        credential_key = "access_" + "token"
        original = (json.dumps({credential_key: "PRIVATE_KIMI_MARKER"}) + "\n").encode()
        credentials.write_bytes(original)
        plan = self.plan()
        self.assertTrue(
            any(
                item["provider"] == "kimi" and item["action"] == "leave-in-place"
                for item in plan["manual_review_assets"]
            )
        )
        applied = self.apply(plan)
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        self.assertEqual(credentials.read_bytes(), original)
        root = self.home / "kgm-agent-workspace"
        self.assertFalse((root / ".kimi").exists())
        script = root / "content/skills/kgm-kimi-agent-workspace-bridge/scripts/workspace_catalog.py"
        result = subprocess.run(
            [sys.executable, "-B", str(script), "--root", str(root), "--json"],
            text=True,
            capture_output=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("PRIVATE_KIMI_MARKER", result.stdout + result.stderr)
        self.assertNotIn(".kimi", result.stdout + result.stderr)

    def test_sensitive_setting_blocks_and_value_is_not_reported(self) -> None:
        secret = "sk-" + ("A" * 24)
        settings = self.home / ".claude/settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({"api_key": secret}), encoding="utf-8")
        plan = self.plan()
        encoded = json.dumps(plan)
        self.assertEqual(plan["verdict"], BLOCKED)
        self.assertNotIn(secret, encoded)
        self.assertIn("openai-style-token", encoded)

    def test_json_and_bearer_secrets_block_without_value_echo(self) -> None:
        settings = self.home / ".claude/settings.json"
        settings.parent.mkdir(parents=True)
        samples = (
            ("client_secret", "Abcdefgh1234"),
            ("access_token", "abcdefGhijk987"),
            ("authorization", "Bearer AbCdEfGhIjKlMnOp"),
        )
        for key, secret in samples:
            with self.subTest(key=key):
                if key == "authorization":
                    settings.write_text(f"Authorization: {secret}\n", encoding="utf-8")
                else:
                    settings.write_text(json.dumps({key: secret}), encoding="utf-8")
                plan = self.plan()
                encoded = json.dumps(plan)
                self.assertEqual(plan["verdict"], BLOCKED)
                self.assertNotIn(secret, encoded)
                self.assertTrue(
                    "assigned-secret" in encoded or "bearer-token" in encoded,
                    encoded,
                )

    def test_symlink_inside_skill_tree_blocks(self) -> None:
        skill = self.make_skill(self.home / ".claude/skills", "linked-skill")
        outside = self.base / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        (skill / "external.txt").symlink_to(outside)
        plan = self.plan()
        self.assertEqual(plan["verdict"], BLOCKED)
        self.assertIn("symlink", json.dumps(plan))

    def test_symlinked_host_parent_blocks_without_traversal(self) -> None:
        external = self.base / "external-claude"
        external.mkdir()
        (external / "skills").mkdir()
        (self.home / ".claude").symlink_to(external, target_is_directory=True)
        plan = self.plan()
        self.assertEqual(plan["verdict"], BLOCKED)
        self.assertIn("symlink-ancestor", json.dumps(plan))

    def test_symlinked_home_ancestor_blocks_without_writes(self) -> None:
        external = self.base / "external"
        external.mkdir()
        alias = self.base / "alias"
        alias.symlink_to(external, target_is_directory=True)
        aliased_home = alias / "home"
        (external / "home").mkdir()
        with clean_agent_workspace_env(aliased_home):
            plan = build_report(aliased_home)
        self.assertEqual(plan["verdict"], BLOCKED)
        self.assertIn("symlink-ancestor", json.dumps(plan))
        self.assertFalse((external / "home/kgm-agent-workspace").exists())

    def test_group_writable_host_parent_blocks(self) -> None:
        parent = self.home / ".claude"
        parent.mkdir()
        parent.chmod(0o775)
        plan = self.plan()
        self.assertEqual(plan["verdict"], BLOCKED)
        self.assertIn("group-writable-parent", json.dumps(plan))

    def test_untrusted_agent_workspace_root_parent_blocks_without_writes(self) -> None:
        shared = self.base / "shared"
        shared.mkdir()
        shared.chmod(0o777)
        target = shared / "kgm-agent-workspace"
        with clean_agent_workspace_env(self.home):
            os.environ["KGM_AGENT_WORKSPACE_HOME"] = str(target)
            plan = build_report(self.home)
        self.assertEqual(plan["verdict"], BLOCKED)
        self.assertIn("untrusted-root", json.dumps(plan))
        applied = self.apply(plan)
        self.assertNotEqual(applied.returncode, 0)
        self.assertFalse(target.exists())

    def test_nested_vcs_inside_skill_tree_blocks(self) -> None:
        skill = self.make_skill(self.home / ".claude/skills", "nested-vcs")
        (skill / ".git").mkdir()
        plan = self.plan()
        self.assertEqual(plan["verdict"], BLOCKED)
        self.assertIn("nested-vcs", json.dumps(plan))

    def test_world_writable_skill_file_blocks(self) -> None:
        skill = self.make_skill(self.home / ".claude/skills", "unsafe-mode")
        note = skill / "note.md"
        note.write_text("portable\n", encoding="utf-8")
        note.chmod(0o666)
        plan = self.plan()
        self.assertEqual(plan["verdict"], BLOCKED)
        self.assertIn("world-writable", json.dumps(plan))

    def test_special_file_inside_skill_tree_blocks(self) -> None:
        skill = self.make_skill(self.home / ".claude/skills", "special-file")
        os.mkfifo(skill / "pipe")
        plan = self.plan()
        self.assertEqual(plan["verdict"], BLOCKED)
        self.assertIn("special-file", json.dumps(plan))

    def test_casefold_skill_name_conflict_blocks(self) -> None:
        self.make_skill(self.home / ".claude/skills", "Example")
        self.make_skill(self.home / ".codex/skills", "example")
        plan = self.plan()
        self.assertEqual(plan["verdict"], BLOCKED)
        self.assertIn("case-or-unicode-name-conflict", json.dumps(plan))

    def test_unsupported_skill_root_entry_blocks(self) -> None:
        root = self.home / ".claude/skills"
        root.mkdir(parents=True)
        (root / "notes.txt").write_text("not a skill\n", encoding="utf-8")
        plan = self.plan()
        self.assertEqual(plan["verdict"], BLOCKED)
        self.assertIn("unsupported-skill-entry", json.dumps(plan))

    def test_private_and_manual_assets_are_reported_but_left_in_place(self) -> None:
        auth = self.home / ".codex/auth.json"
        auth.parent.mkdir(parents=True)
        auth.write_text("private", encoding="utf-8")
        agents = self.home / ".codex/agents"
        agents.mkdir(parents=True)
        plan = self.plan()
        self.assertEqual(plan["verdict"], DRY)
        self.assertTrue(any(item["action"] == "leave-in-place" for item in plan["private_assets"]))
        self.assertTrue(any(item["action"] == "leave-in-place" for item in plan["manual_review_assets"]))
        self.assertEqual(auth.read_text(), "private")

    def test_snapshot_drift_refuses_before_root_creation(self) -> None:
        claude_md = self.home / ".claude/CLAUDE.md"
        claude_md.parent.mkdir(parents=True)
        claude_md.write_text("before\n", encoding="utf-8")
        plan = self.plan()
        claude_md.write_text("after\n", encoding="utf-8")
        applied = self.apply(plan)
        self.assertNotEqual(applied.returncode, 0)
        self.assertIn("snapshot changed", applied.stderr)
        self.assertFalse((self.home / "kgm-agent-workspace").exists())

    def test_per_link_verification_rejects_late_source_drift(self) -> None:
        claude_md = self.home / ".claude/CLAUDE.md"
        claude_md.parent.mkdir(parents=True)
        claude_md.write_text("before\n", encoding="utf-8")
        plan = self.plan()
        record = next(item for item in plan["portable_assets"] if item["key"] == "claude_md")
        claude_md.write_text("late change\n", encoding="utf-8")
        with self.assertRaises(ApplyError):
            verify_live_record(plan, record)

    def test_wrong_plan_digest_refuses_before_root_creation(self) -> None:
        plan = self.plan()
        plan_path = self.write_plan(plan)
        applied = self.run_script(
            "scaffold.py",
            "--plan-file",
            str(plan_path),
            "--approve-digest",
            "0" * 64,
        )
        self.assertNotEqual(applied.returncode, 0)
        self.assertIn("approval digest does not match", applied.stderr)
        self.assertFalse((self.home / "kgm-agent-workspace").exists())

    def test_injected_failure_restores_originals_and_clears_live_root(self) -> None:
        claude_md = self.home / ".claude/CLAUDE.md"
        claude_md.parent.mkdir(parents=True)
        claude_md.write_text("original\n", encoding="utf-8")
        plan = self.plan()
        applied = self.apply(plan, "--fail-after-link", "4")
        self.assertNotEqual(applied.returncode, 0)
        self.assertTrue(claude_md.is_file())
        self.assertFalse(claude_md.is_symlink())
        self.assertEqual(claude_md.read_text(), "original\n")
        self.assertFalse((self.home / "kgm-agent-workspace").exists())
        failed = list(self.home.glob(".kgm-agent-workspace.failed.*"))
        self.assertEqual(len(failed), 1)
        journal = json.loads((failed[0] / "state/transaction.json").read_text())
        self.assertEqual(journal["status"], "ROLLED_BACK")

    def test_final_journal_failure_restores_hermes_assets(self) -> None:
        soul = self.home / ".hermes/SOUL.md"
        soul.parent.mkdir(parents=True)
        soul.write_text("# Original Hermes rules\n", encoding="utf-8")
        agents = self.home / ".hermes/agents"
        agents.mkdir()
        reviewer = agents / "reviewer.md"
        reviewer.write_text("# Original reviewer\n", encoding="utf-8")
        plan = self.plan()
        applied = self.apply(plan, "--fail-final-journal")
        self.assertNotEqual(applied.returncode, 0)
        self.assertTrue(soul.is_file())
        self.assertFalse(soul.is_symlink())
        self.assertEqual(soul.read_text(encoding="utf-8"), "# Original Hermes rules\n")
        self.assertTrue(agents.is_dir())
        self.assertFalse(agents.is_symlink())
        self.assertEqual(reviewer.read_text(encoding="utf-8"), "# Original reviewer\n")
        self.assertFalse((self.home / "kgm-agent-workspace").exists())

    def test_final_journal_failure_rolls_back_all_live_paths(self) -> None:
        claude_md = self.home / ".claude/CLAUDE.md"
        claude_md.parent.mkdir(parents=True)
        claude_md.write_text("original\n", encoding="utf-8")
        plan = self.plan()
        applied = self.apply(plan, "--fail-final-journal")
        self.assertNotEqual(applied.returncode, 0)
        self.assertTrue(claude_md.is_file())
        self.assertFalse(claude_md.is_symlink())
        self.assertEqual(claude_md.read_text(), "original\n")
        self.assertFalse((self.home / "kgm-agent-workspace").exists())
        failed = list(self.home.glob(".kgm-agent-workspace.failed.*"))
        self.assertEqual(len(failed), 1)
        journal = json.loads((failed[0] / "state/transaction.json").read_text())
        self.assertEqual(journal["status"], "ROLLED_BACK")

    def test_crash_after_backup_is_recoverable_from_write_ahead_journal(self) -> None:
        claude_md = self.home / ".claude/CLAUDE.md"
        claude_md.parent.mkdir(parents=True)
        claude_md.write_text("original\n", encoding="utf-8")
        plan = self.plan()
        crashed = self.apply(plan, "--crash-after-backup", "1")
        self.assertEqual(crashed.returncode, 88)
        root = self.home / "kgm-agent-workspace"
        self.assertTrue(root.is_dir())
        self.assertFalse(claude_md.exists())
        doctor = self.run_script("doctor.py", "--home", str(self.home))
        self.assertNotEqual(doctor.returncode, 0)
        alias = self.base / "home-alias"
        alias.symlink_to(self.home, target_is_directory=True)
        refused = self.run_script("scaffold.py", "--recover-root", str(alias / "kgm-agent-workspace"))
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("recovery root has a symlinked", refused.stderr)
        self.assertFalse(claude_md.exists())
        recovered = self.run_script("scaffold.py", "--recover-root", str(root))
        self.assertEqual(recovered.returncode, 0, recovered.stdout + recovered.stderr)
        self.assertIn("RECOVERY: ROLLED_BACK", recovered.stdout)
        self.assertTrue(claude_md.is_file())
        self.assertEqual(claude_md.read_text(), "original\n")
        self.assertFalse(root.exists())

    def test_public_export_replaces_handle_and_builds_archive(self) -> None:
        output = self.base / "public-skill"
        archive = self.base / "public-skill.zip"
        handle = self.export_handle()
        result = self.run_script(
            "export_public.py",
            "--output",
            str(output),
            "--github-handle",
            handle,
            "--archive",
            str(archive),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"Copyright (c) 2026 {handle}", (output / "LICENSE").read_text())
        placeholder = "<YOUR_" + "GITHUB_HANDLE>"
        self.assertNotIn(placeholder, (output / "LICENSE").read_text())
        self.assertTrue(archive.is_file())
        self.assertFalse((output / ".git").exists())

    def test_public_export_allows_root_git_metadata_but_does_not_copy_it(self) -> None:
        copied_skill = self.base / "copied-skill"
        shutil.copytree(SKILL_ROOT, copied_skill, ignore=shutil.ignore_patterns(".git"))
        (copied_skill / ".git").mkdir()
        (copied_skill / ".git/config").write_text("[core]\n\trepositoryformatversion = 0\n")
        output = self.base / "public-skill"
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(copied_skill / "scripts/export_public.py"),
                "--output",
                str(output),
                "--github-handle",
                self.export_handle(),
            ],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((output / ".git").exists())

    def test_public_export_excludes_runtime_caches_from_output(self) -> None:
        copied_skill = self.base / "copied-skill"
        shutil.copytree(SKILL_ROOT, copied_skill, ignore=shutil.ignore_patterns(".git"))
        (copied_skill / "scripts/__pycache__").mkdir(exist_ok=True)
        (copied_skill / "scripts/__pycache__/module.pyc").write_bytes(b"runtime cache")
        (copied_skill / "tests/.pytest_cache").mkdir(exist_ok=True)
        (copied_skill / "tests/.pytest_cache/state").write_text("runtime cache\n", encoding="utf-8")
        (copied_skill / "references/.DS_Store").write_bytes(b"runtime cache")
        (copied_skill / ".pytest_cache").mkdir(exist_ok=True)
        (copied_skill / ".pytest_cache/state").write_text("runtime cache\n", encoding="utf-8")
        output = self.base / "public-skill"
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(copied_skill / "scripts/export_public.py"),
                "--output",
                str(output),
                "--github-handle",
                self.export_handle(),
            ],
            text=True,
            capture_output=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(any(path.name in {"__pycache__", ".pytest_cache", ".DS_Store"} for path in output.rglob("*")))
        self.assertFalse(any(path.suffix == ".pyc" for path in output.rglob("*")))

    def test_public_export_blocks_cache_named_symlink(self) -> None:
        copied_skill = self.base / "copied-skill"
        shutil.copytree(SKILL_ROOT, copied_skill, ignore=shutil.ignore_patterns(".git"))
        outside = self.base / "outside.txt"
        outside.write_text("benign external text\n", encoding="utf-8")
        (copied_skill / "references/evil.pyc").symlink_to(outside)
        output = self.base / "public-skill"
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(copied_skill / "scripts/export_public.py"),
                "--output",
                str(output),
                "--github-handle",
                self.export_handle(),
            ],
            text=True,
            capture_output=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source package scan failed", result.stdout)
        self.assertFalse(output.exists())

    def test_public_export_blocks_root_cache_named_symlink(self) -> None:
        copied_skill = self.base / "copied-skill"
        shutil.copytree(SKILL_ROOT, copied_skill, ignore=shutil.ignore_patterns(".git"))
        outside = self.base / "outside"
        outside.mkdir()
        (copied_skill / ".pytest_cache").symlink_to(outside, target_is_directory=True)
        output = self.base / "public-skill"
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(copied_skill / "scripts/export_public.py"),
                "--output",
                str(output),
                "--github-handle",
                self.export_handle(),
            ],
            text=True,
            capture_output=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unexpected top-level paths: .pytest_cache", result.stdout)
        self.assertFalse(output.exists())

    def test_public_export_blocks_group_writable_runtime_caches(self) -> None:
        for index, relative in enumerate((Path(".pytest_cache"), Path("references/__pycache__")), start=1):
            with self.subTest(relative=relative.as_posix()):
                copied_skill = self.base / f"copied-skill-{index}"
                shutil.copytree(SKILL_ROOT, copied_skill, ignore=shutil.ignore_patterns(".git"))
                cache = copied_skill / relative
                cache.mkdir(parents=True, exist_ok=True)
                cache.chmod(0o775)
                output = self.base / f"public-skill-{index}"
                result = subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        str(copied_skill / "scripts/export_public.py"),
                        "--output",
                        str(output),
                        "--github-handle",
                        self.export_handle(),
                    ],
                    text=True,
                    capture_output=True,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(output.exists())

    def test_public_export_blocks_cache_directory_names_used_as_files(self) -> None:
        for index, relative in enumerate((Path(".pytest_cache"), Path("references/__pycache__")), start=1):
            with self.subTest(relative=relative.as_posix()):
                copied_skill = self.base / f"copied-skill-file-{index}"
                shutil.copytree(SKILL_ROOT, copied_skill, ignore=shutil.ignore_patterns(".git"))
                artifact = copied_skill / relative
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_text("not a cache directory\n", encoding="utf-8")
                output = self.base / f"public-skill-file-{index}"
                result = subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        str(copied_skill / "scripts/export_public.py"),
                        "--output",
                        str(output),
                        "--github-handle",
                        self.export_handle(),
                    ],
                    text=True,
                    capture_output=True,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(output.exists())

    def test_public_export_blocks_git_metadata_inside_allowlisted_content(self) -> None:
        copied_skill = self.base / "copied-skill"
        shutil.copytree(SKILL_ROOT, copied_skill, ignore=shutil.ignore_patterns(".git"))
        (copied_skill / "references/.git").mkdir()
        output = self.base / "public-skill"
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(copied_skill / "scripts/export_public.py"),
                "--output",
                str(output),
                "--github-handle",
                self.export_handle(),
            ],
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source package scan failed: nested-vcs", result.stdout)
        self.assertFalse(output.exists())

    def test_public_export_is_idempotent_for_existing_handle_only(self) -> None:
        handle = self.export_handle()
        first = self.base / "signed-source"
        initial = self.run_script("export_public.py", "--output", str(first), "--github-handle", handle)
        self.assertEqual(initial.returncode, 0, initial.stdout + initial.stderr)
        second = self.base / "same-owner"
        matching = subprocess.run(
            [sys.executable, "-B", str(first / "scripts/export_public.py"), "--output", str(second), "--github-handle", handle],
            text=True,
            capture_output=True,
        )
        self.assertEqual(matching.returncode, 0, matching.stdout + matching.stderr)
        mismatch = subprocess.run(
            [sys.executable, "-B", str(first / "scripts/export_public.py"), "--output", str(self.base / "other-owner"), "--github-handle", "Different-Owner"],
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(mismatch.returncode, 0)
        self.assertIn("existing handle differs", mismatch.stdout)

    def test_public_export_blocks_nested_source_symlink_without_output(self) -> None:
        copied_skill = self.base / "copied-skill"
        shutil.copytree(SKILL_ROOT, copied_skill)
        outside = self.base / "outside.txt"
        outside.write_text("benign external text\n", encoding="utf-8")
        (copied_skill / "references/external.txt").symlink_to(outside)
        output = self.base / "public-skill"
        archive = self.base / "public-skill.zip"
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(copied_skill / "scripts/export_public.py"),
                "--output",
                str(output),
                "--github-handle",
                "Example-Owner",
                "--archive",
                str(archive),
            ],
            text=True,
            capture_output=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source package scan failed: symlink", result.stdout)
        self.assertFalse(output.exists())
        self.assertFalse(archive.exists())

    def test_public_export_rejects_output_or_archive_inside_source(self) -> None:
        copied_skill = self.base / "copied-skill"
        shutil.copytree(SKILL_ROOT, copied_skill)
        internal_output = copied_skill / "assets/release"
        first = subprocess.run(
            [
                sys.executable,
                "-B",
                str(copied_skill / "scripts/export_public.py"),
                "--output",
                str(internal_output),
                "--github-handle",
                "Example-Owner",
            ],
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(first.returncode, 0)
        self.assertFalse(internal_output.exists())
        external_output = self.base / "public-skill"
        second = subprocess.run(
            [
                sys.executable,
                "-B",
                str(copied_skill / "scripts/export_public.py"),
                "--output",
                str(external_output),
                "--github-handle",
                "Example-Owner",
                "--archive",
                str(external_output / "release.zip"),
            ],
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(second.returncode, 0)
        self.assertFalse(external_output.exists())

    def test_public_export_rejects_untrusted_output_parent(self) -> None:
        unsafe_parent = self.base / "shared"
        unsafe_parent.mkdir()
        unsafe_parent.chmod(0o777)
        output = unsafe_parent / "public-skill"
        result = self.run_script(
            "export_public.py",
            "--output",
            str(output),
            "--github-handle",
            "Example-Owner",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("output parent is group/world writable", result.stdout)
        self.assertFalse(output.exists())

    def test_public_export_rejects_symlinked_output_ancestor(self) -> None:
        external = self.base / "external-output"
        external.mkdir()
        alias = self.base / "output-alias"
        alias.symlink_to(external, target_is_directory=True)
        output = alias / "public-skill"
        result = self.run_script(
            "export_public.py",
            "--output",
            str(output),
            "--github-handle",
            "Example-Owner",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("output ancestor is a symlink", result.stdout)
        self.assertFalse((external / "public-skill").exists())

    def test_link_local_refuses_unplanned_existing_content(self) -> None:
        plan = self.plan()
        applied = self.apply(plan)
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        target = self.home / ".claude/CLAUDE.md"
        target.unlink()
        target.write_text("keep me\n", encoding="utf-8")
        earlier = self.home / ".claude/skills"
        earlier.unlink()
        link_script = self.home / "kgm-agent-workspace/control/bin/link-local.sh"
        result = subprocess.run(
            ["bash", str(link_script)],
            text=True,
            capture_output=True,
            env={**os.environ, "HOME": str(self.home)},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(earlier.exists())
        self.assertFalse(earlier.is_symlink())
        self.assertFalse(target.is_symlink())
        self.assertEqual(target.read_text(), "keep me\n")

    def test_link_local_rejects_group_writable_parent_without_partial_links(self) -> None:
        plan = self.plan()
        applied = self.apply(plan)
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        first = self.home / ".claude/skills"
        first.unlink()
        (self.home / ".claude").chmod(0o775)
        link_script = self.home / "kgm-agent-workspace/control/bin/link-local.sh"
        result = subprocess.run(
            ["bash", str(link_script)],
            text=True,
            capture_output=True,
            env={**os.environ, "HOME": str(self.home)},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("group/world writable", result.stderr)
        self.assertFalse(first.exists())
        self.assertFalse(first.is_symlink())


if __name__ == "__main__":
    unittest.main()
