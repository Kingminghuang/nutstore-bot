from __future__ import annotations

import base64
import codecs
import difflib
import fnmatch
import json
import mimetypes
import ntpath
import os
import platform
import posixpath
import re
import shutil
import subprocess
import tempfile
import unicodedata
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from smolagents import Tool


DEFAULT_TEXT_MAX_LINES = 2000
DEFAULT_MAX_BYTES = 50 * 1024
GREP_MAX_LINE_CHARS = 500
DEFAULT_TOOL_TIMEOUT_MS = 30_000
GREP_SIDECAR_SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".xlsx"}
_SPACE_VARIANTS = ["\u00a0", "\u1680", "\u2007", "\u202f", "\u3000"]
_DASH_VARIANTS = ["\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2015", "\u2212"]
_QUOTE_VARIANTS = {
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
}
_SUPPORTED_IMAGE_MIMES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "image/bmp",
    "image/tiff",
}
_WINDOWS_DRIVE_ALIAS_PATTERNS = (
    re.compile(r"^/([a-zA-Z]):(?:[\\/]|$)"),
    re.compile(r"^/([a-zA-Z])(?:[\\/]|$)"),
    re.compile(r"^/cygdrive/([a-zA-Z])(?:[\\/]|$)", re.IGNORECASE),
    re.compile(r"^/mnt/([a-zA-Z])(?:[\\/]|$)", re.IGNORECASE),
)


class ToolLayerError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class TextContent:
    type: Literal["text"]
    text: str


@dataclass(frozen=True)
class ImageContent:
    type: Literal["image"]
    data_base64: str
    mime_type: str


ToolContentItem = TextContent | ImageContent


@dataclass(frozen=True)
class TruncationDetails:
    truncated: bool
    truncatedBy: Literal["lines", "bytes"] | None
    totalLines: int
    totalBytes: int
    outputLines: int
    outputBytes: int
    firstLineExceedsLimit: bool
    maxLines: int
    maxBytes: int


@dataclass
class ToolDetails:
    truncation: TruncationDetails | None = None
    entryLimitReached: int | None = None
    resultLimitReached: int | None = None
    matchLimitReached: int | None = None
    linesTruncated: bool | None = None
    diff: str | None = None
    firstChangedLine: int | None = None


@dataclass(frozen=True)
class ToolError:
    code: str
    message: str


@dataclass(frozen=True)
class ToolCall:
    tool_name: str
    args: dict[str, Any]
    call_id: str


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    tool_name: str
    content: list[ToolContentItem]
    details: ToolDetails | None
    is_error: bool
    error: ToolError | None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.details is not None:
            details_payload = {
                key: value
                for key, value in asdict(self.details).items()
                if value is not None
            }
            payload["details"] = details_payload or None
        return payload


def _normalize_os_type(os_type: str | None) -> Literal["windows", "macos", "posix"]:
    if os_type is None:
        system = platform.system().lower()
        if system.startswith("win"):
            return "windows"
        if system == "darwin":
            return "macos"
        return "posix"

    text = os_type.strip().lower()
    if text.startswith("win"):
        return "windows"
    if text in {"darwin", "macos", "mac"}:
        return "macos"
    return "posix"


def _path_module(os_type: str):
    return ntpath if os_type == "windows" else posixpath


def _is_unc_path(path: str) -> bool:
    return path.startswith("\\\\") or path.startswith("//")


def _normalize_unicode_spaces(value: str) -> str:
    out = value
    for token in _SPACE_VARIANTS:
        out = out.replace(token, " ")
    return out


def _normalize_windows_style(path: str) -> str:
    return path.replace("/", "\\")


def _convert_windows_drive_alias(path: str) -> str:
    for pattern in _WINDOWS_DRIVE_ALIAS_PATTERNS:
        match = pattern.match(path)
        if match is None:
            continue
        drive = match.group(1).upper()
        remainder = path[match.end() :].lstrip("\\/")
        if remainder:
            return f"{drive}:\\{remainder}"
        return f"{drive}:\\"
    return path


def _normalize_windows_drive_prefix(path: str) -> str:
    normalized = _normalize_windows_style(_convert_windows_drive_alias(path))
    if re.match(r"^[a-zA-Z]:", normalized):
        return normalized[0].upper() + normalized[1:]
    return normalized


def _is_hidden_name(name: str) -> bool:
    return name.startswith(".")


