import json
import logging
import re
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup, NavigableString

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
BASE_URL = "https://fastdic.com/word/{word}"


def _is_persian(text: str) -> bool:
    for ch in text:
        if "\u0600" <= ch <= "\u06FF" or "\u0750" <= ch <= "\u077F" or "\uFB50" <= ch <= "\uFDFF" or "\uFE70" <= ch <= "\uFEFF":
            return True
    return False


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def scrape_word(word: str) -> dict | None:
    direction = "fa-en" if _is_persian(word) else "en-fa"
    url = BASE_URL.format(word=quote(word, safe=""))
    logger.info("Scraping %s (%s)", url, direction)
    resp = requests.get(url, headers=HEADERS, timeout=15)
    if resp.status_code != 200:
        logger.warning("HTTP %d for %s", resp.status_code, url)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    result = {
        "word": word,
        "direction": direction,
        "meanings": [],
        "idioms": [],
        "verb_forms": {},
        "phonetic": None,
        "audio_url_us": None,
        "audio_url_uk": None,
        "synonyms_antonyms": [],
        "phrasal_verbs": [],
        "collocations": [],
        "related_words": [],
        "faq": [],
    }

    _parse_jsonld(soup, result)
    _parse_header(soup, result)
    _parse_meanings(soup, result)
    _parse_section_pairs(soup, result, "phrasal-verbs", "phrasal_verbs")
    _parse_section_pairs(soup, result, "collocations", "collocations")
    _parse_section_pairs(soup, result, "idioms", "idioms")
    _parse_synonyms(soup, result)
    _parse_related_words(soup, result)
    _parse_faq(soup, result)

    if not result["meanings"]:
        logger.info("No meanings found for '%s'", word)
        return None

    logger.info(
        "Found %d meanings, %d idioms, %d phrasal, %d collocations, %d syn/ant, %d related, %d faq for '%s'",
        len(result["meanings"]),
        len(result["idioms"]),
        len(result["phrasal_verbs"]),
        len(result["collocations"]),
        len(result["synonyms_antonyms"]),
        len(result["related_words"]),
        len(result["faq"]),
        word,
    )
    return result


def _parse_jsonld(soup: BeautifulSoup, result: dict):
    tag = soup.find("script", type="application/ld+json")
    if not tag:
        return
    try:
        data = json.loads(tag.string)
    except (json.JSONDecodeError, TypeError):
        return

    graph = data.get("@graph", [data]) if isinstance(data, dict) else [data]
    for item in graph:
        if item.get("@type") != "DefinedTermSet":
            continue
        for dt in item.get("hasDefinedTerm", []):
            desc = dt.get("description", "").strip()
            if not desc:
                continue
            result["meanings"].append({"text": desc, "pos": None, "cefr": None, "domain": None, "examples": []})
        audios = item.get("audio", [])
        for a in audios:
            url = a.get("contentUrl", "")
            desc = a.get("description", "")
            if "American" in desc:
                result["audio_url_us"] = url
            elif "British" in desc:
                result["audio_url_uk"] = url


def _parse_header(soup: BeautifulSoup, result: dict):
    phonetic_el = soup.select_one(".results__header--phonetic")
    if phonetic_el:
        result["phonetic"] = _clean(phonetic_el.get_text())

    if result["direction"] != "fa-en":
        for audio_span in soup.select(".results__header--audios .audio"):
            audio_url = audio_span.get("data-url", "")
            audio_src = audio_span.get("data-src", "")
            audio_type = audio_span.get("data-type", "")
            if audio_url and audio_src:
                full_url = f"{audio_url}c-en-audios/{audio_type}/mp3/{audio_src}.mp3"
                if audio_type == "us":
                    result["audio_url_us"] = full_url
                elif audio_type == "uk":
                    result["audio_url_uk"] = full_url

    verb_forms = {}
    for item in soup.select(".results__extra-item"):
        label_el = item.select_one("p")
        value_el = item.select_one("strong")
        if label_el and value_el:
            label = _clean(label_el.get_text()).rstrip(":")
            value = _clean(value_el.get_text())
            if label and value:
                verb_forms[label] = value
    result["verb_forms"] = verb_forms


