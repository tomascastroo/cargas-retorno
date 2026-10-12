"""Agenda de contactos (dadores) por empresa: alta, importación CSV, aislamiento y sugerencias."""

import pytest
from conftest import Chat, auth, signup
from fastapi.testclient import TestClient

from cargas.contacts import ContactError, validate_contact

CONTACT = {
    "name": "Acopio Oncativo",
    "phone": "+54 9 3572 40-0101",
    "cities": ["cordoba"],
    "cargo_types": ["Granel"],
}


def test_validation_normalizes_and_rejects() -> None:
    data = validate_contact(CONTACT)
    assert data.phone == "5493572400101"
    assert data.cities == ["Córdoba"]
    assert data.cargo_types == ["granel"]
    for override, message in [
        ({"phone": None, "email": None}, "teléfono o email"),
        ({"phone": "123"}, "teléfono inválido"),
        ({"email": "no-es-mail"}, "email inválido"),
        ({"cities": ["Pueblo Inventado"]}, "localidad desconocida"),
        ({"cities": []}, "cities"),
        ({"cargo_types": ["electrónica"]}, "mercadería desconocida"),
    ]:
        with pytest.raises(ContactError, match=message):
            validate_contact({**CONTACT, **override})


def test_crud_and_duplicates(client: TestClient, company: dict[str, str]) -> None:
    headers = auth(company)
    created = client.post("/api/contacts", headers=headers, json=CONTACT)
    assert created.status_code == 201, created.text
    contact = created.json()
    assert contact["cities"] == ["Córdoba"]
    # Mismo teléfono escrito distinto: duplicado.
    dup = client.post(
        "/api/contacts", headers=headers, json={**CONTACT, "name": "Otro", "phone": "5493572400101"}
    )
    assert dup.status_code == 409
    bad = client.post("/api/contacts", headers=headers, json={**CONTACT, "cities": ["Atlantis"]})
    assert bad.status_code == 422
    assert "localidad desconocida" in bad.json()["detail"]

    updated = client.put(
        f"/api/contacts/{contact['id']}",
        headers=headers,
        json={**CONTACT, "cities": ["Córdoba", "Villa María"]},
    )
    assert updated.json()["cities"] == ["Córdoba", "Villa María"]
    by_city = client.get("/api/contacts?city=villa maria", headers=headers).json()
    assert [c["id"] for c in by_city] == [contact["id"]]

    assert client.delete(f"/api/contacts/{contact['id']}", headers=headers).status_code == 204
    assert client.get("/api/contacts", headers=headers).json() == []
    actions = {e["action"] for e in client.get("/api/audit-events", headers=headers).json()}
    assert {"contact_created", "contact_updated", "contact_deleted"} <= actions


CSV = """nombre;telefono;email;localidades;mercaderias;notas
Acopio Oncativo;5493572400101;;Córdoba|Villa María;granel;Cosecha gruesa
Molino Rosario;;despacho@molino.example;Rosario;general;
Sin contacto;;;Córdoba;;
Ciudad rara;5491100000000;;Gotham;;
Acopio repetido;+54 9 3572 40-0101;;Córdoba;;

Cerealera Norte;5493874000000;;Salta;GRANEL;
"""


def test_csv_import_reports_rows_and_updates_existing(
    client: TestClient, company: dict[str, str]
) -> None:
    headers = auth(company)
    report = client.post("/api/contacts/import", headers=headers, json={"csv": CSV}).json()
    assert report["created"] == 3
    assert report["updated"] == 0
    reasons = {r["line"]: r["reason"] for r in report["rejected"]}
    assert set(reasons) == {4, 5, 6}
    assert "teléfono o email" in reasons[4]
    assert "Gotham" in reasons[5]
    assert "repetido" in reasons[6]

    # Reimportar actualiza en vez de duplicar.
    again = "nombre,telefono,localidades\nAcopio Oncativo SA,5493572400101,Córdoba\n"
    report = client.post("/api/contacts/import", headers=headers, json={"csv": again}).json()
    assert (report["created"], report["updated"]) == (0, 1)
    names = [c["name"] for c in client.get("/api/contacts", headers=headers).json()]
    assert names == ["Acopio Oncativo SA", "Cerealera Norte", "Molino Rosario"]


