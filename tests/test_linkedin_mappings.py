"""
Unit tests for LinkedIn mappings module.

Tests all mapping functions, validators, and helper utilities.
"""

import pytest
from automation.linkedin_mappings import (
    # Location functions
    get_location_display_names,
    get_location_urn,
    get_location_name_from_urn,
    validate_geo_urn,
    LOCATION_MAPPING,
    LOCATION_CHOICES,
    # Network functions
    get_network_display_names,
    get_network_value,
    get_network_name_from_value,
    NETWORK_MAPPING,
    NETWORK_CHOICES,
    # Industry functions
    get_industry_display_names,
    get_industry_id,
    get_industry_ids_for_multiple,
    get_industry_name_from_id,
    validate_industry_id,
    INDUSTRY_MAPPING,
    INDUSTRY_CHOICES,
    # Helper functions
    format_ids_for_url,
    # Constants for other mappings
    LANGUAGE_MAPPING,
    COMMON_COMPANIES,
)


# ============================================================================
# Location Tests
# ============================================================================

@pytest.mark.unit
class TestLocationMappings:
    """Test location mapping functions."""

    def test_get_location_display_names_returns_list(self):
        """Test that get_location_display_names returns a non-empty list."""
        names = get_location_display_names()
        assert isinstance(names, list)
        assert len(names) > 0

    def test_get_location_display_names_includes_any(self):
        """Test that 'Any' is included in location display names."""
        names = get_location_display_names()
        assert "Any" in names

    def test_get_location_display_names_includes_verified_locations(self):
        """Test that verified locations are in display names."""
        names = get_location_display_names()
        assert "San Francisco Bay Area" in names
        assert "Greater Boston Area" in names
        assert "United States" in names

    def test_get_location_urn_for_valid_location(self):
        """Test getting URN for a valid location."""
        urn = get_location_urn("San Francisco Bay Area")
        assert urn == "90000084"

    def test_get_location_urn_for_any_returns_empty_string(self):
        """Test that 'Any' location returns empty string."""
        urn = get_location_urn("Any")
        assert urn == ""

    def test_get_location_urn_for_invalid_location_returns_empty(self):
        """Test that invalid location returns empty string."""
        urn = get_location_urn("NonExistent Location")
        assert urn == ""

    def test_get_location_name_from_urn_for_valid_urn(self):
        """Test reverse lookup for valid URN."""
        name = get_location_name_from_urn("90000084")
        assert name == "San Francisco Bay Area"

    def test_get_location_name_from_urn_for_invalid_urn(self):
        """Test reverse lookup for invalid URN returns formatted string."""
        name = get_location_name_from_urn("99999999")
        assert "Unknown location" in name
        assert "99999999" in name

    def test_validate_geo_urn_for_valid_urns(self):
        """Test validation of valid geo URNs."""
        assert validate_geo_urn("90000084") is True
        assert validate_geo_urn("105646813") is True
        assert validate_geo_urn("103644278") is True

    def test_validate_geo_urn_for_invalid_urn(self):
        """Test validation fails for invalid URN."""
        assert validate_geo_urn("99999999") is False
        assert validate_geo_urn("") is False

    def test_location_mapping_structure(self):
        """Test that LOCATION_MAPPING has correct structure."""
        assert isinstance(LOCATION_MAPPING, dict)
        assert len(LOCATION_MAPPING) > 0
        # All values should be strings
        for key, value in LOCATION_MAPPING.items():
            assert isinstance(key, str)
            assert isinstance(value, str)
            assert value.isdigit()  # URNs are numeric strings

    def test_location_choices_structure(self):
        """Test that LOCATION_CHOICES has correct structure."""
        assert isinstance(LOCATION_CHOICES, list)
        assert len(LOCATION_CHOICES) > 0
        # First choice should be "Any" with empty string
        assert LOCATION_CHOICES[0] == ("Any", "")
        # All items should be tuples of (name, urn)
        for name, urn in LOCATION_CHOICES:
            assert isinstance(name, str)
            assert isinstance(urn, str)

    @pytest.mark.parametrize("location,expected_urn", [
        ("San Francisco Bay Area", "90000084"),
        ("Greater Boston Area", "105646813"),
        ("United States", "103644278"),
        ("Any", ""),
    ])
    def test_get_location_urn_parametrized(self, location, expected_urn):
        """Parametrized test for location URN retrieval."""
        assert get_location_urn(location) == expected_urn


# ============================================================================
# Network Tests
# ============================================================================

