from multi_uav_field_toolkit.tuning.config import ParameterSpec
from multi_uav_field_toolkit.tuning.search import candidate_id, generate_round

PARAMETERS = {
    "lookahead_m": ParameterSpec("lookahead_m", "float", 6.0, 4.0, 8.0, 1.0),
    "sample_stride": ParameterSpec("sample_stride", "integer", 2, 1, 3, 1),
}


def test_round_zero_contains_only_baseline():
    candidates = generate_round(PARAMETERS, round_index=0, center=None, shrink_factor=0.5)

    assert len(candidates) == 1
    assert candidates[0].source == "baseline"
    assert candidates[0].parameters == {"lookahead_m": 6.0, "sample_stride": 2}


def test_later_round_is_bounded_coordinate_neighborhood():
    center = {"lookahead_m": 6.0, "sample_stride": 2}
    candidates = generate_round(
        PARAMETERS,
        round_index=1,
        center=center,
        shrink_factor=0.5,
    )

    assert len(candidates) == 4
    for candidate in candidates:
        changed = [name for name in center if candidate.parameters[name] != center[name]]
        assert len(changed) == 1
        assert 4.0 <= candidate.parameters["lookahead_m"] <= 8.0
        assert isinstance(candidate.parameters["sample_stride"], int)


def test_seen_candidates_are_not_generated_again():
    center = {"lookahead_m": 4.0, "sample_stride": 1}
    first = generate_round(PARAMETERS, 1, center, 0.5)
    seen = {candidate.candidate_id for candidate in first}

    assert generate_round(PARAMETERS, 1, center, 0.5, seen=seen) == []


def test_candidate_fingerprint_normalizes_float_values():
    assert candidate_id({"value": 0.1 + 0.2}) == candidate_id({"value": 0.3})
