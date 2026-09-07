import os

file_path = 'manage.py'
lines = []

try:
    with open(file_path, 'r') as f:
        lines = f.readlines()
except FileNotFoundError:
    pass

if lines and lines[0].startswith('#!'):
    lines.insert(1, 'print("naber")\n')
else:
    lines.insert(0, 'print("naber")\n')

with open(file_path, 'w') as f:
    f.writelines(lines)