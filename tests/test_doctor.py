"""`qobit doctor`: each check against a canned machine (no real mpv, ALSA or
PipeWire is touched), the parsers against real-looking output, and the report."""

from __future__ import annotations

from pathlib import Path

import pytest

from qobit import doctor
from qobit.audio.device import DeviceInfo
from qobit.doctor import Check, Probe

MPV_DEVICES = """List of detected audio devices:
  'auto' (Autoselect device)
  'pipewire' (Default (pipewire))
  'pipewire/alsa_output.usb-FiiO_FiiO_K13_R2R-00.analog-stereo' (FiiO K13 R2R Analog Stereo)
  'alsa/front:CARD=K13R2R,DEV=0' (FiiO K13 R2R, USB Audio/Front output / speakers)
"""

CARDS = """ 0 [PCH            ]: HDA-Intel - HDA Intel PCH
                      HDA Intel PCH at 0xf7210000 irq 32
 1 [K13R2R         ]: USB-Audio - FiiO K13 R2R
                      FiiO FiiO K13 R2R at usb-0000:00:14.0-4, high speed
"""

STREAM = """FiiO FiiO K13 R2R at usb-0000:00:14.0-4, high speed : USB Audio

Playback:
  Status: Stop
  Interface 1
    Altset 1
    Format: S16_LE
    Channels: 2
    Rates: 44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000
    Bits: 16
  Interface 1
    Altset 2
    Format: S32_LE
    Channels: 2
    Rates: 44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000
    Bits: 32

Capture:
  Status: Stop
  Interface 2
    Rates: 48000
"""

PW_NARROW = """Found "settings" metadata 32
update: id:0 key:'log.level' value:'2' type:''
update: id:0 key:'clock.rate' value:'48000' type:''
update: id:0 key:'clock.allowed-rates' value:'[ 48000 ]' type:''
update: id:0 key:'clock.force-rate' value:'0' type:''
"""

PW_WIDE = PW_NARROW.replace("[ 48000 ]", "[ 44100 48000 88200 96000 176400 192000 352800 384000 ]")


class Fake(Probe):
    """A machine made of canned files and command outputs."""

    def __init__(
        self, files=None, commands=None, env=None, system="Linux", writable=True, node_exists=True
    ):
        self._node_exists = node_exists
        self.system = system
        self.home = Path("/home/me")
        self.files = files or {}
        self.commands = commands or {}
        self.environ = env or {}
        self._writable = writable

    def run(self, cmd, timeout=5.0):
        return self.commands.get(tuple(cmd))

    def read(self, path):
        return self.files.get(str(path))

    def glob(self, pattern):
        prefix, _, suffix = pattern.partition("*")
        return sorted(
            f for f in self.files if f.startswith(prefix) and f.endswith(suffix.split("*")[-1])
        )

    def exists(self, path):
        return self._node_exists

    def writable(self, path):
        return self._writable

    def which(self, name):
        return (
            f"/usr/bin/{name}"
            if name == "mpv" and self.commands.get(("mpv", "--version"))
            else None
        )

    def env(self, name):
        return self.environ.get(name)


def machine(**kw) -> Fake:
    files = {
        "/proc/asound/cards": CARDS,
        "/proc/asound/card1/stream0": STREAM,
        **kw.pop("files", {}),
    }
    commands = {
        ("mpv", "--version"): "mpv 0.37.0 Copyright © 2000-2023 mpv/MPlayer/mplayer2 projects",
        ("mpv", "--audio-device=help"): MPV_DEVICES,
        ("pw-metadata", "-n", "settings"): PW_WIDE,
        ("pactl", "info"): "Server Name: PulseAudio (on PipeWire 1.0.5)",
        **kw.pop("commands", {}),
    }
    return Fake(files=files, commands=commands, **kw)


def titles(sections, status=None) -> list[str]:
    return [c.title for _, cs in sections for c in cs if status is None or c.status == status]


# ── parsers ──────────────────────────────────────────────────────────────────