def _strip_windows_extended_prefix(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def _restore_windows_existing_case(path: str) -> str:
    normalized = _strip_windows_extended_prefix(path)
    if os.name != "nt":
        return normalized

    if _is_unc_path(normalized):
        parts = [part for part in normalized[2:].split("\\") if part]
        if len(parts) < 2:
            return normalized
        prefix_parts = parts[:2]
        probe = "\\\\" + "\\".join(prefix_parts)
        remaining = parts[2:]
        resolved_parts: list[str] = []
        for part in remaining:
            try:
                names = {entry.name.casefold(): entry.name for entry in os.scandir(probe)}
            except OSError:
                break
            match = names.get(part.casefold())
            if match is None:
                break
            resolved_parts.append(match)
            probe = ntpath.join(probe, match)
        suffix = remaining[len(resolved_parts) :]
        return "\\\\" + "\\".join(prefix_parts + resolved_parts + suffix)

    drive, tail = ntpath.splitdrive(normalized)
    if drive == "":
        return normalized

    canonical_drive = drive[0].upper() + ":"
    probe = canonical_drive + "\\"
    remaining = [part for part in tail.split("\\") if part]
    resolved_parts: list[str] = []
    for part in remaining:
        try:
            names = {entry.name.casefold(): entry.name for entry in os.scandir(probe)}
        except OSError:
            break
        match = names.get(part.casefold())
        if match is None:
            break
        resolved_parts.append(match)
        probe = ntpath.join(probe, match)
    suffix = remaining[len(resolved_parts) :]
    if resolved_parts or suffix:
        return canonical_drive + "\\" + "\\".join(resolved_parts + suffix)
    return canonical_drive + "\\"


def _best_effort_windows_path(path: str) -> str:
    normalized = ntpath.normpath(_normalize_windows_drive_prefix(path))
    if os.name != "nt":
        return normalized

    try:
        return _strip_windows_extended_prefix(str(Path(normalized).resolve(strict=True)))
    except OSError:
        return _restore_windows_existing_case(normalized)


def _validate_windows_form(raw: str) -> None:
    normalized = _normalize_windows_drive_prefix(raw)
    if re.fullmatch(r"[A-Za-z]:", normalized):
        raise ToolLayerError("invalid_args", "drive prefix without absolute path is not allowed")
    if re.fullmatch(r"[A-Za-z]:[^\\].*", normalized):
        raise ToolLayerError("invalid_args", "drive relative path is not allowed")
    if _is_unc_path(normalized):
        tail = normalized.replace("/", "\\")[2:]
        segments = [part for part in tail.split("\\") if part]
        if len(segments) < 2:
            raise ToolLayerError("invalid_args", "invalid UNC path")


def resolve_path_arg(input_path: str, cwd: str, os_type: str | None = None) -> str:
    flavor = _normalize_os_type(os_type)
    if not isinstance(input_path, str):
        raise ToolLayerError("invalid_args", "path must be non-empty string")

    raw = _normalize_unicode_spaces(input_path).strip()
    if raw.startswith("@"):
        raw = raw[1:].strip()
    if raw == "":
        raise ToolLayerError("invalid_args", "path must be non-empty string")

    if raw == "~":
        raw = str(Path.home())
    elif raw.startswith("~/") or raw.startswith("~\\"):
        raw = str(Path.home() / raw[2:])

    if flavor == "windows":
        _validate_windows_form(raw)
        pathmod = ntpath
        normalized_cwd = _normalize_windows_drive_prefix(cwd)
        normalized_raw = _normalize_windows_drive_prefix(raw)
        if pathmod.isabs(normalized_raw):
            resolved = pathmod.normpath(normalized_raw)
        else:
            resolved = pathmod.normpath(pathmod.join(normalized_cwd, normalized_raw))
        return _best_effort_windows_path(resolved)

    pathmod = _path_module(flavor)
    if pathmod.isabs(raw):
        resolved = pathmod.normpath(raw)
    else:
        resolved = pathmod.normpath(pathmod.join(cwd, raw))
    return resolved


def _canonical_for_compare(path: str, os_type: str) -> str:
    if os_type == "windows":
        return ntpath.normcase(ntpath.normpath(_normalize_windows_drive_prefix(path)))
    return posixpath.normpath(path)


def path_identity(path: str, os_type: str | None = None) -> str:
    flavor = _normalize_os_type(os_type)
    return _canonical_for_compare(path, flavor)


def under_root(candidate: str, root: str, os_type: str | None = None) -> bool:
    flavor = _normalize_os_type(os_type)
    c = _canonical_for_compare(candidate, flavor)
    r = _canonical_for_compare(root, flavor)
    try:
        common = _path_module(flavor).commonpath([c, r])
    except ValueError:
        return False
    if flavor == "windows":
        return common.casefold() == r.casefold()
    return common == r


def safe_path(input_path: str, workdir: str, os_type: str | None = None) -> str:
    flavor = _normalize_os_type(os_type)
    canonical = resolve_path_arg(input_path, workdir, flavor)
    if not under_root(canonical, workdir, flavor):
        raise ToolLayerError("permission_denied", "path escapes workspace")
    return canonical


def _native_separator(os_type: str) -> str:
    return "\\" if os_type == "windows" else "/"


def _native_path(path_text: str, os_type: str) -> str:
    if os_type == "windows":
        return path_text.replace("/", "\\")
    return path_text.replace("\\", "/")


def _truncate_utf8(text: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    return text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")


def _truncate_head(text: str, max_lines: int, max_bytes: int) -> tuple[str, TruncationDetails | None]:
    lines = text.splitlines()
    total_lines = len(lines)
    total_bytes = len(text.encode("utf-8"))

    limited = lines[: max(0, max_lines)]
    lines_truncated = total_lines > max_lines
    bytes_used = 0
    output_lines: list[str] = []
    bytes_truncated = False
    first_line_exceeds = False

    for idx, line in enumerate(limited):
        piece = line if idx == 0 else "\n" + line
        piece_bytes = len(piece.encode("utf-8"))
        if bytes_used + piece_bytes <= max_bytes:
            output_lines.append(line)
            bytes_used += piece_bytes
            continue

        bytes_truncated = True
        if not output_lines:
            first_line_exceeds = True
            output_lines.append(_truncate_utf8(line, max_bytes))
            bytes_used = len(output_lines[0].encode("utf-8"))
        break

    output_text = "\n".join(output_lines)
    output_bytes = len(output_text.encode("utf-8"))
    output_line_count = len(output_lines)

    if not lines_truncated and not bytes_truncated:
        return output_text, None

    detail = TruncationDetails(
        truncated=True,
        truncatedBy="bytes" if bytes_truncated else "lines",
        totalLines=total_lines,
        totalBytes=total_bytes,
        outputLines=output_line_count,
        outputBytes=output_bytes,
        firstLineExceedsLimit=first_line_exceeds,
        maxLines=max_lines,
        maxBytes=max_bytes,
    )
    return output_text, detail


def _append_notice(text: str, notices: list[str]) -> str:
    if not notices:
        return text
    suffix = "[" + " ".join(notices) + "]"
    if text.strip() == "":
        return suffix
    return text + "\n" + suffix


def _error_result(call: ToolCall, code: str, message: str) -> ToolResult:
    return ToolResult(
        call_id=call.call_id,
        tool_name=call.tool_name,
        content=[],
        details=None,
        is_error=True,
        error=ToolError(code=code, message=message),
    )


class ToolLayer:
    def __init__(
        self,
        workspace_path: str,
        *,
        os_type: str | None = None,
        fd_executable: str | None = None,
        rg_executable: str | None = None,
        timeout_ms: int = DEFAULT_TOOL_TIMEOUT_MS,
    ):
        self.os_type = _normalize_os_type(os_type)
        if self.os_type == "windows":
            self.workdir = resolve_path_arg(workspace_path, workspace_path, self.os_type)
        else:
            self.workdir = str(Path(workspace_path).expanduser().resolve())

        self.fd_executable = (fd_executable or "").strip()
        self.rg_executable = (rg_executable or "").strip()
        self.timeout_ms = max(1000, timeout_ms)

    def execute_tool(self, call: ToolCall, signal: Any | None = None) -> ToolResult:
        del signal
        handlers = {
            "ls": self._tool_ls,
            "find": self._tool_find,
            "grep": self._tool_grep,
            "read": self._tool_read,
            "write": self._tool_write,
            "edit": self._tool_edit,
        }
        if call.tool_name not in handlers:
            return _error_result(call, "invalid_args", "unknown tool")

        try:
            content, details = handlers[call.tool_name](call.args)
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.tool_name,
                content=content,
                details=details,
                is_error=False,
                error=None,
            )
        except ToolLayerError as exc:
            return _error_result(call, exc.code, exc.message)
        except subprocess.TimeoutExpired:
            return _error_result(call, "timeout", "command timed out")
        except Exception as exc:  # noqa: BLE001
            return _error_result(call, "execution_failed", str(exc))

    def execute_tool_dict(self, tool_name: str, args: dict[str, Any], call_id: str | None = None) -> dict[str, Any]:
        cid = call_id or f"call-{uuid.uuid4()}"
        result = self.execute_tool(ToolCall(tool_name=tool_name, args=args, call_id=cid))
        return result.to_dict()

    def _require_string(
        self,
        args: dict[str, Any],
        key: str,
        *,
        optional: bool = False,
        allow_empty: bool = False,
    ) -> str | None:
        if key not in args:
            if optional:
                return None
            raise ToolLayerError("invalid_args", f"{key} is required")
        value = args[key]
        if value is None and optional:
            return None
        if not isinstance(value, str):
            raise ToolLayerError("invalid_args", f"{key} must be string")
        if not optional and not allow_empty and value.strip() == "":
            raise ToolLayerError("invalid_args", f"{key} must be non-empty string")
        return value

    def _optional_int(self, args: dict[str, Any], key: str, default: int, *, minimum: int) -> int:
        if key not in args or args[key] is None:
            return default
        value = args[key]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ToolLayerError("invalid_args", f"{key} must be integer")
        if value < minimum:
            raise ToolLayerError("invalid_args", f"{key} must be >= {minimum}")
        return value

    def _optional_bool(self, args: dict[str, Any], key: str, default: bool) -> bool:
        if key not in args or args[key] is None:
            return default
        value = args[key]
        if not isinstance(value, bool):
            raise ToolLayerError("invalid_args", f"{key} must be boolean")
        return value

    def _checked_path(self, input_path: str) -> Path:
        canonical = safe_path(input_path, self.workdir, self.os_type)
        return Path(canonical)

    def _display_path(self, path: Path, anchor: Path) -> str:
        try:
            rel = path.resolve().relative_to(anchor.resolve())
            text = str(rel)
        except Exception:
            text = str(path)
        if text in {"", "."}:
            text = path.name or str(path)
        return _native_path(text, self.os_type)

    def _tool_ls(self, args: dict[str, Any]) -> tuple[list[ToolContentItem], ToolDetails | None]:
        path = self._require_string(args, "path", optional=True) or "."
        limit = self._optional_int(args, "limit", default=500, minimum=1)
        target = self._checked_path(path)
        if not target.exists():
            raise ToolLayerError("not_found", f"path not found: {target}")
        if not target.is_dir():
            raise ToolLayerError("execution_failed", f"not a directory: {target}")

        entries = sorted(
            (entry for entry in target.iterdir() if not _is_hidden_name(entry.name)),
            key=lambda item: item.name.lower(),
        )
        suffix = _native_separator(self.os_type)
        rendered: list[str] = []
        details = ToolDetails()
        for idx, entry in enumerate(entries):
            if idx >= limit:
                details.entryLimitReached = limit
                break
            label = entry.name + (suffix if entry.is_dir() else "")
            rendered.append(label)

        text = "\n".join(rendered) if rendered else "(empty directory)"
        text, truncation = _truncate_head(text, max_lines=10_000_000, max_bytes=DEFAULT_MAX_BYTES)
        details.truncation = truncation

        notices: list[str] = []
        if details.entryLimitReached is not None:
            notices.append(f"{limit} entries limit reached.")
        if truncation is not None and truncation.truncatedBy == "bytes":
            notices.append(f"{DEFAULT_MAX_BYTES / 1024:.1f}KB limit reached.")
        text = _append_notice(text, notices)
        if asdict(details) == asdict(ToolDetails()):
            details = None
        return [TextContent(type="text", text=text)], details

    def _pick_fd_executable(self) -> str:
        if self.fd_executable != "":
            return self.fd_executable
        return shutil.which("fd") or shutil.which("fdfind") or ""

    def _tool_find(self, args: dict[str, Any]) -> tuple[list[ToolContentItem], ToolDetails | None]:
        pattern = self._require_string(args, "pattern")
        path = self._require_string(args, "path", optional=True) or "."
        limit = self._optional_int(args, "limit", default=1000, minimum=1)
        search_path = self._checked_path(path)
        if not search_path.exists():
            raise ToolLayerError("not_found", f"path not found: {search_path}")
        if not search_path.is_dir():
            raise ToolLayerError("execution_failed", "find path must be a directory")

        fd_executable = self._pick_fd_executable()
        details = ToolDetails()
        results: list[str]
        if fd_executable != "":
            results = self._find_with_fd(fd_executable, search_path, pattern, limit)
        else:
            raise ToolLayerError("execution_failed", "fd executable not found")

        if len(results) >= limit:
            details.resultLimitReached = limit
            results = results[:limit]

        if not results:
            text = "No files found matching pattern"
            return [TextContent(type="text", text=text)], None

        text = "\n".join(results)
        text, truncation = _truncate_head(text, max_lines=10_000_000, max_bytes=DEFAULT_MAX_BYTES)
        details.truncation = truncation
        notices: list[str] = []
        if details.resultLimitReached is not None:
            notices.append(f"{limit} results limit reached.")
        if truncation is not None and truncation.truncatedBy == "bytes":
            notices.append(f"{DEFAULT_MAX_BYTES / 1024:.1f}KB limit reached.")
        text = _append_notice(text, notices)
        if asdict(details) == asdict(ToolDetails()):
            details = None
        return [TextContent(type="text", text=text)], details

    def _find_with_fd(self, executable: str, root: Path, pattern: str, limit: int) -> list[str]:
        cmd = [
            executable,
            pattern,
            ".",
            "--glob",
            "--color=never",
            "--max-results",
            str(limit),
            "--exclude",
            ".git",
            "--exclude",
            "node_modules",
        ]
        result = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=self.timeout_ms / 1000,
        )
        if result.returncode not in {0, 1}:
            stderr = result.stderr.strip()
            raise ToolLayerError("execution_failed", stderr or "fd command failed")

        suffix = _native_separator(self.os_type)
        outputs: list[str] = []
        for line in result.stdout.splitlines():
            rel = line.strip()
            if rel == "":
                continue
            rel_path = _native_path(rel, self.os_type)
            full = root / rel
            if full.exists() and full.is_dir() and not rel_path.endswith(suffix):
                rel_path = rel_path + suffix
            outputs.append(rel_path)
        return outputs

    def _pick_rg_executable(self) -> str:
        if self.rg_executable != "":
            return self.rg_executable
        return shutil.which("rg") or ""

    def _tool_grep(self, args: dict[str, Any]) -> tuple[list[ToolContentItem], ToolDetails | None]:
        pattern = self._require_string(args, "pattern")
        path = self._require_string(args, "path", optional=True) or "."
        glob = self._require_string(args, "glob", optional=True)
        ignore_case = self._optional_bool(args, "ignore_case", default=False)
        literal = self._optional_bool(args, "literal", default=False)
        context = self._optional_int(args, "context", default=0, minimum=0)
        limit = self._optional_int(args, "limit", default=100, minimum=1)
        search_path = self._checked_path(path)
        if not search_path.exists():
            raise ToolLayerError("not_found", f"path not found: {search_path}")

        rg_executable = self._pick_rg_executable()
        if rg_executable == "":
            raise ToolLayerError("execution_failed", "rg executable not found")

        details = ToolDetails()
        raw_matches, limit_reached = self._grep_with_rg(
            rg_executable=rg_executable,
            search_path=search_path,
            pattern=pattern,
            glob=glob,
            ignore_case=ignore_case,
            literal=literal,
            limit=limit,
        )
        if limit_reached:
            details.matchLimitReached = limit

        if not raw_matches:
            return [TextContent(type="text", text="No matches found")], None

        if context > 0:
            lines = self._render_grep_with_context(search_path, raw_matches, context=context)
        else:
            lines = [f"{item['display_path']}:{item['line_number']}: {item['text']}" for item in raw_matches]

        lines_truncated = False
        clipped_lines: list[str] = []
        for line in lines:
            if len(line) <= GREP_MAX_LINE_CHARS:
                clipped_lines.append(line)
                continue
            clipped_lines.append(line[:GREP_MAX_LINE_CHARS] + "... [truncated]")
            lines_truncated = True
        if lines_truncated:
            details.linesTruncated = True

        text = "\n".join(clipped_lines)
        text, truncation = _truncate_head(text, max_lines=10_000_000, max_bytes=DEFAULT_MAX_BYTES)
        details.truncation = truncation
        notices: list[str] = []
        if details.matchLimitReached is not None:
            notices.append(f"{limit} matches limit reached.")
        if truncation is not None and truncation.truncatedBy == "bytes":
            notices.append(f"{DEFAULT_MAX_BYTES / 1024:.1f}KB limit reached.")
        text = _append_notice(text, notices)
        if asdict(details) == asdict(ToolDetails()):
            details = None
        return [TextContent(type="text", text=text)], details

    def _grep_with_rg(
        self,
        *,
        rg_executable: str,
        search_path: Path,
        pattern: str,
        glob: str | None,
        ignore_case: bool,
        literal: bool,
        limit: int,
    ) -> tuple[list[dict[str, Any]], bool]:
        preprocessor_path = self._create_rg_sidecar_preprocessor()
        cmd = [
            rg_executable,
            "--json",
            "--line-number",
            "--color=never",
            "--hidden",
            "--pre",
            preprocessor_path,
        ]
        cmd.extend(["--pre-glob", "*.pdf"])
        cmd.extend(["--pre-glob", "*.docx"])
        cmd.extend(["--pre-glob", "*.xlsx"])
        if ignore_case:
            cmd.append("--ignore-case")
        if literal:
            cmd.append("--fixed-strings")
        if glob is not None and glob.strip() != "":
            cmd.extend(["--glob", glob])
        cmd.extend([pattern, str(search_path)])

        env = os.environ.copy()
        env["RG_SIDECAR_ROOT"] = str(Path(self.workdir).resolve())

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            matches: list[dict[str, Any]] = []
            limit_reached = False

            assert proc.stdout is not None
            for line in proc.stdout:
                payload = line.strip()
                if payload == "":
                    continue
                try:
                    event = json.loads(payload)
                except Exception:
                    continue
                if event.get("type") != "match":
                    continue
                data = event.get("data") or {}
                path_data = data.get("path") or {}
                path_text = path_data.get("text")
                if not isinstance(path_text, str):
                    continue
                candidate = Path(path_text)
                if not candidate.is_absolute():
                    candidate = (search_path.parent if search_path.is_file() else search_path) / candidate
                anchor = search_path if search_path.is_dir() else Path(self.workdir)
                display_path = self._display_path(candidate, anchor=anchor)
                line_number = int(data.get("line_number") or 0)
                line_text = ((data.get("lines") or {}).get("text") or "").rstrip("\n")
                matches.append(
                    {
                        "path": candidate,
                        "display_path": display_path,
                        "line_number": line_number,
                        "text": line_text,
                    }
                )
                if len(matches) >= limit:
                    limit_reached = True
                    proc.terminate()
                    break

            try:
                _, stderr = proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                _, stderr = proc.communicate()

            rc = proc.returncode or 0
            if not limit_reached and rc not in {0, 1}:
                message = (stderr or "").strip()
                if "regex parse error" in message.lower():
                    raise ToolLayerError("invalid_args", message or "invalid regex pattern")
                raise ToolLayerError("execution_failed", message or "rg command failed")
            return matches, limit_reached
        finally:
            try:
                Path(preprocessor_path).unlink(missing_ok=True)
            except Exception:
                pass

    def _render_grep_with_context(
        self,
        search_path: Path,
        matches: list[dict[str, Any]],
        *,
        context: int,
    ) -> list[str]:
        cache: dict[Path, list[str]] = {}
        rendered: list[str] = []
        for item in matches:
            path = item["path"]
            if path not in cache:
                cache[path] = self._read_context_lines_for_grep_match(path)
            lines = cache[path]
            if not lines:
                rendered.append(f"{item['display_path']}:{item['line_number']}: {item['text']}")
                continue

            line_no = int(item["line_number"])
            start = max(1, line_no - context)
            end = min(len(lines), line_no + context)
            for idx in range(start, end + 1):
                text = lines[idx - 1]
                if idx == line_no:
                    rendered.append(f"{item['display_path']}:{idx}: {text}")
                else:
                    rendered.append(f"{item['display_path']}-{idx}- {text}")
        return rendered

    def _read_context_lines_for_grep_match(self, source_path: Path) -> list[str]:
        sidecar_path = self._sidecar_path_for_source(source_path)
        if sidecar_path is not None:
            try:
                return sidecar_path.read_text(encoding="utf-8").splitlines()
            except Exception:
                pass
        try:
            return source_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return []

    def _sidecar_path_for_source(self, source_path: Path) -> Path | None:
        ext = source_path.suffix.lower()
        if ext not in GREP_SIDECAR_SUPPORTED_EXTENSIONS:
            return None

        workspace_root = Path(self.workdir).resolve()
        try:
            source_resolved = source_path.resolve()
            relative = source_resolved.relative_to(workspace_root)
        except Exception:
            return None

        sidecar_base = workspace_root / ".sidecar" / relative
        if ext in {".pdf", ".docx"}:
            return sidecar_base.with_name(sidecar_base.name + ".md")
        return sidecar_base.with_name(sidecar_base.name + ".csv")

    def _create_rg_sidecar_preprocessor(self) -> str:
        fd, path = tempfile.mkstemp(prefix="rg-sidecar-pre-", suffix=".py")
        script = """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".xlsx"}


def sidecar_path_for(source: Path, root: Path) -> Path | None:
    ext = source.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return None
    try:
        rel = source.resolve().relative_to(root.resolve())
    except Exception:
        return None
    base = root / ".sidecar" / rel
    if ext in {".pdf", ".docx"}:
        return base.with_name(base.name + ".md")
    return base.with_name(base.name + ".csv")


def main() -> int:
    if len(sys.argv) < 2:
        return 0
    root_value = os.environ.get("RG_SIDECAR_ROOT", "")
    if not root_value:
        return 0
    source = Path(sys.argv[1])
    sidecar = sidecar_path_for(source, Path(root_value))
    if sidecar is None or not sidecar.is_file():
        return 0
    with sidecar.open("rb") as f:
        sys.stdout.buffer.write(f.read())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(script)
            os.chmod(path, 0o700)
        except Exception:
            try:
                os.close(fd)
            except Exception:
                pass
            raise
        return path

    def _resolve_read_path(self, input_path: str) -> Path:
        canonical = resolve_path_arg(input_path, self.workdir, self.os_type)
        candidate = Path(canonical)
        if candidate.exists() or self.os_type != "macos":
            return candidate

        variants = self._macos_name_variants(candidate.name)
        for name in variants:
            alt = candidate.with_name(name)
            if alt.exists():
                return alt
        return candidate

    def _macos_name_variants(self, filename: str) -> list[str]:
        variants = {filename}
        variants.add(filename.replace(" AM", "\u202fAM").replace(" PM", "\u202fPM"))
        variants.add(filename.replace("\u202fAM", " AM").replace("\u202fPM", " PM"))
        variants.add(filename.replace("'", "’"))
        variants.add(filename.replace("’", "'"))
        variants.add(filename.replace('"', "”"))
        variants.add(filename.replace("”", '"'))
        expanded = set()
        for value in variants:
            expanded.add(value)
            expanded.add(unicodedata.normalize("NFD", value))
            expanded.add(unicodedata.normalize("NFC", value))
        return [item for item in expanded if item != filename]

    def _tool_read(self, args: dict[str, Any]) -> tuple[list[ToolContentItem], ToolDetails | None]:
        path = self._require_string(args, "path")
        offset = self._optional_int(args, "offset", default=1, minimum=1)
        limit = self._optional_int(args, "limit", default=DEFAULT_TEXT_MAX_LINES, minimum=1)

        resolved = self._resolve_read_path(path)
        checked_path = self._checked_path(str(resolved))
        if not checked_path.exists():
            raise ToolLayerError("not_found", f"path not found: {checked_path}")
        if not checked_path.is_file():
            raise ToolLayerError("execution_failed", "read path must be a file")

        mime_type, _ = mimetypes.guess_type(str(checked_path))
        if mime_type in _SUPPORTED_IMAGE_MIMES:
            data = checked_path.read_bytes()
            data, resize_note = self._maybe_resize_image(data)
            text = f"Read image file {checked_path.name} ({mime_type}, {len(data)} bytes)."
            if resize_note:
                text += f" {resize_note}"
            return [
                TextContent(type="text", text=text),
                ImageContent(type="image", data_base64=base64.b64encode(data).decode("ascii"), mime_type=mime_type),
            ], None

        raw = checked_path.read_bytes()
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolLayerError("execution_failed", f"file is not UTF-8 text: {checked_path}") from exc

        lines = decoded.splitlines()
        start = offset - 1
        end = min(len(lines), start + limit)
        window = lines[start:end] if start < len(lines) else []
        window_text = "\n".join(window)
        window_text, truncation = _truncate_head(
            window_text,
            max_lines=DEFAULT_TEXT_MAX_LINES,
            max_bytes=DEFAULT_MAX_BYTES,
        )

        details = ToolDetails(truncation=truncation)
        notices: list[str] = []
        next_offset = end + 1
        if truncation is not None:
            next_offset = start + truncation.outputLines + 1
            if truncation.firstLineExceedsLimit:
                notices.append("First line exceeds output limit; use sed/head to inspect a smaller range.")
        if end < len(lines) or truncation is not None:
            notices.append(f"Use offset={max(1, next_offset)} to continue")

        text = _append_notice(window_text, notices)
        if asdict(details) == asdict(ToolDetails()):
            details = None
        return [TextContent(type="text", text=text)], details

    def _maybe_resize_image(self, data: bytes) -> tuple[bytes, str]:
        try:
            from PIL import Image
        except Exception:
            return data, "Image resize skipped (Pillow unavailable)."

        try:
            import io

            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                if width <= 2000 and height <= 2000:
                    return data, ""
                image.thumbnail((2000, 2000))
                out = io.BytesIO()
                fmt = image.format or "PNG"
                image.save(out, format=fmt)
                return out.getvalue(), "Image resized to fit within 2000x2000."
        except Exception:
            return data, "Image resize failed; returned original bytes."

    def _tool_write(self, args: dict[str, Any]) -> tuple[list[ToolContentItem], ToolDetails | None]:
        path = self._require_string(args, "path")
        content = self._require_string(args, "content", allow_empty=True)
        target = self._checked_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        byte_count = len(content.encode("utf-8"))
        text = f"Successfully wrote {byte_count} bytes to {target}"
        return [TextContent(type="text", text=text)], None

    def _tool_edit(self, args: dict[str, Any]) -> tuple[list[ToolContentItem], ToolDetails | None]:
        path = self._require_string(args, "path")
        old_text = self._require_string(args, "old_text")
        new_text = self._require_string(args, "new_text", allow_empty=True)
        if old_text == "":
            raise ToolLayerError("invalid_args", "old_text must be non-empty string")

        target = self._checked_path(path)
        if not target.exists():
            raise ToolLayerError("not_found", f"path not found: {target}")
        if not target.is_file():
            raise ToolLayerError("execution_failed", "edit path must be a file")

        raw = target.read_bytes()
        has_bom = raw.startswith(codecs.BOM_UTF8)
        text = raw.decode("utf-8-sig")
        newline_style = "\n"
        if "\r\n" in text:
            newline_style = "\r\n"

        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        old_norm = old_text.replace("\r\n", "\n").replace("\r", "\n")
        new_norm = new_text.replace("\r\n", "\n").replace("\r", "\n")

        occurrences = normalized.count(old_norm)
        if occurrences == 1:
            updated = normalized.replace(old_norm, new_norm, 1)
        elif occurrences > 1:
            raise ToolLayerError("invalid_args", "old_text is ambiguous")
        else:
            updated = self._fuzzy_replace(normalized, old_norm, new_norm)

        if updated == normalized:
            raise ToolLayerError("invalid_args", "edit is no-op")

        output_text = updated if newline_style == "\n" else updated.replace("\n", newline_style)
        payload = output_text.encode("utf-8")
        if has_bom:
            payload = codecs.BOM_UTF8 + payload
        target.write_bytes(payload)

        diff = "\n".join(
            difflib.unified_diff(
                normalized.splitlines(),
                updated.splitlines(),
                fromfile=str(target),
                tofile=str(target),
                lineterm="",
            )
        )
        first_line = self._first_changed_line(normalized.splitlines(), updated.splitlines())
        details = ToolDetails(diff=diff, firstChangedLine=first_line)
        text_result = f"Successfully replaced text in {target}."
        return [TextContent(type="text", text=text_result)], details

    def _normalize_fuzzy_line(self, line: str) -> str:
        out = line.rstrip()
        for src, dst in _QUOTE_VARIANTS.items():
            out = out.replace(src, dst)
        for dash in _DASH_VARIANTS:
            out = out.replace(dash, "-")
        for space in _SPACE_VARIANTS:
            out = out.replace(space, " ")
        return out

    def _fuzzy_replace(self, source: str, old_text: str, new_text: str) -> str:
        source_lines = source.split("\n")
        old_lines = old_text.split("\n")
        new_lines = new_text.split("\n")

        source_norm = [self._normalize_fuzzy_line(line) for line in source_lines]
        old_norm = [self._normalize_fuzzy_line(line) for line in old_lines]

        hits: list[int] = []
        width = len(old_norm)
        if width == 0:
            raise ToolLayerError("invalid_args", "old_text must be non-empty string")
        for idx in range(0, len(source_norm) - width + 1):
            if source_norm[idx : idx + width] == old_norm:
                hits.append(idx)

        if len(hits) == 0:
            raise ToolLayerError("invalid_args", "old_text not found")
        if len(hits) > 1:
            raise ToolLayerError("invalid_args", "old_text is ambiguous")

        start = hits[0]
        updated_lines = source_lines[:start] + new_lines + source_lines[start + width :]
        return "\n".join(updated_lines)

    def _first_changed_line(self, old_lines: list[str], new_lines: list[str]) -> int:
        max_len = min(len(old_lines), len(new_lines))
        for idx in range(max_len):
            if old_lines[idx] != new_lines[idx]:
                return idx + 1
        if len(old_lines) != len(new_lines):
            return max_len + 1
        return 1


def execute_tool(
    call: ToolCall,
    cwd: str,
    os_type: str | None = None,
    signal: Any | None = None,
    *,
    fd_executable: str | None = None,
    rg_executable: str | None = None,
) -> ToolResult:
    layer = ToolLayer(
        workspace_path=cwd,
        os_type=os_type,
        fd_executable=fd_executable,
        rg_executable=rg_executable,
    )
    return layer.execute_tool(call, signal=signal)


def build_workspace_tools(
    workspace_path: str,
    *,
    fd_executable: str | None = None,
    rg_executable: str | None = None,
    os_type: str | None = None,
):
    layer = ToolLayer(
        workspace_path=workspace_path,
        fd_executable=fd_executable,
        rg_executable=rg_executable,
        os_type=os_type,
    )

    class WorkspaceTool(Tool):
        output_type = "object"

        def __init__(self, tool_layer: ToolLayer):
            super().__init__()
            self._tool_layer = tool_layer

    class LsTool(WorkspaceTool):
        name = "ls"
        description = "List directory entries in lexical order."
        inputs = {
            "path": {
                "type": "string",
                "description": "Directory path under current workspace.",
                "nullable": True,
            },
            "limit": {
                "type": "integer",
                "description": "Max entries to return.",
                "nullable": True,
            },
        }

        def forward(self, path: str = ".", limit: int = 500) -> dict[str, Any]:
            return self._tool_layer.execute_tool_dict(
                "ls", {"path": path, "limit": limit}
            )

    class FindTool(WorkspaceTool):
        name = "find"
        description = "Find files matching a glob pattern."
        inputs = {
            "pattern": {
                "type": "string",
                "description": "Glob pattern, e.g. **/*.py or *.md.",
            },
            "path": {"type": "string", "description": "Search root under workspace."},
            "path": {
                "type": "string",
                "description": "Search root under workspace.",
                "nullable": True,
            },
            "limit": {
                "type": "integer",
                "description": "Max matches to return.",
                "nullable": True,
            },
        }

        def forward(
            self, pattern: str, path: str = ".", limit: int = 1000
        ) -> dict[str, Any]:
            return self._tool_layer.execute_tool_dict(
                "find",
                {"pattern": pattern, "path": path, "limit": limit},
            )

    class GrepTool(WorkspaceTool):
        name = "grep"
        description = "Search file content and return matching lines."
        inputs = {
            "pattern": {
                "type": "string",
                "description": "Regex pattern or literal text.",
            },
            "path": {
                "type": "string",
                "description": "File or directory path under workspace.",
                "nullable": True,
            },
            "glob": {
                "type": "string",
                "description": "Optional glob filter when path is a directory.",
                "nullable": True,
            },
            "ignore_case": {
                "type": "boolean",
                "description": "Whether to perform case-insensitive matching.",
                "nullable": True,
            },
            "literal": {
                "type": "boolean",
                "description": "If true, treat pattern as plain text.",
                "nullable": True,
            },
            "context": {
                "type": "integer",
                "description": "Number of context lines around each match.",
                "nullable": True,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum matches returned.",
                "nullable": True,
            },
        }

        def forward(
            self,
            pattern: str,
            path: str = ".",
            glob: str | None = None,
            ignore_case: bool = False,
            literal: bool = False,
            context: int = 0,
            limit: int = 100,
        ) -> dict[str, Any]:
            return self._tool_layer.execute_tool_dict(
                "grep",
                {
                    "pattern": pattern,
                    "path": path,
                    "glob": glob,
                    "ignore_case": ignore_case,
                    "literal": literal,
                    "context": context,
                    "limit": limit,
                },
            )

    class ReadTool(WorkspaceTool):
        name = "read"
        description = "Read file content with optional line offset and limit."
        inputs = {
            "path": {"type": "string", "description": "File path under workspace."},
            "offset": {
                "type": "integer",
                "description": "Start line index (1-based).",
                "nullable": True,
            },
            "limit": {
                "type": "integer",
                "description": "Max lines to return.",
                "nullable": True,
            },
        }

        def forward(
            self, path: str, offset: int = 1, limit: int = 2000
        ) -> dict[str, Any]:
            return self._tool_layer.execute_tool_dict(
                "read",
                {"path": path, "offset": offset, "limit": limit},
            )

    class WriteTool(WorkspaceTool):
        name = "write"
        description = "Write full file content."
        inputs = {
            "path": {"type": "string", "description": "File path under workspace."},
            "content": {"type": "string", "description": "Full content to write."},
        }

        def forward(self, path: str, content: str) -> dict[str, Any]:
            return self._tool_layer.execute_tool_dict(
                "write", {"path": path, "content": content}
            )

    class EditTool(WorkspaceTool):
        name = "edit"
        description = "Replace one text span in a file."
        inputs = {
            "path": {"type": "string", "description": "File path under workspace."},
            "old_text": {"type": "string", "description": "Text to replace."},
            "new_text": {"type": "string", "description": "Replacement text."},
        }

        def forward(self, path: str, old_text: str, new_text: str) -> dict[str, Any]:
            return self._tool_layer.execute_tool_dict(
                "edit",
                {
                    "path": path,
                    "old_text": old_text,
                    "new_text": new_text,
                },
            )

    return [
        LsTool(layer),
        FindTool(layer),
        GrepTool(layer),
        ReadTool(layer),
        WriteTool(layer),
        EditTool(layer),
    ]
