import re
import tomllib
import unicodedata
from dataclasses import dataclass
from importlib import resources

BANGLA_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


@dataclass(frozen=True)
class PhoneModel:
    brand: str
    model: str


class Catalog:
    def __init__(self, raw: dict):
        self._words = raw.get("words", {})
        self._lines = [b["line"].lower() for b in raw["brand"]]
        sequences = {}
        for brand in raw["brand"]:
            names = [(name, name) for name in brand["models"]] + list(brand.get("aliases", {}).items())
            for name, canonical in names:
                found = PhoneModel(brand["name"], f"{brand['line']} {canonical}")
                tokens = tuple(self._tokens(name))
                sequences[(brand["line"].lower(), *tokens)] = found
                if brand["bare"] and (len(tokens) > 1 or re.fullmatch(r"[a-z]+\d+", tokens[0])):
                    sequences.setdefault(tokens, found)
        self._sequences = sorted(sequences.items(), key=lambda kv: -len(kv[0]))

    def _tokens(self, text: str) -> list[str]:
        text = unicodedata.normalize("NFKC", text).casefold().translate(BANGLA_DIGITS)
        text = re.sub(r"#\S+", " ", text)
        for bangla, english in self._words.items():
            text = text.replace(bangla, f" {english} ")
        text = text.replace("+", " plus ").replace("promax", "pro max")
        if self._lines:
            text = re.sub(rf"\b({'|'.join(map(re.escape, self._lines))})(\d)", r"\1 \2", text)
        text = re.sub(r"(\d)([a-z])", r"\1 \2", text)
        text = re.sub(r"(\d) pm\b", r"\1 pro max", text)
        return re.findall(r"[a-z0-9]+", text)

    def find_models(self, text: str) -> list[PhoneModel]:
        tokens = self._tokens(text)
        found: list[PhoneModel] = []
        i = 0
        while i < len(tokens):
            for seq, model in self._sequences:
                if tuple(tokens[i:i + len(seq)]) == seq:
                    # "iPhone 18 Pro Series" names a family of phones, not one offer.
                    if model not in found and tokens[i + len(seq):i + len(seq) + 1] != ["series"]:
                        found.append(model)
                    i += len(seq)
                    break
            else:
                i += 1
        return found


def load_catalog(name: str) -> Catalog:
    text = resources.files("fcommerce_search.extract").joinpath("catalogs", f"{name}.toml").read_text(encoding="utf-8")
    return Catalog(tomllib.loads(text))
