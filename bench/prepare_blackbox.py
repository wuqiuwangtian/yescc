"""准备独立 AI 黑盒工作区，不执行或伪造独立验收。"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "bench/blackbox"


def prepare(workspace: Path, wheel: Path) -> dict[str, object]:
    if os.name != "posix":
        raise ValueError("此准备入口仅用于当前 WSL/Linux 测试环境")
    evidence = json.loads(
        gzip.decompress((ROOT / "docs/评测/data/rc2-package-ci.json.gz").read_bytes())
    )
    target = evidence["artifacts"]["rc-package-Linux"]
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    if digest != target["wheel_sha256"]:
        raise ValueError("候选 wheel 与已归档 CI 验证版本不一致")
    if workspace == ROOT or workspace.is_relative_to(ROOT):
        raise ValueError("黑盒工作区必须位于项目仓库之外")
    if workspace.exists():
        raise ValueError("工作区已存在，拒绝覆盖测试者数据")
    workspace.mkdir(parents=True)
    for name in ("bin", "cases", "outputs", "reports", "state", "tmp", "home"):
        (workspace / name).mkdir()
    # 无继承用户索引/凭据；候选包依赖为已验证的公共锁定版本。
    install_env = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
    constraints = workspace / ".runtime-constraints.txt"
    constraints.write_text(
        "".join(
            f"{item['name']}=={item['version']}\n"
            for item in target["licenses"]
            if item["name"] != "yescc"
        ),
        encoding="utf-8",
    )
    subprocess.run(
        ["uv", "venv", "--python", "3.11", str(workspace / ".runtime")],
        env=install_env,
        check=True,
        capture_output=True,
        timeout=120,
    )
    python = workspace / ".runtime/bin/python"
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--constraints",
            str(constraints),
            str(wheel),
        ],
        env=install_env,
        check=True,
        capture_output=True,
        timeout=120,
    )
    constraints.unlink()
    for file in TEMPLATES.iterdir():
        if file.suffix in {".txt", ".md", ".json"}:
            name = "AGENTS.md" if file.name == "AGENTS.txt" else file.name
            shutil.copyfile(file, workspace / name)
    shutil.copyfile(TEMPLATES / "launcher.py", workspace / ".launcher.py")
    for name, mode in (("yescc", "yescc"), ("test-env", "exec")):
        entry = workspace / "bin" / name
        entry.write_text(
            '#!/bin/sh\nHERE=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\n'
            f'exec "$HERE/.runtime/bin/python" "$HERE/.launcher.py" {mode} "$@"\n',
            encoding="utf-8",
        )
        entry.chmod(0o755)
    tool_paths = ["/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    for name in ("gcc", "node"):
        path = shutil.which(name)
        if path:
            tool_paths.append(str(Path(path).parent))
    (workspace / "tools.json").write_text(
        json.dumps({"path": list(dict.fromkeys(tool_paths))}, indent=2) + "\n", encoding="utf-8"
    )
    installed = list((workspace / ".runtime/lib").glob("python*/site-packages/yescc"))
    if len(installed) != 1:
        raise ValueError("无法定位独立安装，拒绝生成准备记录")
    runtime_files = [
        file
        for file in installed[0].rglob("*")
        if file.is_file() and file.suffix in {".py", ".typed"}
    ]
    runtime_files.append(workspace / ".runtime/bin/yescc")
    return {
        "runtime_sha256": {
            str(file.relative_to(workspace / ".runtime")): hashlib.sha256(
                file.read_bytes()
            ).hexdigest()
            for file in runtime_files
        },
        "kind": "blind-ai-workspace-preparation",
        "workspace": str(workspace),
        "product_version": target["version"],
        "product_code_commit": evidence["code_commit"],
        "wheel_sha256": digest,
        "ci_run": evidence["run_url"],
        "review_executed": False,
        "human_test": False,
        "scope": "WSL/Linux, independent AI black-box, no real model services",
        "material_sha256": {
            file.name: hashlib.sha256(file.read_bytes()).hexdigest()
            for file in workspace.iterdir()
            if file.is_file()
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument(
        "--receipt", type=Path, required=True, help="准备记录保存在工作区之外，避免泄露开发历史"
    )
    args = parser.parse_args()
    workspace = args.workspace.expanduser().resolve()
    receipt = args.receipt.expanduser().resolve()
    if receipt.is_relative_to(workspace):
        parser.error("准备记录不能放入黑盒工作区")
    result = prepare(workspace, args.wheel.expanduser().resolve())
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "workspace": str(workspace),
                "version": result["product_version"],
                "review_executed": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
