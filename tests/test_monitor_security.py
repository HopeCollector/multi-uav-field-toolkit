import pytest

from multi_uav_field_toolkit.monitor.host.server import WEB_ROOT, is_loopback_bind, parse_args


def test_monitor_defaults_to_loopback():
    args = parse_args([])
    assert args.bind == "127.0.0.1"
    assert is_loopback_bind(args.bind)
    assert (WEB_ROOT / "index.html").is_file()


def test_remote_bind_requires_explicit_opt_in():
    with pytest.raises(SystemExit):
        parse_args(["--bind", "0.0.0.0"])

    args = parse_args(["--bind", "0.0.0.0", "--allow-remote"])
    assert args.allow_remote
