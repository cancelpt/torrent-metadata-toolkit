"""Core torrent parsing and export utilities."""

from __future__ import annotations

import copy
import hashlib
import logging
import math
import os
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Protocol

import bencodepy

logging.basicConfig(level=logging.INFO)

BT_DEFAULT_SOURCE = "pt2bt"
BT_DEFAULT_COMMENT = "BT is Best Taste!"
BT_DEFAULT_PRIVATE = False
BT_DEFAULT_CREATED_BY = "pt2bt v0.1"


def safe_decode(value: bytes | str) -> str:
    """Decode torrent bytes with utf-8 fallback chain, returning text safely."""
    if isinstance(value, str):
        return value
    if not isinstance(value, bytes):
        return str(value)

    for encoding in ("utf-8", "gbk", "big5"):
        try:
            return value.decode(encoding)
        except UnicodeDecodeError:
            continue
    return value.decode("utf-8", errors="replace")


class TorrentFile:  # pylint: disable=too-many-instance-attributes
    """Representation of a decoded `.torrent` file."""

    @dataclass(frozen=True)
    class File:
        """Single file entry inside a multi-file torrent."""

        path: str
        size: int
        name: str

    def __init__(self, torrent_file: str | dict[bytes, Any]):
        """Load torrent metadata from a file path or decoded dictionary."""
        self.private: bool | None = None
        self.comment: str | None = None
        self.created_by: str | None = None
        self.creation_date: int | None = None
        self.trackers: list[str] = []
        self.trackers_count: int = 0
        self.announce: str = ""
        self.file_path: str | dict[bytes, Any] = torrent_file
        self.torrent_data: dict[bytes, Any] | None = None
        self.file_name: str | None = None
        self.source: str | None = None
        self.piece_length: int | None = None
        self.piece_length_k: int = 0
        self.piece_count: int | None = None
        self.files: list[TorrentFile.File] = []
        self._info_hash: str | None = None
        self._is_info_hash_calculated = False

        try:
            if isinstance(torrent_file, dict):
                self.torrent_data = torrent_file
            else:
                with open(torrent_file, "rb") as file_obj:
                    data = file_obj.read()
                    self.torrent_data = bencodepy.decode(data)
        except FileNotFoundError as err:
            raise FileNotFoundError(f"Torrent file {torrent_file} does not exist") from err
        except (OSError, TypeError, ValueError, bencodepy.BencodeDecodeError) as err:
            raise RuntimeError(f"Failed to read torrent file: {err}") from err

        if not self.torrent_data:
            raise ValueError("Unable to parse torrent file")

        self._extract_info()

    def _extract_info(self) -> None:
        """Populate instance fields from decoded torrent metadata."""
        info_dict = self.torrent_data.get(b"info", {})

        self.file_name = safe_decode(info_dict.get(b"name", b"unknown"))
        self.private = info_dict.get(b"private", False) == 1

        files = info_dict.get(b"files", [])
        self.files = []

        self.piece_length = int(info_dict.get(b"piece length", 0) or 0)
        self.piece_length_k = int(math.log2(self.piece_length)) if self.piece_length > 0 else 0
        self.piece_count = int(len(info_dict.get(b"pieces", b"")) / 20)

        for file_entry in files:
            path_parts = file_entry.get(b"path", [])
            length = int(file_entry.get(b"length", 0))

            built_path: PurePosixPath | None = None
            for part in path_parts:
                part_str = safe_decode(part)
                built_path = (
                    PurePosixPath(part_str) if built_path is None else built_path / part_str
                )

            if built_path is None:
                continue

            self.files.append(
                self.File(
                    path=built_path.as_posix(),
                    size=length,
                    name=built_path.name,
                )
            )

        self.comment = safe_decode(self.torrent_data.get(b"comment", b""))
        self.source = safe_decode(info_dict.get(b"source", b""))
        self.created_by = safe_decode(self.torrent_data.get(b"created by", b""))
        self.creation_date = int(self.torrent_data.get(b"creation date", 0) or 0)

        trackers_bytes_list = self.torrent_data.get(b"announce-list", [])
        self.announce = safe_decode(self.torrent_data.get(b"announce", b""))

        if not trackers_bytes_list:
            self.trackers = [self.announce] if self.announce else []
        else:
            self.trackers = []
            for trackers_bytes in trackers_bytes_list:
                for tracker in trackers_bytes:
                    tracker_str = safe_decode(tracker).strip()
                    if tracker_str:
                        self.trackers.append(tracker_str)

        self.trackers_count = len(self.trackers)

    @property
    def info_hash(self) -> str:
        """Return SHA-1 info-hash of the current info dictionary."""
        if self._is_info_hash_calculated:
            return self._info_hash or ""

        info_section = self.torrent_data.get(b"info")
        self._info_hash = hashlib.sha1(bencodepy.encode(info_section)).hexdigest()
        self._is_info_hash_calculated = True
        return self._info_hash

    def change_announce(self, announce_list: list[str]) -> None:
        """Replace announce and announce-list fields with the provided trackers."""
        cleaned_list = [tracker.strip() for tracker in announce_list if tracker and tracker.strip()]
        if cleaned_list:
            self.torrent_data[b"announce-list"] = [[item.encode() for item in cleaned_list]]
            self.torrent_data[b"announce"] = cleaned_list[0].encode()
            self.announce = cleaned_list[0]
        else:
            self.torrent_data[b"announce-list"] = []
            self.torrent_data[b"announce"] = b""
            self.announce = ""

        self.trackers = cleaned_list
        self.trackers_count = len(cleaned_list)

    def add_trackers(self, new_trackers: list[str]) -> None:
        """Add new trackers to the existing list."""
        current_trackers = [
            tracker.strip() for tracker in self.trackers if tracker and tracker.strip()
        ]

        existing_set = set(current_trackers)
        to_add: list[str] = []
        for tracker in new_trackers:
            if tracker:
                tracker_stripped = tracker.strip()
                if tracker_stripped and tracker_stripped not in existing_set:
                    to_add.append(tracker_stripped)
                    existing_set.add(tracker_stripped)

        if not to_add:
            if len(current_trackers) != len(self.trackers):
                self.change_announce(current_trackers)
            return

        self.change_announce(current_trackers + to_add)

    def change_creation_date(self, date: int) -> None:
        """Update torrent creation date."""
        self.torrent_data[b"creation date"] = date
        self.creation_date = date

    def change_comment(self, comment: str) -> None:
        """Update comment and invalidate cached info-hash."""
        self.torrent_data[b"comment"] = comment.encode()
        self.comment = comment
        self._is_info_hash_calculated = False

    def change_source(self, source: str) -> None:
        """Update source and invalidate cached info-hash."""
        self.torrent_data[b"info"][b"source"] = source.encode()
        self.source = source
        self._is_info_hash_calculated = False

    def change_created_by(self, created_by: str) -> None:
        """Update `created by` field."""
        self.torrent_data[b"created by"] = created_by.encode()
        self.created_by = created_by

    def change_private(self, private: bool) -> None:
        """Update private flag and invalidate cached info-hash."""
        self.torrent_data[b"info"][b"private"] = 1 if private else 0
        self._is_info_hash_calculated = False

    def save(self, save_path: str) -> bool:
        """Serialize torrent data and save it to `save_path`."""
        try:
            dir_path = os.path.dirname(save_path)
            if dir_path and not os.path.exists(dir_path):
                os.makedirs(dir_path)

            with open(save_path, "wb") as file_obj:
                file_obj.write(bencodepy.encode(self.torrent_data))
            return True
        except (OSError, TypeError, ValueError) as err:
            logging.error("Failed to save torrent file: %s", err)
            return False