def test_parse_mpv_version():
    assert doctor.parse_mpv_version("mpv 0.37.0 Copyright") == (0, 37)
    assert doctor.parse_mpv_version("mpv v0.34.1-dirty") == (0, 34)
    assert doctor.parse_mpv_version("nothing") is None


def test_parse_mpv_conf_reads_the_options_before_the_first_profile():
    text = """# comment
volume=80
--no-audio-pitch-correction
audio-samplerate = 48000
ao="pulse"
[big]
volume=100
"""
    assert doctor.parse_mpv_conf(text) == [
        (2, "volume", "80"),
        (3, "audio-pitch-correction", "no"),
        (4, "audio-samplerate", "48000"),
        (5, "ao", "pulse"),
    ]


def test_parse_asound_cards_and_stream():
    cards = doctor.parse_asound_cards(CARDS)
    assert [(c.num, c.id, c.name) for c in cards] == [
        (0, "PCH", "HDA-Intel - HDA Intel PCH"),
        (1, "K13R2R", "USB-Audio - FiiO K13 R2R"),
    ]
    caps = doctor.parse_stream(STREAM)
    assert caps.rates == [44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000]
    assert caps.bits == [16, 32]  # the capture side's 48000 is not counted


def test_parse_stream_expands_a_continuous_range():
    text = "Playback:\n  Interface 1\n    Rates: 8000 - 96000 (continuous)\n    Bits: 24\n"
    assert doctor.parse_stream(text).rates == [44100, 48000, 88200, 96000]
    assert doctor.parse_stream("Capture:\n    Rates: 48000\n") is None


def test_parse_pipewire_settings_and_pcm_status():
    s = doctor.parse_pw_settings(PW_NARROW)
    assert s["clock.allowed-rates"] == "[ 48000 ]" and s["clock.force-rate"] == "0"
    assert doctor.parse_rates("[ 44100 48000 ]") == [44100, 48000]
    assert doctor.parse_pcm_status("state: RUNNING\nowner_pid   : 4242\n") == ("RUNNING", 4242)
    assert doctor.parse_pcm_status("closed\n") == ("closed", None)


@pytest.mark.parametrize(
    ("device", "kind"),
    [
        (None, "auto"),
        ("alsa/hw:CARD=K13R2R,DEV=0", "alsa-hw"),
        ("alsa/front:CARD=K13R2R,DEV=0", "alsa"),
        ("coreaudio/AppleUSB", "coreaudio"),
        ("pipewire/alsa_output.x", "pipewire"),
        ("pulse/x", "pulse"),
        ("jack/x", "other"),
    ],
)
def test_device_kind(device, kind):
    assert doctor.device_kind(device) == kind


# ── mpv ──────────────────────────────────────────────────────────────────────


def test_no_mpv_is_a_failure_with_how_to_install_and_nothing_else_is_checked():
    sections = doctor.run_checks(Fake(), device=None)
    assert titles(sections, "fail") == ["mpv is not installed"]
    assert len(sections) == 1
    assert doctor.exit_code(sections) == 1


def test_an_old_mpv_warns_about_gapless():
    p = machine(commands={("mpv", "--version"): "mpv 0.33.1 Copyright"})
    checks, _ = doctor.check_mpv(p)
    assert checks[0].status == "warn" and "0.34" in checks[0].detail


# ── the device ───────────────────────────────────────────────────────────────


def test_no_device_chosen_warns_and_says_how():
    (c,) = doctor.check_device(None, [], "Linux")
    assert c.status == "warn" and "qobit set-device" in c.fix


def test_a_saved_device_that_is_gone_fails():
    listed = [DeviceInfo("alsa/front:CARD=X,DEV=0", "x")]
    out = doctor.check_device("alsa/hw:CARD=K13R2R,DEV=0", listed, "Linux")
    assert out[0].status == "fail" and "not there" in out[0].title


@pytest.mark.parametrize(
    ("device", "status"),
    [
        ("alsa/hw:CARD=K13R2R,DEV=0", "ok"),
        ("coreaudio/x", "ok"),
        ("alsa/front:CARD=K13R2R,DEV=0", "warn"),
        ("pipewire/x", "info"),
    ],
)
def test_the_kind_of_device_decides_the_verdict(device, status):
    listed = [DeviceInfo(device, "x")]
    assert doctor.check_device(device, listed, "Linux")[0].status == status


