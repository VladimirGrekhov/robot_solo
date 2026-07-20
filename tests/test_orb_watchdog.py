"""Тесты watchdog связи (пункт №3): _probe_connection различает живую/мёртвую связь
с QUIK, чтобы алерт «нет данных» говорил правду — чинить график или модем."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import orb_robot


class Conn:
    def __init__(self, val=None, raise_=False):
        self.val = val
        self.raise_ = raise_

    def is_connected(self):
        if self.raise_:
            raise RuntimeError("socket dead")
        return {"data": self.val}


class NoMethod:
    pass


def test_connected():
    assert orb_robot._probe_connection(Conn(val=1)) == "ok"


def test_disconnected():
    assert orb_robot._probe_connection(Conn(val=0)) == "down"


def test_socket_dead_raises():
    assert orb_robot._probe_connection(Conn(raise_=True)) == "down"


def test_no_method_unknown():
    assert orb_robot._probe_connection(NoMethod()) == "unknown"


def test_plain_bool_return():
    class C:
        def is_connected(self):
            return True
    assert orb_robot._probe_connection(C()) == "ok"