@dataclass(frozen=True)
class ExportOptions:  # pylint: disable=too-few-public-methods,too-many-instance-attributes
    """Parameter object for torrent export behavior."""

    trackers: Sequence[str] | None = None
    source: str | None = None
    comment: str | None = None
    private: bool | None = None
    created_by: str | None = None
    creation_date: int | None = None
    output_name: str | None = None
    output_dir: str = ""

    @classmethod
    def bt_seed(
        cls,
        trackers: Sequence[str],
        *,
        output_dir: str = "",
        output_name: str | None = None,
        creation_date: int | None = None,
    ) -> ExportOptions:
        """Build preset options for quick BT-style export."""
        resolved_creation_date = int(time.time()) if creation_date is None else creation_date
        return cls(
            trackers=trackers,
            source=BT_DEFAULT_SOURCE,
            comment=BT_DEFAULT_COMMENT,
            private=BT_DEFAULT_PRIVATE,
            created_by=BT_DEFAULT_CREATED_BY,
            creation_date=resolved_creation_date,
            output_name=output_name,
            output_dir=output_dir,
        )


@dataclass(frozen=True)
class ExportResult:  # pylint: disable=too-few-public-methods
    """Structured export result."""

    success: bool
    output_name: str
    output_path: str
    torrent: TorrentFile


