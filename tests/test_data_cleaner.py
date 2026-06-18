from bom_tool.core.data_cleaner import clean_mpn


def test_clean_mpn_removes_common_suffixes() -> None:
    assert clean_mpn(" STM32F103C8T6-TR ") == "STM32F103C8T6"
    assert clean_mpn("ABC-CT") == "ABC"
    assert clean_mpn("ABC/t&r") == "ABC"


def test_clean_mpn_handles_empty_values() -> None:
    assert clean_mpn(None) == ""
    assert clean_mpn("") == ""


def test_clean_mpn_extracts_model_from_mixed_cell_values() -> None:
    assert clean_mpn("XC7Z020-2CLG484I（云汉）") == "XC7Z020-2CLG484I"
    assert clean_mpn("MT41K256M16HA-125:E,BGA96_B") == "MT41K256M16HA-125:E"
    assert clean_mpn("TPS51200_1-TPS51200,PVSON-N10-A") == "TPS51200"
