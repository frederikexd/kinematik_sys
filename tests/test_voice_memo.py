# ============================================================================
#  KinematiK — voice-memo transcription pipeline
# ============================================================================
"""The voice feature: a member uploads a phone voice memo and it becomes
editable text (spoken documentation, and spoken Lead Notes). The moving parts
that were finished — and are guarded here — are:

  • _vm_to_wav16k   : decode ANY uploaded audio format (iPhone .m4a, .mp3,
                      .ogg, .webm, …) to the 16 kHz mono WAV the offline
                      recognizer needs. This is a codec step (ffmpeg), the
                      piece that made "upload a phone memo → text" actually work
                      instead of only accepting raw WAV.
  • _vm_vosk_*      : locate an offline Vosk model — an env override, a model
                      committed beside the app, or the self-installing cache —
                      so a bare deploy has a guaranteed, no-API-key, offline
                      speech-to-text floor.
  • _vm_route_transcript / _vm_apply_prefill : split the transcript across the
                      picked template sections so the editors prefill.
  • _vm_transcribe  : never raises; returns (None, None) when nothing can run,
                      so a memo is still attached as audio backup regardless.

The functions live in streamlit_app.py (a Streamlit script, not an importable
package module), so we extract them from source and exec them in an isolated
namespace with a stub `st`. That keeps the test on the REAL code while avoiding
a hard Streamlit import at collection time.
"""
import io
import math
import os
import re
import struct
import subprocess
import tempfile
import wave

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP = os.path.join(_ROOT, "streamlit_app.py")

_FUNCS = [
    "_vm_ffmpeg", "_vm_to_wav16k",
    "_vm_vosk_cache_root", "_vm_vosk_is_model", "_vm_vosk_find_local",
    "_vm_vosk_model_path",
    "_vm_words", "_vm_sentences", "_vm_route_transcript", "_vm_apply_prefill",
    "_vm_transcribe", "_vm_engine_available", "_vm_digest",
    "_vm_wav_duration", "_vm_fmt_dur",
]
_CONSTS = [
    "_VM_ENGINE_CACHE = {}",
    '_VM_VOSK_MODEL_DIR = "vosk-model-small-en-us-0.15"',
    '_VM_VOSK_MODEL_URLS = ["https://example.invalid/model.zip"]',
    "_VM_STOPWORDS = set()",
]


def _grab(src, name):
    m = re.search(
        rf"\ndef {name}\(.*?\n(?=\ndef |\n# ---|\n_VM_STOPWORDS|\Z)", src, re.S)
    assert m, f"could not find def {name} in streamlit_app.py"
    return m.group(0)


@pytest.fixture(scope="module")
def vm():
    """The real voice helpers, exec'd into an isolated namespace."""
    src = open(_APP, encoding="utf-8").read()

    class _Stub:  # minimal st stand-in; the pure helpers don't touch it
        def __getattr__(self, _n):
            def _noop(*a, **k):
                return None
            return _noop

    ns = {"__name__": "vm_test", "__file__": _APP, "st": _Stub()}
    for c in _CONSTS:
        exec(c, ns)
    # _vm_words references a real stopword set in the app; pull the true one so
    # routing behaves as it does in production.
    m = re.search(r"\n_VM_STOPWORDS = \{.*?\n\}", src, re.S)
    if m:
        exec(m.group(0), ns)
    for name in _FUNCS:
        exec(_grab(src, name), ns)
    return ns


def _has_ffmpeg(vm):
    return bool(vm["_vm_ffmpeg"]())


