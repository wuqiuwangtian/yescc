"""测试环境入口；与被测产品分离，不读取继承的凭据。"""

from __future__ import annotations

import ipaddress
import json
import os
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent


def environment() -> dict[str, str]:
    tools = json.loads((ROOT / "tools.json").read_text("utf-8"))
    paths = [str(ROOT / "bin"), str(ROOT / ".runtime/bin"), *tools["path"]]
    env = {
        "PATH": os.pathsep.join(paths),
        "HOME": str(ROOT / "home"),
        "TMPDIR": str(ROOT / "tmp"),
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
        "NO_PROXY": "localhost,127.0.0.1,::1",
        "XDG_CONFIG_HOME": str(ROOT / "state/config"),
        "XDG_CACHE_HOME": str(ROOT / "state/cache"),
        "YESCC_CONFIG_DIR": str(ROOT / "state/yescc-config"),
        "YESCC_CACHE_DIR": str(ROOT / "state/yescc-cache"),
    }
    settings = ROOT / "model-test.json"
    if not settings.exists():
        return env
    data = json.loads(settings.read_text("utf-8"))
    if not isinstance(data, dict) or set(data) - {"l2", "l3"}:
        raise ValueError("测试环境配置只支持 l2/l3")
    names = {
        "base_url": "BASE_URL",
        "name": "NAME",
        "timeout_s": "TIMEOUT_S",
        "input_price_per_mtok": "INPUT_PRICE_PER_MTOK",
        "output_price_per_mtok": "OUTPUT_PRICE_PER_MTOK",
    }
    for tier, config in data.items():
        if not isinstance(config, dict) or set(config) - names.keys():
            raise ValueError("测试环境配置含未知字段，不支持真实凭据")
        endpoint = urlsplit(config.get("base_url", ""))
        host = endpoint.hostname or ""
        try:
            loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
        if (
            endpoint.scheme not in {"http", "https"}
            or not loopback
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
        ):
            raise ValueError("测试环境只允许无凭据的回环模型地址")
        prefix = f"YESCC_MODEL_{tier.upper()}_"
        env[prefix + "PROVIDER"] = "openai-compatible"
        env[prefix + "API_KEY"] = "blackbox-dummy-key"
        for name, value in config.items():
            env[prefix + names[name]] = str(value)
    return env


def main() -> None:
    if not Path.cwd().resolve().is_relative_to(ROOT):
        raise ValueError("请在测试工作区内执行")
    env = environment()
    if sys.argv[1] == "yescc":
        command = [str(ROOT / ".runtime/bin/yescc"), *sys.argv[2:]]
    else:
        command = sys.argv[2:]
        if not command:
            raise ValueError("用法：bin/test-env <命令> [参数]")
    resolved = shutil.which(command[0], path=env["PATH"])
    if resolved is None:
        raise ValueError(f"测试环境找不到命令：{command[0]}")
    os.execve(resolved, command, env)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        print(f"测试环境入口：{exc}", file=sys.stderr)
        raise SystemExit(2) from None