class TrackerMergeStrategy(Protocol):  # pylint: disable=too-few-public-methods
    """Strategy interface for deriving output tracker list."""

    def merge(self, existing: Sequence[str], requested: Sequence[str]) -> list[str]:
        """Return merged trackers for export."""


class OutputNameStrategy(Protocol):  # pylint: disable=too-few-public-methods
    """Strategy interface for generating export file names."""

    def build_name(self, torrent: TorrentFile) -> str:
        """Return output file name."""


class ReplaceTrackers:  # pylint: disable=too-few-public-methods
    """Tracker strategy that fully replaces existing trackers."""

    def merge(self, existing: Sequence[str], requested: Sequence[str]) -> list[str]:
        """Return only requested trackers after cleanup."""
        del existing
        return [tracker.strip() for tracker in requested if tracker and tracker.strip()]


class AppendUniqueTrackers:  # pylint: disable=too-few-public-methods
    """Tracker strategy that appends unique trackers to existing ones."""

    def merge(self, existing: Sequence[str], requested: Sequence[str]) -> list[str]:
        """Append requested trackers that are not already present."""
        merged = [tracker.strip() for tracker in existing if tracker and tracker.strip()]
        known = set(merged)

        for tracker in requested:
            tracker_str = tracker.strip()
            if tracker_str and tracker_str not in known:
                merged.append(tracker_str)
                known.add(tracker_str)

        return merged


class DefaultOutputNameStrategy:  # pylint: disable=too-few-public-methods
    """Default file-naming strategy for exported torrent files."""

    def build_name(self, torrent: TorrentFile) -> str:
        """Build sanitized output name using info-hash prefix and torrent name."""
        hash_prefix = torrent.info_hash[:6].upper()
        raw_name = f"[BT].[{hash_prefix}].{torrent.file_name}.torrent"
        return re.sub(r'[\\/:*?"<>|]', "_", raw_name)


class TorrentExportService:  # pylint: disable=too-few-public-methods
    """Application service that exports torrent data using configurable strategies."""

    def __init__(
        self,
        tracker_strategy: TrackerMergeStrategy | None = None,
        output_name_strategy: OutputNameStrategy | None = None,
        clock: Callable[[], int] | None = None,
    ):
        """Create service with optional strategy overrides."""
        self._tracker_strategy = tracker_strategy or ReplaceTrackers()
        self._output_name_strategy = output_name_strategy or DefaultOutputNameStrategy()
        self._clock = clock or (lambda: int(time.time()))

    def export(
        self,
        torrent_data: dict[bytes, Any],
        options: ExportOptions,
    ) -> ExportResult:
        """Export torrent with provided options and return a structured result."""
        export_torrent_file = TorrentFile(copy.deepcopy(torrent_data))

        if options.comment is not None:
            export_torrent_file.change_comment(options.comment)
        if options.source is not None:
            export_torrent_file.change_source(options.source)
        if options.private is not None:
            export_torrent_file.change_private(options.private)
        if options.created_by is not None:
            export_torrent_file.change_created_by(options.created_by)
        if options.creation_date is not None:
            export_torrent_file.change_creation_date(options.creation_date)

        if options.trackers is not None:
            merged_trackers = self._tracker_strategy.merge(
                export_torrent_file.trackers,
                options.trackers,
            )
            export_torrent_file.change_announce(merged_trackers)

        output_name = options.output_name or self._output_name_strategy.build_name(
            export_torrent_file
        )
        output_path = os.path.join(options.output_dir, output_name)
        success = export_torrent_file.save(output_path)

        return ExportResult(
            success=success,
            output_name=output_name,
            output_path=output_path,
            torrent=export_torrent_file,
        )


def export_as_torrent(
    torrent_data: dict[bytes, Any],
    options: ExportOptions,
    service: TorrentExportService | None = None,
) -> ExportResult:
    """Facade function for exporting torrent data with options."""
    export_service = service or TorrentExportService()
    return export_service.export(torrent_data, options)


def export_as_bt_torrent(
    torrent_data: dict[bytes, Any],
    trackers: Sequence[str],
    *,
    output_dir: str = "",
    output_name: str | None = None,
    creation_date: int | None = None,
) -> ExportResult:
    """One-step convenience API for exporting a BT-style torrent."""
    options = ExportOptions.bt_seed(
        trackers=trackers,
        output_dir=output_dir,
        output_name=output_name,
        creation_date=creation_date,
    )
    return export_as_torrent(torrent_data, options)
