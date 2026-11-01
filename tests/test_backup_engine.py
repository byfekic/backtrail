# -*- coding: utf-8 -*-
"""Tests for backup_engine.py (the GUI in backtrail.py is not imported).   Run:  python -m pytest -q tests

Everything runs on copies of the demo folder in a temporary directory with ZIP archives,
so no WinRAR is needed.
"""
import io
import json
import os
import shutil
import sys
import time
import zipfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import backup_engine as e  # noqa: E402

DEMO = HERE.parent / "demo" / "Line1"


def _cfg(tmp_path, **over):
    cfg = {"backup_root": str(tmp_path / "bk"), "archive_format": "zip", "compress_level": 1,
           "keep_last": 0, "keep_days": 0, "keys_dir": str(tmp_path / "keys")}   # never the real profile key
    cfg.update(over)
    return cfg


@pytest.fixture
def prod(tmp_path):
    """A copy of the demo folder and a source pointing at it."""
    src = tmp_path / "source"
    shutil.copytree(str(DEMO), str(src))
    return src, {"name": "Demo", "backup_name": "Machine_A", "path": str(src)}


def _three_backups(tmp_path, prod, cfg):
    """Three runs with a change in between each: (first, second, third) Backup objects."""
    src, loc = prod
    e.run(loc, cfg, log=lambda s: None, note="before firmware", mode="manual")
    time.sleep(1.05)
    (src / "settings.json").write_text(json.dumps(
        {"machine": "Machine_A", "speed": 1.5, "recirculation": 10, "newkey": True}), encoding="utf-8")
    (src / "added.txt").write_text("x", encoding="utf-8")
    (src / "settings.ini").unlink()
    e.run(loc, cfg, log=lambda s: None, note="", mode="scheduled")
    time.sleep(1.05)
    (src / "third.txt").write_text("y", encoding="utf-8")
    e.run(loc, cfg, log=lambda s: None, note="third", mode="manual")
    bks = e.list_backups(Path(cfg["backup_root"]) / "Machine_A")
    assert len(bks) == 3
    return bks


# ---------------------------------------------------------------- who --
def test_identity_and_cli_note():
    user, host = e.identity()
    assert user and host and e.who() == "%s @ %s" % (user, host)
    assert e.cli_note(["Backtrail.exe", "--run", "--note", "hello world"]) == "hello world"
    assert e.cli_note(["--note=abc"]) == "abc"
    assert e.cli_note(["--run"]) == ""


def test_run_signs_manifest_and_changelog(tmp_path, prod):
    src, loc = prod
    cfg = _cfg(tmp_path)
    cp = e.run(loc, cfg, log=lambda s: None, note="ticket 4711", mode="manual")
    bks = e.list_backups(Path(cfg["backup_root"]) / "Machine_A")
    assert len(bks) == 1 and bks[0].complete and bks[0].changelog == cp
    meta, files = e.load_manifest(bks[0].manifest)
    user, host = e.identity()
    assert meta["version"] == e.MANIFEST_VERSION and meta["user"] == user and meta["host"] == host
    assert meta["note"] == "ticket 4711" and meta["mode"] == "manual" and meta["previous"] == ""
    assert sorted(files) == ["settings.ini", "settings.json"]
    assert all({"size", "mtime", "sha256"} <= set(v) for v in files.values())
    head = cp.read_text(encoding="utf-8").splitlines()[:14]
    assert "By: %s @ %s" % (user, host) in head
    assert "Mode: manual" in head and "Note: ticket 4711" in head
    assert "Previous: (none: first backup)" in head
    # the Backup object reads the same facts back
    assert bks[0].by() == user and "ticket 4711" in bks[0].label()
    assert bks[0].time.strftime("%Y-%m-%d_%H-%M-%S") == bks[0].stamp


def test_second_run_names_its_previous_and_detects_changes(tmp_path, prod):
    cfg = _cfg(tmp_path)
    first, second, third = _three_backups(tmp_path, prod, cfg)
    assert second.meta["previous"] == first.prefix and second.meta["mode"] == "scheduled"
    text = second.changelog.read_text(encoding="utf-8")
    assert "+ added.txt" in text and "- settings.ini" in text and "* settings.json" in text
    assert "~ $.speed: 1.0 -> 1.5" in text and "+ $.newkey: True" in text
    assert e.summary_counts(second.changelog) == {"added": 1, "modified": 1, "deleted": 1}


