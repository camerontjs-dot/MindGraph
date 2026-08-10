# Shared MCP daemon (workbench only)

This example is not installed into MainFrame. Use fixture databases and a
non-default port during review:

```bash
mindgraph serve-daemon --knowledge-db /path/to/knowledge.sqlite \
  --projects-db /path/to/projects.sqlite --host 127.0.0.1 \
  --port 8765 --path /mcp
mindgraph mcp-proxy --url http://127.0.0.1:8765/mcp
```

Lifecycle commands are `daemon-start`, `daemon-status`, `daemon-health`, and
`daemon-stop`. Tests pass `--state-dir` under temporary storage. The default
operational state directory is `~/.mindgraph/run`, but this task does not use it.

## Later root-promotion map

- Review and promote `src/mindgraph/mcp_server.py`, `mcp_proxy.py`, `daemon.py`,
  and CLI additions into `/Users/admin/Desktop/MainFrame/mindgraph`.
- After root tests pass, decide whether `/Users/admin/Desktop/MainFrame/bin`
  needs lifecycle/proxy wrappers; do not copy workbench paths blindly.
- Only after an approved live smoke test consider changing
  `/Users/admin/Desktop/MainFrame/.mcp.json`.
- Stop on any list-shape regression, non-loopback bind, scope/trust loss, live
  DB write, orphaned process, or root-suite failure.