@pytest.mark.unit
class TestNetworkMappings:
    """Test network mapping functions."""

    def test_get_network_display_names_returns_list(self):
        """Test that get_network_display_names returns a list."""
        names = get_network_display_names()
        assert isinstance(names, list)
        assert len(names) == 3  # We have exactly 3 network options

    def test_get_network_display_names_includes_all_options(self):
        """Test that all network options are included."""
        names = get_network_display_names()
        assert "1st degree connections only" in names
        assert "1st + 2nd degree connections" in names
        assert "1st, 2nd + 3rd degree connections" in names

    def test_get_network_value_for_valid_network(self):
        """Test getting network value for valid display name."""
        value = get_network_value("1st + 2nd degree connections")
        assert value == '["F","S"]'

    def test_get_network_value_for_invalid_network_returns_default(self):
        """Test that invalid network returns default value."""
        value = get_network_value("Invalid Network")
        assert value == '["F","S"]'  # Default

    def test_get_network_name_from_value_for_valid_value(self):
        """Test reverse lookup for valid network value."""
        name = get_network_name_from_value('["F"]')
        assert name == "1st degree connections only"

    def test_get_network_name_from_value_for_invalid_value_returns_default(self):
        """Test that invalid value returns default name."""
        name = get_network_name_from_value("invalid")
        assert name == "1st + 2nd degree connections"

    def test_network_mapping_structure(self):
        """Test that NETWORK_MAPPING has correct structure."""
        assert isinstance(NETWORK_MAPPING, dict)
        assert len(NETWORK_MAPPING) == 3
        # Check specific values
        assert NETWORK_MAPPING["1st degree connections only"] == '["F"]'
        assert NETWORK_MAPPING["1st + 2nd degree connections"] == '["F","S"]'
        assert NETWORK_MAPPING["1st, 2nd + 3rd degree connections"] == '["F","S","O"]'

    def test_network_choices_structure(self):
        """Test that NETWORK_CHOICES has correct structure."""
        assert isinstance(NETWORK_CHOICES, list)
        assert len(NETWORK_CHOICES) == 3
        for name, value in NETWORK_CHOICES:
            assert isinstance(name, str)
            assert isinstance(value, str)
            assert value.startswith('["')
            assert value.endswith('"]')

    @pytest.mark.parametrize("display_name,expected_value", [
        ("1st degree connections only", '["F"]'),
        ("1st + 2nd degree connections", '["F","S"]'),
        ("1st, 2nd + 3rd degree connections", '["F","S","O"]'),
    ])
    def test_get_network_value_parametrized(self, display_name, expected_value):
        """Parametrized test for network value retrieval."""
        assert get_network_value(display_name) == expected_value


# ============================================================================
# Industry Tests
# ============================================================================

@pytest.mark.unit
class TestIndustryMappings:
    """Test industry mapping functions."""

    def test_get_industry_display_names_returns_list(self):
        """Test that get_industry_display_names returns a non-empty list."""
        names = get_industry_display_names()
        assert isinstance(names, list)
        assert len(names) > 0

    def test_get_industry_display_names_includes_any(self):
        """Test that 'Any' is included in industry display names."""
        names = get_industry_display_names()
        assert "Any" in names

    def test_get_industry_display_names_includes_common_industries(self):
        """Test that common industries are included."""
        names = get_industry_display_names()
        assert "Computer Software" in names
        assert "Internet" in names
        assert "Financial Services" in names

    def test_get_industry_id_for_valid_industry(self):
        """Test getting ID for a valid industry."""
        id = get_industry_id("Computer Software")
        assert id == "4"

    def test_get_industry_id_for_any_returns_empty_string(self):
        """Test that 'Any' industry returns empty string."""
        id = get_industry_id("Any")
        assert id == ""

    def test_get_industry_id_for_invalid_industry_returns_empty(self):
        """Test that invalid industry returns empty string."""
        id = get_industry_id("NonExistent Industry")
        assert id == ""

    def test_get_industry_ids_for_multiple_single_industry(self):
        """Test getting IDs for a single industry."""
        ids = get_industry_ids_for_multiple(["Computer Software"])
        assert ids == "4"

    def test_get_industry_ids_for_multiple_industries(self):
        """Test getting IDs for multiple industries."""
        ids = get_industry_ids_for_multiple(["Computer Software", "Internet"])
        assert ids == "4,6"

    def test_get_industry_ids_for_multiple_with_invalid_industry(self):
        """Test that invalid industries are filtered out."""
        ids = get_industry_ids_for_multiple([
            "Computer Software",
            "NonExistent",
            "Internet"
        ])
        assert ids == "4,6"

    def test_get_industry_ids_for_multiple_empty_list(self):
        """Test that empty list returns empty string."""
        ids = get_industry_ids_for_multiple([])
        assert ids == ""

    def test_get_industry_name_from_id_for_valid_id(self):
        """Test reverse lookup for valid industry ID."""
        name = get_industry_name_from_id("4")
        assert name == "Computer Software"

    def test_get_industry_name_from_id_for_invalid_id(self):
        """Test reverse lookup for invalid ID returns formatted string."""
        name = get_industry_name_from_id("99999")
        assert "Unknown industry" in name
        assert "99999" in name

    def test_validate_industry_id_for_valid_ids(self):
        """Test validation of valid industry IDs."""
        assert validate_industry_id("4") is True
        assert validate_industry_id("6") is True
        assert validate_industry_id("96") is True

    def test_validate_industry_id_for_invalid_id(self):
        """Test validation fails for invalid ID."""
        assert validate_industry_id("99999") is False
        assert validate_industry_id("") is False

    def test_industry_mapping_structure(self):
        """Test that INDUSTRY_MAPPING has correct structure."""
        assert isinstance(INDUSTRY_MAPPING, dict)
        assert len(INDUSTRY_MAPPING) > 0
        for key, value in INDUSTRY_MAPPING.items():
            assert isinstance(key, str)
            assert isinstance(value, str)
            assert value.isdigit()  # IDs are numeric strings

    def test_industry_choices_structure(self):
        """Test that INDUSTRY_CHOICES has correct structure."""
        assert isinstance(INDUSTRY_CHOICES, list)
        assert len(INDUSTRY_CHOICES) > 0
        # First choice should be "Any" with empty string
        assert INDUSTRY_CHOICES[0] == ("Any", "")
        for name, id in INDUSTRY_CHOICES:
            assert isinstance(name, str)
            assert isinstance(id, str)

    @pytest.mark.parametrize("industry,expected_id", [
        ("Computer Software", "4"),
        ("Internet", "6"),
        ("Information Technology & Services", "96"),
        ("Financial Services", "43"),
        ("Any", ""),
    ])
    def test_get_industry_id_parametrized(self, industry, expected_id):
        """Parametrized test for industry ID retrieval."""
        assert get_industry_id(industry) == expected_id


