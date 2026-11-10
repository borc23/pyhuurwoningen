from __future__ import annotations

import dataclasses
import types
import typing
from datetime import date, datetime
from typing import Any

from pyhuurwoningen.models import Listing, ListingDetails, SitemapEntry

JSON_SCHEMA_DRAFT = "https://json-schema.org/draft/2020-12/schema"

MODELS = (Listing, ListingDetails, SitemapEntry)

# What each field means. A test checks that every field of every model is here.
DESCRIPTIONS: dict[str, str] = {
    # Listing
    "id": "Short id from the URL, e.g. 'ea81e68d'. Unique per home.",
    "url": "Link to the home's page on huurwoningen.nl.",
    "title": "Title as shown, e.g. 'Appartement Zuidplein'.",
    "price": "Rent in euros per month.",
    "area": "Living area in square metres (m²).",
    "rooms": "Number of rooms.",
    "postcode": "Dutch postcode without a space, e.g. '3083CR'.",
    "city": "City name as shown, e.g. 'Rotterdam'. In a SitemapEntry: the URL form, e.g. 'den-haag'.",
    "neighbourhood": "Neighbourhood as shown, e.g. 'Zuidplein'.",
    "build_year": "Year the building was built.",
    "is_new": "Marked 'new' on the search page.",
    # ListingDetails
    "street": "Street name (sometimes with house number).",
    "latitude": "Latitude (WGS84).",
    "longitude": "Longitude (WGS84).",
    "deposit": "Deposit in euros.",
    "service_costs": "Service costs in euros per month.",
    "bedrooms": "Number of bedrooms.",
    "bathrooms": "Number of bathrooms.",
    "dwelling_type": "Kind of home as shown, e.g. 'Appartement', 'Huis', 'Studio', 'Kamer'.",
    "property_type": "Building type as shown, e.g. 'Galerijflat'.",
    "construction_type": "As shown, e.g. 'Bestaande bouw' (existing) or 'Nieuwbouw' (new build).",
    "interior": "As shown: usually 'Kaal' (bare), 'Gestoffeerd' (floors/curtains) or 'Gemeubileerd' (furnished).",
    "energy_label": "Energy label as shown, e.g. 'A', 'C'.",
    "status": "As shown, e.g. 'Te huur' (for rent), 'Onder optie' (under option), 'Verhuurd' (rented).",
    "is_available": "True if the status says it is still for rent.",
    "available_from": "As shown, e.g. 'Per direct' (right away) or a date in Dutch.",
    "offered_since": "Date the home was put online (YYYY-MM-DD).",
    "description": "The landlord's description text.",
    "image_url": "Link to the main photo.",
    "features": "Every label and value on the page, exactly as shown (Dutch).",
    # SitemapEntry
    "lastmod": "When the listing last changed (edits count too, so not the publish date).",
}

MODEL_DESCRIPTIONS = {
    Listing: "One home from the search results.",
    ListingDetails: "Everything on a home's own page (a Listing plus more fields).",
    SitemapEntry: "One line of the site's list of all listings.",
}

_SIMPLE = {
    str: {"type": "string"},
    int: {"type": "integer"},
    float: {"type": "number"},
    bool: {"type": "boolean"},
    date: {"type": "string", "format": "date"},
    datetime: {"type": "string", "format": "date-time"},
}


def schema(model: type | None = None) -> dict[str, Any]:
    """JSON Schema (draft 2020-12) for the results.

    model:  Listing, ListingDetails or SitemapEntry for just that one.
            Default: one line of `huurwoningen --json` output, which is a
            ListingDetails (with --details/--all) or a Listing.

    Every field is always present; fields the site doesn't show are null.
    """
    if model is not None:
        return {"$schema": JSON_SCHEMA_DRAFT, **_model_schema(model)}
    return {
        "$schema": JSON_SCHEMA_DRAFT,
        "title": "huurwoningen --json line",
        "description": "One home per line: a ListingDetails with --details/--all, otherwise a Listing.",
        "anyOf": [{"$ref": "#/$defs/ListingDetails"}, {"$ref": "#/$defs/Listing"}],
        "$defs": {m.__name__: _model_schema(m) for m in (Listing, ListingDetails)},
    }


def _model_schema(model: type) -> dict[str, Any]:
    if model not in MODEL_DESCRIPTIONS:
        raise ValueError(f"no schema for {model!r}; use one of {', '.join(m.__name__ for m in MODELS)}")
    hints = typing.get_type_hints(model)
    names = [f.name for f in dataclasses.fields(model)]
    return {
        "title": model.__name__,
        "description": MODEL_DESCRIPTIONS[model],
        "type": "object",
        "properties": {n: {**_type_schema(hints[n]), "description": DESCRIPTIONS[n]} for n in names},
        "required": names,
    }


def _type_schema(tp: Any) -> dict[str, Any]:
    args = typing.get_args(tp)
    if typing.get_origin(tp) in (typing.Union, types.UnionType) and type(None) in args:
        inner = _type_schema(next(a for a in args if a is not type(None)))
        return {**inner, "type": [inner["type"], "null"]}
    if typing.get_origin(tp) is dict:
        return {"type": "object", "additionalProperties": _type_schema(args[1])}
    if tp in _SIMPLE:
        return dict(_SIMPLE[tp])
    raise TypeError(f"no JSON Schema type for {tp!r}")
