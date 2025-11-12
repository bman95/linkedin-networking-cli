"""
LinkedIn Mappings - Constants for LinkedIn search parameters

This module contains mappings between human-readable values and LinkedIn's
internal IDs/URNs for various search parameters.

Last updated: 2025-11-12
"""

from typing import Dict, List, Tuple

# ============================================================================
# LOCATION MAPPINGS (geoUrn)
# ============================================================================

LOCATION_MAPPING: Dict[str, str] = {
    "San Francisco Bay Area": "90000084",
    "Greater Boston Area": "105646813",
    "New York City Metropolitan Area": "102571732",  # To verify
    "Greater Los Angeles Area": "102448103",  # To verify
    "Greater Chicago Area": "103112676",  # To verify
    "Austin, Texas Area": "102748797",  # To verify
    "Greater Seattle Area": "103658393",  # To verify
    "United States": "103644278",
}

# Display names for UI (ordered)
LOCATION_CHOICES: List[Tuple[str, str]] = [
    ("Any", ""),  # Empty string means no filter
    ("San Francisco Bay Area", "90000084"),
    ("New York City Metropolitan Area", "102571732"),
    ("Greater Los Angeles Area", "102448103"),
    ("Greater Chicago Area", "103112676"),
    ("Austin, Texas Area", "102748797"),
    ("Greater Seattle Area", "103658393"),
    ("Greater Boston Area", "105646813"),
    ("United States", "103644278"),
]

def get_location_display_names() -> List[str]:
    """Get list of location display names for UI"""
    return [name for name, _ in LOCATION_CHOICES]

def get_location_urn(display_name: str) -> str:
    """Get geoUrn code for a location display name"""
    for name, urn in LOCATION_CHOICES:
        if name == display_name:
            return urn
    return ""

# ============================================================================
# NETWORK MAPPINGS (connection degree)
# ============================================================================

NETWORK_MAPPING: Dict[str, str] = {
    "1st degree connections only": '["F"]',
    "1st + 2nd degree connections": '["F","S"]',
    "1st, 2nd + 3rd degree connections": '["F","S","O"]',
}

# Display names for UI (ordered)
NETWORK_CHOICES: List[Tuple[str, str]] = [
    ("1st degree connections only", '["F"]'),
    ("1st + 2nd degree connections", '["F","S"]'),
    ("1st, 2nd + 3rd degree connections", '["F","S","O"]'),
]

def get_network_display_names() -> List[str]:
    """Get list of network display names for UI"""
    return [name for name, _ in NETWORK_CHOICES]

def get_network_value(display_name: str) -> str:
    """Get network value for a display name"""
    for name, value in NETWORK_CHOICES:
        if name == display_name:
            return value
    return '["F","S"]'  # Default: 1st + 2nd connections

# ============================================================================
# INDUSTRY MAPPINGS
# ============================================================================

INDUSTRY_MAPPING: Dict[str, str] = {
    "Computer Software": "4",
    "Information Technology & Services": "96",
    "Internet": "6",
    "Financial Services": "43",
    "Banking": "41",
    "Investment Banking": "45",
    "Venture Capital & Private Equity": "106",
    "Management Consulting": "11",
    "Marketing & Advertising": "80",
    "Public Relations & Communications": "18",
    "E-Learning": "100",
    "Higher Education": "69",
    "Hospital & Health Care": "14",
    "Biotechnology": "12",
    "Pharmaceuticals": "15",
    "Medical Devices": "54",
    "Real Estate": "44",
    "Construction": "51",
    "Architecture & Planning": "22",
    "Legal Services": "10",
    "Accounting": "47",
    "Human Resources": "137",
    "Staffing & Recruiting": "104",
    "Design": "27",
    "Entertainment": "28",
    "Media Production": "126",
    "Telecommunications": "8",
    "Automotive": "53",
    "Aviation & Aerospace": "94",
    "Consumer Goods": "25",
    "Retail": "27",
    "E-commerce": "6",
}