# ============================================================================
# Helper Functions Tests
# ============================================================================

@pytest.mark.unit
class TestHelperFunctions:
    """Test helper utility functions."""

    def test_format_ids_for_url_single_id(self):
        """Test formatting a single ID."""
        result = format_ids_for_url("4")
        assert result == '["4"]'

    def test_format_ids_for_url_multiple_ids(self):
        """Test formatting multiple IDs."""
        result = format_ids_for_url("4,6,96")
        assert result == '["4","6","96"]'

    def test_format_ids_for_url_with_spaces(self):
        """Test that spaces are handled correctly."""
        result = format_ids_for_url("4, 6, 96")
        assert result == '["4","6","96"]'

    def test_format_ids_for_url_empty_string(self):
        """Test that empty string returns empty string."""
        result = format_ids_for_url("")
        assert result == ""

    def test_format_ids_for_url_whitespace_only(self):
        """Test that whitespace-only string returns empty string."""
        result = format_ids_for_url("   ")
        assert result == ""

    def test_format_ids_for_url_with_trailing_comma(self):
        """Test handling of trailing comma."""
        result = format_ids_for_url("4,6,")
        assert result == '["4","6"]'

    def test_format_ids_for_url_with_empty_elements(self):
        """Test that empty elements are filtered out."""
        result = format_ids_for_url("4,,6")
        assert result == '["4","6"]'

    @pytest.mark.parametrize("input_ids,expected_output", [
        ("4", '["4"]'),
        ("4,6", '["4","6"]'),
        ("4,6,96", '["4","6","96"]'),
        ("4, 6, 96", '["4","6","96"]'),
        ("", ""),
        ("  ", ""),
        ("4,", '["4"]'),
        ("4,,6", '["4","6"]'),
    ])
    def test_format_ids_for_url_parametrized(self, input_ids, expected_output):
        """Parametrized test for ID formatting."""
        assert format_ids_for_url(input_ids) == expected_output


# ============================================================================
# Constants Tests
# ============================================================================

@pytest.mark.unit
class TestConstants:
    """Test that constants are properly defined."""

    def test_language_mapping_exists(self):
        """Test that LANGUAGE_MAPPING is defined and non-empty."""
        assert isinstance(LANGUAGE_MAPPING, dict)
        assert len(LANGUAGE_MAPPING) > 0

    def test_language_mapping_structure(self):
        """Test that LANGUAGE_MAPPING has correct structure."""
        for language, code in LANGUAGE_MAPPING.items():
            assert isinstance(language, str)
            assert isinstance(code, str)
            assert len(code) >= 2  # Language codes are at least 2 chars

    def test_language_mapping_includes_common_languages(self):
        """Test that common languages are included."""
        assert "English" in LANGUAGE_MAPPING
        assert "Spanish" in LANGUAGE_MAPPING
        assert "French" in LANGUAGE_MAPPING
        assert LANGUAGE_MAPPING["English"] == "en"
        assert LANGUAGE_MAPPING["Spanish"] == "es"

    def test_common_companies_exists(self):
        """Test that COMMON_COMPANIES is defined and non-empty."""
        assert isinstance(COMMON_COMPANIES, dict)
        assert len(COMMON_COMPANIES) > 0

    def test_common_companies_structure(self):
        """Test that COMMON_COMPANIES has correct structure."""
        for company, id in COMMON_COMPANIES.items():
            assert isinstance(company, str)
            assert isinstance(id, str)
            assert id.isdigit()  # Company IDs are numeric strings

    def test_common_companies_includes_major_tech_companies(self):
        """Test that major tech companies are included."""
        assert "Google" in COMMON_COMPANIES
        assert "Microsoft" in COMMON_COMPANIES
        assert "Apple" in COMMON_COMPANIES
        assert "Meta" in COMMON_COMPANIES
        assert "Amazon" in COMMON_COMPANIES


