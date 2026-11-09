import os

filepath = r"d:\Projects\autonomous-vision-engine\tests\smoke_e2e.py"
if os.path.exists(filepath):
    os.remove(filepath)
    print(f"Deleted {filepath}")
else:
    print(f"{filepath} not found")
