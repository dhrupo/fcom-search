def test_crawler_library_imports():
    from crawler.config import load_config
    from crawler.search import fetch_source, search
    from crawler.store import Store
    from crawler.transport import Transport

    assert all(callable(x) for x in (load_config, Store, search, fetch_source, Transport))
