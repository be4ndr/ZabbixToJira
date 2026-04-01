#!/usr/bin/env python3

"""Production-oriented Zabbix to Jira alert handler."""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import requests
from jira import JIRA


LOGGER = logging.getLogger("zabbix_to_jira")
DATABASE_NAME = "zabbix-jira.db"
DEFAULT_HTTP_TIMEOUT_SECONDS = 30
OPEN_ISSUE_STATUSES = ("Waiting for support", "In Progress", "Pending")
DEFAULT_GRAPH_SETTINGS = {
    "itemid": "0",
    "triggerid": "0",
    "ok": "0",
    "priority": None,
    "title": None,
    "graphs_period": "3600",
    "graphs_width": "900",
    "graphs_height": "200",
}
PRIORITY_IDS = {
    "Not classified": "5",
    "Information": "5",
    "Warning": "4",
    "Average": "3",
    "High": "2",
    "Disaster": "1",
}


class ApplicationError(Exception):
    """Base exception for controlled application failures."""


class ConfigurationError(ApplicationError):
    """Raised when runtime configuration is missing or invalid."""


class PayloadError(ApplicationError):
    """Raised when the incoming Zabbix payload is malformed."""


class ExternalServiceError(ApplicationError):
    """Raised when Jira or Zabbix interactions fail."""


class TransitionNotFoundError(ApplicationError):
    """Raised when the configured Jira transition cannot be resolved."""


@dataclass(frozen=True)
class AppConfig:
    """Validated runtime configuration for the Jira and Zabbix integrations."""

    jira_server: str
    jira_user: str
    jira_pass: str
    jira_transition: str
    jira_project: str
    jira_issue_type: str
    jira_verify: bool
    zbx_server: str
    zbx_user: str
    zbx_password: str
    zbx_prefix: str
    zbx_tmp_dir: str
    zbx_api_verify: bool = True
    http_timeout_seconds: int = DEFAULT_HTTP_TIMEOUT_SECONDS

    @classmethod
    def from_module(cls, module_name: str = "ztj_config") -> "AppConfig":
        """Load configuration from the user module or the default fallback."""
        try:
            module = import_module(module_name)
        except ModuleNotFoundError:
            module = import_module("ztj_config_default")

        config = cls(
            jira_server=module.jira_server,
            jira_user=module.jira_user,
            jira_pass=module.jira_pass,
            jira_transition=module.jira_transition,
            jira_project=module.jira_project,
            jira_issue_type=module.jira_issue_type,
            jira_verify=module.jira_verify,
            zbx_server=module.zbx_server,
            zbx_user=module.zbx_user,
            zbx_password=module.zbx_password,
            zbx_prefix=module.zbx_prefix,
            zbx_tmp_dir=module.zbx_tmp_dir,
            zbx_api_verify=getattr(module, "zbx_api_verify", True),
            http_timeout_seconds=getattr(module, "http_timeout_seconds", DEFAULT_HTTP_TIMEOUT_SECONDS),
        )
        config.validate()
        return config

    def validate(self) -> None:
        """Validate that the configuration contains the required production values."""
        required_values = {
            "jira_server": self.jira_server,
            "jira_user": self.jira_user,
            "jira_pass": self.jira_pass,
            "jira_transition": self.jira_transition,
            "jira_project": self.jira_project,
            "jira_issue_type": self.jira_issue_type,
            "zbx_server": self.zbx_server,
            "zbx_user": self.zbx_user,
            "zbx_password": self.zbx_password,
            "zbx_prefix": self.zbx_prefix,
            "zbx_tmp_dir": self.zbx_tmp_dir,
        }
        missing_keys = [key for key, value in required_values.items() if not str(value).strip()]
        if missing_keys:
            raise ConfigurationError(f"missing required config values: {', '.join(sorted(missing_keys))}")
        if self.http_timeout_seconds <= 0:
            raise ConfigurationError("http_timeout_seconds must be greater than zero")