def _parse_meanings(soup: BeautifulSoup, result: dict):
    is_fa_en = result["direction"] == "fa-en"

    if not result["meanings"]:
        for meaning_div in soup.select(".meaning"):
            m = _parse_one_meaning(meaning_div, is_fa_en)
            if m:
                result["meanings"].append(m)
    else:
        meaning_divs = soup.select(".meaning")
        for i, meaning_div in enumerate(meaning_divs):
            if i >= len(result["meanings"]):
                break
            pos_el = meaning_div.select_one(".pos")
            if pos_el:
                spans = pos_el.select("span.p")
                result["meanings"][i]["pos"] = ", ".join(_clean(s.get_text()) for s in spans) if spans else None
                cefr_el = pos_el.select_one(".cefr")
                if cefr_el:
                    result["meanings"][i]["cefr"] = _clean(cefr_el.get_text())
                domain_el = pos_el.select_one(".domain")
                if domain_el:
                    result["meanings"][i]["domain"] = _clean(domain_el.get_text())

            examples = []
            for sent_pair in meaning_div.select(".result__sentences"):
                sents = sent_pair.select(".sentence p")
                if len(sents) >= 2:
                    en_text = _clean(sents[0].get_text())
                    fa_text = _clean(sents[1].get_text())
                    if is_fa_en:
                        examples.append({"english": fa_text, "persian": en_text})
                    else:
                        examples.append({"english": en_text, "persian": fa_text})
            result["meanings"][i]["examples"] = examples


def _parse_one_meaning(meaning_div, is_fa_en: bool) -> dict | None:
    pos_el = meaning_div.select_one(".pos")
    pos = None
    cefr = None
    domain = None
    if pos_el:
        spans = pos_el.select("span.p")
        pos = ", ".join(_clean(s.get_text()) for s in spans) if spans else None
        cefr_el = pos_el.select_one(".cefr")
        if cefr_el:
            cefr = _clean(cefr_el.get_text())
        domain_el = pos_el.select_one(".domain")
        if domain_el:
            domain = _clean(domain_el.get_text())

    p_el = meaning_div.select_one("p")
    if not p_el:
        return None
    text = _clean(p_el.get_text())
    if not text:
        return None

    examples = []
    for sent_pair in meaning_div.select(".result__sentences"):
        sents = sent_pair.select(".sentence p")
        if len(sents) >= 2:
            en_text = _clean(sents[0].get_text())
            fa_text = _clean(sents[1].get_text())
            if is_fa_en:
                examples.append({"english": fa_text, "persian": en_text})
            else:
                examples.append({"english": en_text, "persian": fa_text})

    return {"text": text, "pos": pos, "cefr": cefr, "domain": domain, "examples": examples}


def _parse_section_pairs(soup: BeautifulSoup, result: dict, section_id: str, key: str):
    section = soup.select_one(f"section#{section_id}")
    if not section:
        return

    is_fa_en = result["direction"] == "fa-en"
    items = []
    for div in section.select(".results__english-idioms"):
        phrase_el = div.select_one("p.english")
        if not phrase_el:
            continue
        phrase = _clean(phrase_el.get_text())
        persian_parts = [_clean(p.get_text()) for p in div.select("p.persian")]
        if is_fa_en:
            meanings = persian_parts
        else:
            meanings = persian_parts
        if phrase and meanings:
            items.append({"phrase": phrase, "meanings": meanings})
    result[key] = items[:10]


def _parse_synonyms(soup: BeautifulSoup, result: dict):
    section = soup.select_one("section#synonyms-antonyms")
    if not section:
        return

    groups = []
    for li in section.select("li.result__synonyms_antonyms"):
        def_div = li.select_one("div.definition")
        if not def_div:
            continue

        pos_el = def_div.select_one("span.pos span.p")
        pos = _clean(pos_el.get_text()) if pos_el else None

        definition = _clean(def_div.get_text())
        if pos:
            definition = definition.replace(pos, "").strip()

        synonyms = [_clean(a.get_text()) for a in li.select(".synonyms_antonyms--synonyms .synonyms a")]
        antonyms = [_clean(a.get_text()) for a in li.select(".synonyms_antonyms--antonyms .antonyms a")]

        if synonyms or antonyms:
            groups.append({"pos": pos, "definition": definition, "synonyms": synonyms, "antonyms": antonyms})
    result["synonyms_antonyms"] = groups


def _parse_related_words(soup: BeautifulSoup, result: dict):
    section = soup.select_one("section#word-family")
    if not section:
        return

    groups = []
    for div in section.select("div.result__word-family"):
        pos_el = div.select_one("div.pos span.p")
        pos = _clean(pos_el.get_text()) if pos_el else None

        words = []
        for child in div.children:
            if isinstance(child, NavigableString):
                continue
            text = _clean(child.get_text())
            if text and child.name != "div":
                words.append(text)

        if words:
            groups.append({"pos": pos, "words": words})
    result["related_words"] = groups


def _parse_faq(soup: BeautifulSoup, result: dict):
    section = soup.select_one("section#faqs")
    if not section:
        return

    items = []
    for details in section.select("details.results__faqs--row"):
        q_el = details.select_one("summary span.text")
        a_el = details.select_one("div.results__faqs--answer p")
        if q_el and a_el:
            items.append({"question": _clean(q_el.get_text()), "answer": _clean(a_el.get_text())})
    result["faq"] = items