def test_v1_manifest_and_old_changelog_still_read(tmp_path):
    # a manifest from the original tool: the bare file map, no metadata
    flat = {"a.txt": {"size": 1, "mtime": 0.0, "sha256": "x"}}
    p = tmp_path / "2026-08-05_12-24-06_Machine_C_MANIFEST.json"
    p.write_text(json.dumps(flat), encoding="utf-8")
    assert e.load_manifest(p) == ({}, flat)
    # a v2 manifest whose source folder happens to hold files called "files" and "_meta"
    tricky = {"_meta": {"size": 1, "mtime": 0.0, "sha256": "x"}, "files": {"size": 2, "mtime": 0.0, "sha256": "y"}}
    p.write_text(json.dumps(tricky), encoding="utf-8")
    assert e.load_manifest(p) == ({}, tricky)
    # the old changelog header carries no "By:" line: the author is unknown, nothing breaks
    (tmp_path / "2026-08-05_12-24-06_Machine_C_CHANGELOG.txt").write_text(
        "Backtrail\nSigned backups with a readable history\n" + "=" * 76 + "\nBACKUP CHANGELOG\n"
        "Date: 2026-08-05 12:24:06\nName: Machine_C\nSource: C:\\Machine\n", encoding="utf-8")
    b = e.list_backups(tmp_path)[0]
    assert b.by() == "(unknown)" and b.meta["source"] == "C:\\Machine"
    assert "(unknown)" in b.label() and not b.complete


# ----------------------------------------------------------- backups --
def test_parse_stamp_knows_both_naming_schemes():
    assert e.parse_stamp("2026-09-07_16-47-49_LINE1_PLC_ARCHIVE.rar").strftime("%Y%m%d%H%M%S") == "20260907164749"
    assert e.parse_stamp("LINE1_PLC_20260907_1647.rar").strftime("%Y%m%d%H%M") == "202609071647"
    assert e.parse_stamp("ARCHIVE.zip") is None
    assert e._name_of(Path("LINE1_PLC_20260907_1647.rar")) == "LINE1_PLC"
    assert e._name_of(Path("2026-09-07_16-47-49_LINE1_PLC_ARCHIVE.rar")) == "LINE1_PLC"


def test_list_backups_groups_the_three_files(tmp_path, prod):
    cfg = _cfg(tmp_path)
    bks = _three_backups(tmp_path, prod, cfg)
    assert [b.stamp for b in bks] == sorted(b.stamp for b in bks)
    for b in bks:
        assert b.archive.suffix == ".zip" and b.manifest.name.endswith("_MANIFEST.json")
        assert b.changelog.name.endswith("_CHANGELOG.txt") and len(b.paths()) == 3
    folders = e.source_folders(cfg["backup_root"])
    assert [d.name for d in folders] == ["Machine_A"]
    assert e.source_folders(tmp_path / "nowhere") == []


# ----------------------------------------------------------- compare --
def test_compare_two_backups_writes_the_changelog_between_them(tmp_path, prod):
    cfg = _cfg(tmp_path)
    first, second, third = _three_backups(tmp_path, prod, cfg)
    out, counts = e.compare(first, third, cfg, log=lambda s: None)
    assert counts == (2, 1, 1)
    assert out.parent == first.folder and out.name == "%s_to_%s_COMPARE.txt" % (first.stamp, third.stamp)
    text = out.read_text(encoding="utf-8")
    user, _ = e.identity()
    assert "BACKUP COMPARISON" in text and "Compared by: %s @" % user in text
    assert "From note: before firmware" in text and "To note: third" in text
    assert "BACKUP RUNS IN BETWEEN" in text
    assert "%s  by %s  [scheduled]  +1 ~1 -1" % (second.stamp, user) in text
    assert "%s  by %s  [manual]  +1 ~0 -0  - third" % (third.stamp, user) in text
    assert "+ added.txt" in text and "+ third.txt" in text and "- settings.ini" in text
    assert "~ $.speed: 1.0 -> 1.5" in text and "+ $.newkey: True" in text
    assert "Added: 2" in text and "Modified: 1" in text and "Deleted: 1" in text
    # a compare file is never mistaken for a backup
    assert len(e.list_backups(first.folder)) == 3
    # given newest first, the sides are swapped rather than reporting the changes backwards
    out2, counts2 = e.compare(third, first, cfg, log=lambda s: None, out=tmp_path / "rev.txt")
    assert counts2 == (2, 1, 1) and "+ added.txt" in out2.read_text(encoding="utf-8")


