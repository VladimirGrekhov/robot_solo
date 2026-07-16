"""load_config: рабочий config_orb.yaml локальный (в git его нет), поэтому при
отсутствии он должен создаваться из config_orb.example.yaml, а не ронять робота."""
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import orb_robot


def test_loads_existing_config(tmp_path):
    p = tmp_path / "config_orb.yaml"
    p.write_text("mode: paper\ndeposit_rub: 123.0\n", encoding="utf-8")
    cfg = orb_robot.load_config(p)
    assert cfg["mode"] == "paper"
    assert cfg["deposit_rub"] == 123.0


def test_missing_config_created_from_example(tmp_path):
    # файла нет -> load_config копирует из config_orb.example.yaml (реальный шаблон репо)
    p = tmp_path / "config_orb.yaml"
    assert not p.exists()
    cfg = orb_robot.load_config(p)
    assert p.exists()                       # файл создан
    assert "mode" in cfg and "risk" in cfg  # шаблон распарсился
    # и это валидный YAML на диске
    assert isinstance(yaml.safe_load(p.read_text(encoding="utf-8")), dict)
