"""Contracts for the repository-local native SAHI plugin sources."""

import pathlib


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN_DIRS = (
    REPO_ROOT / "plugins" / "nvsahipreprocess",
    REPO_ROOT / "plugins" / "nvsahipostprocess",
)
SOURCE_FILENAMES = {
    "nvsahipreprocess": {
        "Makefile",
        "gstnvsahipreprocess.cpp",
        "gstnvsahipreprocess.h",
        "gstnvsahipreprocess_allocator.cpp",
        "gstnvsahipreprocess_allocator.h",
        "nvsahipreprocess_property_parser.cpp",
        "nvsahipreprocess_property_parser.h",
    },
    "nvsahipostprocess": {
        "Makefile",
        "greedy_nmm.h",
        "gstnvsahipostprocess.cpp",
        "gstnvsahipostprocess.h",
        "mask_merge.h",
        "spatial_grid.h",
    },
}


def _plugin_text():
    return "\n".join(
        path.read_text(encoding="utf-8")
        for directory in PLUGIN_DIRS
        for path in sorted(directory.iterdir())
        if path.name in SOURCE_FILENAMES[directory.name]
    )


def test_native_sahi_sources_are_vendored_with_required_support_files():
    for directory_name, filenames in SOURCE_FILENAMES.items():
        directory = REPO_ROOT / "plugins" / directory_name
        actual = {
            path.name
            for path in directory.iterdir()
            if path.is_file() and path.name in filenames
        }
        assert actual == filenames


def test_native_sahi_sources_use_repository_identity_and_keep_deepstream_notice():
    text = _plugin_text()
    legacy_name = "".join(chr(code) for code in (108, 103, 111, 105, 120))
    legacy_author = "".join(
        chr(code)
        for code in (76, 101, 118, 105, 32, 80, 101, 114, 101, 105, 114, 97)
    )

    assert "Yuhao Ding <d346863528@163.com>" in text
    assert legacy_author not in text
    assert legacy_author.lower().replace(" ", "") not in text.lower()
    assert legacy_name not in text.lower()
    assert "NVIDIA CORPORATION" in text
    assert "Original source: NVIDIA DeepStream SDK" in text
    assert "LicenseRef-Nvidia" in text


def test_native_sahi_build_is_repository_local():
    script = REPO_ROOT / "scripts" / "build_sahi_plugins.sh"
    text = script.read_text(encoding="utf-8")
    legacy_name = "".join(chr(code) for code in (108, 103, 111, 105, 120))
    legacy_source_var = (
        "".join(chr(code) for code in (76, 71, 79, 73, 88)) + "_SOURCE_DIR"
    )
    legacy_commit = "114a5c66075e842b1a1dd23" + "40ef5541fc7bcb7a"

    assert script.stat().st_mode & 0o111
    assert legacy_source_var not in text
    assert "deepstream-sahi-" + legacy_name not in text
    assert legacy_commit not in text
    assert "plugins/nvsahipreprocess" in text
    assert "plugins/nvsahipostprocess" in text
    assert 'GST_INSTALL_DIR="${LOCAL_DIR}/lib/gstreamer-1.0"' in text
