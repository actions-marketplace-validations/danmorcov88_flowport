import pytest

from flowport.rules.expression import find_parameter_references, find_references


def names(text: str) -> list[str]:
    return [r.name for r in find_references(text)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("${host}", ["host"]),
        ("http://${host}:${port}/api", ["host", "port"]),
        ("${ host }", ["host"]),
        ("${'my var'}", ["my var"]),
        ('${"quoted.name"}', ["quoted.name"]),
        ("${host:toUpper()}", ["host"]),
        ("${path:append('/x'):replace('a', 'b')}", ["path"]),
        ("${now()}", []),
        ("${literal('x'):toUpper()}", []),
        ("${UUID()}", []),
        ("${a:equals(${b})}", ["a", "b"]),
        ("${allAttributes('a', 'b'):join(',')}", []),
        ("$${host}", []),
        ("$$${host}", ["host"]),
        ("plain text", []),
        ("#{param}", []),
        ("${", []),
        ("${}", []),
        ("${'unterminated}", []),
    ],
)
def test_find_references(text: str, expected: list[str]) -> None:
    assert names(text) == expected


def test_reference_positions_and_flags() -> None:
    refs = find_references("x=${host:toUpper()} y=${'a b'}")
    assert refs[0].name == "host"
    assert refs[0].has_function is True
    assert refs[0].quoted is False
    assert "x=${host:toUpper()} y=${'a b'}"[refs[0].subject_start : refs[0].subject_end] == "host"
    assert refs[1].name == "a b"
    assert refs[1].quoted is True
    assert refs[1].has_function is False


def test_parameter_references() -> None:
    assert find_parameter_references("#{host}/#{path.x}") == ["host", "path.x"]
    assert find_parameter_references("##{escaped}") == []
    assert find_parameter_references("###{escaped}") == ["escaped"]
    assert find_parameter_references("####{escaped}") == []
    assert find_parameter_references("#{bad/name}") == []
