import pytest

from fcommerce_search.config import ConfigError, load_config


def write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def test_relative_crawler_data_dir_resolves_like_crawler_cli(tmp_path):
    write(tmp_path / "crawler" / "crawler.toml", 'places = ["Sylhet"]\ndata_dir = "data-run2"\n')
    cfg = load_config(write(tmp_path / "fcs" / "fcommerce-search.toml",
                            'crawler_config = "../crawler/crawler.toml"\n'))
    assert cfg.crawler_data_dir.resolve() == (tmp_path / "crawler" / "data-run2").resolve()
    assert cfg.crawler.places == ("Sylhet",)


def test_missing_crawler_config_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="crawler_config"):
        load_config(write(tmp_path / "fcommerce-search.toml", ""))


@pytest.mark.parametrize("broken", ["ours", "crawler"])
def test_non_utf8_config_is_a_config_error(tmp_path, broken):
    crawler = write(tmp_path / "crawler.toml", 'places = ["Sylhet"]\n')
    ours = write(tmp_path / "fcommerce-search.toml", 'crawler_config = "crawler.toml"\n')
    ({"ours": ours, "crawler": crawler}[broken]).write_bytes(b'places = ["\xff"]\n')
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(ours)


@pytest.mark.parametrize("url", ["fcommerce-search.pages.dev", "ftp://x", 42])
def test_site_url_needs_http_scheme(tmp_path, url):
    write(tmp_path / "crawler.toml", 'places = ["Sylhet"]\n')
    value = f'"{url}"' if isinstance(url, str) else url
    with pytest.raises(ConfigError, match="site.url"):
        load_config(write(tmp_path / "fcommerce-search.toml", f'crawler_config = "crawler.toml"\n[site]\nurl = {value}\n'))
