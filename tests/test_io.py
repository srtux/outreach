import pytest
from pathlib import Path

from outreach.io import contact_to_row, read_regions, CsvRepository
from outreach.models import SchoolContact

def test_contact_to_row():
    contact = SchoolContact(school_name="S1", faculty_name="F1", email="e1@example.com")
    row = contact_to_row(contact, "Austin", "TX")
    assert row["City/State"] == "Austin, TX"
    assert row["Email"] == "e1@example.com"

def test_read_regions(tmp_path):
    csv_file = tmp_path / "regions.csv"
    content = "City,State\nAustin,TX\nDallas,TX"
    csv_file.write_text(content)

    regions = read_regions(csv_file)
    assert len(regions) == 2
    assert regions[0]["City"] == "Austin"
    assert regions[1]["State"] == "TX"

@pytest.mark.asyncio
async def test_read_completed_cities(tmp_path):
    csv_file = tmp_path / "output.csv"
    csv_file.write_text("City/State,School Name,School Link,Faculty Name,Email,Dear Line,Comments\n"
                        "\"Austin, TX\",School A,,John,,, \n"
                        "\"Austin, TX\",School A,,Jill,,, \n"
                        "\"Dallas, TX\",School B,,Jane,,, \n")
    repo = CsvRepository(csv_file)
    seen = repo.get_completed_cities()
    await repo.shutdown()
    assert "Austin|TX" in seen
    assert "Dallas|TX" in seen
    assert len(seen) == 2
    assert seen["Austin|TX"]["School A"] == 2
    assert seen["Dallas|TX"]["School B"] == 1

@pytest.mark.asyncio
async def test_read_completed_cities_missing_file(tmp_path):
    repo = CsvRepository(tmp_path / "nonexistent.csv")
    seen = repo.get_completed_cities()
    await repo.shutdown()
    assert len(seen) == 0

@pytest.mark.asyncio
async def test_append_output_csv(tmp_path):
    csv_file = tmp_path / "output.csv"
    repo = CsvRepository(csv_file)
    rows = [{"City/State": "Austin, TX", "School Name": "S1", "School Link": "", "Faculty Name": "F1", "Email": "", "Dear Line": "", "Comments": ""}]
    
    # Should create file and headers
    await repo.append_rows(rows)
    await repo._queue.join()
    assert csv_file.exists()
    content = csv_file.read_text()
    assert "City/State,School Name" in content
    assert '"Austin, TX",S1' in content
    
    # Append another row, shouldn't duplicate headers or the row itself due to deduplication
    await repo.append_rows(rows)
    await repo._queue.join()
    lines = csv_file.read_text().strip().split('\n')
    assert len(lines) == 2  # header + 1 row (duplicate dropped)
    
    # Append a different row, should be added
    rows[0]["Faculty Name"] = "F2"
    await repo.append_rows(rows)
    await repo._queue.join()
    lines = csv_file.read_text().strip().split('\n')
    assert len(lines) == 3  # header + 2 rows
    await repo.shutdown()
