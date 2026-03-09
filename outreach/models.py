"""Pydantic data models for school outreach contacts."""

from pydantic import BaseModel, Field


class SchoolContact(BaseModel):
    """A single faculty contact found at a school."""

    school_name: str = Field(description="Full name of the school")
    school_link: str = Field(default="", description="URL to the school's website")
    faculty_name: str = Field(description="Full name of the faculty member")
    email: str = Field(default="", description="Faculty email address")
    dear_line: str = Field(
        default="",
        description="Salutation line, e.g. 'Dear Mr. Smith'",
    )
    comments: str = Field(
        default="",
        description="Job title or other notes about this contact",
    )


class SchoolSearchResult(BaseModel):
    """Container for multiple contacts returned by the agent."""

    contacts: list[SchoolContact] = Field(default_factory=list)
