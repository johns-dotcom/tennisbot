from bot.reports import check_mapping


def test_mapping_ok_yes_won():
    assert check_mapping(2, 1, "yes") == "ok"       # we saw YES win 2-1, settled yes
    assert check_mapping(1, 2, "no") == "ok"        # we saw YES lose, settled no


def test_mapping_mismatch_is_flip():
    assert check_mapping(2, 1, "no") == "mismatch"  # we saw YES win but settled no
    assert check_mapping(0, 2, "yes") == "mismatch"


def test_mapping_unverifiable():
    assert check_mapping(1, 1, "yes") == "unverifiable"   # tie / in-progress
    assert check_mapping(2, 0, None) == "unverifiable"    # not settled
    assert check_mapping(2, 0, "void") == "unverifiable"
