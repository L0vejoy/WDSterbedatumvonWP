import re
import urllib.parse
from bs4 import BeautifulSoup
import requests
import streamlit as st
import streamlit.components.v1 as components

# Seiten-Layout konfigurieren
st.set_page_config(
    page_title="CamelBot Maintenance zu QuickStatements",
    page_icon="🌐",
    layout="centered",
)

st.title("CamelBot Wartungsliste $\\rightarrow$ QuickStatements Dev")
st.write(
    "Dieses Tool lädt automatisch die fehlenden Sterbedaten aus der"
    " CamelBot-Wartungsseite, extrahiert Datum & Quelle aus Wikipedia und"
    " bereitet sie für QuickStatements auf."
)

HEADERS = {
    "User-Agent": (
        "WikidataDeadDateChecker/1.0 (Educational Script;"
        " https://de.wikipedia.org)"
    )
}

MONATE = {
    "januar": "01",
    "februar": "02",
    "märz": "03",
    "april": "04",
    "mai": "05",
    "juni": "06",
    "juli": "07",
    "august": "08",
    "september": "09",
    "oktober": "10",
    "november": "11",
    "dezember": "12",
}


def datum_zu_wikidata(deutsches_datum):
  match = re.search(
      r"([0-9]{1,2})\.\s+([äöüA-Za-z]+)\s+([0-9]{4})", deutsches_datum
  )
  if not match:
    return None
  tag = match.group(1).zfill(2)
  monat_name = match.group(2).lower()
  jahr = match.group(3)
  monat = MONATE.get(monat_name)
  if not monat:
    return None
  return f"+{jahr}-{monat}-{tag}T00:00:00Z/11"


def get_wiki_links(page_title):
  """Ruft alle verlinkten Wikipedia-Seiten von der Wartungsseite ab."""
  S = requests.Session()
  S.headers.update(HEADERS)
  URL = "https://de.wikipedia.org/w/api.php"
  PARAMS = {
      "action": "query",
      "titles": page_title,
      "prop": "links",
      "pllimit": "max",
      "plnamespace": 0,
      "format": "json",
  }
  titles = []
  while True:
    try:
      R = S.get(url=URL, params=PARAMS)
      R.raise_for_status()
      DATA = R.json()
    except Exception as e:
      st.error(f"Fehler beim Abrufen der Wikipedia-Links: {e}")
      break
    pages = DATA.get("query", {}).get("pages", {})
    for k, v in pages.items():
      if "links" in v:
        for link in v["links"]:
          titles.append(link["title"])
    if "continue" in DATA:
      PARAMS["plcontinue"] = DATA["continue"]["plcontinue"]
    else:
      break
  return titles


def check_wikidata_death_dates(wiki_titles):
  """Prüft via Wikidata, ob für die Wikipedia-Titel ein Sterbedatum (P570) vorhanden ist."""
  S = requests.Session()
  S.headers.update(HEADERS)
  url = "https://www.wikidata.org/w/api.php"
  batch_size = 50
  missing_death_date = []

  progress_text = st.empty()
  total_batches = (len(wiki_titles) + batch_size - 1) // batch_size
  current_batch = 0

  for i in range(0, len(wiki_titles), batch_size):
    current_batch += 1
    progress_text.text(
        f"Prüfe Wikidata-Batch {current_batch} von {total_batches}..."
    )

    batch = wiki_titles[i : i + batch_size]
    titles_str = "|".join(batch)
    params = {
        "action": "wbgetentities",
        "sites": "dewiki",
        "titles": titles_str,
        "props": "claims|sitelinks",
        "format": "json",
    }
    try:
      response = S.get(url=url, params=params)
      response.raise_for_status()
      data = response.json()
      entities = data.get("entities", {})
      for entity_id, entity_data in entities.items():
        if entity_id.startswith("Q"):
          claims = entity_data.get("claims", {})
          has_death_date = "P570" in claims
          sitelinks = entity_data.get("sitelinks", {})
          dewiki_title = (
              sitelinks.get("dewiki", {}).get("title")
              or entity_data.get("title")
              or entity_id
          )
          if not has_death_date:
            missing_death_date.append((entity_id, dewiki_title))
    except Exception as e:
      print(f"Fehler bei Wikidata: {e}")

  progress_text.empty()
  return missing_death_date


# Session-State initialisieren
if "batch_items" not in st.session_state:
  st.session_state.batch_items = []

# Eingabefeld für die Wartungsseite
page_name = st.text_input(
    "Wikipedia-Wartungsseite",
    value="Benutzer:CamelBot/maintenance_list/categories_of_the_dead",
)

