from __future__ import annotations

import sys
from pathlib import Path

import pytest

from sedna.runners import ToolRunner, nmap_service_scan, nmap_tcp_discovery


def test_runner_executes_only_allowlisted_programs():
    runner = ToolRunner({"python": sys.executable})

    result = runner.run("python", ["-c", "print('ok')"])

    assert result.command[0] == str(Path(sys.executable).resolve())
    assert result.returncode == 0
    assert result.stdout.strip() == "ok"
    assert not result.timed_out


def test_runner_rejects_a_program_outside_its_allowlist():
    with pytest.raises(ValueError, match="not allowed"):
        ToolRunner({}).run("nmap", ["--version"])


def test_runner_returns_a_result_when_command_times_out():
    runner = ToolRunner({"python": sys.executable})

    result = runner.run("python", ["-c", "import time; time.sleep(1)"], timeout=0.01)

    assert result.timed_out
    assert result.returncode is None


def test_nmap_wrappers_build_non_shell_arguments():
    assert nmap_tcp_discovery("10.10.10.3") == [
        "-n",
        "-Pn",
        "-sT",
        "--top-ports",
        "1000",
        "10.10.10.3",
    ]
    assert nmap_service_scan("lame.htb", "22,80,445") == [
        "-n",
        "-Pn",
        "-sT",
        "-sV",
        "-p",
        "22,80,445",
        "lame.htb",
    ]


@pytest.mark.parametrize("target", ["", "host name", "--script=evil"])
def test_nmap_wrappers_reject_invalid_targets(target):
    with pytest.raises(ValueError, match="target"):
        nmap_tcp_discovery(target)


@pytest.mark.parametrize("ports", ["", "22; id", "-p-", "22 abc"])
def test_nmap_service_scan_rejects_invalid_port_specifications(ports):
    with pytest.raises(ValueError, match="ports"):
        nmap_service_scan("10.10.10.3", ports)