@dataclass(frozen=True)
class GraphSettings:
    """Normalized graph-related metadata extracted from the Zabbix payload."""

    itemid: str
    triggerid: int
    ok: bool
    priority: str | None
    title: str | None
    graphs_period: str
    graphs_width: str
    graphs_height: str

    @classmethod
    def from_dict(cls, raw_settings: dict[str, str | None]) -> "GraphSettings":
        """Build a typed graph settings object from raw parsed values."""
        trigger_id_value = raw_settings.get("triggerid") or "0"
        try:
            triggerid = int(trigger_id_value)
        except ValueError as exc:
            raise PayloadError(f"invalid trigger id: {trigger_id_value!r}") from exc

        ok_flag = raw_settings.get("ok") or "0"
        return cls(
            itemid=raw_settings.get("itemid") or DEFAULT_GRAPH_SETTINGS["itemid"],
            triggerid=triggerid,
            ok=ok_flag == "1",
            priority=raw_settings.get("priority"),
            title=raw_settings.get("title"),
            graphs_period=raw_settings.get("graphs_period") or DEFAULT_GRAPH_SETTINGS["graphs_period"],
            graphs_width=raw_settings.get("graphs_width") or DEFAULT_GRAPH_SETTINGS["graphs_width"],
            graphs_height=raw_settings.get("graphs_height") or DEFAULT_GRAPH_SETTINGS["graphs_height"],
        )


@dataclass(frozen=True)
class AlertPayload:
    """Parsed Zabbix alert input ready for issue processing."""

    subject: str
    body_lines: tuple[str, ...]
    graph: GraphSettings

    @property
    def body(self) -> str:
        """Join body lines into the final Jira comment or description."""
        return "\n".join(self.body_lines)


class JiraClientProtocol(Protocol):
    """Protocol describing the Jira methods used by the processor."""

    def create_issue(self, fields: dict[str, Any]) -> Any:
        """Create a Jira issue."""

    def add_attachment(self, issue: str, attachment: Any) -> Any:
        """Attach a file to a Jira issue."""

    def add_comment(self, issue: str, comment: str) -> Any:
        """Add a comment to a Jira issue."""

    def transitions(self, issue: str) -> list[dict[str, str]]:
        """List transitions available for a Jira issue."""

    def transition_issue(self, issue: str, transition_id: str) -> Any:
        """Transition a Jira issue."""

    def search_issues(self, query: str, json_result: bool = True) -> dict[str, Any]:
        """Search Jira issues using JQL."""


