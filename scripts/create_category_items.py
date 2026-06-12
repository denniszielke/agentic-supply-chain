"""Seed the Azure AI Search category index with a canonical category taxonomy.

This script defines a balanced, retailer-agnostic category list derived from
observed German retail flyer extractions and the
full category list requested for this project.

For each category:
  - An embedding is generated from description_text using the configured
    Foundry embedding model (same client as promotion_ingestion/processor.py).
  - The document is upserted into the configured retail-categories index via
    merge_or_upload_documents.

Environment variables (same as processor.py):
  AZURE_SEARCH_ENDPOINT                  — required
  AZURE_SEARCH_ADMIN_KEY                 — optional; falls back to DefaultAzureCredential
  AZURE_SEARCH_CATEGORY_INDEX_NAME       — default: retail-categories
  AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME — required for vector embedding
  FOUNDRY_MODELS_ENDPOINT                — optional; derived from AZURE_AI_PROJECT_ENDPOINT
  FOUNDRY_MODELS_API_KEY                 — optional; falls back to DefaultAzureCredential
  AZURE_AI_PROJECT_ENDPOINT              — used to derive FOUNDRY_MODELS_ENDPOINT

Usage:
  python scripts/create_category_items.py
  python scripts/create_category_items.py --dry-run   # print categories, no upload
"""
from __future__ import annotations

import asyncio
import logging
import os
from urllib.parse import urlparse

from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical category taxonomy
# ---------------------------------------------------------------------------

