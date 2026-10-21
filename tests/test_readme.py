from pathlib import Path

import pytest

from wavguard import Code

README = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("code", list(Code), ids=str)
def test_every_code_is_documented_in_readme(code):
    assert f"| `{code}` |" in README
