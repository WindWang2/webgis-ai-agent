import threading


from app.services.chat.llm_client import LRUCache, parse_minimax_xml_tool_calls


def test_lru_cache_basic():
    cache = LRUCache(capacity=3)
    cache["a"] = 1
    cache["b"] = 2
    cache["c"] = 3

    assert cache["a"] == 1
    assert "b" in cache


def test_lru_cache_eviction():
    cache = LRUCache(capacity=2)
    cache["a"] = 1
    cache["b"] = 2
    cache["c"] = 3

    assert "a" not in cache
    assert cache["b"] == 2
    assert cache["c"] == 3


def test_lru_cache_access_moves_to_end():
    cache = LRUCache(capacity=2)
    cache["a"] = 1
    cache["b"] = 2
    # accessing 'a' makes 'b' the LRU
    _ = cache["a"]
    cache["c"] = 3

    assert "b" not in cache
    assert cache["a"] == 1
    assert cache["c"] == 3


def test_lru_cache_contains():
    cache = LRUCache(capacity=2)
    cache["a"] = 1
    assert "a" in cache
    assert "b" not in cache


def test_lru_cache_pop():
    cache = LRUCache(capacity=2)
    cache["a"] = 1
    cache["b"] = 2

    assert cache.pop("a") == 1
    assert "a" not in cache
    assert cache.pop("c", "default") == "default"


def test_lru_cache_concurrent_access():
    cache = LRUCache(capacity=100)

    def worker(start_idx, num_ops):
        for i in range(num_ops):
            key = f"key_{start_idx + i}"
            cache[key] = i
            _ = cache.get(key)
            if i % 2 == 0:
                cache.pop(key, None)

    threads = []
    for i in range(10):
        t = threading.Thread(target=worker, args=(i * 100, 100))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    # The internal state shouldn't be corrupted (e.g. no KeyError or RuntimeError during mutations)
    assert len(cache) <= 100


def test_parse_minimax_xml_tool_calls_valid():
    content = '''
    Some text before.
    minimax:tool_call <invoke name="weather_tool">
        <parameter name="location">"Beijing"</parameter>
        <parameter name="unit">"celsius"</parameter>
    </invoke>
    Some text after.
    '''
    calls = parse_minimax_xml_tool_calls(content)
    assert len(calls) == 1
    call = calls[0]
    assert call["function"]["name"] == "weather_tool"
    assert call["function"]["arguments"] == {"location": "Beijing", "unit": "celsius"}
    assert call["id"].startswith("call_")


def test_parse_minimax_xml_tool_calls_invalid_json_fallback():
    content = '''
    minimax:tool_call <invoke name="search">
        <parameter name="query">invalid json unquoted</parameter>
    </invoke>
    '''
    calls = parse_minimax_xml_tool_calls(content)
    assert len(calls) == 1
    call = calls[0]
    assert call["function"]["name"] == "search"
    # Should fallback to string
    assert call["function"]["arguments"] == {"query": "invalid json unquoted"}


def test_parse_minimax_xml_tool_calls_multiple():
    content = '''
    minimax:tool_call <invoke name="tool1"><parameter name="p1">1</parameter></invoke>
    minimax:tool_call <invoke name="tool2"><parameter name="p2">2</parameter></invoke>
    '''
    calls = parse_minimax_xml_tool_calls(content)
    assert len(calls) == 2
    assert calls[0]["function"]["name"] == "tool1"
    assert calls[0]["function"]["arguments"] == {"p1": 1}
    assert calls[1]["function"]["name"] == "tool2"
    assert calls[1]["function"]["arguments"] == {"p2": 2}
