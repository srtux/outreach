# 04: Testing Guide

It is unacceptable to rely on the live internet or the Gemini AI model to pass our software tests. The internet changes every day, and calling the LLM API costs us real money. If Google changes their API slightly, our automated builds would break!

We need to test our code *offline*, completely isolated from external apis. We do this using a suite of testing tools: `pytest` and `pytest-mock`.

## What is Pytest?
`pytest` is the most popular testing framework in Python. When you run `pytest` in your terminal, it acts like a detective:
1. It automatically searches your project for any file named `test_something.py`.
2. Inside those files, it runs any function named `test_something()`.
3. You write `assert` statements (e.g. `assert 2 + 2 == 4`). If the math works out, the test turns Green (Pass). If it's `5`, the test turns Red (Fail).

Because our application uses `asyncio` (like our concurrent Chef), normal synchronous test functions don't work. We have to install the `pytest-asyncio` plugin, which allows us to add a special tag called a "decorator" (`@pytest.mark.asyncio`) above our tests. This lets our tests use `await` properly.

## The Magic of Mocking
Mocking is a technique where you replace a real object (like the real internet or the real LLM) with a "fake dummy clone" that you have pre-programmed to spit out exact answers. 

There are two main dummies we use:
- **`MagicMock`**: Used for faking standard, blocking functions or objects.
- **`AsyncMock`**: Used for faking `async` functions (functions that you have to `await`).

### Patching the API (`test_search.py`)

Let's look at `test_search_city_rate_limit_retry()` inside `tests/test_search.py`. This test proves that our Exponential Backoff retry loop works without actually freezing the program for 120 seconds!

```python
@pytest.mark.asyncio
async def test_search_city_rate_limit_retry():
    # 1. We create fake clones of our Runner and Session
    runner = MagicMock()
    session_service = MagicMock()
    contact = SchoolContact(school_name="S1", faculty_name="F1")
    
    # 2. "Patching" hijacks the actual imports during the test run!
    # Instead of running the real `_run_agent_once`, it runs our AsyncMock dummy.
    with patch("outreach.search._run_agent_once", new_callable=AsyncMock) as mock_run, \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        
        # 3. We violently force the dummy to crash with a 429 on the first try, 
        #    but magically succeed and return data on the second try.
        mock_run.side_effect = [Exception("429 Too Many Requests"), [contact]]
        
        # 4. We run the actual code
        result = await search_city(runner, "Austin", "TX", session_service, MagicMock(), MagicMock())
        
        # 5. We verify the outcome!
        assert len(result) == 1
        assert mock_run.call_count == 2
        mock_sleep.assert_awaited_once() # We prove that the program caught the crash and slept exactly once!
```

This pattern guarantees our code is bulletproof, tested instantly, without relying on wifi!

## Testing Asynchronous Queues (`test_io.py`)

Remember the `CsvRepository` background worker (the mail drop box) we built? Testing queues requires special care. Data goes in "fire and forget", so how do we `assert` the file was actually saved?

We use `await repo._queue.join()`. This is `asyncio` magic that essentially tells your test: *"Pause here and wait until the background worker has completely emptied out every single item in the mailbox."* Once that line finishes, it is visually guaranteed that the CSV file on your hard drive contains the data, and we can safely `assert` its file contents!

```python
    await repo.append_rows(rows) # Drop envelope in box
    await repo._queue.join()     # Wait for worker to finish routing it
    content = csv_file.read_text()
    assert "City/State,School Name" in content # Inspect the hard drive!
    await repo.shutdown()        # Turn off the worker cleanly
```

## Running the Coverage Suite

When writing tests, the ultimate goal is "Test Coverage"—this is the percentage of lines of code in your script that were executed *at least once* during the entire test suite run. The `pytest-cov` tool watches the program run like a security camera to calculate this.

Run the tests using:
```bash
uv run pytest tests/ -v --cov=outreach --cov-report=term-missing
```

- `uv run` injects the locked development environment.
- `-v` gives verbose output so you can see every single test name run.
- `--cov=outreach` points the security cameras at the `outreach/` folder.
- `--cov-report=term-missing` prints a beautiful dashboard at the end showing exactly the line numbers of code that you still haven't written a test for!

Dive into the `tests/` folder and see how it all fits together!
