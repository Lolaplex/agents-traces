import tempfile
import time
from agents_traces.models import TraceEvent
from agents_traces.store import TraceStore


def test_append_and_stream_speed():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = TraceStore(traces_dir=tmpdir)
        
        # Benchmark append 1000 events
        t0 = time.perf_counter()
        for i in range(1000):
            store.append(TraceEvent(
                session=f"s-{i%10}",
                type="tool_call",
                tool="search_docs",
                duration_ms=12.0,
                status="ok",
            ))
        t1 = time.perf_counter()
        avg_ms_per_write = ((t1 - t0) / 1000.0) * 1000.0
        assert avg_ms_per_write < 1.0
        
        # Benchmark reading 1000 events streaming
        t0 = time.perf_counter()
        count = sum(1 for _ in store.iter_events())
        t1 = time.perf_counter()
        read_total_ms = (t1 - t0) * 1000.0
        assert count == 1000
        assert read_total_ms < 50.0