class ZabbixClient:
    """HTTP client for logging into Zabbix and downloading graph images."""

    def __init__(
        self,
        server: str,
        username: str,
        password: str,
        *,
        verify: bool,
        timeout_seconds: int,
        session: requests.Session | None = None,
    ) -> None:
        """Initialize the client with a reusable HTTP session."""
        self.server = server.rstrip("/")
        self.username = username
        self.password = password
        self.verify = verify
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()
        self.cookies: requests.cookies.RequestsCookieJar | None = None

    def login(self) -> None:
        """Authenticate against the Zabbix UI and persist the returned cookies."""
        try:
            response = self.session.post(
                f"{self.server}/",
                data={"name": self.username, "password": self.password, "enter": "Sign in"},
                verify=self.verify,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ExternalServiceError(f"failed to authenticate with Zabbix at {self.server}") from exc

        if len(response.history) > 1 and response.history[0].status_code == 302:
            LOGGER.warning(
                "Zabbix server may be missing the full path; expected something like '%s/zabbix'",
                self.server,
            )
        if not response.cookies:
            raise ExternalServiceError(f"authorization failed for Zabbix at {self.server}")
        self.cookies = response.cookies

    def download_graph(self, graph: GraphSettings, tmp_dir: Path) -> Path | None:
        """Download the alert graph to a temporary file for later Jira attachment."""
        if self.cookies is None:
            raise ExternalServiceError("zabbix login must be completed before requesting a graph")

        graph_url = self._build_graph_url(graph)
        target_file = tmp_dir / f"{graph.itemid}.png"

        try:
            response = self.session.get(
                graph_url,
                cookies=self.cookies,
                verify=self.verify,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise ExternalServiceError(f"failed to download graph from {graph_url}") from exc

        if response.status_code == 404:
            LOGGER.warning("graph image not found at %s", graph_url)
            return None

        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ExternalServiceError(f"failed to download graph from {graph_url}") from exc

        target_file.write_bytes(response.content)
        return target_file

    def _build_graph_url(self, graph: GraphSettings) -> str:
        """Construct the Zabbix graph endpoint URL for the current alert."""
        encoded_title = quote(graph.title or "")
        return (
            f"{self.server}/chart3.php?period={graph.graphs_period}&name={encoded_title}"
            f"&width={graph.graphs_width}&height={graph.graphs_height}&graphtype=0&legend=1"
            f"&items[0][itemid]={graph.itemid}&items[0][sortorder]=0"
            "&items[0][drawtype]=5&items[0][color]=00CC00"
        )


class JiraService:
    """Service wrapper around the Jira client used by the alert processor."""

    def __init__(self, client: JiraClientProtocol, config: AppConfig) -> None:
        """Store the Jira client and static Jira project configuration."""
        self.client = client
        self.config = config

    def create_issue(self, subject: str, description: str, priority_id: str) -> str:
        """Create a Jira issue for a new problem event and return its key."""
        issue = self.client.create_issue(
            fields={
                "project": {"key": self.config.jira_project},
                "summary": subject,
                "description": description,
                "issuetype": {"name": self.config.jira_issue_type},
                "priority": {"id": priority_id},
            }
        )
        return str(issue.key)

    def add_attachment(self, issue_key: str, attachment_path: Path) -> None:
        """Attach the graph file to the newly created Jira issue."""
        with attachment_path.open("rb") as attachment_file:
            self.client.add_attachment(issue=issue_key, attachment=attachment_file)

    def add_comment(self, issue_key: str, comment: str) -> None:
        """Add a Jira comment for a recovery event."""
        self.client.add_comment(issue_key, comment)

    def close_issue(self, issue_key: str) -> None:
        """Resolve a Jira issue using the configured transition name."""
        transition_id = self._resolve_transition_id(issue_key)
        if transition_id is None:
            raise TransitionNotFoundError(
                f"transition '{self.config.jira_transition}' not found for issue {issue_key}"
            )
        self.client.transition_issue(issue_key, transition_id)

    def get_open_issue_keys(self) -> set[str]:
        """Fetch the currently open Jira issues reported by the configured user."""
        status_query = " OR ".join(f"status='{status}'" for status in OPEN_ISSUE_STATUSES)
        escaped_user = self.config.jira_user.replace('"', '\\"')
        query = f"({status_query}) AND reporter=\"{escaped_user}\""
        result = self.client.search_issues(query, json_result=True)
        return {issue["key"] for issue in result.get("issues", []) if issue.get("key")}

    def _resolve_transition_id(self, issue_key: str) -> str | None:
        """Find the configured transition id for a Jira issue."""
        for transition in self.client.transitions(issue_key):
            if transition["name"] == self.config.jira_transition:
                return transition["id"]
        return None


class IssueRepository:
    """SQLite repository that stores Zabbix trigger to Jira issue mappings."""

    def __init__(self, database_path: str | Path) -> None:
        """Initialize the repository with a file-backed SQLite database path."""
        self.database_path = str(database_path)

    def initialize(self) -> None:
        """Create the required schema if it does not already exist."""
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS issues ("
                "zbx_trigger_id INTEGER PRIMARY KEY, "
                "jira_issue_id TEXT NOT NULL)"
            )
            connection.commit()

    def get_issue_key(self, trigger_id: int) -> str | None:
        """Return the Jira issue currently mapped to the given trigger id."""
        with closing(sqlite3.connect(self.database_path)) as connection:
            row = connection.execute(
                "SELECT jira_issue_id FROM issues WHERE zbx_trigger_id = ?",
                (trigger_id,),
            ).fetchone()
        return None if row is None else str(row[0])

    def store_issue_key(self, trigger_id: int, issue_key: str) -> None:
        """Upsert a trigger to Jira issue mapping."""
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO issues (zbx_trigger_id, jira_issue_id) VALUES (?, ?)",
                (trigger_id, issue_key),
            )
            connection.commit()

    def delete_issue_key(self, trigger_id: int) -> None:
        """Delete the stored mapping for a resolved trigger."""
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute("DELETE FROM issues WHERE zbx_trigger_id = ?", (trigger_id,))
            connection.commit()

    def has_mappings(self) -> bool:
        """Return whether any trigger mappings exist locally."""
        with closing(sqlite3.connect(self.database_path)) as connection:
            row = connection.execute("SELECT 1 FROM issues LIMIT 1").fetchone()
        return row is not None

    def prune_closed_issues(self, open_issue_keys: set[str]) -> list[str]:
        """Remove local mappings that no longer correspond to open Jira issues."""
        with closing(sqlite3.connect(self.database_path)) as connection:
            rows = connection.execute("SELECT jira_issue_id FROM issues").fetchall()
            stale_issue_keys = [str(row[0]) for row in rows if row[0] not in open_issue_keys]
            if not stale_issue_keys:
                return []

            placeholders = ", ".join("?" for _ in stale_issue_keys)
            connection.execute(
                f"DELETE FROM issues WHERE jira_issue_id IN ({placeholders})",
                stale_issue_keys,
            )
            connection.commit()
        return stale_issue_keys


class AlertProcessor:
    """Application service that handles one Zabbix alert end to end."""

    def __init__(
        self,
        config: AppConfig,
        repository: IssueRepository,
        jira_service: JiraService,
        zabbix_client: ZabbixClient,
    ) -> None:
        """Store dependencies needed to process an alert."""
        self.config = config
        self.repository = repository
        self.jira_service = jira_service
        self.zabbix_client = zabbix_client

    def process(self, payload: AlertPayload) -> int:
        """Process a problem or recovery alert and keep local state synchronized."""
        self.repository.initialize()

        existing_issue_key = self.repository.get_issue_key(payload.graph.triggerid)
        if existing_issue_key is None and not payload.graph.ok:
            self._handle_problem(payload)
        elif existing_issue_key is not None and payload.graph.ok:
            self._handle_recovery(payload, existing_issue_key)
        else:
            LOGGER.info(
                "no issue action required for trigger_id=%s existing_issue=%s ok=%s",
                payload.graph.triggerid,
                existing_issue_key,
                payload.graph.ok,
            )

        if self.repository.has_mappings():
            stale_issue_keys = self.repository.prune_closed_issues(self.jira_service.get_open_issue_keys())
            if stale_issue_keys:
                LOGGER.info("removed stale sqlite mappings for issues: %s", stale_issue_keys)

        return 0

    def _handle_problem(self, payload: AlertPayload) -> None:
        """Create a Jira issue, attach the graph, and persist the trigger mapping."""
        issue_key = self.jira_service.create_issue(
            subject=payload.subject,
            description=payload.body,
            priority_id=get_priority_id(payload.graph.priority),
        )
        LOGGER.info("created Jira issue %s for trigger_id=%s", issue_key, payload.graph.triggerid)

        graph_path: Path | None = None
        tmp_dir = Path(self.config.zbx_tmp_dir)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.zabbix_client.login()
            graph_path = self.zabbix_client.download_graph(payload.graph, tmp_dir)
            if graph_path is not None:
                self.jira_service.add_attachment(issue_key, graph_path)
            else:
                LOGGER.warning("graph could not be downloaded for trigger_id=%s", payload.graph.triggerid)
        finally:
            if graph_path is not None:
                graph_path.unlink(missing_ok=True)

        self.repository.store_issue_key(payload.graph.triggerid, issue_key)

    def _handle_recovery(self, payload: AlertPayload, issue_key: str) -> None:
        """Comment on and close the Jira issue mapped to the recovered trigger."""
        self.jira_service.add_comment(issue_key, payload.body)
        self.jira_service.close_issue(issue_key)
        self.repository.delete_issue_key(payload.graph.triggerid)
        LOGGER.info("closed Jira issue %s for trigger_id=%s", issue_key, payload.graph.triggerid)


def configure_logging() -> None:
    """Configure application logging once for CLI execution."""
    if LOGGER.handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )


