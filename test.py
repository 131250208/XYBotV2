import re

content = "[msg_id=2077190504674656354] [2025-02-27 12:07:01] 留几手：呃."
main_name = "留几手"
print(re.sub(f'\[msg_id.*?{main_name}：', '', content))