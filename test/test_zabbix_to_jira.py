import tempfile
import unittest
from pathlib import Path

import zabbix_to_jira


class FakeIssue:
    """Small Jira issue stub used by the unit tests."""

    def __init__(self, key: str) -> None:
        """Store the synthetic issue key returned by the fake Jira client."""
        self.key = key


class FakeJiraClient:
    """In-memory Jira client double that records calls instead of using the network."""

    def __init__(self) -> None:
        """Initialize call logs and default fake Jira responses."""
        self.created_issues = []
        self.attachments = []
        self.comments = []
        self.transitions_called = []
        self.search_result = {"issues": []}
        self.transition_options = [{"name": "Done", "id": "31"}]
        self.search_queries = []

    def create_issue(self, fields):
        """Simulate Jira issue creation and record the payload."""
        self.created_issues.append(fields)
        return FakeIssue("ZTJ-101")

    def add_attachment(self, issue, attachment):
        """Record attachment uploads without sending bytes anywhere."""
        self.attachments.append((issue, getattr(attachment, "name", "")))

    def add_comment(self, issue, comment):
        """Record recovery comments for later assertions."""
        self.comments.append((issue, comment))

    def transitions(self, issue):
        """Return the configured transition list for the fake issue."""
        return self.transition_options

    def transition_issue(self, issue_key, transition_id):
        """Record Jira transition requests."""
        self.transitions_called.append((issue_key, transition_id))

    def search_issues(self, query, json_result=True):
        """Return the preset search result used by cleanup tests."""
        self.search_queries.append((query, json_result))
        return self.search_result


class FakeZabbixClient:
    """Zabbix client double that can create a local graph file on demand."""

    def __init__(self, graph_bytes: bytes | None = b"image") -> None:
        """Store the graph payload and login state used by the tests."""
        self.graph_bytes = graph_bytes
        self.logged_in = False
        self.download_requests = []

    def login(self) -> None:
        """Pretend to authenticate successfully."""
        self.logged_in = True

    def download_graph(self, graph: zabbix_to_jira.GraphSettings, tmp_dir: Path):
        """Create a small placeholder graph image or simulate a missing graph."""
        self.download_requests.append((graph, tmp_dir))
        if self.graph_bytes is None:
            return None
        image_path = tmp_dir / f"{graph.itemid}.png"
        image_path.write_bytes(self.graph_bytes)
        return image_path


