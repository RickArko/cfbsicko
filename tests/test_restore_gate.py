import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fly_db_restore.sh"


def test_restore_refuses_without_confirm():
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
        env={"CONFIRM": "", "CONFIRM_PROD": "", "BACKUP": "/tmp/nope.db"},
    )
    assert result.returncode == 2
    assert "Refusing restore" in result.stderr


def test_restore_refuses_wrong_prod(tmp_path):
    backup = tmp_path / "cfbsicko-20260902-000000.db"
    backup.write_bytes(b"not-a-real-db")
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
        env={
            "CONFIRM": "1",
            "CONFIRM_PROD": "cfbfantasy",
            "BACKUP": str(backup),
            "FLY_APP": "cfbsicko",
        },
    )
    assert result.returncode == 2
