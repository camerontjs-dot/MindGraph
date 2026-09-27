"""Create an opt-in isolated synthetic app fixture with the real candidate CLI.

Run with the candidate MindGraph environment. No pre-existing directory can be
used. No cloud/model call is needed: the owned wrapper adds --lexical-only to
query, and otherwise runs the real CLI. The failure marker is explicit.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from mindgraph import db

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("home", type=Path)
args = parser.parse_args()
home = args.home.resolve()
if home.exists():
    raise SystemExit("REFUSED: fixture home must not already exist")
home.mkdir(parents=True)
(home / ".repair88-isolated-fixture").write_text("synthetic developer/qualification fixture\n")
root = home / "MainFrame"
project = root / "30_projects/fixture"
project.mkdir(parents=True)
(project / "README.md").write_text("# Existing context\nThis prepared context must not change on inspection.\n")
(project / "AGENTS.md").write_text("# Explicit canary context\nDo not send any provider input.\n")
(home / ".mindgraph").mkdir()
text = "binding fixture source-backed passage " * 60
for filename, index, trust in [("mainframe.sqlite", "fixture-knowledge", "durable_knowledge"), ("mainframe-projects.sqlite", "fixture-projects", "project_status")]:
    conn = db.init_db(str(home / ".mindgraph" / filename), semantic_enabled=False)
    conn.execute("INSERT INTO documents(id,title,path,content_hash,index_id,namespace,trust_profile,metadata_json) VALUES(?,?,?,?,?,?,?,?)", ("same-doc", "Binding fixture", "fixture.md", hashlib.sha256(text.encode()).hexdigest(), index, "fixture", trust, "{}"))
    conn.execute("INSERT INTO chunks(doc_id,chunk_index,text) VALUES(?,?,?)", ("same-doc",0,text))
    conn.execute("INSERT INTO documents_fts(id,title,content) VALUES(?,?,?)", ("same-doc","Binding fixture",text))
    conn.commit(); conn.close()
(root / "bin").mkdir()
wrapper = root / "bin/mindgraph"
wrapper.write_text("#!" + sys.executable + "\n" + '''import os, sys
from pathlib import Path
home = Path(__file__).resolve().parents[2]
args = sys.argv[1:]
if args and args[0] == "expand-nomination" and (home / ".repair88-fail-expansion").exists():
    print('{"chunk_text":"REPAIR88_MALFORMED_SOURCE_MARKER"}')
    sys.exit(0)
if args and args[0] == "query":
    args += ["--lexical-only"]
os.execv(sys.executable, [sys.executable, "-c", "from mindgraph.cli import app; app()", *args])
''')
wrapper.chmod(0o755)
print(json.dumps({"fixture_home":str(home), "mainframe_root":str(root), "python":sys.executable, "synthetic":True}, indent=2))
