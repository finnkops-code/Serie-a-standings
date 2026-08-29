"""
Standen-scraper voor FIBS (Federazione Italiana Baseball Softball) — Serie A
Gold Baseball.
Source: https://www.fibs.it/en/events/2026-serie-a-gold-baseball/standings

BELANGRIJK: fibs.it blokkeert gewone (niet-browser) HTTP-requests met een
kale 403 — bevestigd doordat zelfs een browserachtige fetch zonder JS-uitvoering
werd geweigerd, terwijl een echte (headless) Chromium-browser de pagina
gewoon kreeg. Vandaar dat deze scraper Playwright gebruikt om de pagina
in een echte browser te laden, in plaats van urllib zoals bij de andere
scrapers in deze set. robots.txt staat scrapen overigens gewoon toe
("Allow: /"), dus dit is puur een technische kwestie, geen beleidskwestie.

Alle standen (Regular season/Gironi, Quarti, Semifinali, Italian Baseball
Series) staan al in de eerste server-gerenderde pagina — geen tabklikken
nodig, gewoon één page-load.
"""
import json
import re
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

URL = "https://www.fibs.it/en/events/2026-serie-a-gold-baseball/standings"


def clean_text(html_fragment):
    """Alle tags verwijderen en witruimte normaliseren."""
    tekst = re.sub(r'<[^>]+>', '', html_fragment)
    return re.sub(r'\s+', ' ', tekst).strip()


def fetch_html(url):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pagina = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
        )
        pagina.goto(url, wait_until="networkidle", timeout=60000)
        # Wachten tot er minstens 1 standen-tabel in de DOM staat.
        pagina.wait_for_selector(".box-container table", timeout=30000)
        html = pagina.content()
        browser.close()
        return html


def parse_team_cellen(logo_td_html, naam_td_html):
    """
    Team staat verspreid over 2 <td>'s:
      - logo_td: <a href="team-link"><img src="logo" title="volledige naam"></a>
      - naam_td: <a href="team-link"><p class="team-name">CODE<br><small>NAAM</small></p></a>
    """
    href_match = re.search(r'href="([^"]+)"', naam_td_html) or re.search(r'href="([^"]+)"', logo_td_html)
    logo_match = re.search(r'<img[^>]+src="([^"]+)"', logo_td_html)
    code_match = re.search(r'<p class="team-name">\s*([^<]+?)\s*<br', naam_td_html, re.DOTALL)
    naam_match = re.search(r'<small>(.*?)</small>', naam_td_html, re.DOTALL)
    return {
        "team_link": href_match.group(1) if href_match else '',
        "team_logo": logo_match.group(1) if logo_match else '',
        "team_code": clean_text(code_match.group(1)) if code_match else '',
        "team":      clean_text(naam_match.group(1)) if naam_match else clean_text(naam_td_html),
    }


def parse_standings(html):
    result = {}
    # Elke groep/fase staat in een eigen "box-container" div met een <h3>
    # (groepsnaam) direct gevolgd door de standen-<table>. We splitsen op de
    # openings-marker (net als bij de andere scrapers op <h3>) i.p.v. te
    # proberen de sluitende </div> exact te matchen — de box-container-div
    # zit namelijk genest in andere divs (col-md-6, row, tab-pane, ...), dus
    # de eerstvolgende </div> is niet per se de eigen sluit-tag. Omdat we
    # per box toch alleen naar de EERSTE <h3> en <table> zoeken, maakt het
    # niet uit dat de rest van de pagina achter elk stuk "meehangt".
    boxes = re.split(r'<div class="box-container">', html)[1:]
    for box in boxes:
        h3_match = re.search(r'<h3[^>]*>(.*?)</h3>', box, re.DOTALL)
        table_match = re.search(r'<table\b[^>]*>(.*?)</table>', box, re.DOTALL)
        if not h3_match or not table_match:
            continue
        fase_naam = clean_text(h3_match.group(1))
        table_html = table_match.group(1)
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table_html, re.DOTALL)
        fase_rijen = []
        for row in rows:
            # Header-rij bestaat uit <th>'s, geen <td>'s — die valt hier
            # vanzelf af doordat tds_raw dan leeg blijft.
            tds_raw = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
            # #, logo, naam, W, L, T, PCT, GB
            if len(tds_raw) < 8:
                continue
            positie = clean_text(tds_raw[0]).rstrip('.')
            team_info = parse_team_cellen(tds_raw[1], tds_raw[2])
            if not team_info["team"]:
                continue
            rij = {
                "positie":    positie,
                "team":       team_info["team"],
                "team_code":  team_info["team_code"],
                "team_logo":  team_info["team_logo"],
                "team_link":  team_info["team_link"],
                "w":          clean_text(tds_raw[3]),
                "l":          clean_text(tds_raw[4]),
                "t":          clean_text(tds_raw[5]),
                "pct":        clean_text(tds_raw[6]),
                "gb":         clean_text(tds_raw[7]),
            }
            fase_rijen.append(rij)
        if fase_rijen:
            result[fase_naam] = fase_rijen
    return result


def main():
    print(f"Ophalen van {URL} via headless browser...")
    html = fetch_html(URL)
    print(f"Ontvangen: {len(html)} bytes")
    standen = parse_standings(html)
    print(f"Gevonden groepen/fases: {list(standen.keys())}")
    output = {
        "bijgewerkt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bron":       URL,
        "standen":    standen,
    }
    with open("standen_serie_a_gold.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("✅ standen_serie_a_gold.json opgeslagen")


if __name__ == "__main__":
    main()
