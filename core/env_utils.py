import os
import shutil
from pathlib import Path
from typing import Dict, Optional

FORBIDDEN_API_KEYS = [
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "AZURE_OPENAI_API_KEY"
]

def get_sanitized_env() -> Dict[str, str]:
    """
    Builds a sanitized environment dictionary with all pay-per-use API keys stripped.
    Ensures that official CLI tools operate strictly using user subscription login sessions.
    """
    env = os.environ.copy()
    for k in FORBIDDEN_API_KEYS:
        env.pop(k, None)
    return env

def web_access_enabled() -> bool:
    """config.yaml `web_access.enabled`: lets the CLIs use their own built-in web search / fetch tools
    (Codex `--search`, Claude Code WebSearch/WebFetch). Read-only lookups only; sandboxes are unchanged.
    Always off under the test harness so stubbed argv stays deterministic."""
    if os.environ.get("DIRECTOR_TESTING") == "1":
        return False
    try:
        import yaml
        cfg = yaml.safe_load((Path(__file__).resolve().parent.parent / "config.yaml").read_text(encoding="utf-8")) or {}
        return bool((cfg.get("web_access") or {}).get("enabled", False))
    except Exception:
        return False


def codex_web_args() -> list:
    """Top-level Codex flags (placed before `exec`)."""
    return ["--search"] if web_access_enabled() else []


def claude_web_args() -> list:
    return ["--allowedTools", "WebSearch,WebFetch"] if web_access_enabled() else []


def resolve_cli(name: str) -> Optional[str]:
    """
    Discovers the official CLI executable from OS PATH or standard install locations.
    Returns the resolved absolute path or None.
    """
    # Desktop CLIs live in versioned directories that change after app updates.
    # Prefer native binaries over stale npm wrappers, without changing PATH globally.
    if os.name == 'nt' and os.environ.get('DIRECTOR_TESTING') != '1':
        local = Path(os.environ.get('LOCALAPPDATA', ''))
        roaming = Path(os.environ.get('APPDATA', ''))
        root = {'codex': local / 'OpenAI/Codex/bin',
                'claude': roaming / 'Claude/claude-code'}.get(name)
        if root:
            try:
                candidates = sorted(root.glob('*/' + name + '.exe'),
                                    key=lambda p: p.stat().st_mtime, reverse=True)
                if candidates:
                    return str(candidates[0].resolve())
            except OSError:
                pass
    try:
        p = shutil.which(name)
    except (PermissionError, OSError):
        p = None
    if p:
        try:
            return str(Path(p).resolve())
        except (PermissionError, OSError):
            return str(p)

    local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
    app_data = Path(os.environ.get("APPDATA", ""))

    known = {
        "claude": [
            app_data / "npm" / "claude.cmd",
            app_data / "npm" / "claude"
        ],
        "codex": [
            app_data / "npm" / "codex.cmd",
            app_data / "npm" / "codex"
        ],
        "agy": [
            local_app_data / "agy" / "bin" / "agy.exe",
            local_app_data / "agy" / "bin" / "agy.cmd"
        ]
    }

    for cand in known.get(name, []):
        # A candidate that cannot even be stat'ed (WinError 5 / PermissionError / OSError) is skipped;
        # the next candidate is tried and, if none works, None (= CLI unavailable) is returned.
        try:
            present = cand.exists()
        except (PermissionError, OSError):
            continue
        if not present:
            continue
        try:
            bin_dir = str(cand.parent)
            if bin_dir not in os.environ.get("PATH", ""):
                os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
            try:
                return str(cand.resolve())
            except (PermissionError, OSError):
                return str(cand)
        except (PermissionError, OSError):
            continue

    return None


def run_capture(argv, cwd=None, timeout=None, env=None):
    """
    subprocess.run wrapper for short non-AI commands (git, --version probes).
    Always shell=False, captured output decoded as UTF-8 with replacement so Korean/Unicode
    paths on Windows (cp949 default codec) never raise inside the reader thread.
    """
    import subprocess
    return subprocess.run(
        list(argv),
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=timeout,
        env=env,
    )


def probe_version(executable, timeout: float = 5.0, arg: str = "--version") -> str:
    """
    Runtime-availability probe (NOT an inference call). Runs `<executable> --version` through
    ProcessTreeRunner: shell=False, hard timeout, stdout/stderr drained, whole process tree killed on
    timeout (Windows taskkill /T). Returns the first stdout line or "UNKNOWN".
    """
    from core.process_runner import ProcessTreeRunner
    if not executable:
        return "UNKNOWN"
    try:
        res = ProcessTreeRunner.run(
            argv=[str(executable), arg],
            env=get_sanitized_env(),
            timeout=float(timeout),
            no_output_timeout=None,
            heartbeat_interval=max(float(timeout), 1.0),
        )
    except Exception:
        return "UNKNOWN"
    if res.timed_out or res.exit_code != 0:
        return "UNKNOWN"
    first = (res.stdout or "").strip().split("\n")[0].strip()
    return first or "UNKNOWN"
