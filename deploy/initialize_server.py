"""Initialize AI Assist's isolated services on the target CentOS server as root.

Copy this directory to /opt/aiassist/deploy and build the backend image first.
Credentials are generated on the server and never printed.
"""
from __future__ import annotations

import argparse
import grp
import os
from pathlib import Path
import pwd
import secrets
import shutil
import subprocess
import time


def run(*args: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=True, text=True, **kwargs)


def write_private(destination: Path, content: str) -> None:
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(content)
    destination.chmod(0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("Run as root on the target server")
    run("docker", "image", "inspect", args.image, stdout=subprocess.DEVNULL)
    source = Path(__file__).resolve().parent
    services = ("aiassist-mysql.service", "aiassist-api.service")
    for service in services:
        destination = Path("/etc/systemd/system") / service
        if destination.exists() and "# Managed by AIassist report backend deployment" not in destination.read_text():
            raise SystemExit(f"Refusing to replace an unrelated service: {destination}")
    configuration = Path("/etc/aiassist")
    configuration.mkdir(mode=0o755, exist_ok=True)
    for username in ("aiassist", "aiassist-db"):
        try:
            pwd.getpwnam(username)
        except KeyError:
            run("useradd", "--system", "--no-create-home", "--shell", "/sbin/nologin", username)
    for username, directory in (("aiassist", "/var/lib/aiassist"), ("aiassist-db", "/var/lib/aiassist-mysql")):
        account = pwd.getpwnam(username)
        run("install", "-d", "-m", "750", "-o", username, "-g", grp.getgrgid(account.pw_gid).gr_name, directory)
    shutil.copyfile(source / "aiassist-mysql.cnf", configuration / "mysql.cnf")
    (configuration / "mysql.cnf").chmod(0o644)
    for service in services:
        destination = Path("/etc/systemd/system") / service
        if destination.exists():
            shutil.copyfile(destination, destination.with_suffix(".service.previous"))
        shutil.copyfile(source / service, destination)
    datadir = Path("/var/lib/aiassist-mysql")
    if not (datadir / "mysql").exists():
        print("Initializing isolated MySQL data directory", flush=True)
        run("/usr/sbin/mysqld", "--defaults-file=/etc/aiassist/mysql.cnf", "--initialize-insecure")
    run("systemctl", "daemon-reload")
    run("systemctl", "enable", "--now", "aiassist-mysql.service")
    admin = configuration / "mysql-admin.cnf"
    mysql = ["mysql", f"--defaults-file={admin}"] if admin.exists() else [
        "mysql", "--no-defaults", "--protocol=SOCKET", "--socket=/var/lib/aiassist-mysql/mysql.sock", "--user=root"
    ]
    for attempt in range(60):
        probe = subprocess.run(mysql + ["--batch", "--execute=SELECT 1"], capture_output=True)
        if probe.returncode == 0:
            break
        time.sleep(1)
    else:
        raise SystemExit("Isolated MySQL did not become ready; inspect journalctl -u aiassist-mysql")
    backend_env = configuration / "backend.env"
    if not backend_env.exists():
        database_password = secrets.token_hex(32)
        root_password = secrets.token_hex(32)
        sql = (
            "CREATE DATABASE IF NOT EXISTS aiassist CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n"
            f"CREATE USER 'aiassist'@'127.0.0.1' IDENTIFIED BY '{database_password}';\n"
            "GRANT ALL PRIVILEGES ON aiassist.* TO 'aiassist'@'127.0.0.1';\n"
            f"ALTER USER 'root'@'localhost' IDENTIFIED BY '{root_password}';\n"
        )
        run(*mysql, input=sql, stdout=subprocess.DEVNULL)
        write_private(admin, "[client]\nuser=root\nprotocol=SOCKET\nsocket=/var/lib/aiassist-mysql/mysql.sock\n" + f"password={root_password}\n")
        write_private(backend_env, (
            "APP_ENV=production\n"
            "AIASSIST_DATA_DIR=/var/lib/aiassist\n"
            f"DATABASE_URL=mysql+pymysql://aiassist:{database_password}@127.0.0.1:3307/aiassist?charset=utf8mb4\n"
            "RULE_API_BASE_URL=http://127.0.0.1:5000/rules\n"
            "CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173\n"
            "DEEPSEEK_API_KEY=\n"
            "DEEPSEEK_API_URL=https://api.deepseek.com/v1\n"
            "DEEPSEEK_API_BASE_URL=https://api.deepseek.com/v1\n"
            "DEEPSEEK_MODEL=deepseek-chat\n"
            "MODEL_USE_ENV_PROXY=false\n"
            "NO_PROXY=localhost,127.0.0.1,10.60.150.179\n"
        ))
    account = pwd.getpwnam("aiassist")
    write_private(configuration / "container.env", (
        f"AIASSIST_BACKEND_IMAGE={args.image}\nAIASSIST_UID={account.pw_uid}\nAIASSIST_GID={account.pw_gid}\n"
    ))
    run("systemctl", "enable", "aiassist-api.service")
    run("systemctl", "restart", "aiassist-api.service")
    print("AI Assist services configured; credentials are in /etc/aiassist with mode 0600.", flush=True)


if __name__ == "__main__":
    main()
