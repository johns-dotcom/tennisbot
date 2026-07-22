from bot.prob.confidence import confidence_band, confidence_label


def test_bands_cover_the_range_monotonically():
    assert confidence_band(0.0).label == "Minimal"
    assert confidence_band(0.1).label == "Minimal"
    assert confidence_band(0.3).label == "Low"
    assert confidence_band(0.5).label == "Fair"
    assert confidence_band(0.7).label == "Good"
    assert confidence_band(0.9).label == "Strong"
    assert confidence_band(1.0).label == "Strong"


def test_band_tiers_map_to_ui_colours():
    assert confidence_band(0.05).tier == "critical"
    assert confidence_band(0.30).tier == "warn"
    assert confidence_band(0.50).tier == "neutral"
    assert confidence_band(0.90).tier == "good"


def test_none_is_minimal():
    assert confidence_band(None).label == "Minimal"


def test_label_shows_band_and_value():
    assert confidence_label(0.78) == "Good (78%)"
    assert confidence_label(None) == "Minimal (0%)"