def get_priority_id(priority_name: str | None) -> str:
    """Translate a Zabbix severity label into a Jira priority id."""
    if not priority_name:
        return "5"
    return PRIORITY_IDS.get(priority_name, "5")


def load_config(module_name: str = "ztj_config") -> AppConfig:
    """Load and validate runtime configuration."""
    return AppConfig.from_module(module_name)


def merge_graph_settings(raw_payload: dict[str, Any]) -> dict[str, str | None]:
    """Overlay provided graph metadata on top of the default graph settings."""
    merged = dict(DEFAULT_GRAPH_SETTINGS)
    for graph_entry in raw_payload.get("ztj", {}).get("graphs", []):
        for key, value in graph_entry.items():
            merged[key] = None if value is None else str(value)
    return merged


def parse_zabbix_body(body: str, prefix: str) -> tuple[dict[str, str | None], list[str]]:
    """Split the raw Zabbix body into metadata settings and Jira body lines."""
    body_lines: list[str] = []
    raw_settings: dict[str, Any] = {}

    for raw_line in body.splitlines():
        stripped_line = raw_line.strip()
        if not stripped_line:
            body_lines.append(raw_line)
            continue
        if f'"{prefix}"' in stripped_line:
            try:
                raw_settings = json.loads(stripped_line)
            except json.JSONDecodeError as exc:
                raise PayloadError("invalid JSON metadata in Zabbix body") from exc
        else:
            body_lines.append(raw_line)

    return merge_graph_settings(raw_settings), body_lines


