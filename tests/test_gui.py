from src.gui import build_export_args, build_import_args, build_login_args, build_reprice_args


def test_build_login_args_defaults_to_bare_command():
    assert build_login_args(False) == ["login"]


def test_build_login_args_includes_debug():
    assert build_login_args(True) == ["login", "--debug-browser"]


def test_build_reprice_args_defaults_to_bare_command():
    assert build_reprice_args(False, False, False, "") == ["reprice"]


def test_build_reprice_args_includes_all_flags():
    args = build_reprice_args(True, True, True, "5")
    assert args == ["reprice", "--dry-run", "--debug-browser", "--inspect-modal", "--limit", "5"]


def test_build_reprice_args_ignores_blank_limit():
    assert build_reprice_args(False, False, False, "   ") == ["reprice"]


def test_build_reprice_args_omits_default_price_source():
    assert build_reprice_args(False, False, False, "", "7d") == ["reprice"]


def test_build_reprice_args_includes_non_default_price_source():
    args = build_reprice_args(False, False, False, "", "30d")
    assert args == ["reprice", "--price-source", "30d"]


def test_build_reprice_args_includes_adjustment_pct():
    args = build_reprice_args(False, False, False, "", "7d", "+5%")
    assert args == ["reprice", "--adjustment-pct", "+5%"]


def test_build_reprice_args_ignores_blank_adjustment_pct():
    assert build_reprice_args(False, False, False, "", "7d", "   ") == ["reprice"]


def test_build_export_args_defaults_to_bare_command():
    assert build_export_args("", False) == ["export"]


def test_build_export_args_includes_output_and_debug():
    args = build_export_args(" reports\\out.csv ", True)
    assert args == ["export", "--output", "reports\\out.csv", "--debug-browser"]


def test_build_import_args_defaults_to_bare_command():
    assert build_import_args("", False, False, False, "") == ["import"]


def test_build_import_args_includes_all_flags():
    args = build_import_args("reports\\in.csv", True, True, True, "3")
    assert args == [
        "import", "--input", "reports\\in.csv", "--dry-run", "--debug-browser",
        "--inspect-modal", "--limit", "3",
    ]