def test_csv_without_required_columns_is_rejected(
    client: TestClient, company: dict[str, str]
) -> None:
    resp = client.post(
        "/api/contacts/import", headers=auth(company), json={"csv": "nombre,notas\nX,Y\n"}
    )
    assert resp.status_code == 422
    assert "faltan columnas" in resp.json()["detail"]


def test_contacts_are_private_to_each_company(client: TestClient) -> None:
    a, b = auth(signup(client)), auth(signup(client))
    contact = client.post("/api/contacts", headers=a, json=CONTACT).json()
    assert client.get("/api/contacts", headers=b).json() == []
    assert client.put(f"/api/contacts/{contact['id']}", headers=b, json=CONTACT).status_code == 404
    assert client.delete(f"/api/contacts/{contact['id']}", headers=b).status_code == 404
    # El mismo teléfono puede estar en la agenda de otra empresa.
    assert client.post("/api/contacts", headers=b, json=CONTACT).status_code == 201
    assert len(client.get("/api/contacts", headers=a).json()) == 1


def test_search_suggests_contacts_on_the_route(
    client: TestClient, company: dict[str, str], chat: Chat
) -> None:
    headers = auth(company)
    for contact in (
        {**CONTACT, "name": "En el origen", "phone": "5493510000001", "cities": ["Córdoba"]},
        {**CONTACT, "name": "Sobre la ruta", "phone": "5493530000002", "cities": ["Villa María"]},
        {**CONTACT, "name": "Lejos", "phone": "5492610000003", "cities": ["Mendoza"]},
    ):
        assert client.post("/api/contacts", headers=headers, json=contact).status_code == 201

    replies = chat.say("Vuelvo mañana de Córdoba a Buenos Aires vacío")
    assert replies[0]["payload"]["kind"] == "options"
    suggestion = replies[1]["payload"]
    assert suggestion["kind"] == "contacts"
    assert [c["name"] for c in suggestion["contacts"]] == ["En el origen", "Sobre la ruta"]
    assert "no les escribe" in replies[1]["text"]
    # Sigue el flujo normal: la elección se hace sobre las opciones.
    assert chat.say("1")[0]["payload"]["kind"] == "negotiation"


def test_contacts_suggested_even_without_results(
    client: TestClient, company: dict[str, str], chat: Chat
) -> None:
    client.post(
        "/api/contacts", headers=auth(company), json={**CONTACT, "cities": ["Comodoro Rivadavia"]}
    )
    replies = chat.say("Vuelvo mañana de Comodoro Rivadavia a Trelew")
    assert [r["payload"]["kind"] for r in replies] == ["no_results", "contacts"]


def test_no_contacts_no_extra_message(chat: Chat) -> None:
    replies = chat.say("Vuelvo mañana de Córdoba a Buenos Aires vacío")
    assert [r["payload"]["kind"] for r in replies] == ["options"]


def test_demo_seed_is_idempotent() -> None:
    import uuid

    from sqlalchemy import func, select

    from cargas import cli
    from cargas.db import tenant_session
    from cargas.models import ShipperContact

    for _ in range(2):
        cli.seed_companies("demo1234")
        cli.seed_contacts()
    for spec in cli.DEMO_COMPANIES:
        company_id = uuid.UUID(spec["id"])
        with tenant_session(company_id) as session:
            count = session.scalar(select(func.count()).select_from(ShipperContact))
        assert count == len(spec["contacts"])


def test_contacts_with_cargo_the_truck_cannot_carry_are_not_suggested(
    client: TestClient, company: dict[str, str], chat: Chat
) -> None:
    headers = auth(company)
    agent = client.get("/api/agent", headers=headers).json()
    agent["excluded_cargo_types"] = ["peligrosa"]
    client.put("/api/agent", headers=headers, json=agent)
    for name, phone, cargo in (
        ("Frigorífico", "5493510000011", ["refrigerada"]),  # el semi sider no tiene frío
        ("Agroquímicos", "5493510000012", ["peligrosa"]),  # excluida por la empresa
        ("Mixto", "5493510000013", ["refrigerada", "general"]),
        ("Sin datos", "5493510000014", []),
    ):
        client.post(
            "/api/contacts",
            headers=headers,
            json={"name": name, "phone": phone, "cities": ["Córdoba"], "cargo_types": cargo},
        )
    replies = chat.say("Vuelvo mañana de Córdoba a Buenos Aires vacío")
    names = [c["name"] for c in replies[1]["payload"]["contacts"]]
    assert names == ["Mixto", "Sin datos"]
