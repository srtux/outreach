import pytest
from src.models import SchoolContact, SchoolSearchResult

def test_school_contact_creation():
    contact = SchoolContact(
        school_name="Test Elementary",
        faculty_name="John Doe",
        email="john@example.com"
    )
    assert contact.school_name == "Test Elementary"
    assert contact.faculty_name == "John Doe"
    assert contact.email == "john@example.com"
    assert contact.school_link == ""

def test_school_contact_missing_required_fields():
    with pytest.raises(ValueError):
        # missing school_name and faculty_name
        SchoolContact(email="john@example.com")

def test_school_search_result_empty():
    result = SchoolSearchResult()
    assert result.contacts == []

def test_school_search_result_with_contacts():
    contact = SchoolContact(
        school_name="Test Elementary",
        faculty_name="John Doe"
    )
    result = SchoolSearchResult(contacts=[contact])
    assert len(result.contacts) == 1
    assert result.contacts[0].school_name == "Test Elementary"
