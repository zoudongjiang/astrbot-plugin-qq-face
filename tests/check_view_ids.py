import re
from pathlib import Path

root = Path(__file__).resolve().parent.parent
html = (root / "views/face-settings/index.html").read_text(encoding="utf-8")
js = (root / "views/face-settings/app.js").read_text(encoding="utf-8")

ids = set(re.findall(r'id="([^"]+)"', html))
used = set(re.findall(r'el\("([^"]+)"\)', js))
print("html ids:", len(ids), "js refs:", len(used))
print("missing in html:", sorted(used - ids))
print("unused ids:", sorted(ids - used))
