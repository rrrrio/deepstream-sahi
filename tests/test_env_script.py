"""Shell contracts for loading the project environment from strict shells."""

import pathlib
import subprocess


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_env_script_loads_when_nounset_is_enabled():
    result = subprocess.run(
        [
            "bash",
            "-eu",
            "-c",
            'source scripts/env.sh; test -n "$DEEPSTREAM_SAHI_ROOT"',
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_env_script_exposes_venv_pyds_to_system_python():
    result = subprocess.run(
        [
            "bash",
            "-eu",
            "-c",
            "source scripts/env.sh; python3 -c 'import pyds; print(pyds.__file__)'",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert ".venv/lib/python3.10/site-packages/pyds" in result.stdout


def test_simulation_launcher_loads_environment_once():
    launcher = (REPO_ROOT / "scripts" / "run_sim_3cam_headless.sh").read_text(
        encoding="utf-8"
    )

    assert (
        launcher.count('source "${REPO_ROOT}/.colcon/install/setup.bash"') == 0
    )
    assert 'source "${SCRIPT_DIR}/env.sh"' in launcher
    assert 'check_runtime.sh" --require-gpu' in launcher
    assert 'EGL_PLATFORM="${SIM_EGL_PLATFORM:-surfaceless}"' in launcher