def test_compare_bare_archives_without_manifests(tmp_path, prod):
    cfg = _cfg(tmp_path)
    first, second, third = _three_backups(tmp_path, prod, cfg)
    bare = tmp_path / "bare"
    bare.mkdir()
    # the plant's own naming, no manifest, no changelog beside them
    shutil.copy(str(first.archive), str(bare / "LINE1_PLC_20260825_1612.zip"))
    shutil.copy(str(third.archive), str(bare / "LINE1_PLC_20260907_1647.zip"))
    out, counts = e.compare(bare / "LINE1_PLC_20260825_1612.zip", bare / "LINE1_PLC_20260907_1647.zip", cfg,
                            log=lambda s: None)
    assert counts == (2, 1, 1)
    assert out.name == "2026-08-25_16-12-00_to_2026-09-07_16-47-00_LINE1_PLC_COMPARE.txt"
    text = out.read_text(encoding="utf-8")
    assert "no manifest: files hashed from the archive" in text
    assert "From taken: 2026-08-25 16:12:00" in text and "To taken: 2026-09-07 16:47:00" in text
    assert "~ $.speed: 1.0 -> 1.5" in text          # the JSON diff works from the unpacked copies too
    assert "BACKUP RUNS IN BETWEEN" not in text      # unrelated archives: no run history to show
    # a zip that holds the folder itself (one top-level directory) compares the same
    nested = bare / "nested_2026-09-08_10-00-00.zip"
    with zipfile.ZipFile(str(nested), "w") as z:
        for p in e.walk(prod[0]):
            z.write(str(p), "Machine/" + p.relative_to(prod[0]).as_posix())
    out3, counts3 = e.compare(bare / "LINE1_PLC_20260825_1612.zip", nested, cfg, log=lambda s: None)
    assert counts3 == (2, 1, 1)


def test_compare_uses_the_manifest_next_to_a_bare_archive(tmp_path, prod):
    cfg = _cfg(tmp_path)
    first, second, third = _three_backups(tmp_path, prod, cfg)
    calls = []
    real = e.extract_all
    e.extract_all = lambda *a, **k: calls.append(a) or real(*a, **k)
    try:
        out, counts = e.compare(first.archive, third.archive, cfg, log=lambda s: None, out=tmp_path / "m.txt")
    finally:
        e.extract_all = real
    assert counts == (2, 1, 1) and not calls           # manifests found beside the archives: nothing unpacked
    assert "by %s" % e.identity()[0] in out.read_text(encoding="utf-8")


def test_compare_missing_archive_fails_clearly(tmp_path):
    with pytest.raises(FileNotFoundError):
        e.compare(tmp_path / "a.zip", tmp_path / "b.zip", _cfg(tmp_path), log=lambda s: None)


# ---------------------------------------------------------- retention --
def test_prune_keeps_the_newest_and_the_compare_files(tmp_path, prod):
    cfg = _cfg(tmp_path)
    first, second, third = _three_backups(tmp_path, prod, cfg)
    e.compare(first, third, cfg, log=lambda s: None)
    folder = first.folder
    assert e.prune(folder, 0, 0, log=lambda s: None) == []            # no limit set: nothing happens
    lines = []
    removed = e.prune(folder, keep_last=2, log=lines.append)
    assert removed == [first.prefix]
    assert not first.archive.exists() and not first.manifest.exists() and not first.changelog.exists()
    assert [b.stamp for b in e.list_backups(folder)] == [second.stamp, third.stamp]
    assert len(list(folder.glob("*_COMPARE.txt"))) == 1
    assert any("by %s" % e.identity()[0] in l for l in lines)         # the log names who took it
    # by age: everything is "old", but the newest complete backup survives
    removed = e.prune(folder, keep_days=0, log=lambda s: None)
    assert removed == []
    # push the older backup back in time by renaming its three files to an old stamp
    old = e.list_backups(folder)[0]
    for p in old.paths():
        p.rename(p.with_name(p.name.replace(old.stamp, "2020-01-01_00-00-00")))
    removed = e.prune(folder, keep_days=30, log=lambda s: None)
    assert removed == ["2020-01-01_00-00-00_Machine_A"]
    assert [b.stamp for b in e.list_backups(folder)] == [third.stamp]