if st.button("Wartungsliste einlesen & Daten extrahieren", type="primary"):
  with st.spinner("Lese verlinkte Seiten von Wikipedia ein..."):
    titles = get_wiki_links(page_name)

  st.info(f"Gefundene Links auf der Wartungsseite: {len(titles)}")

  if titles:
    missing_entries = check_wikidata_death_dates(titles)
    st.info(
        f"Davon Personen ohne Sterbedatum (P570) auf Wikidata:"
        f" {len(missing_entries)}"
    )

    st.session_state.batch_items = []
    api_url = "https://de.wikipedia.org/w/api.php"

    progress_bar = st.progress(0)
    total_missing = len(missing_entries)

    for idx, (qid, seiten_titel) in enumerate(missing_entries):
      parse_params = {
          "action": "parse",
          "page": seiten_titel,
          "prop": "text",
          "redirects": 1,
          "format": "json",
      }
      try:
        data = requests.get(api_url, params=parse_params, headers=HEADERS).json()
        if "error" in data:
          continue

        html_content = data["parse"]["text"]["*"]
        soup = BeautifulSoup(html_content, "html.parser")

        paragraphs = soup.find_all("p")
        lead_p = None
        for p in paragraphs:
          if len(p.get_text(strip=True)) > 30:
            lead_p = p
            break

        if not lead_p:
          continue

        lead_text = lead_p.get_text()
        match = re.search(
            r"[†\u2020]\s*([0-9]{1,2}\.\s+[äöüA-Za-z]+\s+[0-9]{4})", lead_text
        )
        if not match:
          continue

        roh_datum = match.group(1)
        wikidata_datum = datum_zu_wikidata(roh_datum)
        if not wikidata_datum:
          continue

        # Quell-URL extrahieren
        quellen_link = ""
        refs = lead_p.find_all("sup", class_="reference")
        for ref in refs:
          a_tag = ref.find("a")
          if a_tag and "href" in a_tag.attrs:
            ref_id = a_tag["href"].lstrip("#")
            ref_content = soup.find("li", id=ref_id)
            if ref_content:
              links = ref_content.find_all("a", href=True)
              for link in links:
                href = link["href"]
                if href.startswith("http"):
                  quellen_link = href
                  break
              if quellen_link:
                break

        if quellen_link:
          qs_code = f'{qid}|P570|{wikidata_datum}|S854|"{quellen_link}"'
        else:
          qs_code = f"{qid}|P570|{wikidata_datum}|S143|Q8447"

        st.session_state.batch_items.append({
            "titel": seiten_titel,
            "qid": qid,
            "datum": roh_datum,
            "qs": qs_code,
            "selected": True,
        })
      except Exception:
        continue

      if total_missing > 0:
        progress_bar.progress((idx + 1) / total_missing)

    progress_bar.empty()
    st.success(
        f"Fertig! {len(st.session_state.batch_items)} gültige Einträge mit"
        " Sterbedatum & Quelle gefunden."
    )

# Anzeige der Liste mit Checkboxen
if st.session_state.batch_items:
  st.markdown("---")
  st.subheader("Gefundene QuickStatements-Befehle (Auswahl)")

  selected_qs_list = []

  for idx, item in enumerate(st.session_state.batch_items):
    col1, col2 = st.columns([0.1, 0.9])
    with col1:
      is_checked = st.checkbox(
          "Auswahl",
          value=item["selected"],
          key=f"check_{idx}",
          label_visibility="collapsed",
      )
      st.session_state.batch_items[idx]["selected"] = is_checked
    with col2:
      st.markdown(
          f"**{item['titel']}** (`{item['qid']}`) – Sterbedatum:"
          f" {item['datum']}"
      )
      st.code(item["qs"], language="text")

    if is_checked:
      selected_qs_list.append(item["qs"])

  # Button zum Kopieren der ausgewählten Befehle
  if selected_qs_list:
    combined_qs = "\n".join(selected_qs_list)
    qs_url = "https://qs-dev.toolforge.org/batch/new/"

    safe_qs_code = (
        combined_qs.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )

    html_template = (
        '<div style="text-align: center; margin-top: 20px;">'
        '<button onclick="copyAndOpen()" style="background-color: #ff4b4b;'
        " color: white; padding: 14px 28px; border: none; border-radius: 6px;"
        ' font-size: 16px; cursor: pointer; font-weight: bold; box-shadow: 0'
        ' 2px 5px rgba(0,0,0,0.2);">'
        "📋 Ausgewählte QuickStatements kopieren & zu QS-Dev"
        "</button>"
        '<p id="status" style="margin-top: 10px; font-size: 14px; color:'
        ' green; font-weight: bold;"></p>'
        "</div>"
        "<script>"
        "function copyAndOpen() {"
        'const text = "REPLACE_CODE";'
        "navigator.clipboard.writeText(text).then(function() {"
        'document.getElementById("status").innerText = "✔ Erfolgreich'
        ' kopiert! Öffne QuickStatements...";'
        "setTimeout(function() {"
        'window.open("REPLACE_URL", "_blank");'
        "}, 500);"
        "}).catch(function(err) {"
        'alert("Fehler beim Kopieren in die Zwischenablage: " + err);'
        "});"
        "}"
        "</script>"
    )

    html_code = html_template.replace("REPLACE_CODE", safe_qs_code).replace(
        "REPLACE_URL", qs_url
    )
    components.html(html_code, height=100)
  else:
    st.warning("Bitte wähle mindestens einen Eintrag über die Checkboxen aus.")
