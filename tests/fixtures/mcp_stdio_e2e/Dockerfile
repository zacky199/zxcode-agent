# Test-only MCP runtime. The base image is a locally verified, digest-pinned
# ZX Code sandbox image; no mutable tag or implicit pull is permitted.
FROM zxcode-sandbox:full@sha256:7fabb8687917bdd2c2ef593ad0824f31ae83325da5545ffd605e41da0f4794ae

# Pin MCP SDK and every transitive Python dependency for repeatable builds.
COPY requirements.lock /tmp/mcp-requirements.lock
RUN python -m pip install --no-cache-dir --disable-pip-version-check \
      -r /tmp/mcp-requirements.lock \
    && python -c "import mcp; from mcp.server.fastmcp import FastMCP"

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/python"]
CMD ["/workspace/server.py"]
