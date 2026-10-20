from fractions import Fraction
from pathlib import Path

from penultimate_cut.io.otio_io import find_relink_candidates, load_project, save_project
from penultimate_cut.model.project import Clip, Gap, MediaRef, Project, Track


def make_media(tmp_path: Path, name: str = "a.mp4", duration: Fraction = Fraction(10)) -> MediaRef:
    media_path = tmp_path / name
    media_path.write_bytes(b"not real media, just needs to exist")
    return MediaRef(
        path=media_path,
        relative_path=None,
        duration=duration,
        frame_rate=Fraction(30000, 1001),
        has_video=True,
        has_audio=True,
        width=1920,
        height=1080,
    )


def test_round_trip_preserves_clips_gaps_and_exact_times(tmp_path: Path) -> None:
    media = make_media(tmp_path)
    clip1 = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(4))
    clip2 = Clip(
        media_ref=media,
        source_in=Fraction(5),
        source_out=Fraction(9),
        volume_db=-6.5,
        muted=True,
        speed=Fraction(1, 2),
        fit_mode="crop_to_fill",
    )
    track = Track(kind="video", items=[clip1, Gap(duration=Fraction(2)), clip2])
    project = Project(frame_rate=Fraction(30000, 1001), tracks=[track], name="My Project")

    project_path = tmp_path / "project.otio"
    save_project(project, project_path)
    result = load_project(project_path)

    assert result.missing_media == []
    loaded = result.project
    assert loaded.name == "My Project"
    assert loaded.frame_rate == Fraction(30000, 1001)
    assert len(loaded.tracks) == 1

    loaded_track = loaded.tracks[0]
    assert loaded_track.kind == "video"
    assert len(loaded_track.items) == 3

    loaded_clip1, loaded_gap, loaded_clip2 = loaded_track.items
    assert isinstance(loaded_clip1, Clip)
    assert loaded_clip1.source_in == Fraction(0)
    assert loaded_clip1.source_out == Fraction(4)

    assert isinstance(loaded_gap, Gap)
    assert loaded_gap.duration == Fraction(2)

    assert isinstance(loaded_clip2, Clip)
    assert loaded_clip2.source_in == Fraction(5)
    assert loaded_clip2.source_out == Fraction(9)
    assert loaded_clip2.volume_db == -6.5
    assert loaded_clip2.muted is True
    assert loaded_clip2.speed == Fraction(1, 2)
    assert loaded_clip2.fit_mode == "crop_to_fill"


def test_round_trip_dedups_shared_media_ref(tmp_path: Path) -> None:
    media = make_media(tmp_path)
    clip1 = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(2))
    clip2 = Clip(media_ref=media, source_in=Fraction(2), source_out=Fraction(4))
    track = Track(kind="video", items=[clip1, clip2])
    project = Project(frame_rate=Fraction(30), tracks=[track])

    project_path = tmp_path / "project.otio"
    save_project(project, project_path)
    loaded = load_project(project_path).project

    loaded_clip1, loaded_clip2 = loaded.tracks[0].items
    assert loaded_clip1.media_ref is loaded_clip2.media_ref
    assert loaded_clip1.media_ref.id == media.id


def test_round_trip_preserves_odd_frame_rate_exactly(tmp_path: Path) -> None:
    # 30000/1001 (29.97 fps) cannot be represented exactly as a float; the
    # exact Fraction must survive the round trip via metadata, not the
    # OTIO float rate field.
    media = make_media(tmp_path)
    clip = Clip(media_ref=media, source_in=Fraction(1, 3), source_out=Fraction(13, 3))
    track = Track(kind="video", items=[clip])
    project = Project(frame_rate=Fraction(30000, 1001), tracks=[track])

    project_path = tmp_path / "project.otio"
    save_project(project, project_path)
    loaded = load_project(project_path).project

    assert loaded.frame_rate == Fraction(30000, 1001)
    loaded_clip = loaded.tracks[0].items[0]
    assert loaded_clip.source_in == Fraction(1, 3)
    assert loaded_clip.source_out == Fraction(13, 3)


