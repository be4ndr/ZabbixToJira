import json

import ztj_config
import zabbix_to_jira

zapi = zabbix_to_jira.ZabbixAPI(server=ztj_config.zbx_server, username=ztj_config.zbx_user, password=ztj_config.zbx_password)
tmp_dir = ztj_config.zbx_tmp_dir

if __name__ == '__main__':
    zbx_body = open("entry.txt", 'r').read()
    zbx_body_text = []
    ztj_settings = {}
    for line in zbx_body.strip().split("\n"):
        if line.__contains__(ztj_config.zbx_prefix):
            ztj_settings = json.loads(line)
        else:
            zbx_body_text.append(line)
    settings = zabbix_to_jira.ztj_settings_parser(ztj_settings)
    zapi.login()
    zbx_file_img = zapi.graph_get(settings["itemid"], settings["graphs_period"], settings["title"], settings["graphs_width"], settings["graphs_height"], tmp_dir)
    # print(settings["graphs_height"])
    print(zbx_file_img)