def test_run_applies_the_retention_setting(tmp_path, prod):
    src, loc = prod
    cfg = _cfg(tmp_path, keep_last=2)
    folder = Path(cfg["backup_root"]) / "Machine_A"
    cp1 = e.run(loc, cfg, log=lambda s: None)
    time.sleep(1.05)
    (src / "a.txt").write_text("a", encoding="utf-8")
    cp2 = e.run(loc, cfg, log=lambda s: None)
    time.sleep(1.05)
    (src / "b.txt").write_text("b", encoding="utf-8")
    lines = []
    cp3 = e.run(loc, cfg, log=lines.append)              # this run prunes the first
    assert not cp1.exists() and cp2.exists() and cp3.exists()
    assert [b.changelog for b in e.list_backups(folder)] == [cp2, cp3]
    assert any(l.startswith("Retention: removed backup") for l in lines)


# --------------------------------------------------------- signatures --
crypto = pytest.mark.skipif(not e.signing_available(), reason="the cryptography package is not installed")


@crypto
def test_backups_are_signed_and_verify(tmp_path, prod):
    src, loc = prod
    cfg = _cfg(tmp_path)
    lines = []
    cp = e.run(loc, cfg, log=lines.append)
    b = e.list_backups(Path(cfg["backup_root"]) / "Machine_A")[0]
    sig = b.meta["signature"]
    assert sig["alg"] == "ed25519" and sig["signer"] == e.signer_id() and len(sig["key_id"]) == 16
    assert sig["archive_sha256"] == e.hash_file(b.archive)
    # the private key in the (test) profile folder, the public key published beside the backups
    assert (tmp_path / "keys" / (e._safe(e.signer_id()) + ".key")).exists()
    pub = Path(cfg["backup_root"]) / "_keys" / (e._safe(e.signer_id()) + ".pub")
    assert pub.exists() and e.key_fingerprint(e.read_pub(pub)) == sig["key_id"]
    assert any("Signing key created" in l for l in lines)
    assert e.verify_manifest(b.manifest)["status"] == "signed"
    deep = e.verify_archive(b)
    assert deep["status"] == "signed" and "archive intact" in deep["detail"]
    assert b.sig_mark() == "✔ signed" and "✔ signed" in b.label()
    assert "Signature: ed25519 key %s by %s" % (sig["key_id"], sig["signer"]) in cp.read_text(encoding="utf-8")
    # the same key signs the next run, and the trust store keeps one key per signer
    time.sleep(1.05)
    e.run(loc, cfg, log=lambda s: None)
    b2 = e.list_backups(b.folder)[-1]
    assert b2.meta["signature"]["key_id"] == sig["key_id"]
    assert len(list((Path(cfg["backup_root"]) / "_keys").glob("*.pub"))) == 1
    # a comparison names the signature state of both sides
    out, _ = e.compare(b, b2, cfg, log=lambda s: None)
    assert out.read_text(encoding="utf-8").count("✔ signed") >= 2


@crypto
def test_tampering_is_detected(tmp_path, prod):
    src, loc = prod
    cfg = _cfg(tmp_path)
    e.run(loc, cfg, log=lambda s: None)
    b = e.list_backups(Path(cfg["backup_root"]) / "Machine_A")[0]
    original = b.manifest.read_bytes()
    # 1. the manifest is edited: the author claim changes
    meta, files = e.load_manifest(b.manifest)
    meta["note"] = "I did not do it"
    e.save_manifest(b.manifest, files, meta)
    assert e.verify_manifest(b.manifest)["status"] == "invalid"
    meta["note"] = ""
    meta["user"] = "somebody.else"
    e.save_manifest(b.manifest, files, meta)
    assert e.verify_manifest(b.manifest)["status"] == "invalid"
    b.manifest.write_bytes(original)
    assert e.verify_manifest(b.manifest)["status"] == "signed"
    # 2. the archive is altered: the quick check cannot see it, the deep check does
    with open(str(b.archive), "ab") as fh:
        fh.write(b"x")
    assert e.verify_manifest(b.manifest)["status"] == "signed"
    r = e.verify_archive(b)
    assert r["status"] == "invalid" and "differs" in r["detail"]
    # 3. the trust store lost the key: the signature is valid but nobody vouches for it
    trust = Path(cfg["backup_root"]) / "_keys"
    shutil.rmtree(str(trust))
    r = e.verify_manifest(b.manifest)
    assert r["status"] == "untrusted" and "no key" in r["detail"]
    # 4. another key is registered for the same signer: untrusted, and the signer's own key is not overwritten
    trust.mkdir()
    pubfile = trust / (e._safe(e.signer_id()) + ".pub")
    pubfile.write_text(e.pub_line(b"\x01" * 32), encoding="utf-8")
    assert e.verify_manifest(b.manifest)["status"] == "untrusted"
    raw = e.read_pub(tmp_path / "keys" / (e._safe(e.signer_id()) + ".pub"))
    assert e.publish_public_key(cfg["backup_root"], e.signer_id(), raw) == "conflict"
    assert e.read_pub(pubfile) == b"\x01" * 32
    # 5. the archive is gone
    pubfile.write_text(e.pub_line(raw), encoding="utf-8")
    b.archive.unlink()
    assert e.verify_archive(b)["status"] == "invalid"