CATEGORIES: list[dict] = [
    # ── Fresh produce ────────────────────────────────────────────────────────
    {
        "id": "obst",
        "category_id": "obst",
        "name": "Obst",
        "description_text": (
            "Frisches Obst umfasst ganze Früchte, Beeren, Steinobst, Kernobst, Zitrusfrüchte und Melonen, "
            "die typischerweise lose, im Netz, in Schalen oder als abgepackte Ware verkauft werden. "
            "Dazu gehören Äpfel, Birnen, Trauben, Erdbeeren, Himbeeren, Bananen, Orangen, Pfirsiche und Kirschen. "
            "Im Unterschied zu Gemüse steht bei Obst meist der süße, roh verzehrte Charakter im Vordergrund, "
            "als Snack, Dessert oder für Obstsalate. Saisonale Aktionsangebote und Bio-Ware gehören ebenfalls hierher."
        ),
        "semantic_tags": ["fruit", "obst", "fresh", "beeren", "kernobst", "steinobst", "saisonal", "bio"],
    },
    {
        "id": "gemuese",
        "category_id": "gemuese",
        "name": "Gemüse",
        "description_text": (
            "Frisches Gemüse umfasst Blatt-, Frucht-, Wurzel- und Kohlgemüse sowie Kräuter und Salate für Kochen und Rohverzehr. "
            "Dazu zählen Tomaten, Paprika, Gurken, Karotten, Brokkoli, Zucchini, Zwiebeln, Knoblauch, Salate und Kräuter, "
            "angeboten als lose Ware, Bund, Schale, Netz oder Beutel mit Herkunftsangaben und Qualitätsklassen. "
            "Gegenüber Obst ist Gemüse meist weniger süß und stärker auf herzhafte Zubereitung ausgerichtet. "
            "Bio-Produkte, regionale Ware und Pilze gehören ebenfalls in diese Kategorie."
        ),
        "semantic_tags": ["vegetables", "gemuese", "salat", "krauter", "bio", "regional", "frisch", "kohl"],
    },
    # ── Meat & poultry ───────────────────────────────────────────────────────
    {
        "id": "fleisch",
        "category_id": "fleisch",
        "name": "Fleisch",
        "description_text": (
            "Diese Kategorie umfasst frisches, mariniertes und küchenfertig vorbereitetes Fleisch von Rind, Schwein und Lamm "
            "für Pfanne, Grill und Ofen. Dazu zählen Steaks, Koteletts, Schnitzel, Bratenstücke, Spieße und SB-Trays "
            "in Schalen, Vakuumverpackungen oder MAP-Packungen mit Haltungsform- und Herkunftskennzeichnungen. "
            "Abzugrenzen ist diese Kategorie von Hackfleisch, Geflügel und Wurstwaren, da hier ganze Fleischzuschnitte im Vordergrund stehen. "
            "Marinierte Grillartikel und Aktionspacks gehören ebenfalls hierher."
        ),
        "semantic_tags": ["meat", "fleisch", "rind", "schwein", "grill", "steak", "frisch", "schnitzel"],
    },
    {
        "id": "hackfleisch",
        "category_id": "hackfleisch",
        "name": "Hackfleisch",
        "description_text": (
            "Hackfleisch umfasst gemischtes, reines Rinder- oder Schweinehackfleisch sowie Spezialvarianten wie Lammhack "
            "oder Geflügelhack für Burger, Frikadellen, Sauce Bolognese und Auflaufgerichte. "
            "Typische Verpackungen sind Schalen und Folienpackungen mit Gewichtsangaben von 400 g bis 1 kg sowie XXL-Packs. "
            "Häufig werden Haltungsform (1–5), Fettgehalt und Fleischanteil angegeben. "
            "Abzugrenzen ist diese Kategorie von ganzen Fleischstücken und Fertigprodukten auf Hackfleischbasis."
        ),
        "semantic_tags": ["minced meat", "hackfleisch", "burger", "bolognese", "rinderhack", "schweinehack", "frikadelle"],
    },
    {
        "id": "gefluegel",
        "category_id": "gefluegel",
        "name": "Geflügel",
        "description_text": (
            "Geflügel umfasst frische und marinierte Produkte von Hähnchen, Pute, Ente und anderen Geflügelarten "
            "wie Filets, Keulen, Hacksteaks, Spieße und Grillartikel in Schalen oder Beuteln. "
            "Typisch sind magere Zubereitungen für Pfanne, Ofen und Grill; Marken reichen von Handelsmarken bis zu regionalen Erzeugnissen. "
            "Haltungsform- und Herkunftskennzeichnungen sind häufige Qualitätsmerkmale. "
            "Abzugrenzen ist Geflügel von Schweine- und Rindfleisch sowie von verarbeiteten Geflügelwurstwaren."
        ),
        "semantic_tags": ["poultry", "gefluegel", "haehnchen", "pute", "grill", "filet", "frisch"],
    },
    {
        "id": "wurstwaren",
        "category_id": "wurstwaren",
        "name": "Wurst & Aufschnitt",
        "description_text": (
            "Wurst- und Aufschnittwaren sind verarbeitete Fleischprodukte wie Kochschinken, Salami, Bratwurst, Leberwurst, "
            "Fleischwurst, Wiener Würstchen und Lyoner in Scheiben, Stücken oder als ganze Würste. "
            "Typische Verpackungen sind Vakuumpackungen, Schlauchbeutel, Gläser, Dosen oder SB-Scheibenpackungen "
            "von regionalen Fleischwarenmarken und Handelsmarken. "
            "Gegenüber frischem Fleisch sind Wurstwaren bereits gegart, geräuchert oder fermentiert und direkt verzehrfertig. "
            "Grillwürstchen, Snackwürste und Aufschnitt für Brotbelag gehören ebenfalls hierher."
        ),
        "semantic_tags": ["sausage", "wurst", "aufschnitt", "schinken", "salami", "bratwurst", "aufschnitt", "verarbeitet"],
    },
    {
        "id": "fisch",
        "category_id": "fisch",
        "name": "Fisch",
        "description_text": (
            "Diese Kategorie umfasst frischen, gekühlten und tiefgekühlten Fisch sowie Fischkonserven für Kochen und Direktverzehr. "
            "Dazu gehören Lachsfilets, Forellen, Pangasius, Hering, Thunfisch, Sardinen, panierten Fisch und Fischstäbchen "
            "in Schalen, Vakuumpackungen, Dosen oder Tiefkühlverpackungen. "
            "Typische Marken sind Eigenmarken und bekannte Fischmarken; Produkte werden für Ofen, Pfanne oder Grill angeboten. "
            "Abzugrenzen ist diese Kategorie von Fleisch, Geflügel und Fertiggerichten, bei denen Fisch nur Zutat ist."
        ),
        "semantic_tags": ["fish", "fisch", "lachs", "thunfisch", "hering", "filet", "meerestier", "tiefkuehl", "konserve"],
    },
    {
        "id": "meeresfruechte",
        "category_id": "meeresfruechte",
        "name": "Meeresfrüchte",
        "description_text": (
            "Meeresfrüchte umfassen Garnelen, Shrimps, Muscheln, Tintenfisch, Scampi und ähnliche Schalentiere und Weichtiere "
            "aus dem Meer, angeboten frisch, gefroren oder tiefgekühlt in Schalen, Beuteln oder Packungen. "
            "Typische Produkte sind Kochgarnelen, Tigergarnelen, Miesmuscheln und Surimi in verschiedenen Gewichtseinheiten. "
            "Diese Kategorie grenzt sich von Fisch dadurch ab, dass es sich um Schalen- und Weichtiere handelt, nicht um Fische. "
            "Fertige Meeresfrüchtecocktails und Meeresfrüchtemischungen gehören ebenfalls hierher."
        ),
        "semantic_tags": ["seafood", "garnelen", "shrimps", "muscheln", "tintenfisch", "scampi", "meerestier"],
    },
    # ── Dairy & eggs ─────────────────────────────────────────────────────────
    {
        "id": "kaese",
        "category_id": "kaese",
        "name": "Käse",
        "description_text": (
            "Käse umfasst Schnittkäse, Weichkäse, Frischkäse, Hartkäse, Reibekäse, Schmelzkäse und Käsezubereitungen "
            "in Stücken, Scheiben, Bechern oder Folienpackungen von Molkerei- und Handelsmarken. "
            "Typische Produkte sind Gouda, Edamer, Emmentaler, Brie, Camembert, Mozzarella, Feta und Parmesan "
            "für Brotbelag, Überbacken, Kochen oder als Snack. "
            "Gegenüber Milchprodukten ist Käse ein gereiftes, würziges oder salziges Produkt mit festerer Konsistenz. "
            "Auch Grillkäse, Käse-Snacks und internationale Spezialitäten gehören hierher."
        ),
        "semantic_tags": ["cheese", "kaese", "gouda", "mozzarella", "frischkaese", "hartkase", "weichkaese", "grillen"],
    },
    {
        "id": "milchprodukte",
        "category_id": "milchprodukte",
        "name": "Milchprodukte",
        "description_text": (
            "Milchprodukte umfassen Trinkmilch, Sahne, Buttermilch, Kefir, Crème fraîche und ähnliche flüssige oder cremige "
            "Erzeugnisse aus Milch in Flaschen, Kartons oder Bechern von Molkereimarken und Handelsmarken. "
            "Diese Kategorie deckt Grundmilchprodukte für Kochen, Backen und Trinken ab und grenzt sich von Joghurt, Quark "
            "und Käse durch den geringeren Verarbeitungsgrad und flüssigere Konsistenz ab. "
            "Auch laktosefreie Milch, Haltbarmilch und proteinreiche Trinkprodukte gehören hierher."
        ),
        "semantic_tags": ["dairy", "milch", "sahne", "butter", "trinkmilch", "kefir", "buttermilch", "laktosefrei"],
    },
    {
        "id": "joghurt",
        "category_id": "joghurt",
        "name": "Joghurt & Quark",
        "description_text": (
            "Diese Kategorie umfasst Joghurt, Quark, Skyr, griechischen Joghurt, Pudding und Dessertbecher "
            "als gekühlte, löffelbare Milchprodukte für Frühstück, Snack und Nachtisch. "
            "Typische Produkte sind Naturjoghurt, Fruchtjoghurt, Magerquark, Speisequark, Pudding und Grießdesserts "
            "in Bechern, Multipacks und Portionspackungen bekannter Molkereimarken und Eigenmarken. "
            "Gegenüber Trinkmilch sind diese Produkte löffelbar und stärker fermentiert oder eingedickt. "
            "Proteinreiche Varianten wie Skyr und Hüttenkäse gehören ebenfalls hierher."
        ),
        "semantic_tags": ["yogurt", "joghurt", "quark", "skyr", "dessert", "pudding", "frühstück", "becher"],
    },
    {
        "id": "eier",
        "category_id": "eier",
        "name": "Eier",
        "description_text": (
            "Diese Kategorie umfasst Hühner-, Wachtel- und andere Eier in verschiedenen Größen und Haltungsformen "
            "für Kochen, Backen und Frühstück. "
            "Typische Verpackungen sind 6er, 10er und 12er Packs mit Kennzeichnungen zu Haltungsform (0–3), "
            "Herkunft und Mindesthaltbarkeit von Eigenmarken und Markenherstellern. "
            "Abzugrenzen sind Eier von Milchprodukten; sie sind ein eigenständiges tierisches Grundnahrungsmittel. "
            "Bio-Eier, Freilandeier und regionale Eier sind häufig beworbene Kategorisierungen."
        ),
        "semantic_tags": ["eggs", "eier", "hühner", "bio", "freiland", "haltungsform", "kochen", "backen"],
    },
    {
        "id": "pflanzliche-alternativen",
        "category_id": "pflanzliche-alternativen",
        "name": "Pflanzliche Alternativen",
        "description_text": (
            "Pflanzliche Alternativen umfassen vegane und vegetarische Ersatzprodukte für Milch, Joghurt, Käse, Fleisch und Ei "
            "auf Basis von Hafer, Soja, Mandel, Erbse, Kokos oder anderen pflanzlichen Rohstoffen. "
            "Typische Produkte sind Hafermilch, Sojajoghurt, veganer Aufschnitt, Tofu, Tempeh, veganer Käse und pflanzliche Kochcremes "
            "in Kartons, Bechern, Beuteln oder Schalen von Plant-Based-Marken und Handelsmarken. "
            "Diese Kategorie richtet sich an vegane, vegetarische und laktoseintolerante Verbraucher. "
            "Abzugrenzen ist sie von klassischen Milchprodukten, da keine tierische Milch verwendet wird."
        ),
        "semantic_tags": ["vegan", "pflanzlich", "hafer", "soja", "tofu", "lactosefrei", "vegetarisch", "plant-based"],
    },
    # ── Bakery & frozen ──────────────────────────────────────────────────────
    {
        "id": "backwaren",
        "category_id": "backwaren",
        "name": "Backwaren",
        "description_text": (
            "Backwaren umfassen Brot, Brötchen, Baguettes, Laugengebäck, Croissants, Toast, Knäckebrot und Kuchen "
            "für Frühstück, Brotzeit und Snack, frisch oder verpackt. "
            "Typische Verpackungen sind Beutel, Kartons, Folienpackungen oder lose Ware; Marken reichen von Bäckereilinien "
            "bis zu Handelsmarken mit Frische- und Aufbackhinweisen. "
            "Abzugrenzen ist diese Kategorie von Süßwaren durch den klaren Teigwaren- und Brotharakter. "
            "Auch Aufbackbrötchen, vorgefertigte Teiglinge und glutenfreie Varianten gehören hierher."
        ),
        "semantic_tags": ["bakery", "brot", "brötchen", "baguette", "toast", "kuchen", "laugenbrot", "frisch"],
    },
    {
        "id": "tiefkuehlkost",
        "category_id": "tiefkuehlkost",
        "name": "Tiefkühlkost",
        "description_text": (
            "Tiefkühlkost umfasst tiefgekühlte Lebensmittel wie Gemüse, Fisch, Fleisch, Backwaren, Snacks und Fertiggerichte "
            "aus dem Gefrierbereich für längere Lagerung und bequeme Zubereitung. "
            "Typische Verpackungen sind Beutel, Schalen, Kartons und Becher; Produkte werden für Ofen, Pfanne oder Mikrowelle angeboten. "
            "Gegenüber gekühlten Frischeprodukten zeichnet sich Tiefkühlkost durch Lagerung bei Minusgraden und portionsweise Entnahme aus. "
            "Diese Kategorie schließt tiefgekühltes Gemüse, TK-Fisch, TK-Backwaren und Convenience-Produkte ein."
        ),
        "semantic_tags": ["frozen", "tiefkuehl", "tk", "gefroren", "convenience", "mikrowelle", "ofen", "vorrat"],
    },
    {
        "id": "pizza",
        "category_id": "pizza",
        "name": "Pizza & Flammkuchen",
        "description_text": (
            "Diese Kategorie umfasst tiefgekühlte und backfrische Pizzen, Flammkuchen, Calzone und ähnliche Ofenprodukte "
            "auf Teigbasis mit Belägen wie Tomate, Käse, Salami, Gemüse oder Schinken. "
            "Typische Marken sind Dr. Oetker, Wagner, Buitoni und Eigenmarken; Verpackungen sind Kartons in Einzel- oder Familiengrößen. "
            "Abzugrenzen ist diese Kategorie von allgemeiner Tiefkühlkost durch das charakteristische Pizza-Format und die Belagsvielfalt. "
            "Auch Snack-Pizzen, Pizzabrötchen und Minipizzen gehören hierher."
        ),
        "semantic_tags": ["pizza", "flammkuchen", "tiefkuehl", "ofen", "salami", "vegetarisch", "wagner", "dr-oetker"],
    },
    {
        "id": "fertiggerichte",
        "category_id": "fertiggerichte",
        "name": "Fertiggerichte",
        "description_text": (
            "Fertiggerichte umfassen verzehrfertige oder nur kurz aufzuwärmende Mahlzeiten wie Suppen, Eintöpfe, Pasta-Gerichte, "
            "Reispfannen, Currys, asiatische Gerichte und Instantnudeln in Gläsern, Schalen, Beuteln oder Kartons. "
            "Typische Marken sind Knorr, Maggi, Iglo und Eigenmarken; Produkte für Mikrowelle, Herd oder heißes Wasser. "
            "Abzugrenzen ist diese Kategorie von Tiefkühlkost durch die Frische- oder Regalware-Form und von Pizza durch das breitere Gerichteformat. "
            "Auch Cup Noodles, Instantsuppen und Convenience-Schalen gehören hierher."
        ),
        "semantic_tags": ["ready meal", "fertiggericht", "suppe", "eintopf", "convenience", "mikrowelle", "instant", "pasta"],
    },
    # ── Snacks & confectionery ───────────────────────────────────────────────
    {
        "id": "snacks",
        "category_id": "snacks",
        "name": "Snacks & Knabberartikel",
        "description_text": (
            "Snacks und Knabberartikel umfassen Chips, Flips, Cracker, Salzgebäck, Nüsse, Studentenfutter, Popcorn "
            "und ähnliche herzhafte Zwischenmahlzeiten in Beuteln, Tüten oder kleinen Multipacks. "
            "Typische Marken sind Lay's, Funny-Frisch, Pringles und Eigenmarken; Produkte für Sofortverzehr und Partys. "
            "Abzugrenzen ist diese Kategorie von Süßwaren durch den herzhaft-salzigen Charakter und von Backwaren durch fehlenden Brotharakter. "
            "Auch internationale Snackspezialitäten und gewürzte Varianten gehören hierher."
        ),
        "semantic_tags": ["snacks", "chips", "cracker", "nuesse", "salzgebäck", "knabbern", "party", "popcorn"],
    },
    {
        "id": "suesswaren",
        "category_id": "suesswaren",
        "name": "Süßwaren & Konfekt",
        "description_text": (
            "Süßwaren umfassen Bonbons, Fruchtgummi, Kaugummi, Waffeln, Kekse, Müslibarren und allgemeine Naschprodukte "
            "abseits von reiner Schokolade und Eis. Typische Marken sind Haribo, Storck, Bahlsen und Eigenmarken "
            "in Beuteln, Rollen, Schachteln und Geschenkverpackungen. "
            "Gegenüber Schokolade dominieren Zuckerwaren, Gummiwaren und Keksprodukte. "
            "Auch saisonale Aktionsartikel wie Adventskalender oder Osterformen gehören hierher."
        ),
        "semantic_tags": ["sweets", "bonbons", "gummi", "haribo", "kekse", "waffeln", "suessigkeiten", "konfekt"],
    },
    {
        "id": "schokolade",
        "category_id": "schokolade",
        "name": "Schokolade & Pralinen",
        "description_text": (
            "Diese Kategorie umfasst Schokoladentafeln, Pralinen, Schokoriegel, Trüffel, Konfektboxen und Schokoladenspezialitäten "
            "von Marken wie Milka, Ritter Sport, Lindt, Ferrero und Eigenmarken. "
            "Typische Verpackungen sind Tafeln, Schachteln, Dosen, Riegel und Geschenkboxen für alle Anlässe. "
            "Gegenüber allgemeinen Süßwaren steht hier der Kakao- und Schokoladencharakter im Mittelpunkt. "
            "Auch weiße Schokolade, Zartbitterschokolade, Nougat und gefüllte Pralinen gehören hierher."
        ),
        "semantic_tags": ["chocolate", "schokolade", "pralinen", "milka", "ferrero", "riegel", "konfekt", "kakaoanteil"],
    },
    {
        "id": "eis",
        "category_id": "eis",
        "name": "Eis & Speiseeis",
        "description_text": (
            "Eis und Speiseeis umfassen Eiscreme, Wassereis, Gelato, Sorbet, Eisbecher und Eisriegel "
            "in Bechern, Boxen, am Stiel oder als Multipack von Marken wie Magnum, Langnese, Ben & Jerry's und Eigenmarken. "
            "Typische Produkte sind Familienpackungen für den Heimgebrauch und Portionseis für den Sofortverzehr. "
            "Abzugrenzen ist diese Kategorie von Tiefkühlkost durch den spezifischen Dessertzweck und Süßecharakter. "
            "Auch vegane Eisvarianten auf pflanzlicher Basis gehören hierher."
        ),
        "semantic_tags": ["ice cream", "eis", "speiseeis", "gelato", "magnum", "sorbet", "becher", "tiefkuehl"],
    },
    {
        "id": "muesli",
        "category_id": "muesli",
        "name": "Müsli & Frühstücksprodukte",
        "description_text": (
            "Diese Kategorie umfasst Müsli, Granola, Cerealien, Cornflakes, Haferflocken, Porridge und ähnliche Frühstücksprodukte "
            "in Kartons, Beuteln und Gläsern von Marken wie Kellogg's, Dr. Oetker, Seitenbacher und Handelsmarken. "
            "Typische Merkmale sind Ballaststoffgehalt, Zuckergehalt, Nuss- und Trockenfrüchteanteil und Vollkorncharakter. "
            "Gegenüber Backwaren steht hier der trockene Getreidekost-Charakter für Frühstück mit Milch oder Joghurt im Vordergrund. "
            "Auch Müslibarren, Schokocerealien und glutenfreie Varianten gehören hierher."
        ),
        "semantic_tags": ["muesli", "cerealien", "granola", "haferflocken", "cornflakes", "frühstück", "vollkorn"],
    },
    # ── Beverages ────────────────────────────────────────────────────────────
    {
        "id": "kaffee",
        "category_id": "kaffee",
        "name": "Kaffee",
        "description_text": (
            "Kaffee umfasst gemahlenen Kaffee, ganze Bohnen, Instantkaffee, Kaffeepads und Kapseln "
            "für Filterkaffeemaschinen, Vollautomaten, Siebträger und Portionssysteme wie Nespresso oder Dolce Gusto. "
            "Typische Marken sind Jacobs, Dallmayr, Lavazza, Tchibo und Handelsmarken in Packungen, Beuteln, Dosen und Kapselboxen. "
            "Abzugrenzen ist Kaffee von trinkfertigen Kaffeegetränken, Kakao und Tee, da hier das Rohprodukt zur Zubereitung im Vordergrund steht. "
            "Espresso, Ristretto und Spezialröstungen gehören ebenfalls hierher."
        ),
        "semantic_tags": ["kaffee", "coffee", "espresso", "kapseln", "bohnen", "gemahlen", "filterkaffee", "jacobs"],
    },
    {
        "id": "tee",
        "category_id": "tee",
        "name": "Tee & Kräutertee",
        "description_text": (
            "Diese Kategorie umfasst Schwarz-, Grün-, Kräuter-, Früchte- und Funktionstees in Teebeuteln, losem Tee, "
            "Instanttee und Ready-to-drink-Formaten von Marken wie Teekanne, Meßmer, Lipton und Handelsmarken. "
            "Typische Produktmerkmale sind Aromarichtung, Koffeingehalt, Ziehzeit und Bio-Zertifizierung. "
            "Abzugrenzen ist Tee von Kaffee und Kakao durch die pflanzliche Kräuter- oder Teebasis ohne Kaffeebohnen. "
            "Auch Kombucha-Tee, Matcha und aromatisierte Teeblends gehören hierher."
        ),
        "semantic_tags": ["tea", "tee", "kraeutertee", "fruechtetee", "gruener-tee", "schwarztee", "teekanne", "bio"],
    },
    {
        "id": "kakao",
        "category_id": "kakao",
        "name": "Kakao & Heißgetränkepulver",
        "description_text": (
            "Kakao und Heißgetränkepulver umfassen Trinkschokolade, Kakaopulver, Malzgetränkepulver, "
            "Cappuccinopulver und ähnliche Pulvermischungen für warme Milch- und Wassergetränke. "
            "Typische Marken sind Kaba, Nesquik, Ovomaltine und Eigenmarken in Dosen, Tüten und Kartons. "
            "Gegenüber Kaffee und Tee dominiert hier der Schokoladen- oder Malzgeschmack; "
            "Produkte sind oft auf Kinder und Frühstücksrituale ausgerichtet. "
            "Auch Instant-Cappuccino, Chai Latte Pulver und heiße Schokolade gehören hierher."
        ),
        "semantic_tags": ["kakao", "trinkschokolade", "nesquik", "malz", "cappuccino-pulver", "heissgetränk", "kinder"],
    },
    {
        "id": "mineralwasser",
        "category_id": "mineralwasser",
        "name": "Mineralwasser & Wasser",
        "description_text": (
            "Mineralwasser und Trinkwasser umfassen stilles, leicht und stark kohlensäurehaltiges Mineralwasser, "
            "Quellwasser und Tafelwasser in Flaschen, Kästen und Multipacks von Marken wie Volvic, Evian, Gerolsteiner und Eigenmarken. "
            "Typische Verpackungen sind PET-Flaschen und Glasflaschen in 0,5-l-, 1,0-l- und 1,5-l-Größen mit Mehrwegpfand. "
            "Abzugrenzen ist diese Kategorie von aromatisierten Getränken, Säften und Sportdrinks. "
            "Auch Leitungswasserfilterprodukte und Wassersprudler-Patronen können hierzu gezählt werden."
        ),
        "semantic_tags": ["water", "mineralwasser", "wasser", "stilles-wasser", "kohlensaeure", "volvic", "evian", "pfand"],
    },
    {
        "id": "getraenke-alkoholfrei",
        "category_id": "getraenke-alkoholfrei",
        "name": "Alkoholfreie Getränke",
        "description_text": (
            "Alkoholfreie Getränke umfassen Säfte, Limonaden, Cola, Fanta, Eistee, Schorlen, Energy-Drinks, Sportgetränke "
            "und Multivitaminsäfte in Flaschen, Dosen, Kartons und Multipacks. "
            "Typische Marken sind Coca-Cola, Fanta, Sprite, Innocent, Hohes C und Handelsmarken in verschiedenen Größen. "
            "Gegenüber Mineralwasser enthält diese Kategorie Getränke mit Aroma, Fruchtsaft oder Zuckerzusatz. "
            "Auch zuckerfreie Varianten, Bio-Säfte und frisch gepresste Säfte gehören hierher."
        ),
        "semantic_tags": ["soft drinks", "limonade", "saft", "cola", "eistee", "energy-drink", "schorle", "alkoholfrei"],
    },
    {
        "id": "bier",
        "category_id": "bier",
        "name": "Bier & Biermischgetränke",
        "description_text": (
            "Diese Kategorie umfasst Pils, Export, Helles, Weizenbier, Radler, Biermischgetränke und alkoholfreies Bier "
            "von nationalen und regionalen Brauereien in Flaschen, Dosen, Sixpacks und Kästen mit Pfandhinweisen. "
            "Typische Marken sind Bitburger, Krombacher, Beck's, Warsteiner und regionale Brauereien. "
            "Abzugrenzen ist Bier von Wein, Spirituosen und alkoholfreien Getränken durch den Brauprozess und Gärungscharakter. "
            "Aktionsangebote mit Kastenpreisen und Mehrweggebinden sind häufige Vermarktungsformen."
        ),
        "semantic_tags": ["beer", "bier", "pils", "weizenbier", "alkoholfrei", "kasten", "brauerei", "radler"],
    },
    {
        "id": "wein",
        "category_id": "wein",
        "name": "Wein & Sekt",
        "description_text": (
            "Wein und Sekt umfassen Rot-, Weiß- und Roséwein, Sekt, Prosecco, Champagner und Secco "
            "aus deutschen und internationalen Anbaugebieten in Glasflaschen verschiedener Größen. "
            "Typische Produktmerkmale sind Rebsorte, Herkunftsangabe (AOC, QbA), Trocken-/Süßegrad und Jahrgang. "
            "Abzugrenzen ist diese Kategorie von Spirituosen durch den Gär- statt Destillationsprozess. "
            "Auch Bag-in-Box-Wein, Weinsets und alkoholfreier Wein gehören hierher."
        ),
        "semantic_tags": ["wine", "wein", "sekt", "prosecco", "rotwein", "weisswein", "rebsorte", "jahrgang"],
    },
    {
        "id": "spirituosen",
        "category_id": "spirituosen",
        "name": "Spirituosen & Liköre",
        "description_text": (
            "Spirituosen und Liköre umfassen Wodka, Gin, Rum, Whisky, Cognac, Tequila, Kräuterliköre und Fruchtbrände "
            "mit hohem Alkoholgehalt für Cocktails, Longdrinks und puren Genuss. "
            "Typische Marken sind Jägermeister, Jack Daniel's, Baileys und Premium-Destillerien in Glasflaschen 0,35–1,0 l. "
            "Abzugrenzen ist diese Kategorie von Bier und Wein durch den deutlich höheren Alkoholgehalt durch Destillation. "
            "Auch aromatisierte Schnäpse, Bitters und Cocktailbitter gehören hierher."
        ),
        "semantic_tags": ["spirits", "spirituosen", "wodka", "gin", "whisky", "rum", "likör", "cocktail", "destillat"],
    },
    # ── Dry goods & pantry ───────────────────────────────────────────────────
    {
        "id": "nudeln",
        "category_id": "nudeln",
        "name": "Nudeln & Teigwaren",
        "description_text": (
            "Nudeln und Teigwaren umfassen Spaghetti, Penne, Fusilli, Farfalle, Lasagneplatten, Spätzle, Gnocchi "
            "und asiatische Nudeln aus Hartweizen, Dinkel, Vollkorn oder glutenfreien Alternativmehlen. "
            "Typische Marken sind Barilla, De Cecco, Eigenmarken und regionale Hersteller in Paketen von 400 g bis 1 kg. "
            "Diese Kategorie grenzt sich von Fertiggerichten ab, da hier das Basisprodukt zum Kochen verkauft wird. "
            "Auch frische Kühlregal-Nudeln, Eiernudeln und Pasta-Sets gehören hierher."
        ),
        "semantic_tags": ["pasta", "nudeln", "spaghetti", "teigwaren", "barilla", "gnocchi", "spaetzle", "vollkorn"],
    },
    {
        "id": "saucen-dips",
        "category_id": "saucen-dips",
        "name": "Saucen, Dips & Dressings",
        "description_text": (
            "Diese Kategorie umfasst fertige Saucen, Dips, Dressings und Würzsaucen wie Ketchup, Mayonnaise, Senf, "
            "Hollandaise, Barbecue-Sauce, Salsa, Pesto und Salatdressings in Gläsern, Flaschen, Tuben oder Beuteln. "
            "Typische Marken sind Heinz, Thomy, Knorr, Homann und Eigenmarken; Produkte begleiten Hauptgerichte oder Snacks. "
            "Abzugrenzen von Fertiggerichten, da diese Produkte als Begleitung oder Würzung dienen, nicht als vollständige Mahlzeit. "
            "Auch Grillmarinaden, Würzpasten und internationale Dipsaucen gehören hierher."
        ),
        "semantic_tags": ["sauce", "dip", "dressing", "ketchup", "mayo", "senf", "pesto", "bbq", "wuerze"],
    },
    {
        "id": "olivenoel-und-essig",
        "category_id": "olivenoel-und-essig",
        "name": "Öle, Olivenöl & Essig",
        "description_text": (
            "Diese Kategorie umfasst Speiseöle, Olivenöl, Sonnenblumenöl, Rapsöl, Essig und Balsamico "
            "für Salate, Kochen, Braten und Marinieren in Flaschen, Kanistern und Gläsern. "
            "Typische Marken sind Bertolli, Filippo Berio, Mazola und Eigenmarken; Premium-Olivenöl wird oft nach Herkunft und Kaltpressung beworben. "
            "Abzugrenzen von Saucen und Dressings, da diese Produkte reine Zutaten ohne weitere Bestandteile sind. "
            "Auch Kokosnussöl, Sesamöl, Trüffelöl und Kräuteressig gehören hierher."
        ),
        "semantic_tags": ["oil", "essig", "olivenoel", "rapsoel", "balsamico", "kochen", "salat", "braten"],
    },
    {
        "id": "fruchtaufstrich",
        "category_id": "fruchtaufstrich",
        "name": "Fruchtaufstrich, Konfitüre & Honig",
        "description_text": (
            "Fruchtaufstriche umfassen Konfitüre, Marmelade, Gelee, Fruchtaufstrich, Honig, Nuss-Nougat-Cremes "
            "und Erdnussbutter für Brot, Brötchen und Frühstücksrezepte in Gläsern verschiedener Größen. "
            "Typische Marken sind Schwartau, Zentis, Bonne Maman, Nutella und Eigenmarken; Bio-Varianten mit hohem Fruchtgehalt sind beliebt. "
            "Abzugrenzen von Saucen und Dips durch den klaren Frühstücksaufstrich-Charakter. "
            "Auch zuckerreduzierte Varianten, Fruchtpürees und Crunchy-Peanut-Butter gehören hierher."
        ),
        "semantic_tags": ["jam", "konfituere", "honig", "marmelade", "nutella", "erdnussbutter", "fruchtaufstrich", "frühstück"],
    },
    {
        "id": "feinkost",
        "category_id": "feinkost",
        "name": "Feinkost & Delikatessen",
        "description_text": (
            "Feinkost und Delikatessen umfassen Antipasti, Pasteten, eingelegte Spezialitäten, Tapenaden, Aufstriche, "
            "Räucherfisch, Meeresfrüchtekonserven und besondere Brotbeläge für Genussmomente und Käseplatten. "
            "Typische Verpackungen sind Gläser, Dosen, kleine Schalen und Portionspackungen mit Herkunfts- und Genussbezug. "
            "Gegenüber Standardkonserven ist hier der Spezialitäten- und Qualitätscharakter prägend. "
            "Auch regionale Delikatessen, Terrinen, Wildspezialitäten und internationale Feinkostprodukte gehören hierher."
        ),
        "semantic_tags": ["feinkost", "delikatessen", "antipasti", "pastete", "raeucherfisch", "spezialität", "käseplatte"],
    },
    # ── Household ────────────────────────────────────────────────────────────
    {
        "id": "haushaltswaren",
        "category_id": "haushaltswaren",
        "name": "Haushaltswaren & Zubehör",
        "description_text": (
            "Haushaltswaren umfassen Aufbewahrungsboxen, Organizer, Müllbeutel, Folien, Backpapier, Alufolie, Wäscheklammern, "
            "Schwämme, Bürsten und sonstige Verbrauchsartikel für Küche, Bad und Haushalt. "
            "Typische Marken sind Toppits, Melitta, Eigenmarken und Drogeriemarken in Packungen, Rollen und Sets. "
            "Abzugrenzen von Elektrogeräten und Küchenartikeln durch den Non-Food-Verbrauchsartikelcharakter. "
            "Auch Einweggeschirr, Aluschalen und Partygeschirr gehören hierher."
        ),
        "semantic_tags": ["household", "haushalt", "aufbewahrung", "muellbeutel", "alufolie", "bürsten", "verbrauch"],
    },
    {
        "id": "kuechen-artikel",
        "category_id": "kuechen-artikel",
        "name": "Küchenartikel & Kochzubehör",
        "description_text": (
            "Küchenartikel umfassen Töpfe, Pfannen, Schneidbretter, Messer, Küchenutensilien, Backformen, Messbecher "
            "und Küchenhelfer für Kochen, Backen und Lebensmittelzubereitung. "
            "Typische Marken sind Tefal, WMF, Silit und Eigenmarken; Produkte aus Edelstahl, Kunststoff, Keramik und Silikon. "
            "Abzugrenzen von Elektrogeräten, die eine Stromversorgung benötigen, und von allgemeinen Haushaltswaren. "
            "Auch Grillzubehör, Küchen-Sets und Spezialwerkzeug für die Küche gehören hierher."
        ),
        "semantic_tags": ["kueche", "kochen", "topf", "pfanne", "messer", "backform", "tefal", "wmf", "kochzubehör"],
    },
    {
        "id": "waschmittel",
        "category_id": "waschmittel",
        "name": "Waschmittel & Reinigungsmittel",
        "description_text": (
            "Diese Kategorie umfasst Waschmittel, Weichspüler, Geschirrreiniger, Spülmaschinentabs, Allzweckreiniger, "
            "WC-Reiniger, Badreiniger, Entkalker und Haushaltsreinigungsprodukte in Flaschen, Pulverkartons, Tabs und Nachfüllformaten. "
            "Typische Marken sind Persil, Ariel, Pril, Fairy und Eigenmarken für verschiedene Anwendungsbereiche. "
            "Abzugrenzen von Körperpflegeprodukten durch den Fokus auf Textil- und Oberflächenreinigung. "
            "Auch Scheuermilch, Edelstahlreiniger und Wäscheduftprodukte gehören hierher."
        ),
        "semantic_tags": ["detergent", "waschmittel", "reiniger", "spülmittel", "persil", "ariel", "tabs", "haushalt"],
    },
    {
        "id": "koerperpflege",
        "category_id": "koerperpflege",
        "name": "Körperpflege & Hygiene",
        "description_text": (
            "Körperpflege und Hygiene umfassen Duschgel, Shampoo, Conditioner, Bodylotion, Seife, Deodorant, Rasierbedarf, "
            "Zahnpflege, Mundpflege, Hygieneartikel und Periodenprodukte für die tägliche persönliche Pflege. "
            "Typische Marken sind Nivea, Dove, Pantene, Colgate, Gillette und Eigenmarken in Flaschen, Tuben, Sprays und Sticks. "
            "Abzugrenzen von Waschmitteln durch den Fokus auf den menschlichen Körper, nicht auf Oberflächen oder Textilien. "
            "Auch Pflegecreme, Sonnenschutz und dekorative Kosmetik gehören in diese breite Kategorie."
        ),
        "semantic_tags": ["personal care", "körperpflege", "shampoo", "duschgel", "deo", "zahnpflege", "nivea", "hygiene"],
    },
    {
        "id": "kosmetik",
        "category_id": "kosmetik",
        "name": "Dekorative Kosmetik & Make-up",
        "description_text": (
            "Dekorative Kosmetik und Make-up umfassen Foundation, Concealer, Puder, Rouge, Lippenstift, Lipgloss, "
            "Mascara, Eyeliner, Lidschatten, Highlighter und Nagellack für das tägliche Schminken und besondere Anlässe. "
            "Typische Marken sind L'Oréal, Maybelline, Catrice, Essence und dm-Eigenmarken in verschiedenen Farb- und Texturvarianten. "
            "Abzugrenzen von Gesichtspflege, die pflegende Inhaltsstoffe ohne Farbpigmente bietet, und von allgemeiner Körperpflege. "
            "Auch Make-up-Entferner, Pinsel-Sets, Beauty-Tools und Nagelpflege-Produkte gehören hierher."
        ),
        "semantic_tags": ["makeup", "kosmetik", "lippenstift", "mascara", "foundation", "loreal", "maybelline", "schminke"],
    },
    {
        "id": "windeln",
        "category_id": "windeln",
        "name": "Windeln & Babycare",
        "description_text": (
            "Windeln und Babycare umfassen Einweg- und Stoffwindeln, Windeleinlagen, Feuchttücher, Babypflege-Sets, "
            "Babyöl, Babycreme, Wund- und Heilsalben sowie Hygieneartikel für Säuglinge und Kleinkinder. "
            "Typische Marken sind Pampers, Huggies, dm Babylove und Eigenmarken in Größen Newborn bis Größe 6 mit Komfort- und Trockenheitskennzeichnungen. "
            "Abzugrenzen von allgemeiner Körperpflege durch die speziell auf empfindliche Babyhaut abgestimmten Formulierungen. "
            "Auch Babytücher, Windeleimer, Lauflernwindeln und Bio-Windeln gehören hierher."
        ),
        "semantic_tags": ["diapers", "windeln", "pampers", "huggies", "baby", "feuchttücher", "babypflege", "kleinkind"],
    },
    {
        "id": "zahnpflege",
        "category_id": "zahnpflege",
        "name": "Zahnpflege & Mundpflege",
        "description_text": (
            "Zahnpflege und Mundpflege umfassen Zahnpasta, Zahnbürsten, elektrische Zahnbürsten, Zahnersatz-Haftklebstoffe, "
            "Mundwasser, Zahnseide, Interdentalbürsten und Mundspülungen für die tägliche Mundhygiene. "
            "Typische Marken sind Colgate, Oral-B, Sensodyne, Elmex und Eigenmarken mit Schwerpunkten auf Whitening, Kariesschutz und Empfindlichkeit. "
            "Abzugrenzen von allgemeiner Körperpflege durch den spezifischen Mund- und Zahngesundheitsbezug. "
            "Auch Kinderzahnbürsten, Aufsteckköpfe für elektrische Bürsten und Zungenschaber gehören hierher."
        ),
        "semantic_tags": ["dental", "zahnpflege", "zahnpasta", "mundpflege", "colgate", "oral-b", "sensodyne", "mundwasser"],
    },
    {
        "id": "sonnenpflege",
        "category_id": "sonnenpflege",
        "name": "Sonnenpflege & Sonnenschutz",
        "description_text": (
            "Sonnenpflege und Sonnenschutz umfassen Sonnenschutzcremes, -sprays, -öle, After-Sun-Produkte, Selbstbräuner "
            "und UV-Schutzprodukte für Gesicht und Körper in verschiedenen Lichtschutzfaktoren (LSF 6–50+). "
            "Typische Marken sind Nivea Sun, Garnier Ambre Solaire, Hawaiian Tropic und Eigenmarken für Strand, Outdoor und Alltag. "
            "Abzugrenzen von allgemeiner Körper- und Gesichtspflege durch den spezifischen UV-Schutz- und Sonnencharakter. "
            "Auch Kinderssonnencreme, Lip-Balm mit LSF, After-Sun-Gels und Tan-Enhancer gehören hierher."
        ),
        "semantic_tags": ["sunscreen", "sonnenschutz", "sonnenpflege", "lsf", "after-sun", "nivea-sun", "strand", "uv"],
    },
    {
        "id": "gesichtspflege",
        "category_id": "gesichtspflege",
        "name": "Gesichtspflege",
        "description_text": (
            "Gesichtspflege umfasst Gesichtscremes, Tagespflege, Nachtcreme, Augencreme, Seren, Tonikums, "
            "Gesichtsmasken, Reinigungsgele, Mizellenwasser und Peelings für die tägliche Hautpflegeroutine. "
            "Typische Marken sind Nivea, Garnier, L'Oréal, ISANA und Neutrogena mit Produkten für verschiedene Hauttypen (trocken, fettig, sensibel, Mischhaut). "
            "Abzugrenzen von Körperpflege durch den ausschließlichen Gesichtsbezug und von dekorativer Kosmetik durch den pflegenden statt abdeckenden Charakter. "
            "Auch BB-Creams mit Pflege, Hyaluron-Seren, Anti-Aging-Produkte und Gesichtsmist gehören hierher."
        ),
        "semantic_tags": ["face care", "gesichtspflege", "creme", "serum", "reinigung", "nivea", "garnier", "anti-aging"],
    },
    {
        "id": "haarpflege",
        "category_id": "haarpflege",
        "name": "Haarpflege & Haarstyling",
        "description_text": (
            "Haarpflege und Haarstyling umfassen Shampoo, Spülung, Conditioner, Haarkur, Leave-in-Pflege, Haarmasken, "
            "Haargel, Haarspray, Mousse, Haarfarbe, Tönungen und Haarwachse für verschiedene Haartypen und Stylingzwecke. "
            "Typische Marken sind Pantene, Head & Shoulders, Syoss, Schwarzkopf und Eigenmarken; Produkte decken Pflege und Styling gleichermaßen ab. "
            "Abzugrenzen von allgemeiner Körperpflege durch den spezifischen Haar- und Kopfhautbezug. "
            "Auch Haartrockengeräte, Glätteisen und Haarbürsten-Sets als Zubehör können hierher gehören."
        ),
        "semantic_tags": ["hair care", "haarpflege", "shampoo", "haarpflege", "haarfarbe", "syoss", "schwarzkopf", "styling"],
    },
    {
        "id": "batterien",
        "category_id": "batterien",
        "name": "Batterien & Elektronikzubehör",
        "description_text": (
            "Diese Kategorie umfasst Einweg- und Ladebatterien, Akkus, Ladegeräte und einfaches Elektronikzubehör "
            "wie USB-Kabel, Adapter und Speicherkarten für den täglichen Gerätebetrieb. "
            "Typische Marken sind Duracell, Energizer, Varta und Eigenmarken in Einzel- und Multipacks verschiedener Batterietypen (AA, AAA, 9V). "
            "Abzugrenzen von vollständigen Elektrogeräten und Smartphones, da hier nur Zubehör und Stromversorgungsartikel im Fokus stehen. "
            "Auch Knopfzellen, Hörgerätebatterien und Powerbanks gehören hierher."
        ),
        "semantic_tags": ["batteries", "batterien", "akku", "duracell", "varta", "usb", "ladegerät", "elektronik"],
    },
    {
        "id": "haustierbedarf",
        "category_id": "haustierbedarf",
        "name": "Haustierbedarf",
        "description_text": (
            "Haustierbedarf umfasst Futter, Leckerlis, Spielzeug, Pflegeartikel, Körbe, Leinen, Transportboxen "
            "und Hygienezubehör für Hunde, Katzen, Kleintiere und Vögel. "
            "Typische Marken sind Whiskas, Pedigree, RC Royal Canin, Trixie und Eigenmarken in Dosen, Beuteln, Sets und Einzelprodukten. "
            "Abzugrenzen von Lebensmitteln für Menschen und allgemeinen Haushaltswaren durch den Tierversorgungszweck. "
            "Auch Tierfutterautomaten, Leckblöcke und Käfigzubehör gehören hierher."
        ),
        "semantic_tags": ["pet", "haustierbedarf", "tierfutter", "hund", "katze", "leckerlis", "pedigree", "whiskas"],
    },
    {
        "id": "tierfutter",
        "category_id": "tierfutter",
        "name": "Tierfutter & Tiernahrung",
        "description_text": (
            "Tierfutter und Tiernahrung umfassen Nass- und Trockenfutter für Hunde, Katzen, Kleintiere, Fische und Vögel "
            "in Dosen, Beuteln, Packungen und Eimern mit Angaben zu Tier, Rasse, Alter und Geschmack. "
            "Typische Marken sind Friskies, Felix, Chappi, Animonda und Eigenmarken; Produkte reichen von Basisfutter bis zu Premium-Tiernahrung. "
            "Abzugrenzen von Tierpflegezubehör und allgemeinem Haustierbedarf, da hier ausschließlich Futtermittel im Vordergrund stehen. "
            "Auch Snacks, Kauartikel und Ergänzungsfuttermittel für Tiere gehören hierher."
        ),
        "semantic_tags": ["pet food", "tierfutter", "hundefutter", "katzenfutter", "nass-trocken", "felix", "chappi"],
    },
    # ── Clothing & textiles ──────────────────────────────────────────────────
    {
        "id": "bekleidung",
        "category_id": "bekleidung",
        "name": "Bekleidung & Mode",
        "description_text": (
            "Bekleidung und Mode umfassen Damen-, Herren- und Unisex-Bekleidung wie T-Shirts, Hosen, Pullover, Jacken, "
            "Unterwäsche, Socken, Shorts und Freizeitbekleidung in verschiedenen Größen und Passformen. "
            "Typische Materialien sind Baumwolle, Polyester, Elasthan und Mischgewebe; angeboten von Eigenmarken und ModeMarken. "
            "Abzugrenzen von Kinderbekleidung durch die Ausrichtung auf Erwachsenengrößen und von Schuhen durch den Textilcharakter. "
            "Auch Saisonware, Sportbekleidung und Loungewear gehören hierher."
        ),
        "semantic_tags": ["clothing", "bekleidung", "mode", "shirt", "hose", "jacke", "unterwaesche", "freizeit"],
    },
    {
        "id": "kinderkleidung",
        "category_id": "kinderkleidung",
        "name": "Kinderkleidung",
        "description_text": (
            "Kinderkleidung umfasst Bekleidung für Babys, Kleinkinder und Kinder in Größen von 50 bis 176, "
            "darunter Bodys, Strampler, T-Shirts, Hosen, Jacken, Schuluniformen und Sportkleidung. "
            "Typische Merkmale sind kindgerechte Designs, Lizenzprints (z. B. Disney, Paw Patrol), robuste Materialien und pflegeleichte Textilien. "
            "Abzugrenzen von Erwachsenenbekleidung durch die Größenauslegung und von Spielzeug durch den Textilcharakter. "
            "Auch Baby-Accessoires, Mützen und Handschuhe für Kinder gehören hierher."
        ),
        "semantic_tags": ["kids clothing", "kinderkleidung", "baby", "kleinkind", "schulkind", "lizenz", "baumwolle"],
    },
    {
        "id": "schuhe",
        "category_id": "schuhe",
        "name": "Schuhe & Schuhzubehör",
        "description_text": (
            "Schuhe umfassen Sneaker, Sandalen, Hausschuhe, Gummistiefel, Freizeitschuhe und Sportschuhe "
            "für Damen, Herren und Kinder in verschiedenen Größen und Farbvarianten. "
            "Typische Materialien sind Textil, Synthetik, Lederimitat und Gummi; Merkmale sind Sohle, Verschluss und Einsatzzweck. "
            "Abzugrenzen von Bekleidung und Accessoires durch den Schuhcharakter als Fußbekleidung. "
            "Auch Einlegesohlen, Schuhpflege und Zubehör für Schuhe gehören hierher."
        ),
        "semantic_tags": ["shoes", "schuhe", "sneaker", "sandalen", "hausschuhe", "sport", "gummistiefel"],
    },
    {
        "id": "heimtextilien",
        "category_id": "heimtextilien",
        "name": "Heimtextilien & Wohntextilien",
        "description_text": (
            "Heimtextilien umfassen Küchentücher, Handtücher, Badetücher, Tischdecken, Vorhänge, Gardinen, Kissen, "
            "Dekokissen und textile Wohnaccessoires für Küche, Bad und Wohnzimmer. "
            "Typische Materialien sind Baumwolle, Frottee, Leinen und Polyester; angeboten in Sets, Einzelstücken und Rollen. "
            "Abzugrenzen von Bettwaren durch den Wohn- und Dekocharakter und von Bekleidung durch den Haustextilbezug. "
            "Auch Badezimmertextilien, Küchenhandtücher und Tischläufer gehören hierher."
        ),
        "semantic_tags": ["home textiles", "heimtextilien", "handtuch", "kissen", "vorhang", "küchentextil", "bad", "deko"],
    },
    {
        "id": "bettwaren",
        "category_id": "bettwaren",
        "name": "Bettwaren & Bettwäsche",
        "description_text": (
            "Bettwaren umfassen Bettwäsche-Sets, Kissenbezüge, Bettbezüge, Spannbettlaken, Kissen, Bettdecken, "
            "Matratzenschoner und Füllkissen für erholsamen Schlaf in verschiedenen Größen (135×200 cm, 155×220 cm). "
            "Typische Materialien sind Baumwolle, Renforcé, Satin, Micro und Allergikergeeignete Materialien; "
            "angeboten als Garnitur oder Einzelstück. "
            "Abzugrenzen von Heimtextilien durch den Schlafbereich-Fokus und von Möbeln durch den textilen Charakter. "
            "Auch Kinderbettgarnituren, Reisekissen und Schlafmasken gehören hierher."
        ),
        "semantic_tags": ["bedding", "bettwaesche", "bettdecke", "kissen", "spannbettlaken", "schlaf", "garnitur"],
    },
    # ── Electronics & appliances ─────────────────────────────────────────────
    {
        "id": "elektrogeraete",
        "category_id": "elektrogeraete",
        "name": "Elektrogeräte & Haushaltsgeräte",
        "description_text": (
            "Elektrogeräte umfassen Staubsauger, Wasserkocher, Toaster, Mixer, Kaffeemaschinen, Bügeleisen, "
            "Ventilatoren, Heizlüfter und andere elektrisch betriebene Haushaltsgeräte für Küche und Haushalt. "
            "Typische Marken sind Philips, Tefal, Bosch, Rowenta und Eigenmarken mit technischen Leistungsdaten und Ausstattungsmerkmalen. "
            "Abzugrenzen von Smartphones und Computern durch den Haushalts- und Küchengerätecharakter. "
            "Auch kleine Akku-Geräte wie Stabmixer und Küchenmaschinen gehören hierher."
        ),
        "semantic_tags": ["appliances", "elektrogeraete", "haushalt", "staubsauger", "kaffeemaschine", "philips", "bosch"],
    },
    {
        "id": "klimageraete",
        "category_id": "klimageraete",
        "name": "Klimageräte & Ventilatoren",
        "description_text": (
            "Klimageräte und Ventilatoren umfassen Standventilatoren, Turmventilatoren, mobile Klimaanlagen, "
            "Luftkühler, Luftentfeuchter und Luftbefeuchter für Kühlung und Klimatisierung in Wohn- und Arbeitsräumen. "
            "Typische Marken sind Honeywell, Trotec, Suntec und Eigenmarken; Produkte werden mit Leistungsangaben in Watt und BTU beworben. "
            "Abzugrenzen von allgemeinen Elektrogeräten durch den spezifischen Klimatisierungs- und Lüftungszweck. "
            "Auch Heizlüfter, Infrarotheizungen und Raumluftprodukte gehören hierher."
        ),
        "semantic_tags": ["air conditioning", "klimageraet", "ventilator", "kuehlung", "sommer", "lüfter", "trotec"],
    },
    {
        "id": "computer",
        "category_id": "computer",
        "name": "Computer & Laptops",
        "description_text": (
            "Diese Kategorie umfasst Laptops, Notebooks, Tablets, Desktop-Computer, Chromebooks und dazugehöriges Zubehör "
            "wie Mäuse, Tastaturen, Monitore und Taschen für Arbeit, Studium und Freizeit. "
            "Typische Marken sind Acer, Lenovo, HP, Samsung und Apple mit technischen Spezifikationen wie RAM, Speicher und Prozessor. "
            "Abzugrenzen von Smartphones durch den größeren Formfaktor und Produktivitätsfokus. "
            "Auch Gaming-Laptops, 2-in-1-Geräte und Refurbished-Geräte gehören hierher."
        ),
        "semantic_tags": ["computer", "laptop", "notebook", "tablet", "acer", "lenovo", "hp", "bildschirm"],
    },
    {
        "id": "smartphones",
        "category_id": "smartphones",
        "name": "Smartphones & Mobiltelefone",
        "description_text": (
            "Smartphones und Mobiltelefone umfassen Handys mit Touchscreen, Kameras, Apps und Mobilfunkverbindung "
            "in verschiedenen Speicher- und Displaygrößen von Marken wie Samsung, Apple, Xiaomi und Motorola. "
            "Typische Vermarktungsmerkmale sind Kamerasystem, Akkukapazität, 5G-Fähigkeit und Betriebssystem. "
            "Abzugrenzen von Tablets und Laptops durch den primären Telefon- und Kommunikationscharakter. "
            "Auch Smartwatches, Fitnessbänder und Mobilfunkzubehör können hierher gehören."
        ),
        "semantic_tags": ["smartphones", "handy", "mobiltelefon", "samsung", "apple", "5g", "kamera", "android"],
    },
    # ── Garden & outdoor ─────────────────────────────────────────────────────
    {
        "id": "pflanzen",
        "category_id": "pflanzen",
        "name": "Pflanzen & Gartenpflanzen",
        "description_text": (
            "Pflanzen umfassen Topfpflanzen, Zimmerpflanzen, Balkonpflanzen, Kräuter, Gemüsepflanzen, Setzlinge "
            "und saisonale Beetpflanzen für Innen- und Außenbereiche in Töpfen, Trays und Schalen. "
            "Typische Produkte sind Geranien, Petunien, Sukkulenten, Basilikum, Tomaten-Jungpflanzen und Rosen "
            "mit Angaben zu Standort, Gießbedarf und Topfgröße. "
            "Abzugrenzen von Schnittblumen und Saatgut, da hier lebende Pflanzen mit Wurzelballen im Mittelpunkt stehen. "
            "Auch Bäume, Sträucher und Dauerstauden gehören hierher."
        ),
        "semantic_tags": ["plants", "pflanzen", "balkon", "kräuter", "blumen", "zimmerpflanze", "garten", "setzling"],
    },
    {
        "id": "garten-artikel",
        "category_id": "garten-artikel",
        "name": "Gartenartikel & Gartenzubehör",
        "description_text": (
            "Gartenartikel und Gartenzubehör umfassen Bewässerungssysteme, Gießkannen, Pflanzerde, Dünger, Töpfe, "
            "Pflanzkübel, Rankgitter, Gartenwerkzeug und Zubehör für Pflege und Gestaltung von Garten und Balkon. "
            "Typische Marken sind Gardena, Dehner und Eigenmarken; Produkte aus Kunststoff, Metall und Terracotta. "
            "Abzugrenzen von Gartenmöbeln durch den Pflege- und Zubehörcharakter und von Pflanzen durch den Non-Living-Charakter. "
            "Auch Vogelschutznetze, Schneckenabwehr und Pflanzensubstrate gehören hierher."
        ),
        "semantic_tags": ["garden", "garten", "giesskanne", "erde", "duenger", "töpfe", "gardena", "balkon"],
    },
    {
        "id": "garten-moebel",
        "category_id": "garten-moebel",
        "name": "Gartenmöbel & Outdoormöbel",
        "description_text": (
            "Gartenmöbel und Outdoormöbel umfassen Gartenstühle, Loungegarnituren, Gartenliegen, Sonnenliegen, "
            "Gartentische, Hollywoodschaukeln, Sonnenschirme und Balkonmöbel-Sets für Terrasse, Garten und Balkon. "
            "Typische Materialien sind Polyrattan, Aluminium, Stahl, Holz und Kunststoff; angeboten als Einzelstücke oder Sets. "
            "Abzugrenzen von Gartenzubehör durch den Möbelcharakter und von Wohnmöbeln durch die Outdoor-Auslegung. "
            "Auch Sitzauflagen, Abdeckhauben und Ersatzpolster gehören hierher."
        ),
        "semantic_tags": ["garden furniture", "gartenmoebel", "lounge", "sonnenschirm", "terrasse", "polyrattan", "outdoor"],
    },
    {
        "id": "insektenschutz",
        "category_id": "insektenschutz",
        "name": "Insektenschutz & Mückenschutz",
        "description_text": (
            "Insektenschutz umfasst Fliegengitter, Mückenschutzsprays, Mückenspiralen, Insektenlampen, Klebefallen, "
            "Wespenköder und Produkte zur Abwehr von Insekten in Haus und Garten. "
            "Typische Marken sind Autan, Raid, Mosquito und Eigenmarken; Produkte für Fenster, Türen, Körperschutz und Außenbereiche. "
            "Abzugrenzen von allgemeinem Gartenartikeln durch den spezifischen Insektenschutzzweck. "
            "Auch UV-Insektenvernichter, Ultraschallvertreiber und Ameisenköder gehören hierher."
        ),
        "semantic_tags": ["insect protection", "mueckenschutz", "fliegengitter", "autan", "raid", "insekten", "sommer"],
    },
    # ── Travel & transport ───────────────────────────────────────────────────
    {
        "id": "reise-gepaeck",
        "category_id": "reise-gepaeck",
        "name": "Reisegepäck & Koffer",
        "description_text": (
            "Reisegepäck und Koffer umfassen Trolleys, Hartschalen- und Weichgepäck-Koffer, Reisetaschen, Weekender "
            "und Reise-Sets in verschiedenen Größen für Kabine, Check-in und langen Urlaub. "
            "Typische Materialien sind Polyester, Polycarbonat, ABS und Aluminium mit Teleskopgriffen, Rollen und TSA-Schlössern. "
            "Abzugrenzen von allgemeinen Taschen durch die Auslegung auf Reisen, Mobilität und Flugzeug-Gepäckregeln. "
            "Auch Packtaschen, Kompressionspackwürfel und Kofferanhänger gehören hierher."
        ),
        "semantic_tags": ["luggage", "koffer", "trolley", "reise", "reisetasche", "handgepäck", "urlaub"],
    },
    {
        "id": "auto-zubehoer",
        "category_id": "auto-zubehoer",
        "name": "Autozubehör",
        "description_text": (
            "Autozubehör umfasst Produkte für Innenraum, Pflege und Komfort im Auto wie Sonnenschutz, Organizer, "
            "Sitzbezüge, Lenkradbezüge, Reifendruckprüfer, Verbandskästen, Warnwesten und Navigationshalterungen. "
            "Typische Marken sind Carpoint, Streetwize und Eigenmarken; Produkte aus Kunststoff, Textil und Gummi. "
            "Abzugrenzen von allgemeinen Haushaltsartikeln durch den spezifischen Fahrzeug- und Kfz-Bezug. "
            "Auch Autopflege, Scheibenreiniger und Kfz-Reinigungsartikel gehören hierher."
        ),
        "semantic_tags": ["car accessories", "auto", "fahrzeug", "sonnenschutz", "organizer", "kfz", "pflege"],
    },
    {
        "id": "taschen",
        "category_id": "taschen",
        "name": "Taschen & Rucksäcke",
        "description_text": (
            "Taschen und Rucksäcke umfassen Handtaschen, Damentaschen, Schulrucksäcke, Sporttaschen, Umhängetaschen, "
            "Clutches und Tagesrucksäcke für Schule, Freizeit, Sport und Alltag. "
            "Typische Materialien sind Kunstleder, Nylon, Polyester und Canvas; angeboten von Eigenmarken und Modemarken. "
            "Abzugrenzen von Reisegepäck durch den Alltagscharakter und fehlende Rollensysteme. "
            "Auch Gürteltaschen, Laptop-Rucksäcke und Fahrradtaschen gehören hierher."
        ),
        "semantic_tags": ["bags", "taschen", "rucksack", "handtasche", "sporttasche", "schule", "alltag"],
    },
    # ── Leisure & stationery ─────────────────────────────────────────────────
    {
        "id": "spielzeug",
        "category_id": "spielzeug",
        "name": "Spielzeug & Spiele",
        "description_text": (
            "Spielzeug und Spiele umfassen Gesellschaftsspiele, Kartenspiele, Puzzles, Actionfiguren, Puppen, "
            "Fahrzeugspielzeug, Konstruktionsspielzeug und pädagogisches Spielzeug für Kinder aller Altersgruppen. "
            "Typische Marken sind Lego, Mattel, Ravensburger, Playmobil und Eigenmarken mit Altersempfehlungen und Sicherheitskennzeichen. "
            "Abzugrenzen von Schreibwaren und Bastelbedarf durch den reinen Spielcharakter. "
            "Auch Outdoor-Spielzeug, Wasserspielzeug und elektrisches Spielzeug gehören hierher."
        ),
        "semantic_tags": ["toys", "spielzeug", "spiele", "lego", "puzzle", "kinder", "ravensburger", "mattel"],
    },
    {
        "id": "schreibwaren",
        "category_id": "schreibwaren",
        "name": "Schreibwaren & Schulbedarf",
        "description_text": (
            "Schreibwaren und Schulbedarf umfassen Stifte, Hefte, Blöcke, Füller, Radiergummis, Scheren, Kleber, "
            "Lineale, Schulranzen, Federmäppchen und Schulutensilien für Schüler und Büroarbeit. "
            "Typische Marken sind Pelikan, Stabilo, Faber-Castell und Eigenmarken; Produkte für Schulanfang-Aktionen besonders relevant. "
            "Abzugrenzen von Bürobedarf durch den Schulkind-Fokus und von Bastelbedarf durch den Lerncharakter. "
            "Auch Schulbücher, Lernhilfen und Etuis gehören hierher."
        ),
        "semantic_tags": ["stationery", "schreibwaren", "schule", "stifte", "pelikan", "schulbedarf", "hefte", "füller"],
    },
    {
        "id": "buerobedarf",
        "category_id": "buerobedarf",
        "name": "Bürobedarf & Bürozubehör",
        "description_text": (
            "Bürobedarf und Bürozubehör umfassen Drucker-Papier, Ordner, Tacker, Locher, Klammern, Notizblöcke, "
            "Tischkalender, Etiketten, Druckerpatronen und Organisationsmittel für Büro und Homeoffice. "
            "Typische Marken sind Avery, Leitz, Herlitz und Eigenmarken; Produkte für produktives Arbeiten zuhause und im Büro. "
            "Abzugrenzen von Schreibwaren durch den professionellen Bürocharakter und von Elektronik durch den analogen Fokus. "
            "Auch Präsentationsartikel, Whiteboards und Postbedarf gehören hierher."
        ),
        "semantic_tags": ["office supplies", "buerobedarf", "ordner", "papier", "tacker", "homeoffice", "leitz", "etiketten"],
    },
    {
        "id": "bastelbedarf",
        "category_id": "bastelbedarf",
        "name": "Bastelbedarf & Kreativbedarf",
        "description_text": (
            "Bastelbedarf und Kreativbedarf umfassen Bastelkleber, Buntstifte, Fingerfarben, Tonpapier, Washi-Tape, "
            "Perlen, Wolle, Sticksets und kreative DIY-Materialien für Kinder und Erwachsene. "
            "Typische Marken sind Creativ Company, Faber-Castell und Eigenmarken; besonders beliebt zu Schulanfang, Weihnachten und Ostern. "
            "Abzugrenzen von Schreibwaren durch den kreativ-handwerklichen Charakter und von Spielzeug durch den Herstellungsfokus. "
            "Auch Näh- und Strickzubehör, Bastelsets und DIY-Kits gehören hierher."
        ),
        "semantic_tags": ["craft", "basteln", "kreativ", "farben", "kleber", "kinder", "diy", "wolle", "stricken"],
    },
    {
        "id": "partyartikel",
        "category_id": "partyartikel",
        "name": "Partyartikel & Festbedarf",
        "description_text": (
            "Partyartikel und Festbedarf umfassen Luftballons, Girlanden, Wimpelketten, Einweggeschirr, Party-Sets, "
            "Dekoration, Kerzen, Tischdekoartikel und Weihnachts-/Osterdekoration für Feiern und Feste. "
            "Typische Marken sind Papstar, Folat und Eigenmarken; Produkte für Geburtstage, Silvester, Weihnachten und andere Anlässe. "
            "Abzugrenzen von Haushaltswaren durch den Festcharakter und temporären Dekorationsbezug. "
            "Auch Konfetti, Partymasken, Trinkhalme und Tischkartenhalter gehören hierher."
        ),
        "semantic_tags": ["party", "feier", "dekoration", "luftballon", "kerzen", "geburtstag", "weihnachten", "fest"],
    },
    # ── Tools ────────────────────────────────────────────────────────────────
    {
        "id": "werkzeug",
        "category_id": "werkzeug",
        "name": "Werkzeug & Heimwerkerbedarf",
        "description_text": (
            "Werkzeug und Heimwerkerbedarf umfassen Schraubenzieher, Bohrmaschinen, Akkuschrauber, Sägen, Zangen, "
            "Hammer, Wasserwaagen, Messgeräte und Heimwerker-Sets für Renovierung, Montage und Reparatur. "
            "Typische Marken sind Bosch, Makita, Metabo und Eigenmarken mit technischen Angaben zu Leistung, Akku und Zubehör. "
            "Abzugrenzen von Gartenartikeln durch den Innen- und Handwerksfokus und von Küchenartikeln durch den Werkzeugcharakter. "
            "Auch Befestigungsmittel, Dübel, Schrauben und Klebeprodukte für Heimwerker gehören hierher."
        ),
        "semantic_tags": ["tools", "werkzeug", "heimwerker", "bosch", "akkuschrauber", "säge", "hammer", "renovierung"],
    },
]

