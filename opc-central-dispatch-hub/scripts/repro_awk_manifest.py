"""复现安装脚本 awk 解析在单行 manifest.json 上的错误行为。"""
import subprocess

line = open(r"C:\Users\kwyt1\AppData\Local\Temp\manifest-debug.json", encoding="utf-8").read()
# 与 install-agent-code-linux.sh manifest_field 相同的 awk 逻辑（key=agent-code-linux-x64）
prog = r'''/agent-code-linux-x64/ { in_entry = 1 }
in_entry && /sha256/ { gsub(/[",]/, "", $2); print $2; exit }'''
r = subprocess.run(["awk", prog], input=line, capture_output=True, text=True)
print("awk 取 sha256:", r.stdout.strip())
prog2 = r'''/agent-code-linux-x64/ { in_entry = 1 }
in_entry && /path/ { gsub(/[",]/, "", $2); print $2; exit }'''
r2 = subprocess.run(["awk", prog2], input=line, capture_output=True, text=True)
print("awk 取 path:  ", r2.stdout.strip())
print("期望 sha256:   91c806aaba31d3efbd85cab430d70b8278b5242bb8ad95f949f1f87ebb1d91cc")
print("期望 path:     agent-code/0.5.0/opc-agent-code-0.5.0-linux-x64.tar.gz")
