"""The seed command line: --reset is an explicit flag, off unless given."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SEED_PATH = Path(__file__).resolve().parents[2] / "scripts" / "seed_demo.py"
BASE_ARGV = ["--confirm", "MedicalBooking", "--credentials-out", "creds.json"]


@pytest.fixture(scope="module")
def seed():
    spec = importlib.util.spec_from_file_location("seed_demo", SEED_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_reset_is_off_unless_the_flag_is_given(seed) -> None:
    assert seed.build_parser().parse_args(BASE_ARGV).reset is False


def test_the_reset_flag_turns_the_reset_on(seed) -> None:
    assert seed.build_parser().parse_args([*BASE_ARGV, "--reset"]).reset is True


def test_existing_options_keep_their_defaults(seed) -> None:
    args = seed.build_parser().parse_args(BASE_ARGV)

    assert (args.days, args.seed, args.allow_production) == (14, 20261113, False)
