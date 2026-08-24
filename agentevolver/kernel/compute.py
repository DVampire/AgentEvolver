"""What the machine the kernel runs on is running on.

GPUs, CPU, memory and disk — the whole host, not a slice of it, because that is
exactly what the agent and the kernel get. There is no container between them.

Module-level rather than a class, and deliberately: the only state here is *one*
cached GPU reading and the task refreshing it. A class would hold the same single
cache behind a `self` that never distinguishes one caller from another.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import List, Optional

from agentevolver.kernel.server import kernel_manager
from agentevolver.kernel.types import ComputeStatus
from agentevolver.logger import logger

#: How often the background sampler re-reads the GPUs.
GPU_SAMPLE_SECONDS = 10.0
#: Stop sampling once nobody has asked this long — an idle gateway should not
#: keep a subprocess running every ten seconds forever.
GPU_IDLE_SECONDS = 120.0

#: Latest GPU reading, refreshed by a background sampler rather than by whoever
#: happens to ask. nvidia-smi takes ~0.5s on a loaded machine: reading it inside
#: the request made every poll wait for it, and two open tabs paid for it twice.
_gpus: List[dict] = []
_wanted_at: float = 0.0
_sampler: Optional[asyncio.Task] = None


async def _sample() -> None:
    """Refresh the GPU reading until nobody has looked for a while."""
    global _gpus, _sampler
    while time.time() - _wanted_at < GPU_IDLE_SECONDS:
        # In a thread, so the half second nvidia-smi takes is not half a second
        # in which the gateway cannot stream the agent's reply.
        _gpus = await asyncio.to_thread(_read_gpus)
        await asyncio.sleep(GPU_SAMPLE_SECONDS)
    _sampler = None


async def gpus() -> List[dict]:
    """The most recent GPU reading, taken without waiting for one."""
    global _gpus, _wanted_at, _sampler
    _wanted_at = time.time()
    if _sampler is None or _sampler.done():
        # The very first caller waits for one sample; after that the panel is
        # answered from the cache and the sampler keeps it fresh.
        _gpus = await asyncio.to_thread(_read_gpus)
        _sampler = asyncio.create_task(_sample(), name="gpu-sampler")
    return _gpus


def stop_sampling() -> None:
    """Drop the background sampler. Called when the gateway shuts down."""
    global _sampler
    if _sampler is not None:
        _sampler.cancel()
        _sampler = None


async def status(session_id: str) -> ComputeStatus:
    """The Compute panel's answer, whether or not a kernel has started."""
    kernel = kernel_manager.status(session_id)
    readings = await gpus()
    total = used = free = None
    try:
        meminfo = dict(
            line.split(":", 1) for line in
            Path("/proc/meminfo").read_text(encoding="utf-8").strip().splitlines())
        total = int(meminfo["MemTotal"].split()[0]) // 1024
        used = total - int(meminfo["MemAvailable"].split()[0]) // 1024
    except (OSError, KeyError, ValueError, IndexError):
        logger.warning("| ⚠️ Could not read /proc/meminfo")
    try:
        free = shutil.disk_usage(kernel.workspace or "/").free // (1024 * 1024)
    except OSError:
        pass

    return ComputeStatus(
        running=kernel.running, busy=kernel.busy, gpus=readings,
        cpu_count=os.cpu_count(), memory_total_mb=total, memory_used_mb=used,
        disk_free_mb=free, executions=kernel.executions,
    )


def _read_gpus() -> List[dict]:
    """What nvidia-smi reports, or an empty list on a machine without GPUs.

    Empty is not an error — plenty of hosts have no NVIDIA card — so the panel
    says "no GPU detected" rather than showing a broken meter.
    """
    binary = shutil.which("nvidia-smi")
    if not binary:
        return []
    try:
        completed = subprocess.run(  # noqa: S603 — fixed argv, no shell
            [binary, "--query-gpu=index,name,memory.used,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(f"| ⚠️ nvidia-smi did not answer: {exc}")
        return []
    if completed.returncode != 0:
        return []
    found: List[dict] = []
    for line in completed.stdout.strip().splitlines():
        fields = [part.strip() for part in line.split(",")]
        if len(fields) != 5 or not fields[0].isdigit():
            continue
        found.append({
            "index": int(fields[0]), "name": fields[1],
            "memory_used_mb": int(fields[2]), "memory_total_mb": int(fields[3]),
            "utilization_percent": int(fields[4]),
        })
    return found


__all__ = ["GPU_IDLE_SECONDS", "GPU_SAMPLE_SECONDS", "gpus", "status", "stop_sampling"]
