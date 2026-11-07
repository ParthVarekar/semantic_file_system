"""The installed-apps engine, on fake registry data and a fake uninstaller: nothing here lists or
removes a real app (listing real apps is read-only and was checked by hand)."""

import os
import subprocess
import sys

import pytest

from lfi import apps

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows apps")

GUID = "{4F0A7C3E-1B52-4D8A-9E67-0C1D2E3F4A5B}"


def _values(**kw):
    base = {"DisplayName": "Acme Studio", "UninstallString": '"C:\\Acme\\unins000.exe" /SILENT',
            "Publisher": "Acme Inc.", "DisplayVersion": "2.1", "InstallDate": "20250102", "EstimatedSize": 2048}
    base.update(kw)
    return base


def _reader(by_path):
    return lambda hive, path: iter(by_path.get((hive, path), []))


def test_only_removable_apps_are_listed_and_runtimes_are_flagged():
    keys = {
        ("HKLM", apps.UNINSTALL_KEYS[0][1]): [
            ("acme", _values()),
            ("update", _values(DisplayName="Security Update for Microsoft Word (KB5002345)")),
            ("hidden", _values(DisplayName="Hidden Helper", SystemComponent=1)),
            ("patch", _values(DisplayName="Acme Patch", ParentKeyName="acme")),
            ("locked", _values(DisplayName="Locked App", NoRemove=1)),
            ("nocmd", _values(DisplayName="No Uninstaller", UninstallString="")),
            ("noname", _values(DisplayName="")),
            ("vc", _values(DisplayName="Microsoft Visual C++ 2015-2022 Redistributable (x64)")),
        ],
        ("HKLM", apps.UNINSTALL_KEYS[1][1]): [("acme32", _values())],  # the 32-bit view lists it again
        ("HKCU", apps.UNINSTALL_KEYS[2][1]): [("mine", _values(DisplayName="Just Mine", EstimatedSize=0))],
    }
    found = {a.name: a for a in apps.list_registry_apps(_reader(keys))}
    assert set(found) == {"Acme Studio", "Microsoft Visual C++ 2015-2022 Redistributable (x64)", "Just Mine"}
    acme = found["Acme Studio"]
    assert acme.size == 2048 * 1024 and acme.installed == "2025-01-02" and acme.size_note
    assert found["Microsoft Visual C++ 2015-2022 Redistributable (x64)"].component and not acme.component
    assert found["Just Mine"].scope == "user" and found["Just Mine"].size == 0  # unknown, not "0 bytes"


def test_the_same_huge_size_repeated_by_several_apps_is_not_believed():
    gb = 2**30
    same = [apps.App(f"m:{i}", f"Game {i}", size=41 * gb) for i in range(3)]
    ok = [apps.App("m:a", "Adobe", size=8 * gb), apps.App("m:b", "Two", size=3 * gb), apps.App("m:c", "Three", size=3 * gb),
          apps.App("m:d", "Tiny1", size=1000), apps.App("m:e", "Tiny2", size=1000), apps.App("m:f", "Tiny3", size=1000)]
    out = apps.distrust_repeated_sizes(same + ok)
    assert [a.size for a in out[:3]] == [0, 0, 0]  # three apps with the identical 41 GB: unknown until measured
    assert [a.size for a in out[3:]] == [8 * gb, 3 * gb, 3 * gb, 1000, 1000, 1000]  # two the same is plausible; small ones are fine