# Display names for UI (ordered by popularity/relevance)
INDUSTRY_CHOICES: List[Tuple[str, str]] = [
    ("Any", ""),
    ("Computer Software", "4"),
    ("Information Technology & Services", "96"),
    ("Internet", "6"),
    ("Financial Services", "43"),
    ("Management Consulting", "11"),
    ("Marketing & Advertising", "80"),
    ("Banking", "41"),
    ("Investment Banking", "45"),
    ("Venture Capital & Private Equity", "106"),
    ("E-Learning", "100"),
    ("Higher Education", "69"),
    ("Hospital & Health Care", "14"),
    ("Biotechnology", "12"),
    ("Pharmaceuticals", "15"),
    ("Medical Devices", "54"),
    ("Real Estate", "44"),
    ("Legal Services", "10"),
    ("Accounting", "47"),
    ("Human Resources", "137"),
    ("Staffing & Recruiting", "104"),
    ("Design", "27"),
    ("Entertainment", "28"),
    ("Telecommunications", "8"),
    ("Automotive", "53"),
    ("Aviation & Aerospace", "94"),
    ("Consumer Goods", "25"),
    ("Retail", "27"),
]

def get_industry_display_names() -> List[str]:
    """Get list of industry display names for UI"""
    return [name for name, _ in INDUSTRY_CHOICES]

def get_industry_id(display_name: str) -> str:
    """Get industry ID for a display name"""
    for name, id in INDUSTRY_CHOICES:
        if name == display_name:
            return id
    return ""

def get_industry_ids_for_multiple(display_names: List[str]) -> str:
    """
    Get comma-separated industry IDs for multiple display names

    Args:
        display_names: List of industry display names

    Returns:
        Comma-separated IDs (e.g., "4,6,96")
    """
    ids = []
    for display_name in display_names:
        id = get_industry_id(display_name)
        if id:
            ids.append(id)
    return ",".join(ids)

# ============================================================================
# LANGUAGE MAPPINGS (for future use)
# ============================================================================

LANGUAGE_MAPPING: Dict[str, str] = {
    "English": "en",
    "Spanish": "es",
    "French": "fr",
    "German": "de",
    "Portuguese": "pt",
    "Italian": "it",
    "Dutch": "nl",
    "Chinese (Simplified)": "zh-cn",
    "Chinese (Traditional)": "zh-tw",
    "Japanese": "ja",
    "Korean": "ko",
    "Russian": "ru",
    "Arabic": "ar",
    "Hindi": "hi",
}

# ============================================================================
# COMMON COMPANIES (for future use)
# ============================================================================

COMMON_COMPANIES: Dict[str, str] = {
    "Google": "1441",
    "Meta": "1586",
    "Apple": "162479",
    "Microsoft": "1035",
    "Amazon": "1586",
    "Netflix": "165158",
    "Tesla": "15564",
    "Uber": "1815218",
    "Airbnb": "2382910",
    "Salesforce": "3185",
    "Oracle": "1028",
    "IBM": "1009",
    "Intel": "1053",
    "Nvidia": "3608",
    "Adobe": "1283",
    "Slack": "2960143",
    "Stripe": "2135371",
    "Coinbase": "4871526",
    "OpenAI": "27039078",
    "Anthropic": "30121960",
}

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def format_ids_for_url(ids: str) -> str:
    """
    Format comma-separated IDs into LinkedIn URL format

    Args:
        ids: Comma-separated IDs (e.g., "4,6,96")

    Returns:
        Formatted string (e.g., '["4","6","96"]')
    """
    if not ids or not ids.strip():
        return ""

    id_list = [id.strip() for id in ids.split(",") if id.strip()]
    formatted = ','.join([f'"{id}"' for id in id_list])
    return f'[{formatted}]'

def validate_geo_urn(geo_urn: str) -> bool:
    """Validate if a geoUrn code exists in our mappings"""
    return geo_urn in [urn for _, urn in LOCATION_CHOICES if urn]

def validate_industry_id(industry_id: str) -> bool:
    """Validate if an industry ID exists in our mappings"""
    return industry_id in [id for _, id in INDUSTRY_CHOICES if id]

# ============================================================================
# REVERSE LOOKUPS (for displaying saved campaigns)
# ============================================================================

def get_location_name_from_urn(urn: str) -> str:
    """Get human-readable location name from geoUrn"""
    for name, code in LOCATION_CHOICES:
        if code == urn:
            return name
    return f"Unknown location ({urn})"

def get_industry_name_from_id(id: str) -> str:
    """Get human-readable industry name from ID"""
    for name, code in INDUSTRY_CHOICES:
        if code == id:
            return name
    return f"Unknown industry ({id})"

def get_network_name_from_value(value: str) -> str:
    """Get human-readable network name from value"""
    for name, val in NETWORK_CHOICES:
        if val == value:
            return name
    return "1st + 2nd degree connections"  # Default
