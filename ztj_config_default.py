jira_server = 'jira_server'
jira_user = 'username'
jira_pass = 'password'
jira_transition = 'transition_name'  # Transition to close issue. Read more https://jira.readthedocs.io/en/master/examples.html#transitions
jira_project = 'project_key'  # Your project key, for example "ZTJ"
jira_issue_type = 'Incident'  # Your issue type in Jira project(Error, Bug, Epic ...)
jira_verify = False  # True - do not ignore self signed certificates, False - ignore

zbx_server = 'zabbix_server'
zbx_user = 'username'
zbx_password = 'password'
zbx_prefix = 'ztj'
zbx_tmp_dir = '/tmp/' + zbx_prefix
zbx_api_verify = False