def test_software_volume_on_is_a_warning():
    assert doctor.check_software_volume(True)[0].status == "warn"
    assert doctor.check_software_volume(False)[0].status == "ok"


# ── mpv.conf ─────────────────────────────────────────────────────────────────


def test_mpv_conf_options_that_change_the_samples_fail():
    conf = "volume=80\naudio-samplerate=48000\nreplaygain=track\nmute=yes\nao=pulse\naf=loudnorm\n"
    p = machine(files={"/home/me/.config/mpv/mpv.conf": conf})
    out = doctor.check_mpv_conf(p)
    fails = [c.title for c in out if c.status == "fail"]
    assert len(fails) == 4
    assert any("sets the volume" in t and "~/.config/mpv/mpv.conf:1" in t for t in fails)
    assert any(c.status == "warn" and "audio output" in c.title for c in out)
    assert any(c.status == "info" and "af is ignored" in c.title for c in out)


def test_a_harmless_mpv_conf_is_fine_and_so_is_none():
    p = machine(
        files={"/home/me/.config/mpv/mpv.conf": "volume=100\nvideo-sync=display-resample\n"}
    )
    assert doctor.check_mpv_conf(p)[0].status == "ok"
    assert doctor.check_mpv_conf(machine())[0].title == "No mpv.conf"


def test_mpv_conf_options_in_a_profile_are_not_applied():
    p = machine(files={"/home/me/.config/mpv/mpv.conf": "[loud]\nvolume=50\n"})
    assert doctor.check_mpv_conf(p)[0].status == "ok"


# ── the DAC, ALSA, PipeWire ──────────────────────────────────────────────────


def test_the_dac_is_found_from_the_alsa_card_name_or_a_pipewire_node_name_or_the_only_usb_card():
    p = machine()
    assert doctor.find_dac(p, "alsa/hw:CARD=K13R2R,DEV=0").card.id == "K13R2R"
    assert (
        doctor.find_dac(p, "pipewire/alsa_output.usb-FiiO_K13R2R-00.analog-stereo").card.id
        == "K13R2R"
    )
    assert doctor.find_dac(p, None).card.id == "K13R2R"  # the only card with a stream
    assert doctor.find_dac(Fake(), None) is None


def test_alsa_reports_the_dac_and_a_busy_exclusive_device():
    files = {
        "/proc/asound/card1/pcm0p/sub0/status": "state: RUNNING\nowner_pid   : 4242\n",
        "/proc/4242/comm": "pipewire\n",
    }
    p = machine(files=files)
    dac = doctor.find_dac(p, "alsa/hw:CARD=K13R2R,DEV=0")
    out = doctor.check_alsa(p, "alsa/hw:CARD=K13R2R,DEV=0", dac)
    assert out[0].status == "info" and "K13 R2R" in out[0].title and "384 kHz" in out[0].detail
    busy = [c for c in out if c.status == "warn"]
    assert busy and "in use by pipewire" in busy[0].title


def test_alsa_hw_without_permission_fails_with_the_group_fix():
    p = machine(writable=False)
    dac = doctor.find_dac(p, "alsa/hw:CARD=K13R2R,DEV=0")
    out = doctor.check_alsa(p, "alsa/hw:CARD=K13R2R,DEV=0", dac)
    assert any(c.status == "fail" and "audio group" in c.fix for c in out)


def test_pipewire_locked_to_48k_warns_and_prints_the_dropin_for_this_dac():
    p = machine(commands={("pw-metadata", "-n", "settings"): PW_NARROW})
    device = "pipewire/alsa_output.usb-FiiO_FiiO_K13_R2R-00.analog-stereo"
    dac = doctor.find_dac(p, device)
    (c,) = doctor.check_pipewire(p, device, dac)
    assert c.status == "warn" and "allows 48 kHz" in c.title
    assert "44.1, 88.2, 96, 176.4, 192, 352.8, 384 kHz" in c.detail
    assert (
        "default.clock.allowed-rates = [ 44100 48000 88200 96000 176400 192000 352800 384000 ]"
        in c.fix
    )
    assert "systemctl --user restart pipewire" in c.fix