class ZabbixToJiraTests(unittest.TestCase):
    """Coverage for parsing, repository behavior, and the main alert workflow."""

    def setUp(self) -> None:
        """Build a reusable config object for isolated tests."""
        self.config = zabbix_to_jira.AppConfig(
            jira_server="https://jira.example.com",
            jira_user="alert-bot",
            jira_pass="secret",
            jira_transition="Done",
            jira_project="ZTJ",
            jira_issue_type="Incident",
            jira_verify=True,
            zbx_server="https://zabbix.example.com/zabbix",
            zbx_user="zabbix-user",
            zbx_password="zabbix-pass",
            zbx_prefix="ztj",
            zbx_tmp_dir="tmp",
            zbx_api_verify=True,
            http_timeout_seconds=30,
        )

    def make_config(self, tmp_dir: str) -> zabbix_to_jira.AppConfig:
        """Create a config bound to a temporary directory for isolated file writes."""
        return self.config.__class__(**{**self.config.__dict__, "zbx_tmp_dir": str(Path(tmp_dir) / "tmp")})

    def make_processor(
        self,
        tmp_dir: str,
        *,
        jira_client: FakeJiraClient | None = None,
        zabbix_client: FakeZabbixClient | None = None,
    ) -> tuple[zabbix_to_jira.AlertProcessor, zabbix_to_jira.IssueRepository, FakeJiraClient, FakeZabbixClient]:
        """Build a fully wired processor with fake external services and a real SQLite repository."""
        config = self.make_config(tmp_dir)
        repository = zabbix_to_jira.IssueRepository(Path(tmp_dir) / "zabbix-jira.db")
        jira_client = jira_client or FakeJiraClient()
        zabbix_client = zabbix_client or FakeZabbixClient()
        processor = zabbix_to_jira.AlertProcessor(
            config=config,
            repository=repository,
            jira_service=zabbix_to_jira.JiraService(jira_client, config),
            zabbix_client=zabbix_client,
        )
        return processor, repository, jira_client, zabbix_client

    def test_parse_zabbix_body_merges_graph_settings(self):
        """Parsing should separate body text from JSON metadata and apply overrides."""
        body = (
            '{"ztj": {"graphs": [{"triggerid": "123"}, {"itemid": "456"}, '
            '{"priority": "High"}, {"title": "CPU load"}]}}\n'
            "line 1\n"
            "line 2\n"
        )

        settings, body_lines = zabbix_to_jira.parse_zabbix_body(body, "ztj")

        self.assertEqual("123", settings["triggerid"])
        self.assertEqual("456", settings["itemid"])
        self.assertEqual("High", settings["priority"])
        self.assertEqual(["line 1", "line 2"], body_lines)

    def test_parse_zabbix_body_preserves_blank_lines_and_last_override(self):
        """Parsing should preserve body spacing and let later graph entries override earlier values."""
        body = (
            '{"ztj": {"graphs": [{"title": "Old title"}, {"title": "New title"}, {"triggerid": "999"}]}}\n'
            "\n"
            "line 1\n"
            "\n"
            "line 2\n"
        )

        settings, body_lines = zabbix_to_jira.parse_zabbix_body(body, "ztj")

        self.assertEqual("New title", settings["title"])
        self.assertEqual("999", settings["triggerid"])
        self.assertEqual(["", "line 1", "", "line 2"], body_lines)

    def test_parse_alert_payload_raises_when_body_argument_is_missing(self):
        """The CLI parser should reject runs without the Zabbix message body."""
        with self.assertRaisesRegex(zabbix_to_jira.PayloadError, "sys.argv\\[2\\]"):
            zabbix_to_jira.parse_alert_payload(["script.py", "Alert subject"], self.config)

    def test_parse_alert_payload_raises_for_invalid_json_payload(self):
        """Malformed metadata should fail fast instead of silently producing bad Jira issues."""
        body = '{"ztj": {"graphs": [invalid json]}}\nProblem details'

        with self.assertRaisesRegex(zabbix_to_jira.PayloadError, "invalid JSON metadata"):
            zabbix_to_jira.parse_alert_payload(["script.py", "Alert subject", body], self.config)

    def test_repository_prune_closed_issues_only_deletes_stale_rows(self):
        """SQLite cleanup should preserve mappings for still-open Jira issues."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            repository = zabbix_to_jira.IssueRepository(Path(tmp_dir) / "zabbix-jira.db")
            repository.initialize()
            repository.store_issue_key(1, "ZTJ-1")
            repository.store_issue_key(2, "ZTJ-2")

            deleted = repository.prune_closed_issues({"ZTJ-2"})

        self.assertEqual(["ZTJ-1"], deleted)

    def test_run_creates_issue_and_attaches_graph_for_problem_event(self):
        """Problem events should create an issue, attach a graph, and store the mapping."""
        body = (
            '{"ztj": {"graphs": [{"triggerid": "321"}, {"itemid": "654"}, '
            '{"priority": "High"}, {"title": "CPU load"}]}}\n'
            "Problem details"
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            processor, repository, jira_client, zabbix_client = self.make_processor(tmp_dir)
            jira_client.search_result = {"issues": [{"key": "ZTJ-101"}]}
            exit_code = zabbix_to_jira.run(
                ["script.py", "Alert subject", body],
                config=self.make_config(tmp_dir),
                processor=processor,
            )

            stored_key = repository.get_issue_key(321)

        self.assertEqual(0, exit_code)
        self.assertEqual("ZTJ-101", stored_key)
        self.assertTrue(zabbix_client.logged_in)
        self.assertEqual("Alert subject", jira_client.created_issues[0]["summary"])
        self.assertEqual({"id": "2"}, jira_client.created_issues[0]["priority"])
        self.assertEqual("ZTJ-101", jira_client.attachments[0][0])
        self.assertEqual(1, len(jira_client.search_queries))
        self.assertIn('reporter="alert-bot"', jira_client.search_queries[0][0])

    def test_run_creates_issue_without_attachment_when_graph_download_fails(self):
        """Problem events should still store the issue mapping when the graph cannot be downloaded."""
        body = (
            '{"ztj": {"graphs": [{"triggerid": "654"}, {"itemid": "321"}, {"priority": "Warning"}]}}\n'
            "Problem details"
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            processor, repository, jira_client, _ = self.make_processor(
                tmp_dir,
                zabbix_client=FakeZabbixClient(graph_bytes=None),
            )
            jira_client.search_result = {"issues": [{"key": "ZTJ-101"}]}
            exit_code = zabbix_to_jira.run(
                ["script.py", "Alert subject", body],
                config=self.make_config(tmp_dir),
                processor=processor,
            )

            stored_key = repository.get_issue_key(654)

        self.assertEqual(0, exit_code)
        self.assertEqual("ZTJ-101", stored_key)
        self.assertEqual([], jira_client.attachments)

    def test_run_comments_and_closes_issue_for_recovery_event(self):
        """Recovery events should comment, transition, and remove the stored mapping."""
        body = (
            '{"ztj": {"graphs": [{"triggerid": "321"}, {"ok": "1"}]}}\n'
            "Resolved details"
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            processor, repository, jira_client, _ = self.make_processor(tmp_dir)
            jira_client.search_result = {"issues": []}
            repository.initialize()
            repository.store_issue_key(321, "ZTJ-101")

            exit_code = zabbix_to_jira.run(
                ["script.py", "Alert subject", body],
                config=self.make_config(tmp_dir),
                processor=processor,
            )

            stored_key = repository.get_issue_key(321)

        self.assertEqual(0, exit_code)
        self.assertIsNone(stored_key)
        self.assertEqual([("ZTJ-101", "Resolved details")], jira_client.comments)
        self.assertEqual([("ZTJ-101", "31")], jira_client.transitions_called)

    def test_run_raises_when_transition_is_missing(self):
        """Recovery events should fail loudly when the configured Jira transition does not exist."""
        body = (
            '{"ztj": {"graphs": [{"triggerid": "321"}, {"ok": "1"}]}}\n'
            "Resolved details"
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            processor, repository, jira_client, _ = self.make_processor(tmp_dir)
            jira_client.transition_options = []
            repository.initialize()
            repository.store_issue_key(321, "ZTJ-101")

            with self.assertRaisesRegex(zabbix_to_jira.TransitionNotFoundError, "transition 'Done' not found"):
                zabbix_to_jira.run(
                    ["script.py", "Alert subject", body],
                    config=self.make_config(tmp_dir),
                    processor=processor,
                )

    def test_run_skips_cleanup_network_request_when_sqlite_is_empty(self):
        """Runs without local mappings should not issue the cleanup Jira search request."""
        body = (
            '{"ztj": {"graphs": [{"triggerid": "321"}, {"ok": "1"}]}}\n'
            "Resolved details"
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            processor, _, jira_client, _ = self.make_processor(tmp_dir)
            exit_code = zabbix_to_jira.run(
                ["script.py", "Alert subject", body],
                config=self.make_config(tmp_dir),
                processor=processor,
            )

        self.assertEqual(0, exit_code)
        self.assertEqual([], jira_client.search_queries)


if __name__ == "__main__":
    unittest.main()
