# ZabbixToJira(ZTJ)
A simple python script to open tickets in Jira when Zabbix Alarm is triggering and add a graph to the ticket. It uses a data in json format
and parrsing it to get parameters from Zabbix.

# ZabbixToJira(ZTJ)
zabbix-jira is python module that allows you to create tasks in Jira with grafs by the trigger from Zabbix.

## Requirements: 
* python >= 2.7
* python libs: requests, jira, json, sqlite

## Installation:
1. Copy this repo to your zabbix-server:
`git clone https://github.com/be4ndr/ZabbixToJira.git` 
2. Copy `zabbix_to_jira.py` to your Zabbix `AlertScriptsPath` directory (see your zabbix_server.conf) 
3. Create and configure `ztj_config.py` near `zabbix_to_jira.py`. You can take as an example `ztj_config_default.py` from repo.  
4. Install python libs: `pip install -r requirements.txt`

## Configuration:
* Create new media type in Zabbix:  

If you use Zabbix 3.0 and higher, add this parameters:
```
{ALERT.SENDTO}
{ALERT.SUBJECT}
```
Example message:  
```
{"ztj": {"graphs": [{"graphs_period": "1800"}, {"itemid": "{ITEM.ID1}"}, {"triggerid": "{TRIGGER.ID}"}, {"title": "{HOST.HOST} - {TRIGGER.NAME}"}, {"priority": "{TRIGGER.SEVERITY}"}]}}
||Last value:|{ITEM.VALUE1} ({TIME})||
||Server:|{HOST.NAME}, {HOSTNAME}, ({HOST.IP})||

{panel:title=Description}
{TRIGGER.DESCRIPTION}
{panel}
```
Example recovery message:
```
{"ztj": {"graphs": [{"triggerid": "{TRIGGER.ID}"}, {"ok": "1"}]}}
||Server:|{HOST.NAME}, {HOSTNAME}, ({HOST.IP})||
||Last value:|{ITEM.VALUE1} ({TIME})||

{panel:title=Description}
Problem resolved!

Time of resolved problem: {DATE} {TIME}
{panel}
```

### Annotations
```
"graphs" -- a part of json data responsible for graphs
"graphs_period": "1800" -- set graphs period (default - 3600 seconds)
graphs_width: "900" -- set graphs width (default - 900px)
"graphs_height": "200" -- set graphs height (default - 300px)
"itemid": "{ITEM.ID1}" -- define itemid (from trigger) for attach
"title": "{HOST.HOST} - {TRIGGER.NAME}" -- graph title
"triggerid": "{TRIGGER.ID}"-- define triggerid to link problem and recovery of event
{"priority": "{TRIGGER.SEVERITY}"} -- set priority task like as priority of trigger from Zabbix
"ok": "1" -- use this parameter only in RECOVERY message, if you don't want create a new task about recovery in Jira
```

You can use Jira format text in your actions: [https://jira.atlassian.com/secure/WikiRendererHelpAction.jspa?section=all](https://jira.atlassian.com/secure/WikiRendererHelpAction.jspa?section=all)

### Test script
You can use the following command to create a ticket in Jira from your command line:  
`python jirabix.py "jira_username" "ticket_subject" "ticket_desc"` where
* jira_username - username from Jira user profile 
* For `ticket_subject` and `ticket_desc` you may use "test" "test"
  * If you want to test real text from zabbix action message copy `test/entry.txt` from repo and change the contents of the file on your real data and change `zabbix_to_jira.py`:
```
    zbx_body = open("test\\entry.txt", 'r').read()
    #    zbx_body = sys.argv[2]
```
  And run:  
  `python jirabix.py "jira_username" "ticket_subject`
  
## Result
* A new ticket should be created in Jira with attached graph.
* When problem is going to OK, script convert the ticket to "Done" status with comment from zabbix recovery message.  