def test_uninstall_commands_are_the_apps_own_and_msi_repair_becomes_uninstall():
    plain = apps.App("machine:a", "A", uninstall='"C:\\Acme\\unins000.exe" /SILENT')
    assert apps.uninstall_command(plain) == '"C:\\Acme\\unins000.exe" /SILENT'
    msi = apps.App("machine:b", "B", uninstall=f"MsiExec.exe /I{GUID}")
    assert apps.uninstall_command(msi) == f"MsiExec.exe /X{GUID}"  # /I is "change or repair"
    store = apps.App("store:x", "X", scope="store", package="Acme.Thing_1.2.3.0_x64__abc123")
    cmd = apps.uninstall_command(store)
    assert cmd[0] == "powershell.exe" and "Remove-AppxPackage -Package 'Acme.Thing_1.2.3.0_x64__abc123'" in cmd[-1]
    evil = apps.App("store:y", "Y", scope="store", package="x'; Remove-Item C:\\ -Recurse; '")
    with pytest.raises(ValueError):
        apps.uninstall_command(evil)  # a package name can never carry a command
    with pytest.raises(ValueError):
        apps.uninstall_command(apps.App("machine:z", "Z", uninstall=""))


def test_launching_asks_windows_for_permission_instead_of_elevating(tmp_path):
    app = apps.App("machine:a", "A", uninstall='"C:\\Acme\\unins000.exe" /SILENT')
    started = []
    assert apps.launch_uninstall(app, popen=lambda cmd, **k: started.append((cmd, k)) or "proc") == "proc"
    assert started[0][0] == app.uninstall and started[0][1]["shell"] is False

    def needs_admin(cmd, **k):
        raise OSError(22, "The requested operation requires elevation", None, 740)

    asked = []
    out = apps.launch_uninstall(app, popen=needs_admin, shell_execute=lambda exe, args: asked.append((exe, args)))
    assert out is None and asked == [("C:\\Acme\\unins000.exe", "/SILENT")]

    def other_error(cmd, **k):
        raise FileNotFoundError("gone")

    with pytest.raises(FileNotFoundError):  # anything but "needs elevation" is reported, not retried
        apps.launch_uninstall(app, popen=other_error, shell_execute=lambda *a: asked.append(a))
    assert len(asked) == 1


def test_split_command_resolves_unquoted_paths_with_spaces(tmp_path):
    d = tmp_path / "My Program"
    d.mkdir()
    exe = d / "uninstall.exe"
    exe.write_bytes(b"MZ")
    assert apps.split_command(f"{exe} /S /norestart") == (str(exe), "/S /norestart")
    assert apps.split_command(f'"{exe}" -x') == (str(exe), "-x")
    assert apps.split_command("msiexec.exe /X{1}") == ("msiexec.exe", "/X{1}")


def test_measuring_adds_up_files_skips_links_and_refuses_broad_folders(tmp_path):
    app = tmp_path / "App"
    (app / "sub").mkdir(parents=True)
    (app / "a.bin").write_bytes(b"x" * 1000)
    (app / "sub" / "b.bin").write_bytes(b"x" * 500)
    outside = tmp_path / "Outside"
    outside.mkdir()
    (outside / "huge.bin").write_bytes(b"x" * 100_000)
    subprocess.run(["cmd", "/c", "mklink", "/J", str(app / "link"), str(outside)], capture_output=True, check=True)
    assert apps.folder_size(str(app)) == (1500, True)  # the junction's target is not followed
    assert apps.folder_size(str(app), seconds=-1)[1] is False  # out of time: reported as incomplete

    drive = os.path.splitdrive(str(app))[0] + "\\"
    assert apps.measurable(str(app))
    for refused in (drive, os.environ["ProgramFiles"], os.environ["USERPROFILE"], os.environ["LOCALAPPDATA"],
                    os.environ["SystemRoot"], os.path.join(os.environ["SystemRoot"], "System32"), "", str(tmp_path / "nope")):
        assert not apps.measurable(refused), refused


def test_still_installed_looks_for_the_exact_key():
    app = apps.App("machine:{abc}", "A")
    seen = []

    def exists(hive, path, sub):
        seen.append((hive, sub))
        return hive == "HKLM" and path == apps.UNINSTALL_KEYS[1][1]  # only in the 32-bit view

    assert apps.still_installed(app, exists) and {s for _, s in seen} == {"{abc}"}
    assert not apps.still_installed(app, lambda *a: False)
    user = apps.App("user:{abc}", "A")
    assert not apps.still_installed(user, lambda h, p, s: h == "HKLM")  # a per-user app is only looked up for the user