def parse_alert_payload(argv: list[str], config: AppConfig) -> AlertPayload:
    """Parse CLI arguments into a typed alert payload."""
    if len(argv) < 3:
        raise PayloadError("expected Zabbix subject in sys.argv[1] and body in sys.argv[2]")

    subject = argv[1] or "Zabbix problem"
    raw_settings, body_lines = parse_zabbix_body(argv[2], config.zbx_prefix)
    graph = GraphSettings.from_dict(raw_settings)
    return AlertPayload(subject=subject, body_lines=tuple(body_lines), graph=graph)


def build_jira_service(config: AppConfig) -> JiraService:
    """Create the Jira service used for alert processing."""
    client = JIRA(
        options={"server": config.jira_server, "verify": config.jira_verify},
        basic_auth=(config.jira_user, config.jira_pass),
    )
    return JiraService(client=client, config=config)


def build_zabbix_client(config: AppConfig) -> ZabbixClient:
    """Create the Zabbix client used for graph download."""
    return ZabbixClient(
        server=config.zbx_server,
        username=config.zbx_user,
        password=config.zbx_password,
        verify=config.zbx_api_verify,
        timeout_seconds=config.http_timeout_seconds,
    )


def build_repository(database_path: str | Path = DATABASE_NAME) -> IssueRepository:
    """Create the SQLite repository used for trigger mappings."""
    return IssueRepository(database_path)


def build_processor(config: AppConfig, database_path: str | Path = DATABASE_NAME) -> AlertProcessor:
    """Create the fully wired alert processor for the CLI entry point."""
    tmp_dir = Path(config.zbx_tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return AlertProcessor(
        config=config,
        repository=build_repository(database_path),
        jira_service=build_jira_service(config),
        zabbix_client=build_zabbix_client(config),
    )


def run(
    argv: list[str],
    *,
    config: AppConfig | None = None,
    processor: AlertProcessor | None = None,
    database_path: str | Path = DATABASE_NAME,
) -> int:
    """Run one alert-processing cycle for CLI and test callers."""
    app_config = config or load_config()
    alert_payload = parse_alert_payload(argv, app_config)
    active_processor = processor or build_processor(app_config, database_path)
    return active_processor.process(alert_payload)


def main() -> int:
    """CLI entry point that configures logging and returns a process exit code."""
    configure_logging()
    try:
        return run(sys.argv)
    except ApplicationError as exc:
        LOGGER.error("%s", exc)
        return 1
    except Exception:
        LOGGER.exception("unexpected unhandled error")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
