#!/usr/bin/env python
# coding: utf-8

import json
import os
import sqlite3
import sys

import requests
from jira import JIRA

import ztj_config

# PARAMS RECEIVED FROM ZABBIX SERVER:
# sys.argv[1] = SUBJECT
# sys.argv[2] = BODY

zbx_body_text = []


class ZabbixAPI:
    def __init__(self, server, username, password):
        self.debug = True
        self.server = server
        self.username = username
        self.password = password
        self.proxies = {}
        self.verify = True
        self.cookie = None

    def login(self):

        data_api = {"name": self.username, "password": self.password, "enter": "Sign in"}
        req_cookie = requests.post(self.server + "/", data=data_api, proxies=self.proxies, verify=self.verify)
        cookie = req_cookie.cookies
        if len(req_cookie.history) > 1 and req_cookie.history[0].status_code == 302:
            print_message(
                "probably the server in your config file has not full URL (for example ""'{0}' instead of '{1}')".format(
                    self.server, self.server + "/zabbix"))
        if not cookie:
            print_message("authorization has failed, url: {0}".format(self.server + "/"))
            cookie = None

        self.cookie = cookie

    def graph_get(self, itemid, period, title, width, height, tmp_dir):
        file_img = tmp_dir + "/{0}.png".format(itemid)

        title = requests.utils.quote(title)

        zbx_img_url = self.server + "/chart3.php?period={1}&name={2}" \
                                    "&width={3}&height={4}&graphtype=0&legend=1" \
                                    "&items[0][itemid]={0}&items[0][sortorder]=0" \
                                    "&items[0][drawtype]=5&items[0][color]=00CC00".format(itemid, period, title,
                                                                                          width, height)
        if self.debug:
            print_message(zbx_img_url)
        res = requests.get(zbx_img_url, cookies=self.cookie, proxies=self.proxies, verify=self.verify, stream=True)
        res_code = res.status_code
        if res_code == 404:
            print_message("can't get image from '{0}'".format(zbx_img_url))
            return False
        res_img = res.content
        with open(file_img, 'wb') as fp:
            fp.write(res_img)
        return file_img


def print_message(string):
    string = str(string) + "\n"
    filename = sys.argv[0].split("/")[-1]
    sys.stderr.write(filename + ": " + string)


zapi = ZabbixAPI(server=ztj_config.zbx_server, username=ztj_config.zbx_user, password=ztj_config.zbx_password)

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

trigger_desc = {
    "not_classified": {"name": "Not classified", "id": "5"},
    "information": {"name": "Information", "id": "5"},
    "warning": {"name": "Warning", "id": "4"},
    "average": {"name": "Average", "id": "3"},
    "high": {"name": "High", "id": "2"},
    "disaster": {"name": "Disaster", "id": "1"},
}


def jira_login():
    jira_server = {'server': ztj_config.jira_server, 'verify': ztj_config.jira_verify}
    return JIRA(options=jira_server, basic_auth=(ztj_config.jira_user, ztj_config.jira_pass))


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


def get_transition(issue_key):
    jira_server = {'server': ztj_config.jira_server, 'verify': ztj_config.jira_verify}
    jira = JIRA(options=jira_server, basic_auth=(ztj_config.jira_user, ztj_config.jira_pass))
    issue = jira.issue(issue_key)
    transitions = jira.transitions(issue)
    for t in transitions:
        if t['name'] == ztj_config.jira_transition:
            return t['id']


def get_issues_numbers():
    jira = jira_login()
    opened_issues = jira.search_issues("(status='Waiting for support' OR status='In Progress' OR status='Pending') AND reporter={0}".format(ztj_config.jira_user), json_result=True)
    return opened_issues


def ztj_settings_parser(ztj_settings):
    for j in range(0, len(ztj_settings['ztj']['graphs'])):
        ztj_tuple = {key: ''.join(values) for key, values in ztj_settings['ztj']['graphs'][j].items()}
        settings.update(ztj_tuple)
    return settings


def remove_issues_in_sqlite():
    issues_list = get_issues_numbers()
    c.execute("SELECT * from issues")
    ids_to_delete = []
    for row in c:
        match_found = False
        sql_issue_id = row[1]
        for j in range(0, len(issues_list['issues'])):
            jira_issue_id = issues_list['issues'][j]['key']
            if jira_issue_id:
                if sql_issue_id.__eq__(jira_issue_id):
                    match_found = True
        if not match_found:
            ids_to_delete.append(sql_issue_id)
    if ids_to_delete:
        sqlite_update_query = "DELETE FROM issues WHERE jira_issue_id IN ({})".format(", ".join("?" * len(ids_to_delete)))
        c.execute(sqlite_update_query, ids_to_delete)
        conn.commit()
        print("Issue key(s) {0} removed from sqlite database".format(ids_to_delete))


if __name__ == '__main__':
    if not os.path.exists(ztj_config.zbx_tmp_dir):
        os.makedirs(ztj_config.zbx_tmp_dir)
    tmp_dir = ztj_config.zbx_tmp_dir
    zbx_body = open("test\\entry.txt", 'r').read()
    #    zbx_body = sys.argv[2]
    tmp_settings = {}
    for line in zbx_body.strip().split("\n"):
        if line.__contains__(ztj_config.zbx_prefix):
            tmp_settings = json.loads(line)
        else:
            zbx_body_text.append(line)
    image_settings = ztj_settings_parser(tmp_settings)
    trigger_ok = int(settings['ok'])
    trigger_id = int(settings['triggerid'])
    conn = sqlite3.connect('zabbix-jira.db')
    c = conn.cursor()
    c.execute("CREATE TABLE if not exists issues (zbx_trigger_id integer, jira_issue_id text)")
    conn.commit()
    c.execute("SELECT jira_issue_id FROM issues WHERE zbx_trigger_id={0}".format(trigger_id))
    result = c.fetchall()
    if not result and trigger_ok == 0:
        priority = 5
        for i in trigger_desc.values():
            if i['name'] == settings['priority']:
                priority = i['id']
                #        issue_key = create_issue(sys.argv[1], '\n'.join(zbx_body_text), ztj_config.jira_project, ztj_config.jira_issue_type, priority)
        issue_key = create_issue('Test subject', '\n'.join(zbx_body_text), ztj_config.jira_project, ztj_config.jira_issue_type, priority)
        print("Issue {0} created".format(issue_key))
        zapi.login()
        zbx_file_img = zapi.graph_get(settings["itemid"], settings["graphs_period"],
                                      settings["title"], settings["graphs_width"],
                                      settings["graphs_height"], tmp_dir)
        if not zbx_file_img:
            print("Can't get image, check URL manually")
        elif isinstance(zbx_file_img, str):
            add_attachment(issue_key, zbx_file_img)
            os.remove(zbx_file_img)
        c.execute("INSERT INTO issues VALUES (?, ?);", (trigger_id, issue_key))
        conn.commit()
    elif trigger_ok == 1:
        if result:
            issue_key = result[0][0]
            add_comment(issue_key, '\n'.join(zbx_body_text))
            close_issue(issue_key, get_transition(issue_key))
            print("Issue {0} was closed".format(issue_key))
    remove_issues_in_sqlite()
    conn.close()