def test_pipewire_that_follows_the_rate_is_ok_and_a_forced_rate_fails():
    device = "pipewire/x"
    p = machine()
    assert doctor.check_pipewire(p, device, doctor.find_dac(p, device))[0].status == "ok"
    forced = machine(
        commands={
            ("pw-metadata", "-n", "settings"): PW_NARROW.replace(
                "'clock.force-rate' value:'0'", "'clock.force-rate' value:'48000'"
            ).replace("key:'clock.force-rate' value:'0'", "key:'clock.force-rate' value:'48000'")
        }
    )
    (c,) = doctor.check_pipewire(forced, device, doctor.find_dac(forced, device))
    assert c.status == "fail" and "forced to 48 kHz" in c.title


def test_pipewire_is_not_checked_for_a_raw_hw_device():
    p = machine(commands={("pw-metadata", "-n", "settings"): PW_NARROW})
    assert doctor.check_pipewire(p, "alsa/hw:CARD=K13R2R,DEV=0", None) == []


# ── macOS and the terminal ───────────────────────────────────────────────────


def test_macos_wants_a_coreaudio_device_when_one_exists():
    listed = [DeviceInfo("coreaudio/x", "x"), DeviceInfo("pipewire/y", "y")]
    assert doctor.check_macos("pipewire/y", listed)[0].status == "warn"
    assert doctor.check_macos("coreaudio/x", listed) == []
    assert doctor.check_macos(None, []) == []


def test_terminal_true_colour_kitty_and_tmux():
    out = doctor.check_terminal(Fake(env={"COLORTERM": "truecolor", "TERM": "xterm-kitty"}))
    assert [c.status for c in out] == ["ok", "ok"] and "sharp" in out[1].title
    bare = doctor.check_terminal(Fake(env={"TERM": "xterm"}))
    assert bare[0].status == "warn" and "COLORTERM is not set" in bare[0].detail
    tmux = doctor.check_terminal(
        Fake(
            env={"COLORTERM": "truecolor", "TERM": "xterm-kitty", "TMUX": "/tmp/tmux"},
            commands={("tmux", "show", "-gv", "allow-passthrough"): "off\n"},
        )
    )
    assert tmux[1].status == "info" and "allow-passthrough on" in tmux[1].fix


# ── the report ───────────────────────────────────────────────────────────────


def test_a_healthy_machine_says_so_and_exits_zero():
    p = machine(env={"COLORTERM": "truecolor", "TERM": "xterm-kitty"})
    sections = doctor.run_checks(p, device="alsa/hw:CARD=K13R2R,DEV=0")
    text = doctor.render(sections)
    assert "Nothing in the way of bit-perfect output." in text
    assert "To fix" not in text and doctor.exit_code(sections) == 0


def test_the_report_lists_the_fixes_and_counts_problems():
    p = machine(
        files={"/home/me/.config/mpv/mpv.conf": "audio-samplerate=48000\n"},
        commands={("pw-metadata", "-n", "settings"): PW_NARROW},
        env={"COLORTERM": "truecolor"},
    )
    device = "pipewire/alsa_output.usb-FiiO_FiiO_K13_R2R-00.analog-stereo"
    sections = doctor.run_checks(p, device=device, software_volume=True)
    text = doctor.render(sections)
    assert "To fix" in text and "1. " in text
    assert "10-allowed-rates.conf" in text and "Software volume is on" in text
    assert "1 problem, 2 warnings." in text
    assert doctor.exit_code(sections) == 1


def test_run_checks_on_macos_skips_the_linux_section():
    p = machine(system="Darwin", env={"COLORTERM": "truecolor"})
    headings = [h for h, _ in doctor.run_checks(p, device="coreaudio/x")]
    assert "DAC and sound server" not in headings and "Terminal" in headings


