"""Launch defaults must start the three-file detection-topic acceptance path."""

import ast
import importlib.util
import pathlib
import sys
import types


LAUNCH_FILE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "launch"
    / "deepstream.launch.py"
)


def _launch_defaults():
    tree = ast.parse(LAUNCH_FILE.read_text(encoding="utf-8"))
    values = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if (
            not isinstance(node.func, ast.Name)
            or node.func.id != "DeclareLaunchArgument"
        ):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        for keyword in node.keywords:
            if keyword.arg == "default_value" and isinstance(
                keyword.value, ast.Constant
            ):
                values[node.args[0].value] = keyword.value.value
    return values


def test_launch_leaves_optional_values_to_the_selected_profile():
    defaults = _launch_defaults()

    assert defaults["display"] == ""
    assert defaults["publish_detections"] == ""
    assert defaults["publish_annotated"] == ""
    assert defaults["allowed_class_ids"] == ""
    assert defaults["display_sink"] == ""


def test_default_profile_is_the_three_file_headless_profile():
    source = LAUNCH_FILE.read_text(encoding="utf-8")

    assert '"sim_3cam_headless.yaml"' in source
    assert "OpaqueFunction(function=_create_node)" in source


class _FakeLaunchConfiguration:
    def __init__(self, name):
        self.name = name

    def perform(self, context):
        return context.launch_configurations[self.name]


def _load_launch_module(monkeypatch):
    launch = types.ModuleType("launch")
    launch_actions = types.ModuleType("launch.actions")
    launch_substitutions = types.ModuleType("launch.substitutions")
    launch.LaunchDescription = lambda actions: actions
    launch_actions.DeclareLaunchArgument = lambda *args, **kwargs: (
        args,
        kwargs,
    )
    launch_actions.OpaqueFunction = lambda **kwargs: kwargs
    launch_substitutions.LaunchConfiguration = _FakeLaunchConfiguration
    launch_substitutions.PathJoinSubstitution = lambda parts: parts
    launch_ros = types.ModuleType("launch_ros")
    launch_ros_actions = types.ModuleType("launch_ros.actions")
    launch_ros_substitutions = types.ModuleType("launch_ros.substitutions")
    launch_ros_actions.Node = _FakeNode
    launch_ros_substitutions.FindPackageShare = lambda package: package
    launch_ros.actions = launch_ros_actions
    launch_ros.substitutions = launch_ros_substitutions
    monkeypatch.setitem(sys.modules, "launch", launch)
    monkeypatch.setitem(sys.modules, "launch.actions", launch_actions)
    monkeypatch.setitem(
        sys.modules, "launch.substitutions", launch_substitutions
    )
    monkeypatch.setitem(sys.modules, "launch_ros", launch_ros)
    monkeypatch.setitem(sys.modules, "launch_ros.actions", launch_ros_actions)
    monkeypatch.setitem(
        sys.modules,
        "launch_ros.substitutions",
        launch_ros_substitutions,
    )
    spec = importlib.util.spec_from_file_location(
        "deepstream_launch", LAUNCH_FILE
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _FakeNode:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _launch_context(values):
    context = types.SimpleNamespace(
        launch_configurations={
            "params_file": "/tmp/profile.yaml",
            "display": "",
            "publish_detections": "",
            "publish_annotated": "",
            "allowed_class_ids": "",
            "display_sink": "",
            "runtime_preflight": "true",
        }
    )
    context.launch_configurations.update(values)
    return context


def test_empty_launch_overrides_leave_profile_values_unchanged(monkeypatch):
    module = _load_launch_module(monkeypatch)

    context = _launch_context({})
    node = module._create_node(context)[0]

    parameters = node.kwargs["parameters"]
    assert len(parameters) == 1
    assert parameters[0].perform(context) == "/tmp/profile.yaml"


def test_explicit_launch_overrides_are_appended_after_profile(monkeypatch):
    module = _load_launch_module(monkeypatch)

    context = _launch_context(
        {"display": "true", "publish_detections": "false"}
    )
    node = module._create_node(context)[0]

    parameters = node.kwargs["parameters"]
    assert parameters[0].perform(context) == "/tmp/profile.yaml"
    assert parameters[1] == {"display": True, "publish_detections": False}


def test_class_id_launch_override_is_parsed_as_an_integer_array(monkeypatch):
    module = _load_launch_module(monkeypatch)

    context = _launch_context({"allowed_class_ids": "0, 2"})
    node = module._create_node(context)[0]

    assert node.kwargs["parameters"][1] == {"allowed_class_ids": [0, 2]}


def test_display_sink_launch_override_is_passed_as_a_string(monkeypatch):
    module = _load_launch_module(monkeypatch)

    context = _launch_context({"display_sink": "fakesink"})
    node = module._create_node(context)[0]

    assert node.kwargs["parameters"][1] == {"display_sink": "fakesink"}


def test_preflight_allows_a_mapped_jetson_runtime(monkeypatch):
    module = _load_launch_module(monkeypatch)
    monkeypatch.setattr(module, "_jetson_gpu_devices_available", lambda: True)

    assert module._preflight_runtime(_launch_context({})) == []


def test_preflight_rejects_missing_jetson_gpu_devices(monkeypatch):
    module = _load_launch_module(monkeypatch)
    monkeypatch.setattr(module, "_jetson_gpu_devices_available", lambda: False)

    try:
        module._preflight_runtime(_launch_context({}))
    except RuntimeError as exc:
        assert "/dev/nvmap" in str(exc)
        assert "check_runtime.sh --require-gpu" in str(exc)
    else:
        raise AssertionError("missing Jetson GPU devices must stop the launch")


def test_preflight_can_be_disabled_for_non_jetson_development(monkeypatch):
    module = _load_launch_module(monkeypatch)
    monkeypatch.setattr(module, "_jetson_gpu_devices_available", lambda: False)

    assert (
        module._preflight_runtime(
            _launch_context({"runtime_preflight": "false"})
        )
        == []
    )
