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
# Note: Codes marked with ✅ are verified, ❓ need verification
# Use dynamic search for locations not in this list

LOCATION_MAPPING: Dict[str, str] = {
    # === VERIFIED LOCATIONS ===
    "San Francisco Bay Area": "90000084",  # ✅ Verified
    "Greater Boston Area": "105646813",  # ✅ Verified
    "United States": "103644278",  # ✅ Verified

    # === MAJOR US CITIES (To verify) ===
    "New York City Metropolitan Area": "102571732",  # ❓
    "Greater Los Angeles Area": "102448103",  # ❓
    "Greater Chicago Area": "103112676",  # ❓
    "Greater Seattle Area": "103658393",  # ❓
    "Austin, Texas Area": "102748797",  # ❓
    "Greater Denver Area": "104545681",  # ❓
    "Greater Miami Area": "104170880",  # ❓
    "Greater Atlanta Area": "102571732",  # ❓
    "Greater Dallas-Fort Worth Area": "102530516",  # ❓
    "Greater Houston Area": "104335990",  # ❓
    "Greater Phoenix Area": "105646813",  # ❓
    "Greater Philadelphia Area": "102409204",  # ❓
    "Greater San Diego Area": "104619630",  # ❓
    "Greater Minneapolis-St. Paul Area": "105656822",  # ❓
    "Greater Washington DC-Baltimore Area": "103819254",  # ❓
    "Portland, Oregon Metropolitan Area": "104684891",  # ❓
    "Greater Nashville Area": "104245995",  # ❓
    "Greater Raleigh-Durham Area": "103291313",  # ❓
    "Greater Salt Lake City Area": "104381804",  # ❓
    "Greater Las Vegas Area": "103782860",  # ❓
    "Greater Orlando Area": "104633076",  # ❓
    "Greater Tampa Bay Area": "104849051",  # ❓
    "Greater Charlotte Area": "104675105",  # ❓
    "Greater San Antonio Area": "104713634",  # ❓
    "Greater Pittsburgh Area": "105644635",  # ❓
    "Greater Columbus, Ohio Area": "104658868",  # ❓
    "Greater Indianapolis Area": "103191971",  # ❓
    "Greater Cleveland Area": "104671603",  # ❓
    "Greater Detroit Area": "104571732",  # ❓
    "Greater Cincinnati Area": "104596308",  # ❓
    "Greater Milwaukee Area": "104066036",  # ❓

    # === CANADA ===
    "Greater Toronto Area": "101174742",  # ❓
    "Greater Vancouver Area": "104684410",  # ❓
    "Greater Montreal Area": "104363214",  # ❓
    "Calgary, Canada Area": "100406114",  # ❓
    "Ottawa, Canada Area": "104819692",  # ❓

    # === MEXICO & LATIN AMERICA ===
    "Mexico City Metropolitan Area": "104215913",  # ❓
    "Monterrey, Mexico Area": "105112090",  # ❓
    "Guadalajara Metropolitan Area": "101603050",  # ❓
    "São Paulo, Brazil": "104246759",  # ❓
    "Buenos Aires, Argentina": "104099388",  # ❓
    "Bogotá, Colombia": "104514075",  # ❓
    "Lima, Peru": "102736126",  # ❓
    "Santiago, Chile": "104365893",  # ❓

    # === EUROPE ===
    "London, United Kingdom": "102257491",  # ❓
    "Greater Paris Metropolitan Region": "105015875",  # ❓
    "Berlin, Germany": "106967730",  # ❓
    "Munich, Germany": "107478210",  # ❓
    "Amsterdam, Netherlands": "100994331",  # ❓
    "Brussels, Belgium": "100472588",  # ❓
    "Madrid, Spain": "104506182",  # ❓
    "Barcelona, Spain": "100565514",  # ❓
    "Zürich, Switzerland": "106693272",  # ❓
    "Milan, Italy": "103350119",  # ❓
    "Rome, Italy": "103350364",  # ❓
    "Stockholm, Sweden": "105117694",  # ❓
    "Copenhagen, Denmark": "106808692",  # ❓
    "Dublin, Ireland": "104738515",  # ❓
    "Vienna, Austria": "106693272",  # ❓
    "Warsaw, Poland": "105072130",  # ❓
    "Prague, Czech Republic": "104508036",  # ❓
    "Lisbon, Portugal": "105117694",  # ❓

    # === ASIA-PACIFIC ===
    "Singapore": "102454443",  # ❓
    "Hong Kong": "102095887",  # ❓
    "Tokyo, Japan": "101490751",  # ❓
    "Seoul, South Korea": "106251556",  # ❓
    "Beijing, China": "102890883",  # ❓
    "Shanghai, China": "102890883",  # ❓
    "Bangalore, India": "105214831",  # ❓
    "Mumbai, India": "102713980",  # ❓
    "New Delhi, India": "106057199",  # ❓
    "Sydney, Australia": "104769905",  # ❓
    "Melbourne, Australia": "104508801",  # ❓
    "Auckland, New Zealand": "105490917",  # ❓
    "Bangkok, Thailand": "106500832",  # ❓
    "Jakarta, Indonesia": "102478259",  # ❓
    "Manila, Philippines": "103121230",  # ❓
    "Ho Chi Minh City, Vietnam": "102963472",  # ❓

    # === MIDDLE EAST & AFRICA ===
    "Dubai, United Arab Emirates": "104305776",  # ❓
    "Tel Aviv, Israel": "101620260",  # ❓
    "Cairo, Egypt": "106155005",  # ❓
    "Johannesburg, South Africa": "102590854",  # ❓
    "Cape Town, South Africa": "102590854",  # ❓
    "Nairobi, Kenya": "106808692",  # ❓
}