def test_unsigned_backups_are_reported_as_such(tmp_path, prod):
    src, loc = prod
    cfg = _cfg(tmp_path, sign=False)
    cp = e.run(loc, cfg, log=lambda s: None)
    b = e.list_backups(Path(cfg["backup_root"]) / "Machine_A")[0]
    assert "signature" not in b.meta and b.sig_mark() == "unsigned"
    assert "Signature: (none)" in cp.read_text(encoding="utf-8")
    assert not (Path(cfg["backup_root"]) / "_keys").exists()
    # an old bare-map manifest
    flat = {"a.txt": {"size": 1, "mtime": 0.0, "sha256": "x"}}
    p = tmp_path / "2026-08-05_12-24-06_Machine_C_MANIFEST.json"
    p.write_text(json.dumps(flat), encoding="utf-8")
    assert e.verify_manifest(p)["status"] == "unsigned"
    assert e.verify_archive(p)["status"] == "unsigned"


# ------------------------------------------------------------ restore --
def test_restore_into_a_new_folder_verifies_every_file(tmp_path, prod):
    src, loc = prod
    cfg = _cfg(tmp_path)
    (src / "sub").mkdir()
    (src / "sub" / "deep.csv").write_text("a;b\n1;2\n", encoding="utf-8")
    e.run(loc, cfg, log=lambda s: None, note="golden")
    b = e.list_backups(Path(cfg["backup_root"]) / "Machine_A")[0]
    target = tmp_path / "restored"
    plan = e.restore_plan(b, target)
    assert sorted(plan["add"]) == ["settings.ini", "settings.json", "sub/deep.csv"] and not plan["replace"]
    lines = []
    out = e.restore(b, target, cfg, log=lines.append)
    assert sorted(out["restored"]) == ["settings.ini", "settings.json", "sub/deep.csv"]
    assert not out["failed"] and not out["unverified"] and not out["removed"]
    for rel in out["restored"]:
        assert e.hash_file(target / rel) == b.files[rel]["sha256"]
    # the report sits beside the backups and names who restored what, where, from which backup
    rec = out["record"]
    assert rec and rec.parent == b.folder and e.RESTORE_RE.match(rec.name) and e.list_restores(b.folder) == [rec]
    text = rec.read_text(encoding="utf-8")
    assert "Restored from: %s" % b.prefix in text and "Restored to: %s" % target in text
    assert "Restored by: %s" % e.who() in text and "Backup note: golden" in text and "✔ signed" in text
    assert "> sub/deep.csv" in text and "Restored: 3" in text
    assert any(l.startswith("SUCCESS: restored 3 file(s)") for l in lines)
    # restore reports are never mistaken for backups, and a second restore skips identical files
    assert len(e.list_backups(b.folder)) == 1
    out2 = e.restore(b, target, cfg, log=lambda s: None, record=False)
    assert out2["restored"] == [] and len(out2["skipped"]) == 3


def test_restore_selected_files_only(tmp_path, prod):
    src, loc = prod
    cfg = _cfg(tmp_path)
    e.run(loc, cfg, log=lambda s: None)
    b = e.list_backups(Path(cfg["backup_root"]) / "Machine_A")[0]
    good = (src / "settings.json").read_bytes()
    # one file goes wrong on the plant, another is added
    (src / "settings.json").write_text("{\"broken\": true}", encoding="utf-8")
    (src / "new.txt").write_text("keep me", encoding="utf-8")
    plan = e.restore_plan(b, src, members=["settings.json", "settings.ini", "nope.txt"])
    assert plan["replace"] == ["settings.json"] and plan["same"] == ["settings.ini"] and plan["missing"] == ["nope.txt"]
    out = e.restore(b, src, cfg, members=["settings.json"], log=lambda s: None)
    assert out["restored"] == ["settings.json"] and (src / "settings.json").read_bytes() == good
    assert (src / "new.txt").exists()                      # a partial restore never removes anything
    assert "Mode: selected files" in out["record"].read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        e.restore(b, src, cfg, members=["nope.txt"], log=lambda s: None)
    with pytest.raises(ValueError):
        e.restore(b, src, cfg, members=[], log=lambda s: None)