def test_the_real_probe_never_raises_for_a_missing_command_or_file(tmp_path):
    p = Probe()
    assert p.run(["qobit-no-such-command"]) is None
    assert p.read(tmp_path / "missing") is None
    assert p.glob(str(tmp_path / "*")) == []


def test_doctor_cli_prints_the_report_and_the_exit_code(monkeypatch, capsys):
    import sys

    from qobit import __main__ as cli

    monkeypatch.setattr(sys, "argv", ["qobit", "doctor"])

    def no_writes():
        raise AssertionError("the start-up clean-ups can write: doctor must run before them")

    monkeypatch.setattr(cli, "purge_legacy_credentials", no_writes)
    monkeypatch.setattr(cli, "move_token_to_keyring", no_writes)
    monkeypatch.setattr(
        doctor, "run_checks", lambda **kw: [("mpv", [Check("fail", "mpv is not installed")])]
    )
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 1
    assert "mpv is not installed" in capsys.readouterr().out


def test_the_device_node_follows_the_dev_number():
    p = machine(writable=False)
    dac = doctor.find_dac(p, "alsa/hw:CARD=K13R2R,DEV=1")
    out = doctor.check_alsa(p, "alsa/hw:CARD=K13R2R,DEV=1", dac)
    assert any(c.status == "fail" and "pcmC1D1p" in c.title for c in out)


def test_a_missing_device_does_not_also_fail_on_access():
    p = machine(writable=False)
    dac = doctor.find_dac(p, "alsa/hw:CARD=K13R2R,DEV=0")
    out = doctor.check_alsa(p, "alsa/hw:CARD=K13R2R,DEV=0", dac, device_listed=False)
    assert [c.status for c in out] == ["info"]


def test_a_profile_in_mpv_conf_is_noted_not_checked():
    p = machine(files={"/home/me/.config/mpv/mpv.conf": "profile=loud\n[loud]\nvolume=50\n"})
    out = doctor.check_mpv_conf(p)
    assert any(c.status == "info" and "profile" in c.title for c in out)
    assert not any(c.status == "fail" for c in out)


def test_the_dac_in_use_by_mpv_may_be_qobit_itself():
    files = {
        "/proc/asound/card1/pcm0p/sub0/status": "state: RUNNING\nowner_pid   : 7\n",
        "/proc/7/comm": "mpv\n",
    }
    p = machine(files=files)
    dac = doctor.find_dac(p, "alsa/hw:CARD=K13R2R,DEV=0")
    out = doctor.check_alsa(p, "alsa/hw:CARD=K13R2R,DEV=0", dac)
    assert any("by mpv (qobit itself, if it is playing)" in c.title for c in out)


# ── what the audit found ─────────────────────────────────────────────────────


def test_a_missing_device_node_is_not_a_permission_problem():
    p = machine(writable=False, node_exists=False)
    dac = doctor.find_dac(p, "alsa/hw:CARD=K13R2R,DEV=0")
    assert not any(
        c.status == "fail" for c in doctor.check_alsa(p, "alsa/hw:CARD=K13R2R,DEV=0", dac)
    )


def test_mpv_conf_defaults_and_inactive_options_are_not_failures():
    conf = "audio-channels=auto-safe\nreplaygain-preamp=0.0\nreplaygain-fallback=-3\nvolume=100.0\n"
    p = machine(files={"/home/me/.config/mpv/mpv.conf": conf})
    assert doctor.check_mpv_conf(p)[0].status == "ok"  # replaygain is off: its extras do nothing
    on = machine(files={"/home/me/.config/mpv/mpv.conf": "replaygain=track\nreplaygain-preamp=6\n"})
    fails = [c.title for c in doctor.check_mpv_conf(on) if c.status == "fail"]
    assert len(fails) == 2 and any("applies gain" in t for t in fails)
    stereo = machine(files={"/home/me/.config/mpv/mpv.conf": "audio-channels=stereo\n"})
    assert doctor.check_mpv_conf(stereo)[0].status == "fail"


