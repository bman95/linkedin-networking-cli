# LinkedIn Networking CLI - Test Suite

Comprehensive test suite for the LinkedIn Networking CLI application using pytest.

## 📋 Table of Contents

- [Overview](#overview)
- [Installation](#installation)
- [Running Tests](#running-tests)
- [Test Structure](#test-structure)
- [Test Categories](#test-categories)
- [Coverage Reports](#coverage-reports)
- [Writing New Tests](#writing-new-tests)
- [Best Practices](#best-practices)
- [Troubleshooting](#troubleshooting)

## 🎯 Overview

This test suite provides comprehensive coverage of the LinkedIn Networking CLI application, including:

- **Unit Tests**: Test individual functions and classes in isolation
- **Integration Tests**: Test interactions between components
- **Mocked Tests**: Test browser automation without actual browser interactions

### Test Modules

- `test_linkedin_mappings.py` - Tests for LinkedIn ID/URN mappings and helper functions
- `test_models.py` - Tests for database models (Campaign, Contact, Analytics, Settings)
- `test_database_operations.py` - Tests for DatabaseManager CRUD operations
- `test_settings.py` - Tests for application settings and configuration
- `test_linkedin_automation.py` - Tests for LinkedIn automation with mocked Playwright
- `conftest.py` - Shared fixtures and test configuration

## 🚀 Installation

### Install Test Dependencies

```bash
# Install development dependencies (includes pytest and plugins)
uv sync --extra dev

# Or install specific test dependencies
uv add --dev pytest pytest-asyncio pytest-cov pytest-mock pytest-xdist freezegun
```

### Verify Installation

```bash
pytest --version
```

## 🧪 Running Tests

### Run All Tests

```bash
# Run all tests with coverage
pytest

# Run all tests with verbose output
pytest -v

# Run all tests with detailed output
pytest -vv
```

### Run Specific Test Files

```bash
# Run tests for a specific module
pytest tests/test_linkedin_mappings.py

# Run tests for database operations
pytest tests/test_database_operations.py

# Run tests for models
pytest tests/test_models.py
```

### Run Specific Test Classes or Functions

```bash
# Run a specific test class
pytest tests/test_models.py::TestCampaignModel

# Run a specific test function
pytest tests/test_models.py::TestCampaignModel::test_create_campaign_with_minimal_fields

# Run tests matching a pattern
pytest -k "campaign" -v
pytest -k "test_create" -v
```

### Run Tests by Markers

```bash
# Run only unit tests
pytest -m unit

# Run only integration tests
pytest -m integration

# Run only slow tests
pytest -m slow

# Run all except slow tests
pytest -m "not slow"
```

### Parallel Test Execution

```bash
# Run tests in parallel (faster)
pytest -n auto

# Run tests using 4 workers
pytest -n 4
```

## 📁 Test Structure

```
tests/
├── __init__.py                      # Package initialization
├── conftest.py                      # Shared fixtures and configuration
├── test_linkedin_mappings.py        # ~200 LOC, 50+ tests
├── test_models.py                   # ~400 LOC, 40+ tests
├── test_database_operations.py      # ~500 LOC, 50+ tests
├── test_settings.py                 # ~300 LOC, 40+ tests
├── test_linkedin_automation.py      # ~300 LOC, 30+ tests
└── README.md                        # This file
```

### Conftest.py Fixtures

The `conftest.py` file provides shared fixtures available to all tests:

#### Database Fixtures
- `temp_db_path` - Temporary database path for testing
- `in_memory_engine` - In-memory SQLite engine
- `db_session` - Database session
- `db_manager` - DatabaseManager instance

#### Model Fixtures
- `sample_campaign` - Pre-configured Campaign instance
- `sample_contact` - Pre-configured Contact instance
- `sample_analytics` - Pre-configured Analytics instance
- `sample_settings` - Pre-configured Settings instance

#### Settings Fixtures
- `mock_env_vars` - Mocked environment variables
- `app_settings` - AppSettings instance

#### Playwright/Browser Mocks
- `mock_page` - Mocked Playwright page
- `mock_browser` - Mocked Playwright browser
- `mock_context` - Mocked browser context
- `mock_playwright` - Mocked Playwright instance
- `mock_element` - Mocked page element

#### LinkedIn Automation Mocks
- `mock_linkedin_automation` - LinkedInAutomation with mocked page
- `mock_profile_data` - Sample profile data

#### Utility Fixtures
- `freeze_time` - Freeze time for datetime testing
- `caplog_debug` - Capture debug-level logs

## 🏷️ Test Categories

Tests are organized by markers defined in `pyproject.toml`:

### Unit Tests (`@pytest.mark.unit`)
Tests that don't require external dependencies:
- LinkedIn mappings
- Database models
- Settings configuration
- Helper functions

### Integration Tests (`@pytest.mark.integration`)
Tests that may require multiple components:
- Database operations with models
- Campaign statistics
- Full automation flows (mocked)

### Slow Tests (`@pytest.mark.slow`)
Tests that take significant time to run:
- Large dataset operations
- Multiple browser operations

## 📊 Coverage Reports

### Generate Coverage Report

```bash
# Run tests with coverage
pytest --cov=src --cov-report=term-missing

# Generate HTML coverage report
pytest --cov=src --cov-report=html

# View HTML report
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux
start htmlcov/index.html  # Windows
```

### Generate XML Coverage Report (for CI/CD)

```bash
pytest --cov=src --cov-report=xml
```

### Coverage Configuration

Coverage settings are configured in `pyproject.toml`:
- Source: `src/` directory
- Omits: Tests, `__init__.py`, migrations
- Excludes: Pragma no cover, abstract methods, TYPE_CHECKING blocks

## ✍️ Writing New Tests

### Test File Template

```python
"""
Unit tests for [module name].

Description of what this module tests.
"""

import pytest
from module import YourClass


@pytest.mark.unit
class TestYourClass:
    """Test YourClass functionality."""

    def test_something(self):
        """Test description."""
        # Arrange
        obj = YourClass()

        # Act
        result = obj.method()

        # Assert
        assert result == expected_value
```

### Best Practices for Test Writing

#### 1. Follow AAA Pattern (Arrange-Act-Assert)

```python
def test_create_campaign(self, db_manager):
    # Arrange
    campaign_data = {"name": "Test Campaign"}

    # Act
    campaign = db_manager.create_campaign(campaign_data)

    # Assert
    assert campaign.id is not None
    assert campaign.name == "Test Campaign"
```

#### 2. Use Descriptive Test Names

```python
# Good
def test_campaign_with_special_characters_in_name(self):
    pass

# Bad
def test_campaign(self):
    pass
```

#### 3. Test One Thing Per Test

```python
# Good
def test_create_campaign(self):
    campaign = create_campaign()
    assert campaign.id is not None

def test_campaign_has_default_values(self):
    campaign = create_campaign()
    assert campaign.active is True

# Bad
def test_campaign(self):
    campaign = create_campaign()
    assert campaign.id is not None
    assert campaign.active is True
    assert campaign.total_sent == 0
```

#### 4. Use Fixtures for Common Setup

```python
@pytest.fixture
def configured_campaign():
    return Campaign(
        name="Test",
        keywords="engineer",
        geo_urn="90000084"
    )

def test_campaign_search_params(self, configured_campaign):
    # Use the fixture
    params = build_search_params(configured_campaign)
    assert "keywords=engineer" in params
```

#### 5. Use Parametrized Tests for Multiple Cases

```python
@pytest.mark.parametrize("status,expected_count", [
    ("sent", 5),
    ("accepted", 2),
    ("pending", 3),
])
def test_get_contacts_by_status(self, status, expected_count):
    contacts = get_contacts_by_status(status)
    assert len(contacts) == expected_count
```

#### 6. Mock External Dependencies

```python
@pytest.mark.asyncio
async def test_search_profiles(self, mock_page):
    """Test profile search with mocked Playwright."""
    mock_page.goto = AsyncMock()
    mock_page.wait_for_selector = AsyncMock()

    automation = LinkedInAutomation(db_manager, settings)
    automation.page = mock_page

    profiles = await automation.search_profiles(campaign)

    assert mock_page.goto.called
```

## 🛠️ Best Practices

### 1. Keep Tests Independent
- Each test should be able to run independently
- Don't rely on test execution order
- Use fixtures to set up test data

### 2. Use Descriptive Names
- Test functions should clearly describe what they test
- Use full sentences: `test_campaign_creation_sets_default_values`

### 3. Test Edge Cases
- Empty inputs
- None values
- Invalid data
- Boundary conditions

### 4. Keep Tests Fast
- Use in-memory databases for database tests
- Mock external services (browser, APIs)
- Use `pytest-xdist` for parallel execution

### 5. Maintain Test Readability
- Keep tests short and focused
- Use clear variable names
- Add comments for complex test logic

### 6. Update Tests When Code Changes
- Tests are part of the codebase
- Update tests when requirements change
- Refactor tests alongside code

## 🐛 Troubleshooting

### Common Issues

#### Issue: Import Errors

```bash
ModuleNotFoundError: No module named 'automation'
```

**Solution**: Ensure you're running tests from the project root:
```bash
cd /path/to/linkedin-networking-cli
pytest
```

#### Issue: Database Locked

```bash
sqlite3.OperationalError: database is locked
```

**Solution**: Use in-memory databases or ensure tests clean up properly:
```python
@pytest.fixture
def db_session(in_memory_engine):
    with Session(in_memory_engine) as session:
        yield session
    # Session automatically closes
```

#### Issue: Async Tests Not Running

```bash
RuntimeWarning: coroutine 'test_...' was never awaited
```

**Solution**: Mark async tests with `@pytest.mark.asyncio`:
```python
@pytest.mark.asyncio
async def test_async_function():
    result = await some_async_function()
    assert result is not None
```

#### Issue: Tests Pass Locally But Fail in CI

**Common Causes**:
- Environment variables not set
- Time zone differences
- File path differences (Windows vs Linux)

**Solution**: Use fixtures to mock environment and normalize paths:
```python
@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    monkeypatch.setenv("LINKEDIN_EMAIL", "test@example.com")
```

### Running Tests with Debug Output

```bash
# Show print statements
pytest -s

# Show detailed test output
pytest -vv

# Show local variables on failure
pytest -l

# Drop into debugger on failure
pytest --pdb
```

### Checking Test Collection

```bash
# List all tests without running them
pytest --collect-only

# Show which tests match a pattern
pytest -k "campaign" --collect-only
```

## 📚 Additional Resources

### Pytest Documentation
- [Pytest Official Docs](https://docs.pytest.org/)
- [Pytest Fixtures](https://docs.pytest.org/en/stable/fixture.html)
- [Pytest Parametrize](https://docs.pytest.org/en/stable/parametrize.html)

### Testing Best Practices
- [Test-Driven Development (TDD)](https://en.wikipedia.org/wiki/Test-driven_development)
- [Arrange-Act-Assert Pattern](http://wiki.c2.com/?ArrangeActAssert)
- [FIRST Principles](https://github.com/ghsukumar/SFDC_Best_Practices/wiki/F.I.R.S.T-Principles-of-Unit-Testing)

### Project-Specific
- See `CLAUDE.md` for project architecture
- See `TODOS.md` for planned features and pending verifications

## 🤝 Contributing Tests

When contributing new tests:

1. **Follow the existing structure**: Place tests in appropriate modules
2. **Use existing fixtures**: Check `conftest.py` before creating new fixtures
3. **Add markers**: Mark tests as `unit`, `integration`, or `slow`
4. **Document complex tests**: Add docstrings explaining what's being tested
5. **Ensure tests pass**: Run the full test suite before submitting
6. **Maintain coverage**: Aim to maintain or improve test coverage

## 📝 Test Checklist

Before committing new code, ensure:

- [ ] All tests pass: `pytest`
- [ ] Coverage is maintained: `pytest --cov=src`
- [ ] No warnings: `pytest -W error`
- [ ] Tests are properly marked: `@pytest.mark.unit` or `@pytest.mark.integration`
- [ ] New features have corresponding tests
- [ ] Edge cases are covered
- [ ] Documentation is updated

---

**Total Tests**: 200+ tests covering all major functionality
**Test Coverage**: Targeting 80%+ code coverage
**Test Execution Time**: ~5-10 seconds (with pytest-xdist)
