"""
Statistieken-scraper voor FIBS Serie A Gold Baseball — batting, pitching en
fielding van alle spelers dit seizoen.
Source: https://www.fibs.it/en/events/2026-serie-a-gold-baseball/stats

BELANGRIJK: fibs.it blokkeert verzoeken die niet van een "schoon" IP-adres
komen (CloudFront/WAF-403), net als bij de standen-scraper van deze
competitie — vandaar dat ook hier via de ScraperAPI-proxy gefetcht wordt.
Zie fibs_scraper.py voor de volledige uitleg/diagnose van die blokkade.

In tegenstelling tot de standen-pagina (kale HTML) haalt de stats-pagina
zijn data op via een JSON-API die de site zelf ook gebruikt:
  https://www.fibs.it/api/v1/stats/events/{event}/index
      ?section=players&stats-section=batting|pitching|fielding&language=en
Dat is veel prettiger dan HTML parsen: geen regex-tabelgepeuter nodig, en de
API levert zelfs zelf al Engelse kolomlabels + tooltips mee (in "headers"),
die we hier direct doorzetten naar de JSON — precies zoals bij de
MLB-stats-scraper.

EVENT_SLUG moet waarschijnlijk elk seizoen handmatig worden bijgewerkt
zodra FIBS een nieuwe competitie-pagina aanmaakt (zelfde aandachtspunt als
season/league bij de andere Italiaanse/Tsjechische bronnen).
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

EVENT_SLUG = "2026-serie-a-gold-baseball"
API_BASE = f"https://www.fibs.it/api/v1/stats/events/{EVENT_SLUG}/index"
STANDINGS_URL = f"https://www.fibs.it/en/events/{EVENT_SLUG}/standings"
SCRAPERAPI_ENDPOINT = "http://api.scraperapi.com/"
JSON_FILE = "stats_serie_a_gold.json"


def fetch_via_proxy(url, pogingen=3):
    api_key = os.environ.get("SCRAPERAPI_KEY")
    if not api_key:
        raise RuntimeError(
            "SCRAPERAPI_KEY ontbreekt. Zet 'm als GitHub Actions secret en geef "
            "'m door aan deze stap via env: SCRAPERAPI_KEY: ${{ secrets.SCRAPERAPI_KEY }}."
        )
    proxy_url = f"{SCRAPERAPI_ENDPOINT}?api_key={api_key}&url={urllib.parse.quote(url, safe='')}"
    laatste_fout = None
    for poging in range(1, pogingen + 1):
        try:
            with urllib.request.urlopen(proxy_url, timeout=90) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            laatste_fout = e
            body = ''
            try:
                body = e.read(500).decode("utf-8", errors="replace")
            except Exception:
                pass
            print(f"Poging {poging}/{pogingen}: HTTP {e.code} {e.reason} via proxy op {url}. Body: {body!r}")
            if poging < pogingen:
                time.sleep(5 * poging)
                continue
            raise
        except urllib.error.URLError as e:
            laatste_fout = e
            print(f"Poging {poging}/{pogingen}: netwerkfout via proxy op {url}: {e.reason}")
            if poging < pogingen:
                time.sleep(5 * poging)
                continue
            raise
    if laatste_fout:
        raise laatste_fout
    raise RuntimeError("fetch_via_proxy: onbekende fout zonder resultaat")


def fetch_stats_sectie(stats_sectie):
    url = f"{API_BASE}?section=players&stats-section={stats_sectie}&team=&round=&split=&language=en"
    ruw = fetch_via_proxy(url)
    data = json.loads(ruw)
    return data.get("headers", []), data.get("data", [])


def fetch_team_namen():
    """teamid -> volledige teamnaam, via de team-aggregaat-stats van de API."""
    url = f"{API_BASE}?section=teams&stats-section=batting&team=&round=&split=&language=en"
    ruw = fetch_via_proxy(url)
    data = json.loads(ruw)
    namen = {}
    for team in data.get("data", []):
        naam = clean_text(team.get("name", ""))
        teamid = team.get("teamid")
        if teamid is not None and naam:
            namen[str(teamid)] = naam
    return namen


def fetch_team_logos():
    """teamid -> logo-URL, gescraped uit de standen-pagina (dezelfde bron als
    de standen-scraper, zie fibs_scraper.py)."""
    html = fetch_via_proxy(STANDINGS_URL)
    logos = {}
    for match in re.finditer(
        rf'/events/{re.escape(EVENT_SLUG)}/teams/(\d+)"[^>]*>\s*<img[^>]+src="([^"]+)"',
        html,
    ):
        teamid, logo = match.group(1), match.group(2)
        logos.setdefault(teamid, logo)
    return logos


def clean_text(html_fragment):
    tekst = re.sub(r'<[^>]+>', '', html_fragment or '')
    return re.sub(r'\s+', ' ', tekst).strip()


def parse_naam(name_html):
    """
    name-veld van de API is bv.
    '<span class="lastname">ACERBI</span><br><span class="firstname">Edoardo</span>'
    """
    achternaam_match = re.search(r'<span class="lastname">(.*?)</span>', name_html or '', re.DOTALL)
    voornaam_match = re.search(r'<span class="firstname">(.*?)</span>', name_html or '', re.DOTALL)
    achternaam = clean_text(achternaam_match.group(1)) if achternaam_match else ''
    voornaam = clean_text(voornaam_match.group(1)) if voornaam_match else ''
    naam = f"{voornaam} {achternaam}".strip() if (voornaam or achternaam) else clean_text(name_html)
    return naam, voornaam, achternaam


def formatteer_getal(waarde):
    """
    De API geeft AVG/SLG/OBP/OPS/BAVG/FLDP als geheel getal keer 1000
    (bv. 219 = .219, 1000 = 1.000), zoals ook op de site zelf getoond wordt.
    """
    try:
        n = int(waarde)
    except (TypeError, ValueError):
        return waarde
    teken = '-' if n < 0 else ''
    n = abs(n)
    if n >= 1000:
        return f"{teken}{n / 1000:.3f}"
    return f"{teken}.{n:03d}"


def parse_innings(waarde):
    """
    IP wordt genoteerd als bv. "23.2": het cijfer na de punt is het aantal
    derde innings (.0/.1/.2), GEEN decimaal tiende — 23.2 betekent dus
    23 + 2/3 innings, niet 23,2.
    """
    try:
        heel, _, derde = str(waarde).partition('.')
        heel = int(heel) if heel else 0
        derde = int(derde) if derde else 0
        return heel + derde / 3
    except (TypeError, ValueError):
        return 0.0


def verwerk_rijen(headers, ruwe_data):
    format_kolommen = {h["column"] for h in headers if h.get("format")}
    verwerkt = []
    for rij in ruwe_data:
        nieuwe_rij = dict(rij)
        naam, voornaam, achternaam = parse_naam(rij.get("name", ""))
        nieuwe_rij["name"] = naam
        nieuwe_rij["voornaam"] = voornaam
        nieuwe_rij["achternaam"] = achternaam
        for kolom in format_kolommen:
            if kolom in nieuwe_rij:
                nieuwe_rij[kolom] = formatteer_getal(nieuwe_rij[kolom])
        verwerkt.append(nieuwe_rij)
    return verwerkt


def sorteer_batting(batting_data):
    """Zelfde conventie als de MLB-stats-scraper: sorteren op AVG, met een
    kwalificatiedrempel van 25% van de AB van de AB-leider, zodat een
    pinch-hitter met 1 at-bat niet bovenaan staat."""
    ab_waarden = [r.get("ab") for r in batting_data if isinstance(r.get("ab"), (int, float))]
    max_ab = max(ab_waarden) if ab_waarden else 0
    drempel = 0.25 * max_ab

    def sleutel(r):
        ab = r.get("ab") or 0
        avg_str = r.get("avg", "")
        try:
            avg_waarde = float(avg_str) if str(avg_str).strip() not in ('', '-') else -1
        except ValueError:
            avg_waarde = -1
        gekwalificeerd = drempel > 0 and ab >= drempel
        return (0 if gekwalificeerd else 1, -avg_waarde)

    batting_data.sort(key=sleutel)
    return drempel


def sorteer_pitching(pitching_data):
    """Zelfde conventie: sorteren op ERA, met een kwalificatiedrempel van
    10% van de IP van de IP-leider."""
    ip_waarden = [parse_innings(r.get("pitch_ip")) for r in pitching_data]
    max_ip = max(ip_waarden) if ip_waarden else 0
    drempel = 0.10 * max_ip

    def sleutel(r):
        ip = parse_innings(r.get("pitch_ip"))
        try:
            era_waarde = float(r.get("era"))
        except (TypeError, ValueError):
            era_waarde = 999
        gekwalificeerd = drempel > 0 and ip >= drempel
        return (0 if gekwalificeerd else 1, era_waarde)

    pitching_data.sort(key=sleutel)
    return drempel


def sorteer_fielding(fielding_data):
    fielding_data.sort(key=lambda r: -(r.get("field_g") or 0))


def bouw_teams_lookup(batting_data, namen_per_id, logos_per_id):
    teams = {}
    for rij in batting_data:
        code = rij.get("teamcode")
        teamid = rij.get("teamid")
        if not code or code in teams:
            continue
        teams[code] = {
            "team_id": teamid,
            "naam":    namen_per_id.get(str(teamid), code),
            "logo":    logos_per_id.get(str(teamid), ''),
        }
    return teams


def laad_bestaand():
    try:
        with open(JSON_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def main():
    seizoen = EVENT_SLUG.split('-')[0]

    print("Batting ophalen...")
    batting_headers, batting_ruw = fetch_stats_sectie("batting")
    batting_data = verwerk_rijen(batting_headers, batting_ruw)
    ab_drempel = sorteer_batting(batting_data)
    print(f"  {len(batting_data)} spelers.")

    print("Pitching ophalen...")
    pitching_headers, pitching_ruw = fetch_stats_sectie("pitching")
    pitching_data = verwerk_rijen(pitching_headers, pitching_ruw)
    ip_drempel = sorteer_pitching(pitching_data)
    print(f"  {len(pitching_data)} spelers.")

    print("Fielding ophalen...")
    fielding_headers, fielding_ruw = fetch_stats_sectie("fielding")
    fielding_data = verwerk_rijen(fielding_headers, fielding_ruw)
    sorteer_fielding(fielding_data)
    print(f"  {len(fielding_data)} spelers.")

    print("Teamnamen en logo's ophalen...")
    namen_per_id = fetch_team_namen()
    logos_per_id = fetch_team_logos()
    teams = bouw_teams_lookup(batting_data, namen_per_id, logos_per_id)

    nu = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    nieuw = {
        "batting":  {"headers": batting_headers,  "data": batting_data},
        "pitching": {"headers": pitching_headers, "data": pitching_data},
        "fielding": {"headers": fielding_headers, "data": fielding_data},
        "teams":    teams,
    }
    bestaand = laad_bestaand()
    ongewijzigd = (
        bestaand is not None
        and bestaand.get("batting", {}).get("data") == batting_data
        and bestaand.get("pitching", {}).get("data") == pitching_data
        and bestaand.get("fielding", {}).get("data") == fielding_data
    )
    last_updated = bestaand["meta"]["last_updated"] if ongewijzigd and bestaand.get("meta") else nu

    output = {
        "meta": {
            "seizoen":       seizoen,
            "last_updated":  last_updated,
            "last_checked":  nu,
            "ab_drempel":    round(ab_drempel, 1),
            "ip_drempel":    round(ip_drempel, 1),
        },
        **nieuw,
    }
    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"✅ {JSON_FILE} opgeslagen")


if __name__ == "__main__":
    main()