def test_the_default_profile_of_mpv_conf_applies_to_everything():
    p = machine(
        files={
            "/home/me/.config/mpv/mpv.conf": (
                "[default]\naudio-samplerate=48000\n[other]\nvolume=1\n"
            )
        }
    )
    out = doctor.check_mpv_conf(p)
    assert [c.status for c in out] == ["fail"] and "resamples" in out[0].title


def test_only_the_mpv_conf_files_mpv_reads_are_looked_at():
    stale = {"/home/me/.mpv/config": "volume=10\n", "/home/me/.config/mpv/config": "volume=10\n"}
    assert doctor.check_mpv_conf(machine(files=stale))[0].title == "No mpv.conf"
    home = machine(
        env={"MPV_HOME": "/opt/mpv"}, files={"/home/me/.config/mpv/mpv.conf": "volume=10\n"}
    )
    assert doctor.check_mpv_conf(home)[0].title == "No mpv.conf"  # MPV_HOME replaces it
    there = machine(env={"MPV_HOME": "/opt/mpv"}, files={"/opt/mpv/mpv.conf": "volume=10\n"})
    assert doctor.check_mpv_conf(there)[0].status == "fail"
    xdg = machine(env={"XDG_CONFIG_HOME": "/x"}, files={"/x/mpv/mpv.conf": "mute=yes\n"})
    assert doctor.check_mpv_conf(xdg)[0].status == "fail"


def test_a_named_device_of_another_card_does_not_borrow_the_only_usb_dac():
    p = machine()
    assert doctor.find_dac(p, "pipewire/alsa_output.pci-0000_00_1f.3.analog-stereo") is None
    assert doctor.find_dac(p, "auto").card.id == "K13R2R"


def test_a_dac_with_one_rate_that_pipewire_allows_is_fine():
    one = STREAM.replace("44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000", "48000")
    p = machine(
        files={"/proc/asound/card1/stream0": one},
        commands={("pw-metadata", "-n", "settings"): PW_NARROW},
    )
    device = "pipewire/alsa_output.usb-FiiO_FiiO_K13_R2R-00.analog-stereo"
    (c,) = doctor.check_pipewire(p, device, doctor.find_dac(p, device))
    assert c.status == "ok"


def test_macos_with_no_device_gets_one_warning_not_two():
    p = machine(system="Darwin", env={"COLORTERM": "truecolor"})
    p.commands[("mpv", "--audio-device=help")] = "  'coreaudio/x' (Speakers)\n"
    sections = doctor.run_checks(p, device=None)
    assert len(titles(sections, "warn")) == 1


def test_the_images_setting_decides_what_the_terminal_check_says():
    kitty = Fake(env={"COLORTERM": "truecolor", "TERM": "xterm-kitty"})
    assert "sharp" in doctor.check_terminal(kitty)[1].title
    assert "images setting" in doctor.check_terminal(kitty, images="blocks")[1].title
    assert "off" in doctor.check_terminal(kitty, images="off")[1].title


def test_the_real_probe_survives_output_that_is_not_utf8():
    import sys

    out = Probe().run([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'caf\\xe9')"])
    assert out is not None and out.startswith("caf")


def test_pipewire_with_no_allowed_rates_key_means_its_base_rate_only():
    no_list = "\n".join(
        ln for ln in PW_NARROW.splitlines() if "allowed-rates" not in ln
    )  # still has clock.rate 48000
    p = machine(commands={("pw-metadata", "-n", "settings"): no_list})
    device = "pipewire/alsa_output.usb-FiiO_FiiO_K13_R2R-00.analog-stereo"
    (c,) = doctor.check_pipewire(p, device, doctor.find_dac(p, device))
    assert c.status == "warn" and "only 48 kHz" in c.title
    nothing = machine(
        commands={
            ("pw-metadata", "-n", "settings"): "update: id:0 key:'log.level' value:'2' type:''\n"
        }
    )
    (c,) = doctor.check_pipewire(nothing, device, doctor.find_dac(nothing, device))
    assert c.status == "info" and "could not be read" in c.title
