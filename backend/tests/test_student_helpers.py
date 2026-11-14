import pytest

from app.student_helpers import (
    fix_encoding,
    normalize_student_name,
    parse_student_date,
    student_matricule,
    valid_email,
)


def test_normalizes_accented_name():
    assert normalize_student_name("Éboué") == "EBOUE"


def test_rejects_name_without_letters():
    with pytest.raises(ValueError):
        normalize_student_name("1234")


def test_generates_compatible_matricule():
    assert student_matricule("Kouadio", "Alain", "1998-03-17") == "KOUA1703980001"


def test_accepts_supported_date_formats():
    assert parse_student_date("17/03/1998") == "1998-03-17"
    assert parse_student_date("17-03-1998") == "1998-03-17"
    assert parse_student_date("1998-03-17") == "1998-03-17"


def test_rejects_invalid_or_future_dates():
    assert parse_student_date("31/02/1998") is None
    assert parse_student_date("2999-01-01") is None


def test_valid_email():
    assert valid_email("")
    assert valid_email("nom@exemple.ci")
    assert not valid_email("pas-un-email")


def test_fix_encoding():
    assert fix_encoding("Ã©tudiant") == "étudiant"
    assert fix_encoding("étudiant") == "étudiant"
