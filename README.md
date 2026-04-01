# ZabbixToJira (ZTJ)

ZabbixToJira is a Python alert script that creates Jira issues from Zabbix problem events, attaches a Zabbix graph image, and closes the related Jira issue when the Zabbix problem is resolved.

The current implementation is structured for production use:

- validated runtime configuration
- typed alert parsing and normalization
- dedicated Jira and Zabbix service layers
- isolated SQLite repository for trigger mappings
- structured logging and explicit error handling

## What this project does

- Creates Jira issues for new Zabbix problems.
- Maps Zabbix trigger severity to Jira priority.
- Downloads and attaches a Zabbix graph image to the issue.
- Tracks trigger-to-issue mapping in a local SQLite database (`zabbix-jira.db`).
- On recovery events, adds a comment and transitions the issue to your configured "close" transition.
- Periodically cleans stale mappings from SQLite if issues were closed manually in Jira.

## Requirements

- Python 3.12
- Network access from your Zabbix server to Jira and Zabbix web UI

Python dependencies are in `requirements.txt` and include upgraded versions of:

- `requests`
- `jira`
- `urllib3`
- `certifi`

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

## Installation

1. Clone this repository:

   ```bash
   git clone https://github.com/be4ndr/ZabbixToJira.git
   cd ZabbixToJira
   ```

2. Copy `zabbix_to_jira.py` into your Zabbix `AlertScriptsPath` directory (from `zabbix_server.conf`).

3. Create `ztj_config.py` next to `zabbix_to_jira.py` by copying `ztj_config_default.py` and filling real values.

4. Ensure the script can write to:
   - temp graph directory (`zbx_tmp_dir`, default OS temp dir + `/ztj`)
   - local SQLite file (`zabbix-jira.db`)

## Configuration

Example `ztj_config.py` keys:

- `jira_server`, `jira_user`, `jira_pass`
- `jira_project`, `jira_issue_type`
- `jira_transition` (transition name used to close issues)
- `jira_verify` (`True` to verify TLS certs)
- `zbx_server`, `zbx_user`, `zbx_password`
- `zbx_prefix`
- `zbx_tmp_dir`
- `http_timeout_seconds` (optional, default: `30`)

## Zabbix media/action payload format

The script expects JSON metadata in the message body with a `ztj` prefix block.

### Problem event example

```text
{"ztj": {"graphs": [{"graphs_period": "1800"}, {"itemid": "{ITEM.ID1}"}, {"triggerid": "{TRIGGER.ID}"}, {"title": "{HOST.HOST} - {TRIGGER.NAME}"}, {"priority": "{TRIGGER.SEVERITY}"}]}}
||Last value:|{ITEM.VALUE1} ({TIME})||
||Server:|{HOST.NAME}, {HOSTNAME}, ({HOST.IP})||

{panel:title=Description}
{TRIGGER.DESCRIPTION}
{panel}
```

### Recovery event example

```text
{"ztj": {"graphs": [{"triggerid": "{TRIGGER.ID}"}, {"ok": "1"}]}}
||Server:|{HOST.NAME}, {HOSTNAME}, ({HOST.IP})||
||Last value:|{ITEM.VALUE1} ({TIME})||

{panel:title=Description}
Problem resolved!

Time of resolved problem: {DATE} {TIME}
{panel}
```

## Graph options reference

- `graphs_period` (default: `3600` seconds)
- `graphs_width` (default: `900`)
- `graphs_height` (default: `200`)
- `itemid` (Zabbix item ID used for chart)
- `title` (chart title)
- `triggerid` (used to correlate problem and recovery)
- `priority` (Zabbix severity text)
- `ok` (`1` in recovery messages)

## Runtime behavior

- The script reads the alert subject from `sys.argv[1]`.
- The script reads the Zabbix message body from `sys.argv[2]`.
- If `ztj_config.py` is missing, the script falls back to `ztj_config_default.py`.
- Trigger-to-issue mappings are stored in `zabbix-jira.db` in the current working directory.
- Application logs are written to stderr using Python `logging`.
- Invalid config, payload, or external-service failures return a non-zero exit code.

## Tests

Run the automated test suite with:

```bash
python -m unittest discover -s test
```

## Notes

- Jira text formatting in message body is supported by Jira as plain issue description/comment content.
- Keep credentials secure; avoid committing real values in `ztj_config.py`.

## License

This project is licensed under the terms in `LICENSE`.
