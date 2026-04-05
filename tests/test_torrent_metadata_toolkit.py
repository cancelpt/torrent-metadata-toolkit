"""Tests for torrent_metadata_toolkit public behaviors."""

from pathlib import Path

from torrent_metadata_toolkit import (
    AppendUniqueTrackers,
    ExportOptions,
    ReplaceTrackers,
    TorrentExportService,
    TorrentFile,
    export_as_bt_torrent,
    export_as_torrent,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"


def _sample_torrent_path() -> Path:
    torrent_files = sorted(FIXTURES_DIR.glob("*.torrent"))
    if not torrent_files:
        raise FileNotFoundError("No .torrent file found in tests/fixtures/ for tests.")
    return torrent_files[0]


def test_parse_sample_torrent_file():
    """Parse the sample torrent and expose expected core metadata."""
    sample = _sample_torrent_path()

    torrent = TorrentFile(str(sample))

    assert torrent.info_hash == sample.stem
    assert torrent.file_name
    assert torrent.piece_length > 0
    assert torrent.piece_count > 0
    assert torrent.trackers_count > 0


def test_export_updates_trackers_and_metadata(tmp_path: Path):
    """Default export should replace trackers and persist metadata."""
    sample = _sample_torrent_path()
    source_torrent = TorrentFile(str(sample))

    trackers = [
        "udp://tracker.opentrackr.org:1337/announce",
        "http://tracker.example.com/announce",
    ]

    result = export_as_torrent(
        source_torrent.torrent_data,
        ExportOptions(
            trackers=trackers,
            source="torrent-metadata-toolkit-tests",
            comment="generated in pytest",
            created_by="pytest",
            creation_date=1700000000,
            output_dir=str(tmp_path),
        ),
    )

    assert result.success is True
    assert Path(result.output_path).exists()
    assert result.output_name
    assert result.torrent.trackers == trackers
    assert result.torrent.trackers_count == len(trackers)

    reloaded = TorrentFile(result.output_path)
    assert reloaded.announce == trackers[0]
    assert reloaded.trackers == trackers
    assert reloaded.trackers_count == len(trackers)
    assert reloaded.comment == "generated in pytest"
    assert reloaded.source == "torrent-metadata-toolkit-tests"
    assert reloaded.created_by == "pytest"
    assert reloaded.creation_date == 1700000000


def test_append_unique_strategy_keeps_existing_trackers(tmp_path: Path):
    """Append strategy should keep existing trackers and add unique new trackers."""
    sample = _sample_torrent_path()
    source_torrent = TorrentFile(str(sample))

    existing = list(source_torrent.trackers)
    assert existing

    extra_tracker = "http://tracker.example.com/announce"
    service = TorrentExportService(tracker_strategy=AppendUniqueTrackers())

    result = service.export(
        source_torrent.torrent_data,
        ExportOptions(
            trackers=[existing[0], extra_tracker],
            output_dir=str(tmp_path),
            creation_date=1700000001,
        ),
    )

    assert result.success is True
    assert len(result.torrent.trackers) == len(existing) + 1
    assert result.torrent.trackers[-1] == extra_tracker


def test_replace_strategy_drops_existing_trackers(tmp_path: Path):
    """Replace strategy should ignore existing trackers and keep only requested trackers."""
    sample = _sample_torrent_path()
    source_torrent = TorrentFile(str(sample))

    new_trackers = [
        "udp://tracker.opentrackr.org:1337/announce",
        "http://tracker.example.com/announce",
    ]
    service = TorrentExportService(tracker_strategy=ReplaceTrackers())

    result = service.export(
        source_torrent.torrent_data,
        ExportOptions(
            trackers=new_trackers,
            output_dir=str(tmp_path),
            creation_date=1700000002,
        ),
    )

    assert result.success is True
    assert result.torrent.trackers == new_trackers
    assert result.torrent.trackers_count == len(new_trackers)


def test_change_trackers_only_keeps_other_fields_unchanged(tmp_path: Path):
    """Changing trackers only should keep all non-tracker metadata unchanged."""
    sample = _sample_torrent_path()
    source_torrent = TorrentFile(str(sample))

    new_trackers = [
        "udp://tracker.opentrackr.org:1337/announce",
        "http://tracker.example.com/announce",
    ]

    result = export_as_torrent(
        source_torrent.torrent_data,
        ExportOptions(
            trackers=new_trackers,
            output_dir=str(tmp_path),
        ),
    )

    assert result.success is True
    reloaded = TorrentFile(result.output_path)

    # Tracker fields changed.
    assert reloaded.trackers == new_trackers
    assert reloaded.trackers_count == len(new_trackers)
    assert reloaded.announce == new_trackers[0]

    # Everything else stays the same.
    assert reloaded.info_hash == source_torrent.info_hash
    assert reloaded.file_name == source_torrent.file_name
    assert reloaded.files == source_torrent.files
    assert reloaded.piece_length == source_torrent.piece_length
    assert reloaded.piece_count == source_torrent.piece_count
    assert reloaded.comment == source_torrent.comment
    assert reloaded.source == source_torrent.source
    assert reloaded.private == source_torrent.private
    assert reloaded.created_by == source_torrent.created_by
    assert reloaded.creation_date == source_torrent.creation_date


def test_bt_seed_options_preset_applies_bt_defaults(tmp_path: Path):
    """BT preset options should apply BT metadata defaults in one call."""
    sample = _sample_torrent_path()
    source_torrent = TorrentFile(str(sample))

    new_trackers = [
        "udp://tracker.opentrackr.org:1337/announce",
        "http://tracker.example.com/announce",
    ]
    result = export_as_torrent(
        source_torrent.torrent_data,
        ExportOptions.bt_seed(
            trackers=new_trackers,
            output_dir=str(tmp_path),
            creation_date=1700000003,
        ),
    )

    assert result.success is True
    reloaded = TorrentFile(result.output_path)
    assert reloaded.trackers == new_trackers
    assert reloaded.source == "pt2bt"
    assert reloaded.comment == "BT is Best Taste!"
    assert reloaded.private is False
    assert reloaded.created_by == "pt2bt v0.1"
    assert reloaded.creation_date == 1700000003


def test_export_as_bt_torrent_is_one_step_quick_export(tmp_path: Path):
    """Helper API should expose one-step BT-style export."""
    sample = _sample_torrent_path()
    source_torrent = TorrentFile(str(sample))

    new_trackers = [
        "udp://tracker.opentrackr.org:1337/announce",
        "http://tracker.example.com/announce",
    ]
    result = export_as_bt_torrent(
        source_torrent.torrent_data,
        new_trackers,
        output_dir=str(tmp_path),
        creation_date=1700000004,
    )

    assert result.success is True
    reloaded = TorrentFile(result.output_path)
    assert reloaded.trackers == new_trackers
    assert reloaded.source == "pt2bt"
    assert reloaded.comment == "BT is Best Taste!"
    assert reloaded.private is False
    assert reloaded.created_by == "pt2bt v0.1"
    assert reloaded.creation_date == 1700000004