# ---------------------------------------------------------------------------
# Embedding client (copied from promotion_ingestion/processor.py)
# ---------------------------------------------------------------------------

_EMBED_BATCH_SIZE = 256
_EMBED_MAX_RETRIES = 3
_EMBED_INITIAL_BACKOFF = 1.0


class _ScopedCredential:
    """Wraps a TokenCredential to always request the Cognitive Services scope."""
    _SCOPE = "https://cognitiveservices.azure.com/.default"

    def __init__(self, inner) -> None:
        self._inner = inner

    def get_token(self, *_scopes, **kwargs):
        return self._inner.get_token(self._SCOPE, **kwargs)


def _create_embedding_client(model: str):
    """Return a FoundryEmbeddingClient for *model* (mirrors processor.py logic)."""
    from agent_framework.foundry import FoundryEmbeddingClient  # type: ignore

    endpoint = os.getenv("FOUNDRY_MODELS_ENDPOINT", "").strip()
    if not endpoint:
        project_endpoint = (
            os.getenv("AZURE_AI_PROJECT_ENDPOINT", "").strip()
            or os.getenv("FOUNDRY_PROJECT_ENDPOINT", "").strip()
        )
        if project_endpoint:
            parsed = urlparse(project_endpoint)
            endpoint = f"{parsed.scheme}://{parsed.netloc}/models"

    api_key = os.getenv("FOUNDRY_MODELS_API_KEY", "").strip() or None
    logger.info(
        "Embedding client: endpoint=%r  model=%r  auth=%s",
        endpoint,
        model,
        "api_key" if api_key else "DefaultAzureCredential",
    )
    if api_key:
        return FoundryEmbeddingClient(model=model, endpoint=endpoint, api_key=api_key)
    return FoundryEmbeddingClient(
        model=model,
        endpoint=endpoint,
        credential=_ScopedCredential(DefaultAzureCredential()),
    )


