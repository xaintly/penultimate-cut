from fractions import Fraction
from pathlib import Path

from penultimate_cut.model.project import Clip, MediaRef
from penultimate_cut.ui.clip_properties import ClipPropertiesPanel


def make_clip(name: str = "a.mp4", *, has_video: bool = True, has_audio: bool = True) -> Clip:
    media = MediaRef(
        path=Path(name),
        relative_path=None,
        duration=Fraction(10),
        frame_rate=Fraction(30) if has_video else None,
        has_video=has_video,
        has_audio=has_audio,
    )
    return Clip(media_ref=media, source_in=Fraction(2), source_out=Fraction(7))


def test_initial_state_shows_no_clip_selected(qapp) -> None:
    panel = ClipPropertiesPanel()
    assert panel._name_label.text() == "No clip selected"
    assert not panel._fit_mode_combo.isEnabled()


def test_set_clip_displays_name_and_duration(qapp) -> None:
    panel = ClipPropertiesPanel()
    clip = make_clip("interview.mp4")
    panel.set_clip(clip)

    assert panel._name_label.text() == "interview.mp4"
    assert panel._duration_label.text() == "00:05"  # source_out - source_in = 5s
    assert panel._fit_mode_combo.isEnabled()


def test_set_clip_none_clears_display(qapp) -> None:
    panel = ClipPropertiesPanel()
    panel.set_clip(make_clip())
    panel.set_clip(None)

    assert panel._name_label.text() == "No clip selected"
    assert panel._duration_label.text() == ""
    assert not panel._fit_mode_combo.isEnabled()


def test_fit_mode_combo_reflects_clip_fit_mode(qapp) -> None:
    panel = ClipPropertiesPanel()
    clip = make_clip()
    clip.fit_mode = "crop_to_fill"
    panel.set_clip(clip)

    assert panel._fit_mode_combo.currentData() == "crop_to_fill"
    assert panel._fit_mode_combo.currentText() == "Crop to fill"


def test_audio_only_clip_disables_fit_mode_combo(qapp) -> None:
    panel = ClipPropertiesPanel()
    clip = make_clip(has_video=False, has_audio=True)
    panel.set_clip(clip)

    assert not panel._fit_mode_combo.isEnabled()


def test_changing_fit_mode_emits_signal(qapp) -> None:
    panel = ClipPropertiesPanel()
    panel.set_clip(make_clip())  # defaults to black_bars
    received: list[str] = []
    panel.fit_mode_changed.connect(received.append)

    index = panel._fit_mode_combo.findData("blurred_background")
    panel._fit_mode_combo.setCurrentIndex(index)

    assert received == ["blurred_background"]


def test_set_clip_does_not_emit_fit_mode_changed(qapp) -> None:
    panel = ClipPropertiesPanel()
    received: list[str] = []
    panel.fit_mode_changed.connect(received.append)

    clip = make_clip()
    clip.fit_mode = "crop_to_fill"
    panel.set_clip(clip)

    assert received == []


def test_long_filename_is_elided_and_does_not_widen_panel(qapp) -> None:
    panel = ClipPropertiesPanel()
    panel.show()
    max_width = panel.maximumWidth()
    long_name = "a_very_long_recording_name_from_the_group_project_final_v3_really_final.mp4"
    panel.set_clip(make_clip(long_name))
    qapp.processEvents()

    assert panel.maximumWidth() == max_width
    assert panel.minimumSizeHint().width() <= max_width
    shown = panel._name_label.text()
    assert shown != long_name
    assert shown.endswith(".mp4")  # elided in the middle, keeping the extension
    assert panel._name_label.toolTip() == long_name
    panel.close()


def test_sound_and_fade_controls_show_the_clip_and_report_changes(qapp) -> None:
    panel = ClipPropertiesPanel()
    clip = make_clip()
    clip.volume, clip.fade_out = 60, True
    panel.set_clip(clip)
    received: list[tuple[str, dict]] = []
    panel.options_changed.connect(lambda text, options: received.append((text, options)))

    assert panel._volume_slider.value() == 60
    assert panel._volume_label.text() == "60%"
    assert panel._fade_out_box.isChecked() and not panel._fade_in_box.isChecked()
    assert received == []  # displaying isn't editing

    panel._volume_slider.setValue(120)
    panel._mute_box.setChecked(True)
    panel._fade_in_box.setChecked(True)
    panel._fade_out_box.setChecked(False)

    assert received == [
        ("Change volume", {"volume": 120}),
        ("Mute", {"muted": True}),
        ("Fade in", {"fade_in": True}),
        ("Remove fade out", {"fade_out": False}),
    ]


def test_muted_clip_greys_out_the_volume_slider(qapp) -> None:
    panel = ClipPropertiesPanel()
    clip = make_clip()
    clip.muted = True
    panel.set_clip(clip)
    assert panel._mute_box.isChecked()
    assert not panel._volume_slider.isEnabled()


def test_clip_without_sound_disables_volume_and_mute_but_not_fades(qapp) -> None:
    panel = ClipPropertiesPanel()
    panel.set_clip(make_clip(has_audio=False))
    assert not panel._volume_slider.isEnabled()
    assert not panel._mute_box.isEnabled()
    assert panel._volume_label.text() == ""
    assert panel._fade_in_box.isEnabled() and panel._fade_out_box.isEnabled()
