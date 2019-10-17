#!/usr/bin/env python
# coding: utf-8

import json
import os
import sqlite3

from jira import JIRA
from pyzabbix import ZabbixAPI

import config

zapi = ZabbixAPI(config.zbx_server)
zapi.session.verify = config.zbx_api_verify
zbx_body_text = []

settings = {
    "itemid": "0",  # itemid for graph
    "triggerid": "0",  # uniqe trigger id of event
    "ok": "0",  # flag of resolve problem, 0 - no, 1 - yes
    "priority": None,  # zabbix trigger priority
    "title": None,  # title for graph
    "graphs_period": "3600",
    "graphs_width": "900",
    "graphs_height": "200",
}


def jira_login():
    jira_server = {'server': config.jira_server, 'verify': config.jira_verify}
    return JIRA(options=jira_server, basic_auth=(config.jira_user, config.jira_pass))


def create_issue(title, body, project, issue_type, priority):
    jira = jira_login()
    issue_params = {
        'project': {'key': project},
        'summary': title,
        'description': body,
        'issuetype': {'name': issue_type},
        'priority': {'id': priority}
    }
    return jira.create_issue(fields=issue_params).key


def add_attachment(issue, attachment):
    jira = jira_login()
    jira.add_attachment(issue, attachment)


def close_issue(issue, status):
    jira = jira_login()
    jira.transition_issue(issue, status)


def add_comment(issue, comment):
    jira = jira_login()
    jira.add_comment(issue, comment)


def get_issues_numbers():
    jira = jira_login()
    opened_issues = jira.search_issues(f"(status='Waiting for support' OR status='In Progress' OR status='Pending') AND reporter={config.jira_user}", json_result=True)
    return opened_issues


def ztj_settings_parser(ztj_settings):
    for j in range(0, len(ztj_settings['ztj']['graphs'])):
        ztj_tuple = {key: ''.join(values) for key, values in ztj_settings['ztj']['graphs'][j].items()}
        settings.update(ztj_tuple)
    return settings


def remove_issues_in_sqlite():
    c.execute("SELECT count() from issues")
    row_number = c.fetchall()[0][0]
    for i in range(0, row_number):
        c.execute(f"SELECT * FROM events LIMIT 1 OFFSET {i};")
        sql_issue_id = c.fetchall()[0][0]
        match_found = False
        for j in range(0, len(issues_list['issues'])):
            jira_issue_id = issues_list['issues'][j]['key']
            if jira_issue_id:
                if sql_issue_id.equal(jira_issue_id):
                    match_found = True
        if not match_found:
            c.execute(f"DELETE FROM events WHERE jira_issue_id={sql_issue_id}")
            print(f"Issue key {sql_issue_id} removed from sqlite database")


if __name__ == '__main__':
    if not os.path.exists(config.zbx_tmp_dir):
        os.makedirs(config.zbx_tmp_dir)
    tmp_dir = config.zbx_tmp_dir
    zbx_body = open("test\\entry.txt", 'r').read()
    #    zbx_body = sys.argv[2]
    tmp_settings = {}
    for line in zbx_body.strip().split("\n"):
        if line.__contains__(config.zbx_prefix):
            tmp_settings = json.loads(line)
        else:
            zbx_body_text.append(line)
    image_settings = ztj_settings_parser(tmp_settings)
    trigger_ok = int(settings['ok'])
    trigger_id = int(settings['triggerid'])
    issues_list = get_issues_numbers()
    conn = sqlite3.connect('zabbix-jira.db')
    c = conn.cursor()
    c.execute("CREATE TABLE if not exists issues (zbx_trigger_id integer, jira_issue_id text)")
    conn.commit()
    c.execute(f"SELECT jira_issue_id FROM issues WHERE zbx_trigger_id={trigger_id}")
    result = c.fetchall()
    if not result and trigger_ok == 0:
        priority = 5
        for i in config.trigger_desc.values():
            if i['name'] == settings['priority']:
                priority = i['id']
                #        issue_key = create_issue(sys.argv[1], '\n'.join(zbx_body_text), config.jira_project, config.jira_issue_type, priority)
        issue_key = create_issue('Test subject', '\n'.join(zbx_body_text), config.jira_project, config.jira_issue_type, priority)
        zapi.login(config.zbx_user, config.zbx_password)
        zbx_file_img = zapi.graph_get(settings["itemid"], settings["graphs_period"],
                                      settings["title"], settings["graphs_width"],
                                      settings["graphs_height"], tmp_dir)
        if not zbx_file_img:
            print("Can't get image, check URL manually")
        elif isinstance(zbx_file_img, str):
            add_attachment(issue_key, zbx_file_img)
            os.remove(zbx_file_img)
        zapi.user.logout()
        c.execute(f"INSERT INTO issues VALUES ({trigger_id}, {issue_key});")
        conn.commit()
    remove_issues_in_sqlite()