async def _embed(texts: list[str], model: str) -> list[list[float]] | None:
    """Generate embeddings in batches with retry on rate-limit/transient errors."""
    if not model or not texts:
        return None

    client = _create_embedding_client(model)
    resolved_endpoint = (
        os.getenv("FOUNDRY_MODELS_ENDPOINT")
        or "(derived from AZURE_AI_PROJECT_ENDPOINT)"
    )

    async def _call_batch(batch: list[str]) -> list[list[float]]:
        backoff = _EMBED_INITIAL_BACKOFF
        for attempt in range(1, _EMBED_MAX_RETRIES + 1):
            try:
                resp = await client.get_embeddings(batch)
                return [item.vector for item in resp]
            except HttpResponseError as exc:
                status = exc.status_code
                if status == 404:
                    logger.error(
                        "Embedding 404 — resource not found (config error, not retrying).\n"
                        "  endpoint=%r  model=%r\n  Detail: %s",
                        resolved_endpoint, model, exc,
                    )
                    raise
                if status == 429 or (status is not None and status >= 500):
                    if attempt < _EMBED_MAX_RETRIES:
                        retry_after = float(getattr(exc, "retry_after", None) or backoff)
                        logger.warning(
                            "Embedding HTTP %s on attempt %d/%d — retrying in %.1fs.",
                            status, attempt, _EMBED_MAX_RETRIES, retry_after,
                        )
                        await asyncio.sleep(retry_after)
                        backoff *= 2
                        continue
                    logger.warning(
                        "Embedding HTTP %s — exhausted %d retries, skipping vectors.",
                        status, _EMBED_MAX_RETRIES,
                    )
                    raise
                logger.warning("Embedding HTTP %s (not retrying). Detail: %s", status, exc)
                raise
        raise RuntimeError("unreachable")

    try:
        results: list[list[float]] = []
        for start in range(0, len(texts), _EMBED_BATCH_SIZE):
            batch = texts[start: start + _EMBED_BATCH_SIZE]
            results.extend(await _call_batch(batch))
        return results
    except Exception as exc:
        logger.warning(
            "Embedding generation failed, uploading without vectors: %s\n"
            "  endpoint=%r  model=%r",
            exc, resolved_endpoint, model,
        )
        return None
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Search index upload
# ---------------------------------------------------------------------------

