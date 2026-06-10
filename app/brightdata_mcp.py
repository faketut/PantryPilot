"""
Bright Data MCP integration — scrape any URL to clean markdown.

Uses @brightdata/mcp via npx stdio transport.
Requires BRIGHTDATA_API_TOKEN environment variable.
Free tier: 5,000 requests/month.  https://brightdata.com
"""
import asyncio
import json
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_MCP_CMD = ["npx", "-y", "@brightdata/mcp"]


@dataclass
class ScrapeResult:
    success: bool
    markdown: str = ""
    error: str = ""
    no_token: bool = False


# ---------------------------------------------------------------------------
# Low-level MCP stdio helpers
# ---------------------------------------------------------------------------

async def _send(proc: asyncio.subprocess.Process, msg: dict) -> None:
    line = json.dumps(msg, separators=(",", ":")) + "\n"
    proc.stdin.write(line.encode())
    await proc.stdin.drain()


async def _recv(proc: asyncio.subprocess.Process, req_id: int, timeout: float) -> dict | None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        remaining = deadline - asyncio.get_event_loop().time()
        try:
            raw = await asyncio.wait_for(proc.stdout.readline(), timeout=min(remaining, 5.0))
        except TimeoutError:
            continue
        if not raw:
            break
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if msg.get("id") == req_id:
            return msg
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def scrape_url(url: str) -> ScrapeResult:
    """
    Spawn the Bright Data MCP server, call scrape_as_markdown on url,
    and return the extracted markdown.
    """
    api_token = os.getenv("BRIGHTDATA_API_TOKEN", "").strip()
    if not api_token:
        return ScrapeResult(success=False, no_token=True,
                            error="BRIGHTDATA_API_TOKEN not set in .env")

    proc = None
    stderr_path = f"/tmp/brightdata-mcp-{os.getpid()}.log"
    stderr_fp = None
    try:
        logger.info("Spawning Bright Data MCP: %s", url)
        stderr_fp = open(stderr_path, "wb")
        proc = await asyncio.create_subprocess_exec(
            *_MCP_CMD,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=stderr_fp,
            env={**os.environ, "API_TOKEN": api_token},
        )

        # --- MCP handshake -------------------------------------------------
        await _send(proc, {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pantrpilot", "version": "1.0"},
            },
            "id": 1,
        })
        init_resp = await _recv(proc, req_id=1, timeout=90.0)
        if not init_resp:
            tail = ""
            try:
                with open(stderr_path, "r", errors="replace") as f:
                    tail = f.read()[-400:]
            except Exception:
                pass
            detail = f" (stderr: {tail.strip()})" if tail.strip() else ""
            return ScrapeResult(
                success=False,
                error=f"Bright Data MCP did not respond to initialize within 90s{detail}",
            )

        await _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        # --- scrape_as_markdown --------------------------------------------
        await _send(proc, {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "scrape_as_markdown",
                "arguments": {"url": url},
            },
            "id": 2,
        })
        resp = await _recv(proc, req_id=2, timeout=60.0)
        if not resp:
            return ScrapeResult(success=False, error="scrape_as_markdown timed out")

        # Check for MCP-level error
        if "error" in resp:
            return ScrapeResult(success=False, error=resp["error"].get("message", "MCP error"))

        try:
            content = resp["result"]["content"]
            # content is a list of {type, text} parts
            parts = [c["text"] for c in content if c.get("type") == "text"]
            markdown = "\n\n".join(parts).strip()
        except (KeyError, TypeError):
            return ScrapeResult(success=False, error="Unexpected MCP response format")

        if not markdown:
            return ScrapeResult(success=False, error="No content scraped from URL")

        return ScrapeResult(success=True, markdown=markdown)

    except FileNotFoundError:
        return ScrapeResult(success=False, error="npx not found — Node.js 18+ required")
    except Exception as exc:
        logger.exception("Bright Data MCP error")
        return ScrapeResult(success=False, error=str(exc))
    finally:
        if proc:
            try:
                proc.stdin.close()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3.0)
                except TimeoutError:
                    proc.kill()
            except Exception:
                pass
        try:
            if stderr_fp is not None:
                stderr_fp.close()
        except Exception:
            pass
        try:
            os.unlink(stderr_path)
        except Exception:
            pass
