import sqlite3

import zabbix_to_jira


def remove_issues_in_sqlite():
    issues_list = zabbix_to_jira.get_issues_numbers()
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
    sqlite_update_query = "DELETE FROM issues WHERE jira_issue_id IN ({})".format(", ".join("?" * len(ids_to_delete)))
    c.execute(sqlite_update_query, ids_to_delete)
    conn.commit()
    print("Issue keys {0} removed from sqlite database".format(ids_to_delete))


if __name__ == '__main__':
    conn = sqlite3.connect('zabbix-jira.db')
    c = conn.cursor()
    c.execute("CREATE TABLE if not exists issues (zbx_trigger_id integer, jira_issue_id text)")
    conn.commit()
    c.execute("INSERT INTO issues VALUES (11111, 'DOQSD-11111');")
    conn.commit()
    c.execute("INSERT INTO issues VALUES (22222, 'DOQSD-22222');")
    conn.commit()
    c.execute("INSERT INTO issues VALUES (33333, 'DOQSD-33333');")
    conn.commit()
    c.execute("SELECT * FROM issues")
    result = c.fetchall()
    print(result)
    c.execute("SELECT * from issues")
    # for row in c:
    # print(row[1])
    remove_issues_in_sqlite()
    c.execute("SELECT * FROM issues")
    result = c.fetchall()
    print(result)