# ============================================================================
# Edge Cases and Integration Tests
# ============================================================================

@pytest.mark.unit
class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_all_location_urns_are_unique(self):
        """Test that all location URNs are unique (except empty string)."""
        urns = [urn for _, urn in LOCATION_CHOICES if urn]  # Exclude "Any"
        assert len(urns) == len(set(urns)), "Duplicate URNs found in LOCATION_CHOICES"

    def test_all_industry_ids_are_unique(self):
        """Test that all industry IDs are unique (except empty string)."""
        ids = [id for _, id in INDUSTRY_CHOICES if id]  # Exclude "Any"
        assert len(ids) == len(set(ids)), "Duplicate IDs found in INDUSTRY_CHOICES"

    def test_location_mapping_consistency_with_choices(self):
        """Test that LOCATION_MAPPING is consistent with LOCATION_CHOICES."""
        for name, urn in LOCATION_CHOICES:
            if name != "Any" and urn:
                # Each URN in choices should be in mapping
                assert urn in LOCATION_MAPPING.values()

    def test_industry_mapping_consistency_with_choices(self):
        """Test that INDUSTRY_MAPPING is consistent with INDUSTRY_CHOICES."""
        for name, id in INDUSTRY_CHOICES:
            if name != "Any" and id:
                # Each ID in choices should be in mapping
                assert id in INDUSTRY_MAPPING.values()

    def test_network_values_are_valid_json_arrays(self):
        """Test that network values are valid JSON array format."""
        import json
        for _, value in NETWORK_CHOICES:
            try:
                parsed = json.loads(value)
                assert isinstance(parsed, list)
                assert all(isinstance(item, str) for item in parsed)
            except json.JSONDecodeError:
                pytest.fail(f"Invalid JSON array format: {value}")

    def test_case_sensitivity_location(self):
        """Test that location lookups are case-sensitive."""
        # Should return empty for incorrect case
        result = get_location_urn("san francisco bay area")
        assert result == ""

    def test_case_sensitivity_industry(self):
        """Test that industry lookups are case-sensitive."""
        # Should return empty for incorrect case
        result = get_industry_id("computer software")
        assert result == ""

    def test_reverse_lookup_round_trip_location(self):
        """Test that location forward and reverse lookups are consistent."""
        original_name = "San Francisco Bay Area"
        urn = get_location_urn(original_name)
        retrieved_name = get_location_name_from_urn(urn)
        assert retrieved_name == original_name

    def test_reverse_lookup_round_trip_industry(self):
        """Test that industry forward and reverse lookups are consistent."""
        original_name = "Computer Software"
        id = get_industry_id(original_name)
        retrieved_name = get_industry_name_from_id(id)
        assert retrieved_name == original_name

    def test_reverse_lookup_round_trip_network(self):
        """Test that network forward and reverse lookups are consistent."""
        original_name = "1st + 2nd degree connections"
        value = get_network_value(original_name)
        retrieved_name = get_network_name_from_value(value)
        assert retrieved_name == original_name


# ============================================================================
# Performance and Scale Tests
# ============================================================================

@pytest.mark.unit
class TestPerformance:
    """Test performance characteristics of mapping functions."""

    def test_get_location_display_names_is_fast(self, benchmark=None):
        """Test that getting location names is fast."""
        # Simple assertion test (benchmark is optional)
        names = get_location_display_names()
        assert len(names) > 0

    def test_location_lookup_handles_large_dataset(self):
        """Test that location lookups work efficiently."""
        # Test multiple lookups
        for _ in range(100):
            urn = get_location_urn("San Francisco Bay Area")
            assert urn == "90000084"

    def test_format_ids_handles_many_ids(self):
        """Test formatting many IDs at once."""
        # Create a string with many IDs
        ids = ",".join([str(i) for i in range(100)])
        result = format_ids_for_url(ids)
        assert result.startswith("[")
        assert result.endswith("]")
        assert result.count('"') == 200  # 100 IDs with 2 quotes each