def test_restore_over_the_source_takes_a_safety_backup_first(tmp_path, prod):
    src, loc = prod
    cfg = _cfg(tmp_path, locations=[loc], keep_last=1)
    e.run(loc, cfg, log=lambda s: None, note="known good")
    folder = Path(cfg["backup_root"]) / "Machine_A"
    b = e.list_backups(folder)[0]
    assert e.source_of(b, cfg) == src and e.location_for(b, cfg) == loc
    time.sleep(1.05)
    (src / "settings.json").write_text("{\"broken\": true}", encoding="utf-8")
    (src / "junk.tmp").write_text("x", encoding="utf-8")
    plan = e.restore_plan(b, src)
    assert plan["replace"] == ["settings.json"] and plan["extra"] == ["junk.tmp"] and plan["same"] == ["settings.ini"]
    lines = []
    out = e.restore_to_source(b, cfg, log=lines.append, clean=True)
    bks = e.list_backups(folder)
    # the safety backup exists (keep_last=1 was NOT applied: both backups are still there), is signed and says why it was taken
    assert len(bks) == 2 and bks[0].prefix == b.prefix and bks[1].prefix == out["safety"]
    assert bks[1].meta["note"] == "safety backup before restore of %s" % b.stamp and bks[1].signature()["status"] == "signed"
    assert "junk.tmp" in bks[1].files                      # the overwritten state, junk included, is preserved
    assert out["restored"] == ["settings.json"] and out["removed"] == ["junk.tmp"] and out["skipped"] == ["settings.ini"]
    assert json.loads((src / "settings.json").read_text(encoding="utf-8"))["machine"] == "Machine_A"
    assert not (src / "junk.tmp").exists()
    assert "Safety backup: %s" % out["safety"] in out["record"].read_text(encoding="utf-8")
    assert any("Safety backup of" in l for l in lines)
    # the next regular run finds the restored state unchanged against the backup that was restored
    time.sleep(1.05)
    cp = e.run(loc, cfg, log=lambda s: None)
    c = e.summary_counts(cp)
    assert c == {"added": 0, "modified": 1, "deleted": 1}    # relative to the safety backup: json back, junk gone


def test_restore_refuses_a_tampered_archive(tmp_path, prod):
    src, loc = prod
    cfg = _cfg(tmp_path)
    e.run(loc, cfg, log=lambda s: None)
    b = e.list_backups(Path(cfg["backup_root"]) / "Machine_A")[0]
    # the archive is rewritten with a changed file; its manifest hash no longer matches
    tmp = tmp_path / "evil.zip"
    with zipfile.ZipFile(b.archive) as zin, zipfile.ZipFile(tmp, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            zout.writestr(info, b"{\"evil\": 1}" if info.filename == "settings.json" else data)
    shutil.move(str(tmp), str(b.archive))
    target = tmp_path / "out"
    with pytest.raises(RuntimeError, match="Restore refused"):
        e.restore(b, target, cfg, log=lambda s: None)
    assert not target.exists() or not list(target.iterdir())   # nothing was written
    with pytest.raises(RuntimeError, match="Restore refused"):
        e.restore(b, target, cfg, members=["settings.json"], log=lambda s: None)
    # the untouched file can still be restored on its own
    out = e.restore(b, target, cfg, members=["settings.ini"], log=lambda s: None, record=False)
    assert out["restored"] == ["settings.ini"]


def test_restore_without_a_manifest_is_unverified(tmp_path, prod):
    src, loc = prod
    cfg = _cfg(tmp_path)
    e.run(loc, cfg, log=lambda s: None)
    b = e.list_backups(Path(cfg["backup_root"]) / "Machine_A")[0]
    b.manifest.unlink()
    b = e.list_backups(b.folder)[0]
    assert not b.complete
    out = e.restore(b, tmp_path / "out", cfg, log=lambda s: None)
    assert sorted(out["restored"]) == ["settings.ini", "settings.json"] == sorted(out["unverified"])
    assert "NOT VERIFIED" in out["record"].read_text(encoding="utf-8")
    b.archive.unlink()
    with pytest.raises(FileNotFoundError):
        e.restore(e.list_backups(b.folder)[0], tmp_path / "out2", cfg, log=lambda s: None)
