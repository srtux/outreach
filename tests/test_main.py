import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from outreach.main import _process_city, ResearchApp, main
from outreach.models import SchoolContact

@pytest.mark.asyncio
async def test_process_city():
    stu_runner = MagicMock()
    vol_runner = MagicMock()
    session_service = MagicMock()
    sem = asyncio.Semaphore(1)
    progress = {"done": 0, "total": 1}
    
    app = ResearchApp(
        session_service=session_service,
        semaphore=sem,
        students_runner=stu_runner,
        volunteers_runner=vol_runner,
        students_repo=MagicMock(),
        volunteers_repo=MagicMock(),
        progress=progress
    )
    
    contact = SchoolContact(school_name="S1", faculty_name="F1")
    
    with patch("outreach.main.search_city", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = [contact]
        
        await _process_city(
            app, "Austin", "TX", {}, {}
        )
        
        assert mock_search.call_count == 2
        assert progress["done"] == 1

@pytest.mark.asyncio
async def test_process_city_exception():
    stu_runner = MagicMock()
    vol_runner = MagicMock()
    session_service = MagicMock()
    sem = asyncio.Semaphore(1)
    progress = {"done": 0, "total": 1}
    
    app = ResearchApp(
        session_service=session_service,
        semaphore=sem,
        students_runner=stu_runner,
        volunteers_runner=vol_runner,
        students_repo=MagicMock(),
        volunteers_repo=MagicMock(),
        progress=progress
    )
    
    with patch("outreach.main.search_city", new_callable=AsyncMock) as mock_search:
        # Simulating exceptions in gather
        mock_search.side_effect = Exception("mock error")
        
        await _process_city(
            app, "Austin", "TX", {}, {}
        )
        
        assert progress["done"] == 1

@pytest.mark.asyncio
async def test_main_no_api_key(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with patch("pathlib.Path.exists", return_value=True):
        with pytest.raises(SystemExit):
            await main()

@pytest.mark.asyncio
async def test_main_no_csv():
    with patch("pathlib.Path.exists", return_value=False):
        with pytest.raises(SystemExit):
            await main()

@pytest.mark.asyncio
async def test_main_success(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "fake_key")
    with patch("pathlib.Path.exists", return_value=True), \
         patch("outreach.main.read_regions", return_value=[{"City": "Aus", "State": "TX"}, {"City": "", "State": ""}]), \
         patch("outreach.main.build_agent") as mock_build, \
         patch("outreach.main.CsvRepository.get_completed_cities", return_value={}), \
         patch("outreach.main._process_city", new_callable=AsyncMock) as mock_process:
        
        await main()
        assert mock_build.call_count == 2
        mock_process.assert_awaited_once() # For Aus, TX. The empty city is skipped.

@pytest.mark.asyncio
async def test_main_skip_fully_completed(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "fake_key")
    with patch("pathlib.Path.exists", return_value=True), \
         patch("outreach.main.read_regions", return_value=[{"City": "Aus", "State": "TX"}]), \
         patch("outreach.main.build_agent"), \
         patch("outreach.main._process_city", new_callable=AsyncMock) as mock_process, \
         patch("outreach.main.CsvRepository.get_completed_cities", side_effect=[
            {"Aus|TX": {"Sch1": 10, "Sch2": 10}}, # students (2 schools, 20 contacts)
            {"Aus|TX": {"Sch3": 10, "Sch4": 10}}, # volunteers
         ]), \
         patch("outreach.main.MIN_SCHOOLS_TARGET", 2), \
         patch("outreach.main.MIN_CONTACTS_TARGET", 20):
        
        await main()
        # Should be skipped because targets are met
        assert mock_process.call_count == 0
