from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Literal


AgentSkillState = Literal["not_configured", "not_installed", "installed", "needs_update"]


AGENT_NAMES: dict[str, str] = {
    "codex": "Codex",
    "claude_code": "Claude Code",
    "cursor": "Cursor",
    "copilot_cli": "GitHub Copilot CLI",
}


def get_agent_support_manifest_path() -> Path:
    override = os.getenv("AUTO_EMAIL_SENDER_AGENT_MANIFEST_FILE")
    if override and override.strip():
        return Path(override).expanduser().resolve()

    data_dir = os.getenv("AUTO_EMAIL_SENDER_DATA_DIR")
    if data_dir and data_dir.strip():
        return Path(data_dir).expanduser().resolve() / "agent" / "installation.json"

    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "auto-email-sender-desktop"
    elif sys.platform == "win32":
        app_data = os.getenv("APPDATA")
        base = Path(app_data) if app_data else Path.home() / "AppData" / "Roaming"
        base = base / "auto-email-sender-desktop"
    else:
        state_home = os.getenv("XDG_STATE_HOME")
        base = (Path(state_home).expanduser() if state_home else Path.home() / ".local" / "state") / "auto-email-sender"
    return base / "agent" / "installation.json"


def inspect_agent_skill_installation() -> dict[str, object]:
    manifest_path = get_agent_support_manifest_path()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _not_configured(manifest_path, "尚未启用命令行与 Agent 支持。")
    except (OSError, json.JSONDecodeError):
        return _not_configured(manifest_path, "无法读取 Agent 安装信息；请在个人中心重新安装。", ok=False)

    if not isinstance(manifest, dict) or manifest.get("enabled") is not True:
        return _not_configured(manifest_path, "尚未启用命令行与 Agent 支持。")
    if manifest.get("schema_version") != 4:
        return {
            "ok": False,
            "state": "needs_update",
            "manifest_path": manifest_path.as_posix(),
            "message": "Agent 使用说明来自旧版本，需要在个人中心重新安装。",
            "items": [],
        }

    agents = manifest.get("agents")
    if not isinstance(agents, dict) or not agents:
        return {
            "ok": True,
            "state": "not_installed",
            "manifest_path": manifest_path.as_posix(),
            "message": "尚未为任何 Agent 安装 Skill。",
            "items": [],
        }

    skill_source = manifest.get("skill_source")
    source_hash = _sha256_directory(Path(skill_source)) if isinstance(skill_source, str) else None
    items: list[dict[str, object]] = []
    malformed_agent_entry = False
    for agent_id, record in agents.items():
        if not isinstance(agent_id, str) or not isinstance(record, dict):
            malformed_agent_entry = True
            continue
        target = record.get("skill_target")
        expected_hash = record.get("skill_sha256")
        target_hash = _sha256_directory(Path(target)) if isinstance(target, str) else None
        healthy = (
            isinstance(expected_hash, str)
            and len(expected_hash) == 64
            and target_hash == expected_hash
            and source_hash == expected_hash
        )
        items.append(
            {
                "id": agent_id,
                "name": AGENT_NAMES.get(agent_id, agent_id),
                "state": "installed" if healthy else "needs_update",
                "skill_path": target,
                "message": "已安装官方 Skill" if healthy else "Skill 内容已过期或被修改，需要更新。",
            },
        )

    if malformed_agent_entry or not items:
        return {
            "ok": False,
            "state": "needs_update",
            "manifest_path": manifest_path.as_posix(),
            "message": "Agent 安装信息格式损坏，需要在个人中心重新安装。",
            "items": items,
        }
    if any(item["state"] == "needs_update" for item in items):
        return {
            "ok": False,
            "state": "needs_update",
            "manifest_path": manifest_path.as_posix(),
            "message": "检测到 Agent 使用说明已过期或被修改，需要在个人中心重新安装。",
            "items": items,
        }
    return {
        "ok": True,
        "state": "installed",
        "manifest_path": manifest_path.as_posix(),
        "message": "已安装的 Agent 使用说明为当前官方版本。",
        "items": items,
    }


def _not_configured(manifest_path: Path, message: str, *, ok: bool = True) -> dict[str, object]:
    return {
        "ok": ok,
        "state": "not_configured",
        "manifest_path": manifest_path.as_posix(),
        "message": message,
        "items": [],
    }


def _sha256_directory(directory: Path) -> str | None:
    try:
        if not directory.is_dir() or directory.is_symlink():
            return None
        entries: list[str] = []
        _append_directory_entries(directory, Path(), entries)
    except OSError:
        return None
    canonical_listing = "\n".join(entries)
    if entries:
        canonical_listing += "\n"
    return hashlib.sha256(canonical_listing.encode("utf-8")).hexdigest()


def _append_directory_entries(root: Path, relative_directory: Path, entries: list[str]) -> None:
    directory = root / relative_directory
    children = sorted(directory.iterdir(), key=lambda child: child.name)
    for child in children:
        relative = (relative_directory / child.name).as_posix()
        if child.is_symlink():
            entries.append(f"L\t{relative}\t{os.readlink(child)}")
        elif child.is_dir():
            entries.append(f"D\t{relative}")
            _append_directory_entries(root, relative_directory / child.name, entries)
        elif child.is_file():
            entries.append(f"F\t{relative}\t{_sha256_file(child)}")
        else:
            entries.append(f"O\t{relative}")


def _sha256_file(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
