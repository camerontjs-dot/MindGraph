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
