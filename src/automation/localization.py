"""Message localization and important-contact detection.

The CLI sends connection notes in the campaign's native language to contacts
located in a country that speaks it, and in English to everyone else. Detection
relies on the contact's free-text LinkedIn location (e.g. "Madrid, Spain",
"Mexico City Metropolitan Area", "San Francisco Bay Area").

Personalized notes are scarce (LinkedIn gates them behind Premium / a small free
quota), so they are only spent on "important" contacts: those whose headline or
company matches the campaign's priority keywords, or a built-in seniority list.
"""

from typing import List, Optional, Tuple
import unicodedata


# Native languages the operator can pick for a campaign, besides the always
# available English fallback. Keyed by ISO 639-1 code.
SUPPORTED_NATIVE_LANGUAGES = {
    "es": "Español (Spanish)",
    "fr": "Français (French)",
    "pt": "Português (Portuguese)",
    "de": "Deutsch (German)",
    "it": "Italiano (Italian)",
}

# Default greeting per language, used to prefill the template prompts.
DEFAULT_GREETINGS = {
    "es": "Hola {name}, me gustaría conectar contigo.",
    "fr": "Bonjour {name}, je souhaiterais me connecter avec vous.",
    "pt": "Olá {name}, gostaria de me conectar com você.",
    "de": "Hallo {name}, ich würde mich gerne mit Ihnen vernetzen.",
    "it": "Ciao {name}, mi piacerebbe entrare in contatto con te.",
    "en": "Hi {name}, I'd like to connect with you!",
}

# Lowercase, accent-free markers (countries, regions and major cities) that
# indicate a contact is located in a country speaking the given language.
LANGUAGE_LOCATION_MARKERS = {
    "es": {
        "spain", "espana", "mexico", "argentina", "colombia", "peru", "chile",
        "ecuador", "guatemala", "cuba", "bolivia", "dominican", "republica dominicana",
        "honduras", "paraguay", "el salvador", "nicaragua", "costa rica", "panama",
        "uruguay", "venezuela", "puerto rico",
        "madrid", "barcelona", "valencia", "sevilla", "bilbao", "malaga",
        "mexico city", "monterrey", "guadalajara", "buenos aires", "bogota",
        "lima", "santiago",
    },
    "fr": {
        "france", "francia", "belgique", "luxembourg", "monaco", "senegal",
        "cote d'ivoire", "cote d ivoire", "ivory coast", "cameroun", "cameroon",
        "paris", "lyon", "marseille", "toulouse", "bordeaux", "nice", "lille",
        "nantes", "strasbourg", "quebec", "montreal",
    },
    "pt": {
        "portugal", "brazil", "brasil", "angola", "mozambique", "mocambique",
        "lisbon", "lisboa", "porto", "sao paulo", "rio de janeiro", "brasilia",
        "belo horizonte", "porto alegre", "curitiba", "luanda",
    },
    "de": {
        "germany", "deutschland", "austria", "osterreich", "switzerland",
        "berlin", "munich", "munchen", "hamburg", "frankfurt", "cologne", "koln",
        "stuttgart", "dusseldorf", "vienna", "wien", "zurich", "zuerich",
    },
    "it": {
        "italy", "italia", "rome", "roma", "milan", "milano", "naples", "napoli",
        "turin", "torino", "florence", "firenze", "bologna", "venice", "venezia",
        "genoa", "genova",
    },
}

# Built-in seniority markers (lowercase, accent-free). A contact whose headline
# or company contains any of these is treated as important regardless of the
# campaign's own priority keywords.
SENIORITY_KEYWORDS = {
    "ceo", "chief executive", "cto", "cfo", "coo", "cmo", "ciso", "cpo",
    "c-level", "founder", "co-founder", "cofounder", "owner", "president",
    "vice president", "vicepresident", "vicepresidente", "vp",
    "director", "head of", "partner", "managing director", "managing partner",
    "general manager", "gerente general", "propietario", "dueno", "presidente",
    "socio", "fundador", "fundadora", "directora", "directiva", "directivo",
}


def _normalize(text: Optional[str]) -> str:
    """Lowercase and strip accents so markers match regardless of diacritics."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return without_accents.lower().strip()


def detect_contact_language(location: Optional[str], native_language: str) -> str:
    """Return the language code to use for a contact.

    Returns ``native_language`` when the contact's location matches a country
    that speaks it, or when the location is unknown (so the operator's native
    language is the default). Otherwise returns ``"en"``.
    """
    native_language = (native_language or "es").lower()
    normalized = _normalize(location)

    # Unknown location: fall back to the native language.
    if not normalized:
        return native_language

    markers = LANGUAGE_LOCATION_MARKERS.get(native_language, set())
    if any(marker in normalized for marker in markers):
        return native_language

    return "en"


def select_message_template(campaign, location: Optional[str]) -> Optional[str]:
    """Pick the note template for a contact based on their detected language.

    Falls back to the other template when the preferred one is empty, so a
    campaign that only filled in one language still works.
    """
    native_language = (getattr(campaign, "native_language", None) or "es").lower()
    native_template = getattr(campaign, "message_template", None)
    english_template = getattr(campaign, "message_template_en", None)

    language = detect_contact_language(location, native_language)
    if language == "en":
        return english_template or native_template
    return native_template or english_template


def is_important_contact(
    campaign,
    headline: Optional[str] = None,
    company: Optional[str] = None,
) -> bool:
    """Decide whether a contact deserves a personalized note.

    True when the contact's headline or company matches one of the campaign's
    priority keywords or the built-in seniority list.
    """
    haystack = " ".join(filter(None, [_normalize(headline), _normalize(company)]))
    if not haystack:
        return False

    keywords_raw = getattr(campaign, "priority_keywords", None) or ""
    priority_keywords = [
        _normalize(kw) for kw in keywords_raw.split(",") if kw.strip()
    ]

    if any(kw and kw in haystack for kw in priority_keywords):
        return True
    return any(marker in haystack for marker in SENIORITY_KEYWORDS)


def get_native_language_choices() -> List[Tuple[str, str]]:
    """Return (display_name, code) pairs for the campaign language selector."""
    return [(display, code) for code, display in SUPPORTED_NATIVE_LANGUAGES.items()]


def get_native_language_display(code: str) -> str:
    """Human-readable name for a native-language code (falls back to Spanish)."""
    return SUPPORTED_NATIVE_LANGUAGES.get((code or "es").lower(), SUPPORTED_NATIVE_LANGUAGES["es"])


def default_greeting(code: str) -> str:
    """Default note template for a language code."""
    return DEFAULT_GREETINGS.get((code or "es").lower(), DEFAULT_GREETINGS["es"])