def _get_search_client(index_name: str) -> SearchClient:
    endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
    if not endpoint:
        raise RuntimeError("AZURE_SEARCH_ENDPOINT is required")
    api_key = os.getenv("AZURE_SEARCH_ADMIN_KEY", "").strip()
    credential = AzureKeyCredential(api_key) if api_key else DefaultAzureCredential()
    return SearchClient(endpoint=endpoint, index_name=index_name, credential=credential)


async def seed_categories(dry_run: bool = False) -> None:
    """Embed all categories and upload them to the category search index."""
    category_index = os.getenv("AZURE_SEARCH_CATEGORY_INDEX_NAME", "retail-categories")
    embedding_model = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME", "").strip()

    print(f"Seeding {len(CATEGORIES)} categories into index '{category_index}'.")
    if not embedding_model:
        print("WARNING: AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME not set — uploading without embeddings.")

    descriptions = [c["description_text"] for c in CATEGORIES]

    vectors: list[list[float]] | None = None
    if embedding_model and not dry_run:
        print(f"Generating embeddings via model '{embedding_model}' …")
        vectors = await _embed(descriptions, embedding_model)
        if vectors:
            print(f"Embeddings generated for {len(vectors)} categories.")
        else:
            print("Embedding generation failed — uploading without vectors.")

    docs = []
    for i, cat in enumerate(CATEGORIES):
        doc = {
            "id": cat["id"],
            "category_id": cat["category_id"],
            "name": cat["name"],
            "description_text": cat["description_text"],
            "semantic_tags": cat["semantic_tags"],
            "embedding": vectors[i] if vectors else [],
        }
        docs.append(doc)

    if dry_run:
        import json
        print("\n── DRY RUN — categories that would be uploaded ──")
        for doc in docs:
            preview = dict(doc)
            preview.pop("embedding", None)
            print(json.dumps(preview, ensure_ascii=False, indent=2))
        print(f"\n── {len(docs)} categories total (dry run, not uploaded) ──")
        return

    client = _get_search_client(category_index)
    result = client.merge_or_upload_documents(docs)
    succeeded = sum(1 for r in result if r.succeeded)
    failed = len(result) - succeeded
    print(f"Upload complete: {succeeded} succeeded, {failed} failed.")
    if failed > 0:
        for r in result:
            if not r.succeeded:
                logger.error("Failed to upload category '%s': %s", r.key, r.error_message)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for _noisy in (
        "azure.core.pipeline.policies.http_logging_policy",
        "azure.identity",
        "httpx",
    ):
        logging.getLogger(_noisy).setLevel(logging.WARNING)

    parser = argparse.ArgumentParser(
        description="Seed the retail-categories index with the canonical category taxonomy."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print categories to stdout without uploading to Azure AI Search.",
    )
    args = parser.parse_args()

    try:
        asyncio.run(seed_categories(dry_run=args.dry_run))
    except KeyboardInterrupt:
        print("\nInterrupted by user.", flush=True)
        raise SystemExit(130)


if __name__ == "__main__":
    main()