def _sine_wav(seconds=1.0, rate=8000, freq=220):
    """A mono 16-bit PCM WAV at a deliberately non-16k rate."""
    buf = io.BytesIO()
    w = wave.open(buf, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(rate)
    w.writeframes(b"".join(
        struct.pack("<h", int(3000 * math.sin(2 * math.pi * freq * i / rate)))
        for i in range(int(rate * seconds))))
    w.close()
    return buf.getvalue()


# --------------------------------------------------------------------------- #
#  Pure-logic: routing a transcript into sections + prefill
# --------------------------------------------------------------------------- #
def _picked():
    return [
        ("t1", "Assumptions", "📌", "Assumptions",
         "tire temperature, load split, friction coefficient"),
        ("t2", "Calculation Summary", "🧮", "Calculation Summary",
         "peak lateral acceleration, load transfer, results"),
        ("t3", "Design Intent", "🎯", "Design Intent & Requirements",
         "goal, target, requirement"),
    ]


def test_spoken_markers_route_to_named_sections(vm):
    text = ("assumptions: we assumed cold tires and a fifty fifty static split. "
            "calculation summary: peak lateral was one point six g and load "
            "transfer looked fine. design intent: the goal is to hit the target "
            "camber curve.")
    routed, used_markers = vm["_vm_route_transcript"](text, _picked())
    assert used_markers is True
    assert any("cold tires" in ln or "fifty" in ln for ln in routed["t1"])
    assert any("lateral" in ln or "load transfer" in ln for ln in routed["t2"])
    assert any("goal" in ln or "camber" in ln for ln in routed["t3"])


def test_keyword_scoring_when_no_markers(vm):
    # No spoken section names → each sentence lands in its best-matching section
    # by vocabulary overlap.
    text = ("peak lateral acceleration was high and load transfer shifted. "
            "the goal is to meet the target requirement.")
    routed, used_markers = vm["_vm_route_transcript"](text, _picked())
    assert used_markers is False
    assert routed["t2"], "acceleration/load-transfer sentence should hit calc"
    assert routed["t3"], "goal/target/requirement sentence should hit intent"


def test_route_empty_is_safe(vm):
    routed, used = vm["_vm_route_transcript"]("", _picked())
    assert used is False
    assert all(v == [] for v in routed.values())


def test_apply_prefill_fills_empty_and_appends_to_edited(vm):
    picked = _picked()
    routed = {"t1": ["cold tires", "fifty fifty split"], "t2": [], "t3": []}
    state = {}
    # Patch the stub st to expose a real session_state dict.
    vm["st"].session_state = state
    filled = vm["_vm_apply_prefill"]("kp", picked, routed)
    assert ("Assumptions", 2) in filled
    assert state["kp_edit_t1"] == "cold tires\nfifty fifty split"

    # Now an already-edited editor should get the new text APPENDED, not wiped.
    state["kp_edit_t1"] = "existing hand-typed note"
    routed2 = {"t1": ["added later"], "t2": [], "t3": []}
    vm["_vm_apply_prefill"]("kp", picked, routed2)
    assert state["kp_edit_t1"].startswith("existing hand-typed note")
    assert "added later" in state["kp_edit_t1"]


# --------------------------------------------------------------------------- #
#  Model discovery — no network required
# --------------------------------------------------------------------------- #
def test_is_model_recognizes_shape(vm, tmp_path):
    good = tmp_path / "vosk-model-x"
    (good / "am").mkdir(parents=True)
    (good / "conf").mkdir()
    assert vm["_vm_vosk_is_model"](str(good)) is True
    assert vm["_vm_vosk_is_model"](str(tmp_path / "nope")) is False
    assert vm["_vm_vosk_is_model"](None) is False


def test_find_local_via_env_var(vm, tmp_path, monkeypatch):
    md = tmp_path / "vosk-model-fake"
    (md / "am").mkdir(parents=True)
    (md / "conf").mkdir()
    monkeypatch.setenv("KINEMATIK_VOSK_MODEL", str(md))
    assert vm["_vm_vosk_find_local"]() == str(md)


def test_model_path_no_download_returns_none_when_absent(vm, tmp_path,
                                                         monkeypatch):
    monkeypatch.delenv("KINEMATIK_VOSK_MODEL", raising=False)
    monkeypatch.setenv("KINEMATIK_VOSK_DIR", str(tmp_path / "empty-cache"))
    assert vm["_vm_vosk_model_path"](download=False) is None


# --------------------------------------------------------------------------- #
#  Decode — requires an ffmpeg binary (skip cleanly if none on the box)
# --------------------------------------------------------------------------- #
def test_decode_wav_resamples_to_16k_mono(vm):
    if not _has_ffmpeg(vm):
        pytest.skip("no ffmpeg available to decode audio")
    out = vm["_vm_to_wav16k"](_sine_wav(rate=8000), "wav")
    assert out, "decode returned None"
    w = wave.open(io.BytesIO(out))
    assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)


def test_decode_m4a_phone_format(vm):
    if not _has_ffmpeg(vm):
        pytest.skip("no ffmpeg available to decode audio")
    ff = vm["_vm_ffmpeg"]()
    # Build a real AAC .m4a (the iPhone Voice Memos container) via ffmpeg.
    src = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    src.write(_sine_wav(rate=8000))
    src.close()
    m4a = src.name.replace(".wav", ".m4a")
    try:
        subprocess.run([ff, "-y", "-i", src.name, "-c:a", "aac", m4a],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=True)
        out = vm["_vm_to_wav16k"](open(m4a, "rb").read(), "m4a")
        assert out, "m4a decode returned None"
        w = wave.open(io.BytesIO(out))
        assert w.getframerate() == 16000 and w.getnchannels() == 1
    finally:
        for p in (src.name, m4a):
            try:
                os.unlink(p)
            except OSError:
                pass


# --------------------------------------------------------------------------- #
#  Robustness — transcribe never raises, even with nothing available
# --------------------------------------------------------------------------- #
def test_transcribe_is_graceful_without_a_model(vm, monkeypatch, tmp_path):
    # Point provisioning at an unreachable URL and an empty cache so no engine
    # can actually run; the call must return (None, None), never raise.
    monkeypatch.delenv("KINEMATIK_VOSK_MODEL", raising=False)
    monkeypatch.setenv("KINEMATIK_VOSK_DIR", str(tmp_path / "empty"))
    monkeypatch.setenv("KINEMATIK_VOSK_URL", "https://example.invalid/x.zip")
    txt, eng = vm["_vm_transcribe"](_sine_wav(), "memo.wav")
    assert txt is None and eng is None


def test_digest_and_duration_helpers(vm):
    data = _sine_wav(seconds=2.0, rate=8000)
    assert len(vm["_vm_digest"](data)) == 16
    dur = vm["_vm_wav_duration"](data)
    assert dur is not None and abs(dur - 2.0) < 0.05
    assert vm["_vm_fmt_dur"](None) == ""
    assert "min" in vm["_vm_fmt_dur"](75)
