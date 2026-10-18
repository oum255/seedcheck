import pytest

from seedcheck.errors import ConfigError
from seedcheck.releases import DEFAULT_RELEASES, SUPPORTED, resolve, supported_summary


def test_default_releases_are_the_three_supported_lts() -> None:
    assert DEFAULT_RELEASES == ("22.04", "24.04", "26.04")


@pytest.mark.parametrize(
    ("name", "codename"),
    [("22.04", "jammy"), ("jammy", "jammy"), ("24.04", "noble"), ("26.04", "resolute")],
)
def test_resolve_by_version_or_codename(name: str, codename: str) -> None:
    assert resolve(name).codename == codename


def test_resolve_ignores_case_and_surrounding_spaces() -> None:
    assert resolve("  Noble ").version == "24.04"


def test_each_release_launches_the_matching_ubuntu_image() -> None:
    for release in SUPPORTED:
        assert release.image == f"ubuntu:{release.version}"


@pytest.mark.parametrize("name", ["20.04", "focal", "bionic", "", "24.10"])
def test_unsupported_release_is_rejected_with_the_supported_list(name: str) -> None:
    with pytest.raises(ConfigError) as exc:
        resolve(name)
    assert "unsupported release" in str(exc.value)
    assert supported_summary() in str(exc.value)


def test_supported_summary_lists_versions_and_codenames() -> None:
    assert supported_summary() == "22.04 (jammy), 24.04 (noble), 26.04 (resolute)"
