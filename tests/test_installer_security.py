import io
import json
import stat
import zipfile
from urllib.request import Request

import pytest

from plugins.installer import UniversalInstaller
from plugins.manager import PluginManager


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def geturl(self):
        return "https://codeload.github.com/acme/archive.zip"


def make_zip(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files:
            archive.writestr(name, content)
    return buffer.getvalue()


def bounded_url():
    return "https://github.com/acme/releases/download/v1/bounded.zip"


def test_installer_bounds_download_and_creates_no_plugin_dir(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "plugins"
    installer = UniversalInstaller(plugin_dir)
    installer.MAX_DOWNLOAD_BYTES = 4
    monkeypatch.setattr(installer, "_open_url",
                        lambda request, timeout: FakeResponse(b"12345"))

    with pytest.raises(ValueError, match="download exceeds"):
        installer.install_from_url(bounded_url())

    assert not (plugin_dir / "bounded").exists()


def test_installer_bounds_expanded_size_before_extraction(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "plugins"
    installer = UniversalInstaller(plugin_dir)
    installer.MAX_TOTAL_UNCOMPRESSED_BYTES = 5
    payload = make_zip([("repo/README.md", "this is larger than five bytes")])
    monkeypatch.setattr(installer, "_open_url",
                        lambda request, timeout: FakeResponse(payload))

    with pytest.raises(ValueError, match="expands beyond"):
        installer.install_from_url(bounded_url())

    assert not (plugin_dir / "bounded").exists()


def test_installer_rejects_archive_path_traversal(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "plugins"
    installer = UniversalInstaller(plugin_dir)
    payload = make_zip([("../outside.md", "outside"), ("repo/README.md", "ok")])
    monkeypatch.setattr(installer, "_open_url",
                        lambda request, timeout: FakeResponse(payload))

    with pytest.raises(ValueError, match="traversal"):
        installer.install_from_url(bounded_url())

    assert not (tmp_path / "outside.md").exists()
    assert not (plugin_dir / "bounded").exists()


def test_installer_refuses_symlink_destination(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "plugins"
    outside = tmp_path / "outside"
    outside.mkdir()
    plugin_dir.mkdir()
    (plugin_dir / "demo").symlink_to(outside, target_is_directory=True)
    installer = UniversalInstaller(plugin_dir)
    payload = make_zip([("repo/README.md", "ok")])
    monkeypatch.setattr(installer, "_open_url",
                        lambda request, timeout: FakeResponse(payload))

    with pytest.raises(ValueError, match="symlink"):
        installer.install_from_url("https://github.com/acme/demo")

    assert list(outside.iterdir()) == []


def test_installer_rejects_malformed_repository_names(tmp_path):
    installer = UniversalInstaller(tmp_path / "plugins")

    with pytest.raises(ValueError):
        installer._parse_repo_url("https://github.com/acme/../../escape")


def test_plugin_manager_rejects_traversing_manifest_entrypoint(tmp_path):
    plugin_dir = tmp_path / "plugins"
    plugin = plugin_dir / "demo"
    plugin.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    (plugin / "manifest.json").write_text(json.dumps({
        "name": "demo", "entry_point": "../../outside.md"}), encoding="utf-8")

    assert PluginManager(plugin_dir).get_plugin_prompt("demo") is None


def test_plugin_manager_rejects_symlink_manifest_entrypoint(tmp_path):
    plugin_dir = tmp_path / "plugins"
    plugin = plugin_dir / "demo"
    plugin.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    (plugin / "manifest.json").write_text(json.dumps({
        "name": "demo", "entry_point": "prompt.md"}), encoding="utf-8")
    try:
        (plugin / "prompt.md").symlink_to(outside)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    assert PluginManager(plugin_dir).get_plugin_prompt("demo") is None


def test_plugin_manager_ignores_manifest_symlink(tmp_path):
    plugin_dir = tmp_path / "plugins"
    plugin = plugin_dir / "demo"
    plugin.mkdir(parents=True)
    outside_manifest = tmp_path / "manifest.json"
    outside_manifest.write_text(json.dumps({
        "name": "outside", "entry_point": "../../outside.md"}), encoding="utf-8")
    try:
        (plugin / "manifest.json").symlink_to(outside_manifest)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    assert PluginManager(plugin_dir).get_plugin_info("demo") is None


def test_installer_rejects_symlink_member_in_archive(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "plugins"
    installer = UniversalInstaller(plugin_dir)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        link = zipfile.ZipInfo("repo/link.md")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "../../outside.md")
        archive.writestr("repo/README.md", "ok")
    monkeypatch.setattr(installer, "_open_url",
                        lambda request, timeout: FakeResponse(buffer.getvalue()))

    with pytest.raises(ValueError, match="special file"):
        installer.install_from_url(bounded_url())

    assert not (tmp_path / "outside.md").exists()
    assert not (plugin_dir / "bounded").exists()


def test_installer_rejects_symlink_output_file(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "plugins"
    target = plugin_dir / "demo"
    target.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("keep", encoding="utf-8")
    try:
        (target / "prompt.md").symlink_to(outside)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    installer = UniversalInstaller(plugin_dir)
    payload = make_zip([("repo/README.md", "replacement")])
    monkeypatch.setattr(installer, "_open_url",
                        lambda request, timeout: FakeResponse(payload))

    with pytest.raises(ValueError, match="symlink"):
        installer.install_from_url("https://github.com/acme/demo")

    assert outside.read_text(encoding="utf-8") == "keep"


def test_installer_bounds_archive_entry_count(tmp_path):
    installer = UniversalInstaller(tmp_path / "plugins")
    installer.MAX_ARCHIVE_FILES = 1
    payload = make_zip([("repo/a.md", "a"), ("repo/b.md", "b")])
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        with pytest.raises(ValueError, match="too many entries"):
            installer._validated_archive(archive)


def test_installer_bounds_single_member_size(tmp_path):
    installer = UniversalInstaller(tmp_path / "plugins")
    installer.MAX_MEMBER_BYTES = 5
    payload = make_zip([("repo/README.md", "more than five bytes")])
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        with pytest.raises(ValueError, match="member is too large"):
            installer._validated_archive(archive)


def test_installer_rejects_arbitrary_https_host(tmp_path):
    installer = UniversalInstaller(tmp_path / "plugins")

    with pytest.raises(ValueError, match="supported|host"):
        installer._parse_repo_url("https://example.test/archive.zip")


def test_installer_rejects_unsafe_redirect_before_follow(tmp_path):
    installer = UniversalInstaller(tmp_path / "plugins")
    handler = installer._redirect_handler()
    request = Request("https://github.com/acme/repo/archive.zip")

    with pytest.raises(ValueError, match="supported|HTTPS"):
        handler.redirect_request(
            request, None, 302, "Found", {}, "http://169.254.169.254/latest/meta-data")

    with pytest.raises(ValueError, match="supported|host"):
        handler.redirect_request(
            request, None, 302, "Found", {}, "https://example.test/archive.zip")



def test_installer_rejects_extreme_compression_ratio(tmp_path):
    installer = UniversalInstaller(tmp_path / "plugins")
    payload = "A" * (1024 * 1024)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9) as archive:
        archive.writestr("repo/README.md", payload)

    with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as archive:
        info = archive.infolist()[0]
        assert info.file_size / max(info.compress_size, 1) > installer.MAX_COMPRESSION_RATIO
        with pytest.raises(ValueError, match="compression ratio"):
            installer._validated_archive(archive)


def test_installer_enforces_absolute_download_deadline(tmp_path, monkeypatch):
    installer = UniversalInstaller(tmp_path / "plugins")
    installer.DOWNLOAD_DEADLINE_SECONDS = 10
    clock = [0.0]

    class SlowResponse(FakeResponse):
        def read(self, size=-1):
            clock[0] = 11.0
            return b"late"

    monkeypatch.setattr("plugins.installer.time.monotonic", lambda: clock[0])
    monkeypatch.setattr(installer, "_open_url",
                        lambda request, timeout: SlowResponse(b""))

    with pytest.raises(TimeoutError, match="deadline"):
        installer._download("https://github.com/acme/archive.zip", tmp_path / "late.zip")

    assert not (tmp_path / "late.zip").exists()


def test_installer_requires_preview_approval_before_writing(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "plugins"
    installer = UniversalInstaller(plugin_dir)
    payload = make_zip([("repo/README.md", "Previewed skill content")])
    monkeypatch.setattr(installer, "_open_url",
                        lambda request, timeout: FakeResponse(payload))
    url = "https://github.com/acme/repo"

    with pytest.raises(PermissionError, match="approval"):
        installer.install_from_url(url)
    assert not (plugin_dir / "repo").exists()
    with pytest.raises(PermissionError, match="not approved"):
        installer.install_from_url(url, approval_callback=lambda preview: False)
    assert not (plugin_dir / "repo").exists()

    previews = []
    status = installer.install_from_url(
        url, approval_callback=lambda preview: previews.append(preview) or True)

    assert "berhasil diinstal" in status
    assert len(previews) == 1
    assert "README.md" in previews[0]
    assert "Previewed skill content" in previews[0]
    assert (plugin_dir / "repo" / "prompt.md").exists()
