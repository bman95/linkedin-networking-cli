"""
Tests for the async profile-scraping helpers in src/automation/scraping.py.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from automation.scraping import (
    collect_public_information,
    get_contact_info,
    get_location,
    get_open_to_work_status,
    get_profession,
)


def _element(visible=True, inner_text="", href=None):
    el = AsyncMock()
    el.is_visible = AsyncMock(return_value=visible)
    el.inner_text = AsyncMock(return_value=inner_text)
    el.get_attribute = AsyncMock(return_value=href)
    el.click = AsyncMock()
    el.scroll_into_view_if_needed = AsyncMock()
    el.query_selector = AsyncMock(return_value=None)
    return el


def _page(query_result=None, content=""):
    page = AsyncMock()
    page.query_selector = AsyncMock(return_value=query_result)
    page.query_selector_all = AsyncMock(return_value=[])
    page.content = AsyncMock(return_value=content)
    page.wait_for_timeout = AsyncMock()
    return page


@pytest.mark.unit
class TestProfession:
    @pytest.mark.asyncio
    async def test_returns_headline(self):
        page = _page(query_result=_element(inner_text="Senior Software Engineer"))
        assert await get_profession(page) == "Senior Software Engineer"

    @pytest.mark.asyncio
    async def test_returns_none_when_absent(self):
        page = _page(query_result=None)
        assert await get_profession(page) is None


@pytest.mark.unit
class TestLocation:
    @pytest.mark.asyncio
    async def test_returns_location(self):
        page = _page(query_result=_element(inner_text="San Francisco, CA"))
        assert await get_location(page) == "San Francisco, CA"


@pytest.mark.unit
class TestOpenToWork:
    @pytest.mark.asyncio
    async def test_badge_visible(self):
        page = _page(query_result=_element(visible=True))
        assert await get_open_to_work_status(page) is True

    @pytest.mark.asyncio
    async def test_text_in_page_content_alone_is_not_enough(self):
        # A bare substring match in page.content() (hidden SDUI templates,
        # i18n bundles, "People also viewed") must NOT count as open-to-work;
        # only a scoped, visible badge/photo-frame element does.
        page = _page(query_result=None, content="this person is Open To Work")
        assert await get_open_to_work_status(page) is False

    @pytest.mark.asyncio
    async def test_not_open(self):
        page = _page(query_result=None, content="nothing here")
        assert await get_open_to_work_status(page) is False


@pytest.mark.unit
class TestContactInfo:
    @pytest.mark.asyncio
    async def test_returns_dict_structure(self):
        page = _page(query_result=None)
        info = await get_contact_info(page)
        assert set(info.keys()) == {"email", "phone", "address", "connection_accepted_date"}


@pytest.mark.unit
class TestCollectPublicInformation:
    @pytest.mark.asyncio
    async def test_returns_four_tuple(self):
        page = _page(query_result=None)
        profession, location, experience, education = await collect_public_information(page)
        assert experience == []
        assert education == []
