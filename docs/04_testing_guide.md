# 04: Testing Guide

It is unacceptable to rely on the live internet or the Gemini AI model to pass our software tests because the internet changes every day. And, calling the LLM API costs us real money and quota. What happens if Google changes their API slightly? The build would break!

We need to test our code *offline*, completely isolated from external apis. We do this using `pytest`, `pytest-asyncio`, and `pytest-mock`.

## What is Pytest?
`pytest` is the most popular testing framework in Python. It automatically discovers any python file starting with `test_` and runs all functions inside starting with `test_`. Usually, we write assertions (e.g. `assert 2 + 2 == 4`) to verify truth.

Because our main script uses `async` (concurrent tasks), normal synchronous functions don't work. We install the `pytest-asyncio` plugin, which allows us to add a decorator `@pytest.mark.asyncio` above our tests so they can `await` async tasks correctly.

## The Magic of Mocking
Mocking is a technique where you replace a real object or network class with a "fake" clone that you have pre-programmed to act a certain way. This is required to test things like an Exponential Backoff retry loop without actually freezing the program for 120 seconds. 

### Patching the API (`test_main.py`)

If we look at `test_search_city_rate_limit_retry()`, here is how it works:

```python
@pytest.mark.asyncio
async def test_search_city_rate_limit_retry():
    # 1. We create fake clones of our Runner and Session
    runner = MagicMock()
    session_service = MagicMock()
    contact = SchoolContact(school_name="S1", faculty_name="F1")
    
    # 2. "Patching" hijacks the actual imports during the test run
    with patch("outreach.main._run_agent_once", new_callable=AsyncMock) as mock_run, \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        
        # 3. We forcibly order the mock to crash with a 429 on the first try, 
        #    but magically succeed and return data on the second try.
        mock_run.side_effect = [Exception("429 Too Many Requests"), [contact]]
        
        # 4. We run the actual function we want to test
        result = await search_city(runner, "Austin", "TX", session_service)
        
        # 5. We verify the outcome!
        assert len(result) == 1
        assert mock_run.call_count == 2
        mock_sleep.assert_awaited_once() # It proves that the program slept exactly once!
```

This pattern guarantees that our exponential backoff works precisely as defined, handling the 429 exception without crashing the app, delaying execution, and eventually yielding a correct result, all tested instantly without relying on the network!

## Running the Coverage Suite

When writing tests, the goal is "Test Coverage"—the percentage of lines of code in your script that were executed at least once during the total test run. We install the `pytest-cov` plugin, which watches what code gets run.

You can execute tests with coverage enabled by running:
```bash
uv run pytest tests/ -v --cov=outreach --cov-report=term-missing
```

- `uv run` injects the locked dependencies
- `tests/` points to the test directory
- `-v` gives verbose individual test readouts
- `--cov=outreach` limits coverage tracking to just the actual `outreach` application files
- `--cov-report=term-missing` outputs a report at the end showing exactly the line numbers of code that you did *not* write a test for.

If you modify this project, simply write new tests, run the coverage command above, and ensure the metrics stay green!
