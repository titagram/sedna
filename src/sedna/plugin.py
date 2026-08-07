"""Hades tool registration for the intentionally small Sedna MVP."""

from __future__ import annotations

import json
from typing import Any

from sedna.runners import ToolRunner, nmap_service_scan, nmap_tcp_discovery


def register(ctx: Any) -> None:
    """Register only the two implemented Nmap operations."""
    ctx.register_tool(
        name="sedna_nmap_tcp_discovery",
        toolset="plugin_sedna",
        schema={
            "name": "sedna_nmap_tcp_discovery",
            "description": (
                "Run an unprivileged TCP discovery scan against an authorized HTB target."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "IP address or hostname"},
                    "top_ports": {
                        "type": "integer",
                        "default": 1000,
                        "minimum": 1,
                        "maximum": 65535,
                    },
                    "timeout": {
                        "type": "number", "default": 120, "minimum": 1, "maximum": 600
                    },
                },
                "required": ["target"],
            },
        },
        handler=_tcp_discovery_handler,
    )
    ctx.register_tool(
        name="sedna_nmap_service_scan",
        toolset="plugin_sedna",
        schema={
            "name": "sedna_nmap_service_scan",
            "description": (
                "Identify services on explicitly enumerated ports of an authorized HTB target."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "IP address or hostname"},
                    "ports": {
                        "type": "string",
                        "description": "Ports/ranges, e.g. 22,80,443 or 8000-8010",
                    },
                    "timeout": {"type": "number", "default": 120, "minimum": 1, "maximum": 600},
                },
                "required": ["target", "ports"],
            },
        },
        handler=_service_scan_handler,
    )


def _tcp_discovery_handler(args: dict[str, Any], **_: Any) -> str:
    try:
        command = nmap_tcp_discovery(args["target"], top_ports=int(args.get("top_ports", 1000)))
        return _run(command, float(args.get("timeout", 120)))
    except (KeyError, TypeError, ValueError) as error:
        return json.dumps({"ok": False, "error": str(error)})


def _service_scan_handler(args: dict[str, Any], **_: Any) -> str:
    try:
        command = nmap_service_scan(args["target"], args["ports"])
        return _run(command, float(args.get("timeout", 120)))
    except (KeyError, TypeError, ValueError) as error:
        return json.dumps({"ok": False, "error": str(error)})


def _run(arguments: list[str], timeout: float) -> str:
    result = ToolRunner.default().run("nmap", arguments, timeout=timeout)
    return json.dumps(
        {
            "ok": result.returncode == 0 and not result.timed_out,
            "command": result.command,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timed_out": result.timed_out,
        }
    )
