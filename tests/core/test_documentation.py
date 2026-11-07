import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from core.documentation import (
    INSTRUCTIONS, doc_basis, document_table, markdown_filename, overview_markdown, table_context, table_markdown,
)
from core.parser import parse_database
from core.profiler import profile_database

MODEL = parse_database("data/chinook.db")
PROFILE = profile_database("data/chinook.db", model=MODEL)


class FakeOpenAI:
    def __init__(self):
        self.calls: list[dict] = []
        self.reply = {"summary": "Fake summary.", "columns": []}
        self.responses = self

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        doc = kwargs["text_format"].model_validate(self.reply)
        return SimpleNamespace(output_parsed=doc, usage=SimpleNamespace(input_tokens=100, output_tokens=20))


class RefusingFake(FakeOpenAI):
    def parse(self, **kwargs):
        return SimpleNamespace(output_parsed=None, usage=SimpleNamespace(input_tokens=1, output_tokens=1))


def table(name):
    return next(t for t in MODEL["tables"] if t["name"] == name)


def profile(name):
    return next(t for t in PROFILE["tables"] if t["table"] == name)


def rel(from_table, to_table, status):
    return {"from_table": from_table, "from_columns": [f"{to_table}Id"], "to_table": to_table,
            "to_columns": [f"{to_table}Id"], "source": "inferred", "status": status, "confidence": 0.9}


def test_context_never_holds_masked_values():
    customer = table("Customer")
    context = table_context(customer, profile("Customer"), [])
    assert "luisg@embraer.com.br" not in context and "***" in context  # Customer 1's real email
    marked = profile_database("data/chinook.db", model=MODEL, masked_columns=[("Customer", "City")])
    city_context = table_context(customer, next(t for t in marked["tables"] if t["table"] == "Customer"), [])
    assert "São José dos Campos" not in city_context  # Customer 1's city, user-marked


def test_context_lists_relationships_without_rejected_ones():
    rels = [rel("Album", "Artist", "accepted"), rel("Album", "Genre", "rejected"), rel("Album", "Track", "proposed")]
    context = table_context(table("Album"), profile("Album"), rels)
    assert "Artist" in context and "Genre" not in context and "proposed" in context


def test_document_table_sends_settings_and_instructions():
    fake = FakeOpenAI()
    doc, usage = document_table(fake, table("MediaType"), profile("MediaType"), [], model="gpt-6-luna", effort="low")
    call = fake.calls[0]
    assert (call["model"], call["reasoning"], call["instructions"]) == ("gpt-6-luna", {"effort": "low"}, INSTRUCTIONS)
    assert call["input"] == table_context(table("MediaType"), profile("MediaType"), [])
    assert usage == {"input_tokens": 100, "output_tokens": 20}


def test_doc_has_exactly_the_model_columns_in_order():
    fake = FakeOpenAI()
    fake.reply = {"summary": "s", "columns": [
        {"name": "Name", "description": "a", "certainty": "certain"},
        {"name": "Name", "description": "dup", "certainty": "likely"}]}
    doc, _ = document_table(fake, table("MediaType"), profile("MediaType"), [], model="m", effort="low")
    assert doc["columns"] == [{"name": "MediaTypeId", "description": "", "certainty": "uncertain"},
                              {"name": "Name", "description": "a", "certainty": "certain"}]


def test_schema_refuses_unknown_column_names():
    fake = FakeOpenAI()
    fake.reply = {"summary": "s", "columns": [{"name": "Bogus", "description": "x", "certainty": "certain"}]}
    with pytest.raises(ValidationError):
        document_table(fake, table("MediaType"), profile("MediaType"), [], model="m", effort="low")


def test_refusal_names_the_table():
    fake = RefusingFake()
    with pytest.raises(ValueError, match="MediaType"):
        document_table(fake, table("MediaType"), profile("MediaType"), [], model="m", effort="low")


def test_basis_survives_json_and_tracks_status():
    rels = [rel("Album", "Artist", "proposed")]
    basis = doc_basis(table("Album"), rels)
    assert json.loads(json.dumps(basis)) == basis
    assert doc_basis(table("Album"), [rel("Album", "Artist", "accepted")]) != basis
    assert doc_basis(table("Album"), [rel("Album", "Artist", "rejected")]) == doc_basis(table("Album"), [])
    assert doc_basis(table("Album"), [rel("Track", "Genre", "accepted")]) == doc_basis(table("Album"), [])


def test_markdown_filename_is_safe():
    assert markdown_filename("../x") == "___x.md"
    assert markdown_filename("a/b") == "a_b.md"
    assert markdown_filename("Order Details") == "Order Details.md"


def test_table_markdown_flags_proposed_and_hides_rejected():
    album = table("Album")
    md = table_markdown(album, {"summary": "S.", "status": "generated", "columns": []},
                        [rel("Album", "Artist", "proposed"), rel("Album", "Genre", "rejected")])
    assert md.startswith("# Album") and "Status: generated" in md
    assert "(assumed)" in md and "Genre" not in md


def test_table_markdown_key_column():
    md = table_markdown(table("Album"), {"summary": "S.", "status": "generated", "columns": []},
                        [rel("Album", "Artist", "proposed")])
    assert "| ArtistId | INT | FK → Artist.ArtistId (assumed) |" in md
    md = table_markdown(table("PlaylistTrack"), {"summary": "S.", "status": "generated", "columns": []},
                        [rel("PlaylistTrack", "Playlist", "accepted"), rel("PlaylistTrack", "Track", "proposed")])
    assert "| PlaylistId | INT | PK, FK → Playlist.PlaylistId |" in md
    assert "| TrackId | INT | PK, FK → Track.TrackId (assumed) |" in md


def test_markdown_keeps_newlines_out_of_rows_and_list_items():
    doc = {"summary": "One.\r\nTwo.", "status": "generated",
           "columns": [{"name": "Title", "description": "a\nb|c", "certainty": "likely"}]}
    assert "| likely | a b/c |" in table_markdown(table("Album"), doc, [])
    overview = overview_markdown("chinook", {"tables": [table("Album")]}, {"Album": doc}, [])
    assert "— One. Two." in overview