# Display names for UI (ordered by region and popularity)
LOCATION_CHOICES: List[Tuple[str, str]] = [
    ("Any", ""),  # Empty string means no filter

    # === TOP US TECH HUBS ===
    ("San Francisco Bay Area", "90000084"),
    ("New York City Metropolitan Area", "102571732"),
    ("Greater Seattle Area", "103658393"),
    ("Greater Boston Area", "105646813"),
    ("Austin, Texas Area", "102748797"),

    # === OTHER MAJOR US CITIES ===
    ("Greater Los Angeles Area", "102448103"),
    ("Greater Chicago Area", "103112676"),
    ("Greater Denver Area", "104545681"),
    ("Greater Miami Area", "104170880"),
    ("Greater Atlanta Area", "102571732"),
    ("Greater Dallas-Fort Worth Area", "102530516"),
    ("Greater Houston Area", "104335990"),
    ("Greater Washington DC-Baltimore Area", "103819254"),
    ("Greater San Diego Area", "104619630"),
    ("Greater Phoenix Area", "105646813"),
    ("Greater Philadelphia Area", "102409204"),
    ("Portland, Oregon Metropolitan Area", "104684891"),
    ("Greater Nashville Area", "104245995"),
    ("Greater Minneapolis-St. Paul Area", "105656822"),

    # === CANADA ===
    ("Greater Toronto Area", "101174742"),
    ("Greater Vancouver Area", "104684410"),
    ("Greater Montreal Area", "104363214"),

    # === EUROPE - MAJOR HUBS ===
    ("London, United Kingdom", "102257491"),
    ("Berlin, Germany", "106967730"),
    ("Greater Paris Metropolitan Region", "105015875"),
    ("Amsterdam, Netherlands", "100994331"),
    ("Dublin, Ireland", "104738515"),
    ("Stockholm, Sweden", "105117694"),
    ("Zürich, Switzerland", "106693272"),

    # === ASIA-PACIFIC ===
    ("Singapore", "102454443"),
    ("Hong Kong", "102095887"),
    ("Tokyo, Japan", "101490751"),
    ("Bangalore, India", "105214831"),
    ("Sydney, Australia", "104769905"),
    ("Seoul, South Korea", "106251556"),

    # === LATIN AMERICA ===
    ("Mexico City Metropolitan Area", "104215913"),
    ("São Paulo, Brazil", "104246759"),
    ("Buenos Aires, Argentina", "104099388"),

    # === COUNTRIES ===
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