def test_relative_path_used_when_project_moves_with_media(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    media_dir = project_dir / "media"
    media_dir.mkdir()
    media_path = media_dir / "clip.mp4"
    media_path.write_bytes(b"fake media")

    media = MediaRef(
        path=media_path,
        relative_path=None,
        duration=Fraction(5),
        frame_rate=Fraction(30),
        has_video=True,
        has_audio=True,
    )
    clip = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(5))
    track = Track(kind="video", items=[clip])
    project = Project(frame_rate=Fraction(30), tracks=[track])

    project_path = project_dir / "project.otio"
    save_project(project, project_path)

    # Simulate the whole project folder moving on disk: the absolute path
    # recorded at save time is now stale, but the relative path still works.
    new_root = tmp_path / "moved"
    project_dir.rename(new_root)
    moved_project_path = new_root / "project.otio"

    result = load_project(moved_project_path)
    assert result.missing_media == []
    loaded_clip = result.project.tracks[0].items[0]
    assert loaded_clip.media_ref.path == (new_root / "media" / "clip.mp4").resolve()


def test_missing_media_is_reported(tmp_path: Path) -> None:
    media = make_media(tmp_path)
    clip = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(5))
    track = Track(kind="video", items=[clip])
    project = Project(frame_rate=Fraction(30), tracks=[track])

    project_path = tmp_path / "project.otio"
    save_project(project, project_path)

    media.path.unlink()
    result = load_project(project_path)

    assert len(result.missing_media) == 1
    assert result.missing_media[0].id == media.id


def test_still_image_media_round_trips_with_no_frame_rate(tmp_path: Path) -> None:
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"fake image")
    media = MediaRef(
        path=image_path,
        relative_path=None,
        duration=Fraction(5),
        frame_rate=None,
        has_video=True,
        has_audio=False,
    )
    clip = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(5))
    track = Track(kind="video", items=[clip])
    project = Project(frame_rate=Fraction(30), tracks=[track])

    project_path = tmp_path / "project.otio"
    save_project(project, project_path)
    loaded = load_project(project_path).project

    loaded_media = loaded.tracks[0].items[0].media_ref
    assert loaded_media.frame_rate is None
    assert loaded_media.is_still_image
    assert loaded.tracks[0].items[0].source_out == Fraction(5)


def _missing_ref(name: str) -> MediaRef:
    return MediaRef(
        path=Path(f"/does/not/exist/{name}"),
        relative_path=None,
        duration=Fraction(5),
        frame_rate=Fraction(30),
        has_video=True,
        has_audio=True,
    )


def test_find_relink_candidates_matches_by_filename(tmp_path: Path) -> None:
    search_dir = tmp_path / "found_here"
    search_dir.mkdir()
    (search_dir / "clip_a.mp4").write_bytes(b"a")
    (search_dir / "clip_b.mp4").write_bytes(b"b")

    missing = [_missing_ref("clip_a.mp4"), _missing_ref("clip_c.mp4")]
    matches = find_relink_candidates(missing, search_dir)

    assert matches == {missing[0].id: search_dir / "clip_a.mp4"}


def test_find_relink_candidates_does_not_search_subfolders(tmp_path: Path) -> None:
    search_dir = tmp_path / "top"
    search_dir.mkdir()
    nested = search_dir / "nested"
    nested.mkdir()
    (nested / "clip_a.mp4").write_bytes(b"a")

    missing = [_missing_ref("clip_a.mp4")]
    assert find_relink_candidates(missing, search_dir) == {}


def test_find_relink_candidates_returns_empty_for_no_matches(tmp_path: Path) -> None:
    search_dir = tmp_path / "empty"
    search_dir.mkdir()
    assert find_relink_candidates([_missing_ref("clip_a.mp4")], search_dir) == {}
